"""Canonical, independently verifiable Work-generation fence receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from solver.event_store import (
    WORK_GENERATION_RECORDED,
    CommittedEvent,
    EventStore,
    GenerationRecord,
    InvalidReceiptError,
)
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.redaction import Redactor
from solver.work_generation import GenerationFence, GenerationProjection


SCHEMA_VERSION = 1
RECEIPT_TYPE = "work-generation-fence"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.canonical-state-replay-restart"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


def _transition(event: CommittedEvent) -> dict[str, Any]:
    payload = event.payload
    return {
        "sequence": event.sequence,
        "event_id": payload["event_id"],
        "event_digest": event.event_digest,
        "record": payload["record"],
        "generation_id": payload["generation_id"],
        "work_id": payload["work_id"],
        "attempt_id": payload["attempt_id"],
        "disposition": payload["disposition"] or None,
        "ts": payload["ts"],
    }


def _late_output(event: CommittedEvent) -> dict[str, Any]:
    payload = event.payload
    return {
        "sequence": event.sequence,
        "event_id": payload["event_id"],
        "event_digest": event.event_digest,
        "generation_id": payload["generation_id"],
        "work_id": payload["work_id"],
        "attempt_id": payload["attempt_id"],
        "authority": payload["authority"],
        "classification": payload["classification"],
        "evidence_digest": event.blob_digest,
        "evidence_bytes": event.blob_bytes,
        "ts": payload["ts"],
    }


def receipt_document(run_id: str, events: list[CommittedEvent], projection: GenerationProjection) -> dict[str, Any]:
    work_events = [event for event in events if event.event_type == WORK_GENERATION_RECORDED]
    transitions = [
        _transition(event)
        for event in work_events
        if event.payload["record"] in {GenerationRecord.ACQUIRE.value, GenerationRecord.CLOSE.value}
    ]
    reservations = [
        _late_output(event) for event in work_events if event.payload["record"] == GenerationRecord.AUTHORITY.value
    ]
    late_outputs = [
        _late_output(event) for event in work_events if event.payload["record"] == GenerationRecord.LATE_EVENT.value
    ]
    restart_projection = {
        "generations": [state.document() for state in projection.generations],
        "active_by_work": {work_id: state.generation_id for work_id, state in projection.active_by_work.items()},
    }
    authority_trace = [{**late, "accepted": False} for late in late_outputs]
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "transitions": transitions,
        "late_output_attempts": late_outputs,
        "restart_projection": restart_projection,
        "authority_reservation_trace": [{**reservation, "accepted": True} for reservation in reservations],
        "authority_rejection_trace": authority_trace,
        "projection_digest": projection.digest,
        "chain_head": projection.chain_head,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def build_receipt(store: EventStore, events: list[CommittedEvent], projection: GenerationProjection) -> Path:
    path = store.canonical_dir / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(store.run_id, events, projection)) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Work-generation receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Work-generation receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("Work-generation receipt path does not identify Run state")
    run_dir = receipt_path.parent.parent
    state = run_dir.parent.parent
    run_id = run_dir.name
    store = EventStore(state, run_id=run_id)
    events = store.events()
    projection = GenerationFence(state, run_id, Redactor({}), lambda: "").projection()
    expected = receipt_document(run_id, events, projection)
    if dict(supplied) != expected:
        raise InvalidReceiptError("Work-generation receipt does not match canonical state")
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
    "build_receipt",
    "manifest_receipt",
    "receipt_document",
    "verify_receipt",
]
