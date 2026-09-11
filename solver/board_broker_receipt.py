"""Canonical Board-broker receipt I/O and independent verification."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from solver.board_broker_receipt_projection import receipt_document
from solver.event_store import EventStore, EventStoreDamage, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes

RECEIPT_TYPE = "board-broker"
RECEIPT_FILENAME = "board-broker.receipt.json"
MANIFEST_ROW_ID = "core.brokered-credentials-egress"
MANIFEST_RECEIPT_REF = "receipt:board-broker"


def write_receipt(state: Path, run_id: str) -> Path:
    store = EventStore(state, run_id=run_id)
    document = receipt_document(run_id, store.events())
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Board-broker receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Board-broker receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("Board-broker receipt path does not identify Run state")
    run_dir = receipt_path.parent.parent
    if supplied.get("run_id") != run_dir.name:
        raise InvalidReceiptError("Board-broker receipt Run identity does not match its path")
    try:
        events = EventStore(run_dir.parent.parent, run_id=run_dir.name).events()
        expected = receipt_document(run_dir.name, events)
    except InvalidReceiptError:
        raise
    except (EventStoreDamage, OSError, ValueError) as error:
        raise InvalidReceiptError("Board-broker canonical state is invalid") from error
    if dict(supplied) != expected:
        raise InvalidReceiptError("Board-broker receipt does not match canonical state")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    """Attach this independently verified receipt to its candidate requirement row."""

    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


__all__ = [
    "MANIFEST_RECEIPT_REF",
    "MANIFEST_ROW_ID",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "manifest_receipt",
    "link_manifest",
    "verify_receipt",
    "write_receipt",
]
