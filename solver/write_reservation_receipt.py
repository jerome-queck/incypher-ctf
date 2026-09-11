"""Independent reservation receipt verification and manifest adaptation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.write_reservation_contracts import (
    MANIFEST_RECEIPT_REF,
    MANIFEST_ROW_ID,
    RECEIPT_TYPE,
    SCHEMA_VERSION,
    WriteReservation,
    digest,
    profile_from,
)
from solver.write_reservation_proof import load_controlled_proof
from solver.write_reservation_storage import AuthorityStorage

if TYPE_CHECKING:
    from solver.write_reservation import WriteAuthority


def _receipt_document(
    profile_digest: str,
    reservation: WriteReservation,
    trace: list[dict[str, Any]],
    extent_remaining: int,
) -> dict[str, Any]:
    proof = load_controlled_proof()
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "profile_digest": profile_digest,
        "key": reservation.key,
        "effect_fingerprint": reservation.effect_fingerprint,
        "pool": reservation.pool.value,
        "need": reservation.need.as_dict(),
        "object_slots": list(reservation.object_slots),
        "state": reservation.state.value,
        "trace": trace,
        "extent_remaining": extent_remaining,
        "controlled_proof_digest": digest_bytes(canonical_bytes(proof)),
        "crash_point_outcomes": proof["crash_point_outcomes"],
        "fault_injection_result": proof["fault_injection_result"],
        "reserved_capacity_exhaustion_result": proof["reserved_capacity_exhaustion_result"],
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def receipt_document(authority: WriteAuthority, reservation: WriteReservation) -> dict[str, Any]:
    return _receipt_document(
        authority.profile_digest,
        reservation,
        authority.trace(reservation.key),
        authority.extent_path(reservation.pool).stat().st_size,
    )


def write_receipt(authority: WriteAuthority, key: str, destination: Path | None = None) -> Path:
    reservation = authority.current(key)
    if reservation is None:
        raise ValueError(f"unknown reservation {key!r}")
    document = receipt_document(authority, reservation)
    document["receipt_digest"] = digest(document)
    destination = destination or authority.receipts_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
    atomic_write(destination, canonical_bytes(document) + b"\n")
    return destination


def verify_receipt(authority: WriteAuthority, path: Path) -> dict[str, Any]:
    document = _read_document(path)
    current = authority.current(str(document.get("key", "")))
    if current is None or document != receipt_document(authority, current):
        raise ValueError("write-reservation receipt differs from durable authority")
    return document


def _read_document(path: Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    if raw != canonical_bytes(document) + b"\n":
        raise ValueError("write-reservation receipt is not canonical JSON")
    supplied = document.pop("receipt_digest", None)
    if supplied != digest(document):
        raise ValueError("write-reservation receipt digest mismatch")
    return document


def manifest_receipt(path: Path) -> dict[str, str]:
    receipt_path = Path(path)
    try:
        profile = profile_from(json.loads((receipt_path.parents[1] / "profile.json").read_text()))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("write-reservation receipt does not identify a sealed profile") from error
    document = _read_document(receipt_path)
    storage = AuthorityStorage(receipt_path.parents[2], profile, provision=False)
    with storage.locked():
        current = storage.latest_locked().get(str(document.get("key", "")))
        if current is None:
            raise ValueError("write-reservation receipt identifies no durable reservation")
        expected = _receipt_document(
            digest(profile.as_dict()),
            current,
            [row for row in storage.rows_locked() if row["key"] == current.key],
            storage.extent_path(current.pool).stat().st_size,
        )
    if document != expected:
        raise ValueError("write-reservation receipt differs from durable authority")
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    }
