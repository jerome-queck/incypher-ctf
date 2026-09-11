"""Independent Run-wide reconstruction of Solve Lead Engagement evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from solver.event_store import EventStore, EventStoreDamage, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.lead_contracts import LeadClassification, LeadRecord
from solver.lead_projection import engagement_ids, lead_events, project_lead

SCHEMA_VERSION = 2
RECEIPT_TYPE = "lead-engagement"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.persistent-solve-lead"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


def receipt_document(run_id: str, events: list[Any]) -> dict[str, object]:
    engagements = [_engagement_document(events, run_id, identity) for identity in engagement_ids(events)]
    if not engagements:
        raise InvalidReceiptError("Lead receipt has no Engagement evidence")
    all_events = lead_events(events)
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "role": "solve-lead",
        "engagement_count": len(engagements),
        "turn_count": sum(item["turn_count"] for item in engagements),
        "engagements": engagements,
        "lead_chain_head": all_events[-1].event_digest,
        "deterministic_replay": {
            "engagements_compared": len(engagements),
            "matches": all(item["deterministic_replay"]["matches"] for item in engagements),
        },
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def _engagement_document(events: list[Any], run_id: str, engagement_id: str) -> dict[str, Any]:
    selected = lead_events(events, engagement_id)
    state = project_lead(events, run_id, engagement_id)
    turns = [event for event in selected if event.payload["record"] == LeadRecord.TURN.value]
    admissions = [event for event in selected if event.payload["record"] == LeadRecord.ADMITTED.value]
    quarantined = [event for event in selected if event.payload["record"] == LeadRecord.QUARANTINED.value]
    complete = len(admissions) == len(turns)
    return {
        "engagement_id": engagement_id,
        "binding": state.binding.document(),
        "disposition": state.disposition,
        "outstanding_proposal_id": state.outstanding_proposal_id,
        "turn_count": state.turn_count,
        "pending_turn_index": admissions[-1].payload["turn_index"] if not complete else None,
        "state_transition_digest": state.transition_digest,
        "turn_measures": [
            {
                "turn_index": event.payload["turn_index"],
                "context_bytes": event.payload["context_bytes"],
                "output_bytes": event.payload["output_bytes"],
                "model": event.payload["model"],
                "duration_ms": event.payload["duration_ms"],
                "tokens_in": event.payload["tokens_in"],
                "tokens_out": event.payload["tokens_out"],
                "usage_known": event.payload["usage_known"],
            }
            for event in turns
        ],
        "proposals": [
            {
                "turn_index": event.payload["turn_index"],
                "proposal_id": event.payload["proposal_id"],
                "kind": event.payload["proposal_kind"],
                "digest": event.payload["proposal_digest"],
            }
            for event in turns
            if event.payload["classification"] == LeadClassification.ACCEPTED.value
        ],
        "outcomes": [
            {
                "turn_index": event.payload["turn_index"],
                "classification": event.payload["classification"],
                "detail": event.payload["detail"],
            }
            for event in turns
        ],
        "quarantined_inputs": [
            {
                "turn_index": event.payload["turn_index"],
                "classification": event.payload["classification"],
                "request_digest": event.payload["request_digest"],
                "evidence_digest": event.blob_digest,
                "evidence_bytes": event.blob_bytes,
                "detail": event.payload["detail"],
            }
            for event in quarantined
        ],
        "deterministic_replay": {
            "recorded_digest": turns[-1].payload["transition_digest"] if turns else "",
            "replayed_digest": state.transition_digest,
            "matches": complete and bool(turns) and turns[-1].payload["transition_digest"] == state.transition_digest,
        },
    }


def write_receipt(state: Path, run_id: str) -> Path:
    store = EventStore(state, run_id=run_id)
    path = store.canonical_dir / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(run_id, store.events())) + b"\n")
    return path


def verify_receipt(path: Path, *, require_qualified: bool = False) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Lead Engagement receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Lead Engagement receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("Lead Engagement receipt path does not identify Run state")
    run_dir = receipt_path.parent.parent
    try:
        expected = receipt_document(run_dir.name, EventStore(run_dir.parent.parent, run_id=run_dir.name).events())
    except (EventStoreDamage, OSError, ValueError) as error:
        raise InvalidReceiptError("Lead Engagement canonical state is invalid") from error
    if dict(supplied) != expected:
        raise InvalidReceiptError("Lead Engagement receipt does not match canonical state")
    if not supplied["deterministic_replay"]["matches"]:
        raise InvalidReceiptError("Lead Engagement replay comparison failed")
    if require_qualified and not all(item["turn_count"] > 0 for item in supplied["engagements"]):
        raise InvalidReceiptError("Lead Engagement receipt has no completed Turn")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path, require_qualified=True)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


__all__ = [
    "MANIFEST_RECEIPT_REF",
    "MANIFEST_ROW_ID",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "link_manifest",
    "manifest_receipt",
    "receipt_document",
    "verify_receipt",
    "write_receipt",
]
