"""Canonical native Codex Control receipt, verification and manifest attachment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from solver.event_store import InvalidReceiptError
from solver.event_store import EventStore
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.manifest import attach_requirement_receipt, canonical_manifest_bytes, parse_manifest

SCHEMA_VERSION = 1
RECEIPT_TYPE = "native-codex-control"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.inference-native"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


def receipt_document(state: Path, run_id: str) -> dict[str, object]:
    try:
        store = EventStore(state, run_id=run_id)
        records = [
            event
            for event in store.events()
            if event.event_type == "observation.recorded"
            and event.payload.get("attempt_id") == "codex-control"
            and event.payload.get("tool") == "codex-control-state"
        ]
        canonical = json.loads(store.blob(records[-1].blob_digest))
    except (OSError, IndexError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Codex Control canonical state is unavailable") from error
    if canonical.get("run_id") != run_id or not canonical.get("request") or not canonical.get("result"):
        raise InvalidReceiptError("Codex Control canonical state has no measured request")
    expected_probes = {"environment": "clear", "event": "clear", "file": "clear"}
    if canonical.get("secret_probes") != expected_probes:
        raise InvalidReceiptError("Codex Control canonical state has no clear Attempt secret probes")
    custody = canonical.get("custody")
    if not isinstance(custody, dict) or custody.get("owner") != "codex" or not custody.get("secret_names"):
        raise InvalidReceiptError("Codex Control canonical state has no service custody proof")
    result = canonical["result"]
    if result.get("outcome") != "answered" or not result.get("turn"):
        raise InvalidReceiptError("Codex Control canonical state has no answered Turn")
    turn = dict(result["turn"])
    turn.pop("text", None)
    turn.pop("stream", None)
    catalogue = canonical["catalogue"]
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "catalogue_digest": hashlib.sha256(canonical_bytes({"catalogue": catalogue})).hexdigest(),
        "turn": turn,
        "limits": [
            {
                **item,
                "remaining_percent": None
                if item["used_percent"] is None
                else max(0.0, min(100.0, 100.0 - item["used_percent"])),
            }
            for item in canonical["limits"]
        ],
        "secret_probes": canonical["secret_probes"],
        "custody": custody,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def write_receipt(state: Path, run_id: str, manifest_path: Path) -> Path:
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(state, run_id)) + b"\n")
    manifest = parse_manifest(Path(manifest_path).read_bytes())
    linked = attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path, verify=False))
    atomic_write(Path(manifest_path), canonical_manifest_bytes(linked) + b"\n")
    return path


def verify_receipt(path: Path, manifest_path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Codex Control receipt cannot be read") from error
    run_dir = receipt_path.parent.parent
    if receipt_path.name != RECEIPT_FILENAME or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Codex Control receipt path or encoding is invalid")
    if supplied != receipt_document(run_dir.parent.parent, run_dir.name):
        raise InvalidReceiptError("Codex Control receipt disagrees with canonical state")
    manifest = parse_manifest(Path(manifest_path).read_bytes())
    row = next(item for item in manifest["requirements"] if item["row_id"] == MANIFEST_ROW_ID)
    linked_receipt = manifest_receipt(receipt_path, verify=False)
    if row["receipt_ref"] != linked_receipt["ref"] or linked_receipt not in manifest["receipts"]:
        raise InvalidReceiptError("Codex Control receipt is not attached to its candidate manifest row")
    return receipt_path


def manifest_receipt(path: Path, *, verify: bool = True, manifest_path: Path | None = None) -> dict[str, str]:
    if verify:
        if manifest_path is None:
            raise ValueError("candidate manifest path is required")
        verified = verify_receipt(path, manifest_path)
    else:
        verified = Path(path)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


__all__ = ["manifest_receipt", "receipt_document", "verify_receipt", "write_receipt"]
