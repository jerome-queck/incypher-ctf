"""Independently rebuilt Triage Judge arrival-batch receipt."""

import json
from pathlib import Path

from solver.event_store import EventStore, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes

RECEIPT_FILENAME = "triage-judge.receipt.json"
RECEIPT_TYPE = "triage-judge"
MANIFEST_ROW_ID = "core.adaptive-routing"
MANIFEST_RECEIPT_REF = "receipt:triage-judge"


def receipt_document(run_id, events):
    selected = [e for e in events if e.event_type == "triage-judge.recorded"]
    if not selected:
        raise InvalidReceiptError("Triage Judge receipt has no arrival-batch evidence")
    return {
        "schema_version": 1,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "arrival_batches": [
            {
                "batch_id": e.payload["batch_id"],
                "evidence_digest": e.payload["evidence_digest"],
                "verdict_id": e.payload["verdict_id"],
                "classification": e.payload["classification"],
                "proposal": {
                    "kind": e.payload["proposal_kind"],
                    "value": e.payload["proposal_value"],
                    "confidence": e.payload["proposal_confidence"],
                    "provenance": e.payload["proposal_provenance"],
                },
                "accepted_source": e.payload["accepted_source"],
                "acceptance_reason": e.payload["acceptance_reason"],
                "measured_route": e.payload["measured_route"],
                "measured_model": e.payload["measured_model"],
                "deterministic_fallback_comparison": {"same_proposal": e.payload["deterministic_same"]},
            }
            for e in selected
        ],
        "deterministic_replay": {
            "batch_count": len(selected),
            "unique_verdicts": len({e.payload["verdict_id"] for e in selected}),
            "matches": len(selected) == len({e.payload["batch_id"] for e in selected}),
        },
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def write_receipt(state: Path, run_id: str):
    store = EventStore(state, run_id=run_id)
    path = store.canonical_dir / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(run_id, store.events())) + b"\n")
    return path


def verify_receipt(path: Path):
    path = Path(path)
    try:
        raw, supplied = path.read_bytes(), json.loads(path.read_bytes())
        state, run_id = path.parents[3], path.parent.parent.name
        expected = receipt_document(run_id, EventStore(state, run_id=run_id).events())
    except Exception as error:
        raise InvalidReceiptError("Triage Judge receipt cannot be verified") from error
    if (
        raw != canonical_bytes(supplied) + b"\n"
        or supplied != expected
        or not supplied["deterministic_replay"]["matches"]
    ):
        raise InvalidReceiptError("Triage Judge receipt differs from canonical state")
    return path


def manifest_receipt(path):
    verified = verify_receipt(path)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest, path):
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))
