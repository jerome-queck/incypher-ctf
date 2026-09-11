"""Independent reconstruction of storage pressure and retirement evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from solver.event_store import EventStore, ReservationStatus
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.storage_governor_contracts import (
    ReachabilityRoots,
    RetirableStorageClass,
    StorageCapacity,
    StorageGovernorProfile,
)
from solver.storage_governor_proof import load_controlled_proof
from solver.write_reservation import EffectIdentity, Pool, ReservationState
from solver.write_reservation_storage import AuthorityStorage

SCHEMA_VERSION = 1
RECEIPT_TYPE = "storage-governor"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
PROFILE_FILENAME = f"{RECEIPT_TYPE}.profile.json"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


def persist_profile(canonical_dir: Path, profile: StorageGovernorProfile) -> Path:
    path = canonical_dir / PROFILE_FILENAME
    encoded = canonical_bytes(profile.as_dict()) + b"\n"
    if path.exists():
        if path.read_bytes() != encoded:
            raise ValueError("sealed storage-governor profile changed within one Run")
    else:
        atomic_write(path, encoded)
    return path


def validate_complete_roots(
    store: EventStore,
    profile: StorageGovernorProfile,
    supplied: ReachabilityRoots,
) -> None:
    required = _authoritative_roots(store, profile)
    for field in ReachabilityRoots.__dataclass_fields__:
        if not getattr(required, field).issubset(getattr(supplied, field)):
            raise ValueError(f"storage-governor roots omit authoritative {field}")
    _verify_roots(store, supplied)


def _authoritative_roots(store: EventStore, profile: StorageGovernorProfile) -> ReachabilityRoots:
    events = store.events()
    classifications = {}
    storage = AuthorityStorage(store.run_dir, profile.write_profile(), provision=False)
    with storage.locked():
        reservations = storage.latest_locked()
    for event in events:
        if event.event_type != "storage-governor.recorded" or event.payload.get("record") != "storage-classification":
            continue
        resource = {
            "path": event.payload["path"],
            "digest": event.payload["digest"],
            "length": event.payload["length"],
            "event_sequences": event.payload["event_sequences"],
        }
        expected = EffectIdentity(
            "storage.classification",
            digest_bytes(canonical_bytes(resource)),
            digest_bytes(canonical_bytes({**resource, "storage_class": event.payload["storage_class"]})),
        )
        reservation = reservations.get(event.payload["reservation_key"])
        if (
            reservation is None
            or reservation.state is not ReservationState.COMMITTED
            or reservation.effect_fingerprint != expected.fingerprint
        ):
            raise ValueError("storage classification has no matching authority reservation")
        for sequence in event.payload["event_sequences"]:
            classifications[sequence] = event.payload["storage_class"]
    retirable = {item.value for item in RetirableStorageClass}
    live = [
        event
        for event in events
        if event.blob_bytes and event.body and classifications.get(event.sequence) not in retirable
    ]
    latest = {}
    for reservation in store.reservations():
        latest[reservation.reservation_id] = reservation
    in_flight = [
        reservation
        for reservation in latest.values()
        if reservation.status is ReservationStatus.RESERVED and reservation.blob_bytes
    ]
    return ReachabilityRoots(
        event_sequences=frozenset(event.sequence for event in live),
        blob_digests=frozenset(event.blob_digest for event in live),
        paths=_inventory(store.run_dir, "v1"),
        promoted_paths=_inventory(store.run_dir, "promoted"),
        in_flight_paths=frozenset(f"sealed/sha256/{reservation.blob_digest}" for reservation in in_flight),
        in_flight_digests=frozenset(reservation.blob_digest for reservation in in_flight),
    )


def _inventory(run_dir: Path, directory: str) -> frozenset[str]:
    root = run_dir / directory
    if not root.exists():
        return frozenset()
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"authoritative {directory} root is not a directory")
    paths = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"authoritative {directory} inventory contains a symlink")
        if path.is_file():
            paths.add(path.relative_to(run_dir).as_posix())
    return frozenset(paths)


def write_receipt(
    store: EventStore,
    profile: StorageGovernorProfile,
) -> Path:
    document = _reconstruct(store, profile)
    path = store.canonical_dir / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("storage-governor receipt cannot be read") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise ValueError("storage-governor receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise ValueError("storage-governor receipt path does not identify canonical Run state")
    try:
        profile_raw = json.loads((receipt_path.parent / PROFILE_FILENAME).read_text())
        profile = StorageGovernorProfile.from_dict(profile_raw)
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("storage-governor receipt has an invalid profile or roots") from error
    run_dir = receipt_path.parent.parent
    if run_dir.parent.name != "runs":
        raise ValueError("storage-governor receipt path does not identify canonical Run state")
    store = EventStore(run_dir.parent.parent, run_id=run_dir.name)
    if supplied != _reconstruct(store, profile):
        raise ValueError("storage-governor receipt differs from canonical state")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": digest_bytes(verified.read_bytes()),
    }


def _reconstruct(
    store: EventStore,
    profile: StorageGovernorProfile,
) -> dict[str, Any]:
    events = store.events()
    governor_events = [event for event in events if event.event_type == "storage-governor.recorded"]
    pressure_events = [event for event in governor_events if event.payload["record"] == "pressure-decision"]
    tombstones = [event for event in governor_events if event.payload["record"] == "retirement-tombstone"]
    completions = [event for event in governor_events if event.payload["record"] == "retirement-complete"]
    complete = {(event.payload["path"], event.payload["target_digest"]) for event in completions}
    pending = [
        event.payload["target_digest"]
        for event in tombstones
        if (event.payload["path"], event.payload["target_digest"]) not in complete
    ]
    root_events = [event for event in governor_events if event.payload["record"] == "reachability-snapshot"]
    if not root_events:
        raise ValueError("storage-governor receipt has no canonical reachability snapshot")
    roots = ReachabilityRoots.from_dict(root_events[-1].payload["roots"])
    validate_complete_roots(store, profile, roots)
    authority_decisions, remaining = _authority_evidence(store.run_dir, profile)
    by_request = {decision["request_id"]: decision for decision in authority_decisions}
    pressure = []
    for event in pressure_events:
        expected = by_request.get(event.payload["request_id"])
        observed = {
            name: event.payload[name]
            for name in (
                "request_id",
                "pressure",
                "admission_class",
                "admitted",
                "remaining",
                "need",
                "authority_headroom",
            )
        }
        if expected is None or observed != {name: expected[name] for name in observed}:
            raise ValueError("canonical pressure decision differs from durable authority")
        pressure.append(
            {
                **observed,
                "authority_key": expected["authority_key"],
                "authority_state": expected["authority_state"],
            }
        )
    controlled_proof = load_controlled_proof()
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": store.run_id,
        "profile": profile.as_dict(),
        "roots": roots.as_dict(),
        "pressure_decisions": pressure,
        "retired_digests": [event.payload["target_digest"] for event in completions],
        "retirement_transactions": [
            {
                "path": event.payload["path"],
                "storage_class": event.payload["storage_class"],
                "digest": event.payload["target_digest"],
                "length": event.payload["target_length"],
                "event_sequences": event.payload["event_sequences"],
                "reason": event.payload["reason"],
            }
            for event in completions
        ],
        "remaining_authority_headroom": remaining.as_dict(),
        "post_crash_verification": {
            "pending_retirements": pending,
            "verified_event_count": len(events),
            "canonical_chain_head": events[-1].event_digest if events else "",
        },
        "controlled_proof": controlled_proof,
        "controlled_proof_digest": digest_bytes(canonical_bytes(controlled_proof)),
    }


def _authority_evidence(run_dir: Path, profile: StorageGovernorProfile) -> tuple[list[dict[str, Any]], StorageCapacity]:
    storage = AuthorityStorage(run_dir, profile.write_profile(), provision=False)
    with storage.locked():
        headroom = storage.headroom_locked()
        reservations = storage.latest_locked().values()
    decisions = []
    admission_classes = {
        Pool.ORDINARY: "ordinary",
        Pool.SHARED: "authority",
        Pool.TERMINAL: "terminal",
        Pool.RECOVERY: "recovery",
    }
    for reservation in reservations:
        if reservation.identity.operation != "storage.admission" or not reservation.grant_headroom:
            continue
        remaining = _sum_headroom(reservation.grant_headroom.values())
        authority = _sum_headroom(
            capacity
            for name, capacity in reservation.grant_headroom.items()
            if name in {Pool.SHARED.value, Pool.TERMINAL.value, Pool.RECOVERY.value}
        )
        decisions.append(
            {
                "request_id": reservation.identity.subject,
                "pressure": _pressure(profile, remaining),
                "admission_class": admission_classes[reservation.pool],
                "admitted": reservation.state not in {ReservationState.REFUSED, ReservationState.ABORTED},
                "remaining": remaining.as_dict(),
                "need": StorageCapacity.from_headroom(reservation.need).as_dict(),
                "authority_headroom": authority.as_dict(),
                "authority_key": reservation.key,
                "authority_state": reservation.state.value,
            }
        )
    return decisions, _sum_headroom(headroom[pool.value] for pool in (Pool.SHARED, Pool.TERMINAL, Pool.RECOVERY))


def _sum_headroom(values) -> StorageCapacity:
    zero = StorageCapacity(0, 0, 0, 0, 0, 0, 0)
    total = zero
    for value in values:
        total += StorageCapacity.from_headroom(value)
    return total


def _pressure(profile: StorageGovernorProfile, remaining: StorageCapacity) -> str:
    if remaining.at_or_below(profile.authority_only_remaining):
        return "authority-only"
    if remaining.at_or_below(profile.stop_admission_remaining):
        return "stop-admission"
    if remaining.at_or_below(profile.warning_remaining):
        return "warning"
    return "normal"


def _verify_roots(store: EventStore, roots: ReachabilityRoots) -> None:
    events = {event.sequence: event for event in store.events()}
    for sequence in roots.event_sequences:
        if sequence not in events or (events[sequence].blob_bytes and not events[sequence].body):
            raise ValueError("storage-governor receipt has a missing reachable event body")
    for digest in roots.blob_digests | roots.in_flight_digests:
        store.blob(digest)
    run_dir = store.run_dir.resolve()
    for relative in roots.paths | roots.promoted_paths | roots.in_flight_paths:
        path = (run_dir / relative).resolve()
        if not path.is_file() or not path.is_relative_to(run_dir):
            raise ValueError("storage-governor receipt has a missing reachable path")


__all__ = ["manifest_receipt", "persist_profile", "validate_complete_roots", "verify_receipt", "write_receipt"]
