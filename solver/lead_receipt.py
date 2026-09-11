"""Independently reconstructed receipt for one Solve Lead Engagement."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from solver.event_store import EventStore, EventStoreDamage, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.lead_contracts import LEAD_ENGAGEMENT_RECORDED, LeadClassification, ROLE
from solver.lead_projection import project_lead


SCHEMA_VERSION = 1
RECEIPT_TYPE = "lead-engagement"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.persistent-solve-lead"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


def receipt_document(run_id: str, events: list[Any]) -> dict[str, object]:
    all_lead_events = [event for event in events if event.event_type == LEAD_ENGAGEMENT_RECORDED]
    lead_events = [
        event for event in all_lead_events if event.payload["classification"] != LeadClassification.CONFLICT.value
    ]
    state = project_lead(events, run_id)
    proposals = [
        {
            "turn_index": event.payload["turn_index"],
            "kind": event.payload["proposal_kind"],
            "digest": event.payload["proposal_digest"],
        }
        for event in lead_events
        if event.payload["classification"] == LeadClassification.ACCEPTED.value
    ]
    measures = [
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
        for event in lead_events
    ]
    outcomes = [
        {
            "turn_index": event.payload["turn_index"],
            "classification": event.payload["classification"],
            "detail": event.payload["detail"],
        }
        for event in lead_events
    ]
    conflicts = [
        {
            "turn_index": event.payload["turn_index"],
            "request_digest": event.payload["request_digest"],
            "evidence_digest": event.blob_digest,
            "evidence_bytes": event.blob_bytes,
            "detail": event.payload["detail"],
        }
        for event in all_lead_events
        if event.payload["classification"] == LeadClassification.CONFLICT.value
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "engagement_id": state.engagement_id,
        "generation_id": state.generation_id,
        "role": ROLE,
        "disposition": state.disposition,
        "turn_count": state.turn_count,
        "state_transition_digest": state.transition_digest,
        "turn_measures": measures,
        "proposals": proposals,
        "outcomes": outcomes,
        "quarantined_conflicts": conflicts,
        "lead_chain_head": all_lead_events[-1].event_digest,
        "deterministic_replay": {
            "recorded_digest": lead_events[-1].payload["transition_digest"],
            "replayed_digest": state.transition_digest,
            "matches": lead_events[-1].payload["transition_digest"] == state.transition_digest,
        },
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def write_receipt(state: Path, run_id: str) -> Path:
    store = EventStore(state, run_id=run_id)
    path = store.canonical_dir / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(run_id, store.events())) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
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
    run_id = run_dir.name
    try:
        expected = receipt_document(run_id, EventStore(run_dir.parent.parent, run_id=run_id).events())
    except (EventStoreDamage, OSError, ValueError) as error:
        raise InvalidReceiptError("Lead Engagement canonical state is invalid") from error
    if dict(supplied) != expected:
        raise InvalidReceiptError("Lead Engagement receipt does not match canonical state")
    if not supplied["deterministic_replay"]["matches"]:
        raise InvalidReceiptError("Lead Engagement replay comparison failed")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": digest_bytes(verified.read_bytes()),
    }


__all__ = [
    "MANIFEST_RECEIPT_REF",
    "MANIFEST_ROW_ID",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "manifest_receipt",
    "receipt_document",
    "verify_receipt",
    "write_receipt",
]
