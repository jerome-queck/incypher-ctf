"""Independent receipt projection for confirmed Attempt progress."""

import json

from solver.attempt_progress_contracts import ATTEMPT_PROGRESS_RECORDED, ProgressStatus
from solver.event_store import InvalidReceiptError
from solver.event_store_contracts import GenerationAuthority
from solver.event_store_storage import atomic_write, canonical_bytes

RECEIPT_FILENAME = "attempt-progress.receipt.json"


def late_rejections(events):
    """Recover generation-fenced late requests from their sealed authority evidence."""
    rejected = []
    for event in events:
        if event.event_type != "work-generation.recorded" or event.payload.get("record") != "late-event":
            continue
        if event.payload.get("authority") != GenerationAuthority.CARRY.value or not event.body:
            continue
        try:
            rejected.append(json.loads(event.body)["request"])
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
    return rejected


def receipt_document(run_id, events):
    selected = [event for event in events if event.event_type == ATTEMPT_PROGRESS_RECORDED]
    return {
        "schema_version": 1,
        "receipt_type": "attempt-progress",
        "run_id": run_id,
        "decisions": [
            {
                key: event.payload[key]
                for key in (
                    "generation_id",
                    "attempt_id",
                    "checkpoint_id",
                    "evidence_id",
                    "evidence_digest",
                    "judge_source",
                    "moved",
                    "replay",
                    "status",
                    "epoch_before",
                    "epoch_after",
                    "extension_bound",
                    "carry_digest",
                )
            }
            for event in selected
        ],
        "accepted": sum(event.payload["status"] == ProgressStatus.ACCEPTED.value for event in selected),
        "late_rejections": late_rejections(events),
        "replay_matches": len(
            {event.payload["evidence_id"] for event in selected if event.payload["status"] == "accepted"}
        )
        == sum(event.payload["status"] == "accepted" for event in selected),
        "manifest_link": {
            "row_id": "core.persistent-solve-lead",
            "receipt_ref": "receipt:attempt-progress",
        },
    }


def write_receipt(store):
    path = store.canonical_dir / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(store.run_id, store.events())) + b"\n")
    return path


def verify_receipt(path):
    try:
        raw = path.read_bytes()
        supplied = json.loads(raw)
        from solver.event_store import EventStore

        expected = receipt_document(
            path.parent.parent.name, EventStore(path.parents[3], run_id=path.parent.parent.name).events()
        )
    except Exception as error:
        raise InvalidReceiptError("Attempt progress receipt cannot be verified") from error
    if raw != canonical_bytes(supplied) + b"\n" or supplied != expected or not supplied["replay_matches"]:
        raise InvalidReceiptError("Attempt progress receipt differs from canonical state")
    return path
