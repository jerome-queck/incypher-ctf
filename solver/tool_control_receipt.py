"""Independent projection and verification for Tool-handle receipts."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from solver.event_store import CommittedEvent, EventStore, EventStoreDamage, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes

SCHEMA_VERSION = 1
RECEIPT_TYPE = "tool-handle"
RECEIPT_FILENAME = "tool-handle.receipt.json"
MANIFEST_ROW_ID = "core.tool-surface"
MANIFEST_RECEIPT_REF = "receipt:tool-handle"


def receipt_document(run_id: str, events: Sequence[CommittedEvent]) -> dict[str, object]:
    tool_events = [event for event in events if event.event_type == "tool-control.recorded"]
    reservations = {
        str(event.payload["invocation_id"]): event for event in tool_events if event.payload["record"] == "reserved"
    }
    denied_ids = {str(event.payload["invocation_id"]) for event in tool_events if event.payload["record"] == "denied"}
    invocations = []
    for event in tool_events:
        payload = event.payload
        if payload["record"] != "completed":
            continue
        invocation_id = str(payload["invocation_id"])
        if invocation_id in denied_ids:
            continue
        reserved = reservations.get(invocation_id)
        if reserved is None or reserved.sequence >= event.sequence:
            raise InvalidReceiptError("Tool completion has no earlier reservation")
        stable = (
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "capability_id",
            "component_id",
            "version",
            "image_digest",
            "view_digest",
            "arguments_digest",
            "input_digest",
        )
        if any(reserved.payload[name] != payload[name] for name in stable):
            raise InvalidReceiptError("Tool completion disagrees with its reservation")
        invocations.append(
            {
                "invocation_id": invocation_id,
                **{name: payload[name] for name in stable},
                "output_digest": payload["blob_digest"],
                "output_bytes": payload["blob_bytes"],
                "exit_code": payload["exit_code"],
                "resources": payload["resources"],
            }
        )
    denials = [
        {
            "invocation_id": event.payload["invocation_id"],
            "generation_id": event.payload["generation_id"],
            "capability_id": event.payload["capability_id"],
            "reason": event.payload["reason"],
        }
        for event in tool_events
        if event.payload["record"] == "denied"
    ]
    revocations = [
        {
            "generation_id": event.payload["generation_id"],
            "handle_digest": event.payload["handle_digest"],
            "reason": event.payload["reason"],
        }
        for event in events
        if event.event_type == "capability-custody.recorded"
        and event.payload["record"] == "revoked"
        and str(event.payload["scope"]).startswith("tool.view:")
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "invocations": invocations,
        "denials": denials,
        "revocations": revocations,
    }


def write_receipt(state: Path, run_id: str) -> Path:
    document = receipt_document(run_id, EventStore(state, run_id=run_id).events())
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Tool-handle receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Tool-handle receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("Tool-handle receipt path does not identify Run state")
    run_dir = receipt_path.parent.parent
    if supplied.get("run_id") != run_dir.name:
        raise InvalidReceiptError("Tool-handle receipt Run identity does not match its path")
    try:
        expected = receipt_document(
            run_dir.name,
            EventStore(run_dir.parent.parent, run_id=run_dir.name).events(),
        )
    except (EventStoreDamage, OSError, ValueError) as error:
        raise InvalidReceiptError("Tool-handle canonical state is invalid") from error
    if dict(supplied) != expected:
        raise InvalidReceiptError("Tool-handle receipt does not match canonical state")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))
