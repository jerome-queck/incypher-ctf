"""Load controlled storage-governor proof bound to this implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.storage_governor_proof_lock import CONTROLLED_PROOF_DIGEST

PROOF_FILENAME = "storage-governor.proof.json"
PROOF_TYPE = "storage-governor-controlled-proof"


def subject_digest() -> str:
    package = Path(__file__).parent
    subjects = sorted(
        path for path in package.glob("storage_governor*.py") if path.name != "storage_governor_proof_lock.py"
    )
    return digest_bytes(canonical_bytes({path.name: digest_bytes(path.read_bytes()) for path in subjects}))


def load_controlled_proof() -> dict[str, Any]:
    path = Path(__file__).with_name(PROOF_FILENAME)
    try:
        raw = path.read_bytes()
        proof = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("controlled storage-governor proof is unavailable") from error
    if digest_bytes(raw) != CONTROLLED_PROOF_DIGEST:
        raise ValueError("controlled storage-governor proof differs from its reviewed artifact")
    if not isinstance(proof, dict) or raw != canonical_bytes(proof) + b"\n":
        raise ValueError("controlled storage-governor proof is not canonical JSON")
    if proof.get("schema_version") != 1 or proof.get("proof_type") != PROOF_TYPE:
        raise ValueError("controlled storage-governor proof has an unsupported contract")
    if proof.get("subject_digest") != subject_digest():
        raise ValueError("controlled storage-governor proof does not cover this implementation")
    return proof
