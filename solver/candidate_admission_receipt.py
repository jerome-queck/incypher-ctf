"""Sanitized, independently verified Candidate-admission receipt."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from solver.candidate_admission_contracts import CANDIDATE_ADMISSION_RECORDED
from solver.candidate_admission_projection import project_candidate
from solver.candidate_vault import CandidateVaultCipher
from solver.event_store import EventStore, EventStoreDamage, InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes

SCHEMA_VERSION = 1
RECEIPT_TYPE = "candidate-admission"
RECEIPT_FILENAME = "candidate-admission.receipt.json"
MANIFEST_ROW_ID = "core.submission-tail"
MANIFEST_RECEIPT_REF = "receipt:candidate-admission"


def receipt_document(run_id: str, store: EventStore, cipher: CandidateVaultCipher) -> dict[str, object]:
    decisions = []
    ready = []
    events = store.events()
    for event in events:
        if event.event_type != CANDIDATE_ADMISSION_RECORDED:
            continue
        if event.payload["decision"] == "admitted":
            project_candidate(event, cipher)
        row = {
            "candidate_id": event.payload["candidate_id"],
            "challenge_id": event.payload["challenge_id"],
            "generation_id": event.payload["generation_id"],
            "candidate_digest": event.payload["candidate_digest"],
            "provenance_digest": event.payload["provenance_digest"],
            "vault_digest": event.payload["vault_digest"],
            "admission_rule": event.payload["admission_rule"],
            "decision": event.payload["decision"],
            "duplicate_class": event.payload["duplicate_class"],
        }
        decisions.append(row)
        if event.payload["decision"] == "admitted":
            ready.append(event.payload["candidate_id"])
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
        "decisions": decisions,
        "ready_candidate_ids": ready,
        "restart_proof": {
            "method": "canonical-replay-v1",
            "event_count": len(events),
            "chain_head": events[-1].event_digest if events else "",
            "ready_digest": digest_bytes(canonical_bytes(ready)),
        },
    }


def write_receipt(state: Path, run_id: str, vault_key: bytes) -> Path:
    store = EventStore(state, run_id=run_id)
    path = store.canonical_dir / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(run_id, store, CandidateVaultCipher(vault_key))) + b"\n")
    return path


def verify_receipt(path: Path, vault_key: bytes) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Candidate-admission receipt cannot be read") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Candidate-admission receipt is not canonical JSON")
    run_dir = receipt_path.parent.parent
    if receipt_path.name != RECEIPT_FILENAME or receipt_path.parent.name != "canonical":
        raise InvalidReceiptError("Candidate-admission receipt path does not identify Run state")
    try:
        expected = receipt_document(
            run_dir.name,
            EventStore(run_dir.parent.parent, run_id=run_dir.name),
            CandidateVaultCipher(vault_key),
        )
    except (EventStoreDamage, OSError, ValueError) as error:
        raise InvalidReceiptError("Candidate-admission canonical state is invalid") from error
    if dict(supplied) != expected:
        raise InvalidReceiptError("Candidate-admission receipt does not match canonical state")
    return receipt_path


def manifest_receipt(path: Path, vault_key: bytes) -> dict[str, str]:
    verified = verify_receipt(path, vault_key)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path, vault_key: bytes) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path, vault_key))
