"""Small public facade for capability-custody evidence and receipt I/O."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from solver.capability_evidence import CapabilityEvidence
from solver.capability_receipt_projection import receipt_document
from solver.event_store import EventStore, EventStoreDamage, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes

SCHEMA_VERSION = 1
RECEIPT_TYPE = "capability-custody"
RECEIPT_FILENAME = "capability-custody.receipt.json"
MANIFEST_ROW_ID = "core.brokered-credentials-egress"
MANIFEST_RECEIPT_REF = "receipt:capability-custody"


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
        raise InvalidReceiptError("capability-custody receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("capability-custody receipt is not canonical JSON")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("capability-custody receipt path does not identify Run state")
    run_dir = receipt_path.parent.parent
    run_id = run_dir.name
    if supplied.get("run_id") != run_id:
        raise InvalidReceiptError("capability-custody receipt Run identity does not match its path")
    try:
        events = EventStore(run_dir.parent.parent, run_id=run_id).events()
        expected = receipt_document(run_id, events, strict_probes=True)
    except InvalidReceiptError:
        raise
    except (EventStoreDamage, OSError, ValueError) as error:
        raise InvalidReceiptError("capability-custody canonical state is invalid") from error
    if dict(supplied) != expected:
        raise InvalidReceiptError("capability-custody receipt does not match canonical state")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": digest_bytes(verified.read_bytes()),
    }


__all__ = [
    "CapabilityEvidence",
    "MANIFEST_RECEIPT_REF",
    "MANIFEST_ROW_ID",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "manifest_receipt",
    "receipt_document",
    "verify_receipt",
    "write_receipt",
]
