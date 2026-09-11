"""Generation and independent validation of verified-replay receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from solver.event_store import CANONICAL_SCHEMA_VERSION, CommittedEvent, InvalidReceiptError
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.replay_contracts import (
    MANIFEST_RECEIPT_REF,
    MANIFEST_ROW_ID,
    PROJECTION_VERSION,
    RECEIPT_FILENAME,
    RECEIPT_TYPE,
    REPLAY_SCHEMA_VERSION,
    ReplayVerification,
    VersionedProjection,
)
from solver.replay_proof import load_controlled_proof


def build_receipt(run_id: str, events: list[CommittedEvent], projection: VersionedProjection) -> dict[str, Any]:
    proof = load_controlled_proof()
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "event_count": len(events),
        "chain_head": events[-1].event_digest if events else "",
        "schema_versions": {
            "canonical": CANONICAL_SCHEMA_VERSION,
            "projection:v1": projection.schema_version,
            "receipt": REPLAY_SCHEMA_VERSION,
        },
        "projection_digests": {projection.version: projection.digest},
        "controlled_proof_digest": digest_bytes(canonical_bytes(proof)),
        "corruption_fixture_results": proof["corruption_fixture_results"],
        "pre_authority_refusal": proof["pre_authority_refusal"],
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def validate_receipt(receipt: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    if receipt != expected:
        raise InvalidReceiptError("verified-replay receipt does not match canonical replay")


def read_receipt(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        receipt = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("verified-replay receipt cannot be read as JSON") from error
    if not isinstance(receipt, dict) or raw != canonical_bytes(receipt) + b"\n":
        raise InvalidReceiptError("verified-replay receipt is not canonical JSON")
    return receipt


def receipt_location(path: Path, *, state: Path | None, run_id: str | None) -> tuple[Path, str]:
    if state is not None and run_id is not None:
        return Path(state), run_id
    if path.name != RECEIPT_FILENAME or path.parent.name != "canonical":
        raise InvalidReceiptError("receipt path does not identify a Run state")
    run_dir = path.parent.parent
    path_state, path_run_id = run_dir.parent.parent, run_dir.name
    if run_id is not None and run_id != path_run_id:
        raise InvalidReceiptError("receipt belongs to another Run")
    return Path(state) if state is not None else path_state, run_id or path_run_id


def manifest_receipt(result: ReplayVerification) -> dict[str, str]:
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": digest_bytes(result.receipt_path.read_bytes()),
    }


def projection_digest(receipt: Mapping[str, Any]) -> str | None:
    digests = receipt.get("projection_digests")
    return digests.get(PROJECTION_VERSION) if isinstance(digests, Mapping) else None
