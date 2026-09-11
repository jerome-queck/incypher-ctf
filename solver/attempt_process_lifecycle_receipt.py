"""Independent reconstruction of Attempt process-tree lifecycle evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from solver.attempt_process_contracts import ATTEMPT_PROCESS_RECORDED, ProcessRecord
from solver.event_store import EventStore, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.isolation_receipt import RECEIPT_FILENAME as ISOLATION_RECEIPT_FILENAME
from solver.isolation_receipt import verify_receipt as verify_isolation_receipt


SCHEMA_VERSION = 1
RECEIPT_TYPE = "attempt-process-lifecycle"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.supervisor"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


def write_receipt(state: Path, run_id: str, isolation_receipt: Path) -> Path:
    document = _reconstruct(Path(state), run_id, Path(isolation_receipt))
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path, *, require_qualified: bool = False) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Attempt process receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Attempt process receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("Attempt process receipt path does not identify Run state")
    run_id = receipt_path.parent.parent.name
    state = receipt_path.parents[3]
    isolation = receipt_path.parent / ISOLATION_RECEIPT_FILENAME
    expected = _reconstruct(state, run_id, isolation)
    if dict(supplied) != expected:
        raise InvalidReceiptError("Attempt process receipt differs from canonical lifecycle facts")
    if require_qualified:
        _require_qualified(supplied)
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path, require_qualified=True)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": digest_bytes(verified.read_bytes()),
    }


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    verified = verify_receipt(path, require_qualified=True)
    receipt = json.loads(verified.read_bytes())
    candidate = manifest.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("image_digest") != receipt["candidate_image_id"]:
        raise InvalidReceiptError("Attempt process receipt belongs to a different candidate image")
    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


def _reconstruct(state: Path, run_id: str, isolation_receipt: Path) -> dict[str, Any]:
    verified_isolation = verify_isolation_receipt(isolation_receipt)
    isolation = json.loads(verified_isolation.read_bytes())
    if isolation["run_id"] != run_id:
        raise InvalidReceiptError("Attempt process and Isolation receipts name different Runs")
    events = EventStore(state, run_id=run_id).events()
    process_events = [event for event in events if event.event_type == ATTEMPT_PROCESS_RECORDED]
    fences = {
        event.payload["envelope_id"]: event
        for event in process_events
        if event.payload["record"] == ProcessRecord.FENCED.value
    }
    observed_by_envelope = {}
    for event in process_events:
        if event.payload["record"] == ProcessRecord.OBSERVED.value:
            observed_by_envelope[event.payload["envelope_id"]] = event
    observed = list(observed_by_envelope.values())
    processes = []
    for event in observed:
        payload = event.payload
        fence = fences.get(payload["envelope_id"])
        processes.append(
            {
                "envelope_id": payload["envelope_id"],
                "generation_id": payload["generation_id"],
                "attempt_id": payload["attempt_id"],
                "step_id": payload["step_id"],
                "owner_epoch": payload["owner_epoch"],
                "cgroup_path": payload["cgroup_path"],
                **payload["lifecycle"],
                "fence_sequence": fence.payload["fence_sequence"] if fence is not None else 0,
                "fence_ts": fence.payload["fence_ts"] if fence is not None else "",
                "observation_sequence": event.sequence,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "candidate_image_id": isolation["image_id"],
        "isolation_profile_digest": isolation["profile_digest"],
        "isolation_receipt_digest": digest_bytes(verified_isolation.read_bytes()),
        "canonical_event_head": events[-1].event_digest if events else "",
        "processes": processes,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def _require_qualified(document: Mapping[str, Any]) -> None:
    processes = document["processes"]
    if not processes:
        raise InvalidReceiptError("Attempt process receipt has no observed process tree")
    if not any(len(row["descendants"]) >= 3 and row["term_sent"] and row["kill_sent"] for row in processes):
        raise InvalidReceiptError("Attempt process receipt has no escalated fork and double-fork inventory")
    for row in processes:
        if (
            row["after_kill"]
            or not row["inventory_complete"]
            or (row["term_sent"] and not row["teardown_acknowledged"])
            or row["control_eof"]
            or row["fence_sequence"] < 1
            or row["stream_captured_bytes"] > row["stream_limit_bytes"]
            or row["stream_total_bytes"] < row["stream_captured_bytes"]
        ):
            raise InvalidReceiptError("Attempt process receipt has incomplete fence, stream, or teardown proof")


__all__ = ["link_manifest", "manifest_receipt", "verify_receipt", "write_receipt"]
