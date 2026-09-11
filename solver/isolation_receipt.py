"""Canonical, independently verifiable strict-Isolation preflight receipt."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from solver.event_store import InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.isolation import (
    STRICT_CONTROLS,
    STRICT_PROFILE_DIGEST,
    STRICT_PROFILE_ID,
    STRICT_RUNTIME_PIN,
    SHA256_IMAGE,
    IsolationReceipt,
)

SCHEMA_VERSION = 1
RECEIPT_TYPE = "strict-isolation-preflight"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.strict-isolation"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


def receipt_document(run_id: str, receipt: IsolationReceipt) -> dict[str, Any]:
    """Bind the sanitized probe facts to one Run and manifest row."""

    document = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        **asdict(receipt),
        "checks": dict(receipt.checks),
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }
    document["runtime_pin"] = dict(receipt.runtime_pin)
    document["owned_residue"] = list(receipt.owned_residue)
    return document


def write_receipt(state: Path, run_id: str, receipt: IsolationReceipt) -> Path:
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(run_id, receipt)) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("strict Isolation receipt cannot be read as JSON") from error
    if not isinstance(document, Mapping) or raw != canonical_bytes(document) + b"\n":
        raise InvalidReceiptError("strict Isolation receipt is not canonical JSON")
    expected = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "profile_id": STRICT_PROFILE_ID,
        "profile_digest": STRICT_PROFILE_DIGEST,
        "runtime_pin": STRICT_RUNTIME_PIN,
        "outer_capabilities": "empty",
        "checks": {control: "pass" for control in STRICT_CONTROLS},
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }
    for key, value in expected.items():
        if document.get(key) != value:
            raise InvalidReceiptError(f"strict Isolation receipt has invalid {key}")
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("strict Isolation receipt path does not identify Run state")
    if document.get("run_id") != receipt_path.parent.parent.name:
        raise InvalidReceiptError("strict Isolation receipt Run identity does not match its path")
    if (
        not isinstance(document.get("image_id"), str)
        or not SHA256_IMAGE.fullmatch(document["image_id"])
        or not isinstance(document.get("processes_before_kill"), int)
        or document["processes_before_kill"] < 3
        or document.get("processes_after_kill") != 0
        or document.get("owned_residue") != []
        or document.get("broker_peer_uid") != 20000
    ):
        raise InvalidReceiptError("strict Isolation receipt does not prove teardown and peer identity")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": digest_bytes(verified.read_bytes()),
    }


__all__ = ["manifest_receipt", "verify_receipt", "write_receipt"]
