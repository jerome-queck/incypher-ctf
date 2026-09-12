"""Canonical Target-broker receipt and independent verification."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from solver.event_store import EventStore, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.target_broker_receipt_projection import receipt_document

RECEIPT_FILENAME = "target-broker.receipt.json"
RECEIPT_TYPE = "target-broker"
PRODUCER = "target-broker"
MANIFEST_ROW_ID = "core.board-target-lease"
MANIFEST_RECEIPT_REF = "receipt:target-broker"


def write_receipt(state: Path, run_id: str) -> Path:
    document = receipt_document(run_id, EventStore(state, run_id=run_id).events())
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    path = Path(path)
    try:
        raw = path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Target-broker receipt cannot be read") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Target-broker receipt is not canonical JSON")
    if path.name != RECEIPT_FILENAME or path.parent.name != "canonical":
        raise InvalidReceiptError("Target-broker receipt path is invalid")
    run_dir = path.parent.parent
    if supplied.get("run_id") != run_dir.name:
        raise InvalidReceiptError("Target-broker receipt Run identity is invalid")
    expected = receipt_document(run_dir.name, EventStore(run_dir.parent.parent, run_id=run_dir.name).events())
    if dict(supplied) != expected:
        raise InvalidReceiptError("Target-broker receipt does not match canonical state")
    return path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path, *, binding) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    verified = verify_receipt(path)
    receipt = json.loads(verified.read_bytes())
    candidate = manifest.get("candidate")
    profile = manifest.get("selected_profile")
    isolation = profile.get("isolation") if isinstance(profile, Mapping) else None
    receipt_candidate = receipt["candidate"]
    if (
        not isinstance(candidate, Mapping)
        or not isinstance(profile, Mapping)
        or receipt_candidate["image_id"] != candidate.get("image_digest")
        or receipt_candidate
        != {
            "image_id": binding.image_id,
            "manifest_digest": binding.image_manifest_digest,
            "config_digest": binding.image_config_digest,
            "platform": binding.platform,
        }
        or not isinstance(isolation, Mapping)
        or receipt["profile_digest"] != isolation.get("profile_digest")
    ):
        raise ValueError("Target-broker receipt belongs to another candidate or profile")
    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(verified))


def capsule_contract():
    """Admit only a canonical receipt reproduced from its causal Run state."""

    from solver.evidence_capsule_contracts import ReceiptContract

    def validate(receipt, source):
        if receipt.get("run_id") != source.run_id:
            raise ValueError("Target-broker receipt belongs to another Run")
        events = tuple(_source_event(event) if isinstance(event, Mapping) else event for event in source.events)
        expected = receipt_document(source.run_id, events)
        if dict(receipt) != expected:
            raise ValueError("Target-broker receipt differs from canonical source")
        return ()

    return ReceiptContract(RECEIPT_TYPE, 1, PRODUCER, validate)


def _source_event(event: Mapping[str, object]) -> SimpleNamespace:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("Target-broker source event has no payload")
    return SimpleNamespace(
        sequence=event.get("seq"),
        event_digest=event.get("event_digest"),
        event_type=event.get("event_type"),
        payload=payload,
        blob_digest=payload.get("blob_digest", ""),
    )


__all__ = [
    "MANIFEST_RECEIPT_REF",
    "MANIFEST_ROW_ID",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "link_manifest",
    "capsule_contract",
    "manifest_receipt",
    "verify_receipt",
    "write_receipt",
]
