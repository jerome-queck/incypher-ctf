"""Independent reconstruction of Attempt Resource-envelope evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from solver.attempt_executor_contracts import EnvelopeRecord, ResourceOutcome
from solver.event_store import EventStore, InvalidReceiptError
from solver.event_store_contracts import ATTEMPT_ENVELOPE_RECORDED
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.isolation import STRICT_PROFILE_DIGEST
from solver.isolation_receipt import RECEIPT_FILENAME as ISOLATION_RECEIPT_FILENAME
from solver.isolation_receipt import verify_receipt as verify_isolation_receipt

SCHEMA_VERSION = 1
RECEIPT_TYPE = "attempt-resource-envelope"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.strict-isolation"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"
DENY_PROBES = (
    "deny_control",
    "deny_secrets",
    "deny_sibling",
    "deny_outside_workspace",
    "deny_public_network",
)
BREACH_OUTCOMES = frozenset(
    {
        ResourceOutcome.CPU.value,
        ResourceOutcome.MEMORY.value,
        ResourceOutcome.PIDS.value,
        ResourceOutcome.FILESYSTEM.value,
        ResourceOutcome.NETWORK.value,
        ResourceOutcome.DEADLINE.value,
    }
)


def write_receipt(state: Path, run_id: str, isolation_receipt: Path) -> Path:
    document = _reconstruct(Path(state), run_id, Path(isolation_receipt))
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path, *, require_qualified: bool = False) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Attempt Resource receipt cannot be read as JSON") from error
    if not isinstance(document, Mapping) or raw != canonical_bytes(document) + b"\n":
        raise InvalidReceiptError("Attempt Resource receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("Attempt Resource receipt path does not identify canonical Run state")
    run_id = receipt_path.parent.parent.name
    state = receipt_path.parents[3]
    isolation = receipt_path.parent / ISOLATION_RECEIPT_FILENAME
    expected = _reconstruct(state, run_id, isolation)
    if document != expected:
        raise InvalidReceiptError("Attempt Resource receipt differs from canonical envelope facts")
    if require_qualified:
        _require_qualified(document)
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path, require_qualified=True)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": digest_bytes(verified.read_bytes()),
    }


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    """Attach this qualified receipt to the strict-isolation candidate row."""

    from solver.manifest import attach_requirement_receipt

    verified = verify_receipt(path, require_qualified=True)
    receipt = json.loads(verified.read_bytes())
    candidate = manifest.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("image_digest") != receipt["binding"]["image_id"]:
        raise InvalidReceiptError("Attempt Resource receipt belongs to a different candidate image")
    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


def _reconstruct(state: Path, run_id: str, isolation_receipt: Path) -> dict[str, Any]:
    verified_isolation = verify_isolation_receipt(isolation_receipt)
    isolation = json.loads(verified_isolation.read_bytes())
    if isolation["run_id"] != run_id:
        raise InvalidReceiptError("Attempt Resource receipt and Isolation receipt name different Runs")
    events = EventStore(state, run_id=run_id).events()
    envelope_events = [event for event in events if event.event_type == ATTEMPT_ENVELOPE_RECORDED]
    owners = [event for event in envelope_events if event.payload["record"] == EnvelopeRecord.OWNER_OPENED.value]
    if not owners:
        raise InvalidReceiptError("Attempt Resource receipt has no exclusive owner epoch")
    grouped: dict[str, list[Any]] = {}
    for event in envelope_events:
        envelope_id = event.payload["envelope_id"]
        if envelope_id:
            grouped.setdefault(envelope_id, []).append(event)
    envelopes = [
        _completed(grouped[envelope_id], isolation)
        for envelope_id in sorted(grouped)
        if grouped[envelope_id][-1].payload["record"] == EnvelopeRecord.RESULT.value
    ]
    binding = _one_binding(envelopes, isolation)
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "profile_digest": STRICT_PROFILE_DIGEST,
        "binding": binding,
        "isolation_receipt_digest": digest_bytes(verified_isolation.read_bytes()),
        "canonical_event_head": envelope_events[-1].event_digest,
        "owner_epochs": [event.payload["owner_epoch"] for event in owners],
        "envelopes": envelopes,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def _completed(events: list[Any], isolation: Mapping[str, Any]) -> dict[str, Any]:
    records = [event.payload["record"] for event in events]
    normal = [EnvelopeRecord.RESERVED.value, EnvelopeRecord.LAUNCHED.value, EnvelopeRecord.RESULT.value]
    childless = [EnvelopeRecord.RESERVED.value, EnvelopeRecord.RESULT.value]
    if records not in (normal, childless):
        raise InvalidReceiptError("Attempt Resource envelope lifecycle is incomplete or contradictory")
    payloads = [event.payload for event in events]
    reserved, result = payloads[0], payloads[-1]
    if records == childless and result["outcome"] not in {
        ResourceOutcome.RECONCILED.value,
        ResourceOutcome.LAUNCH_FAILED.value,
    }:
        raise InvalidReceiptError("Attempt Resource envelope skipped launch without a childless result")
    identity_fields = ("envelope_id", "generation_id", "attempt_id", "step_id")
    if any(len({event[field] for event in payloads}) != 1 for field in identity_fields):
        raise InvalidReceiptError("Attempt Resource envelope changed ownership")
    binding_fields = (
        "image_id",
        "image_manifest_digest",
        "image_config_digest",
        "platform",
        "profile_digest",
    )
    if any(len({event[field] for event in payloads}) != 1 for field in binding_fields):
        raise InvalidReceiptError("Attempt Resource envelope changed candidate binding")
    if result["profile_digest"] != STRICT_PROFILE_DIGEST or result["image_id"] != isolation["image_id"]:
        raise InvalidReceiptError("Attempt Resource envelope is stale or not strict-profile evidence")
    if any(event["declared"] != reserved["declared"] for event in payloads):
        raise InvalidReceiptError("Attempt Resource envelope changed its declared limits")
    return {
        **{field: result[field] for field in identity_fields},
        **{field: result[field] for field in binding_fields},
        "owner_epoch": result["owner_epoch"],
        "cgroup_path": result["cgroup_path"],
        "executor_uid": result["executor_uid"],
        "declared": result["declared"],
        "observed": result["observed"],
        "outcome": result["outcome"],
        "exit_code": result["exit_code"],
        "cleanup_complete": result["cleanup_complete"],
        "result_sequence": events[-1].sequence,
    }


def _one_binding(envelopes: list[Mapping[str, Any]], isolation: Mapping[str, Any]) -> dict[str, str]:
    if not envelopes:
        raise InvalidReceiptError("Attempt Resource receipt has no completed envelope")
    fields = ("image_id", "image_manifest_digest", "image_config_digest", "platform", "profile_digest")
    binding = {field: str(envelopes[0][field]) for field in fields}
    if any(any(row[field] != binding[field] for field in fields) for row in envelopes):
        raise InvalidReceiptError("Attempt Resource receipt combines different candidate bindings")
    if binding["image_id"] != isolation["image_id"] or binding["profile_digest"] != STRICT_PROFILE_DIGEST:
        raise InvalidReceiptError("Attempt Resource receipt is not bound to admitted strict Isolation")
    for digest_field in ("image_manifest_digest", "image_config_digest"):
        digest = binding[digest_field]
        if (
            not digest.startswith("sha256:")
            or len(digest) != 71
            or any(character not in "0123456789abcdef" for character in digest[7:])
        ):
            raise InvalidReceiptError(f"Attempt Resource receipt has invalid {digest_field}")
    if binding["platform"] not in {"linux/arm64", "linux/amd64"}:
        raise InvalidReceiptError("Attempt Resource receipt has unsupported architecture")
    return binding


def _require_qualified(document: Mapping[str, Any]) -> None:
    envelopes = document["envelopes"]
    outcomes = {row["outcome"] for row in envelopes}
    if not BREACH_OUTCOMES.issubset(outcomes):
        raise InvalidReceiptError("Attempt Resource receipt does not exercise every semantic breach")
    for row in envelopes:
        observed = row["observed"]
        if (
            not row["cleanup_complete"]
            or observed.get("processes_after_kill") != 0
            or any(observed.get(probe) is not True for probe in DENY_PROBES)
            or not isinstance(observed.get("cleanup_seconds"), (int, float))
            or observed["cleanup_seconds"] > row["declared"]["cleanup_seconds"]
        ):
            raise InvalidReceiptError("Attempt Resource receipt has incomplete isolation or cleanup proof")


__all__ = ["link_manifest", "manifest_receipt", "verify_receipt", "write_receipt"]
