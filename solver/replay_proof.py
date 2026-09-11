"""Load the controlled proof bound to the replay implementation in this image."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from solver.event_store import InvalidReceiptError
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.replay_proof_lock import CONTROLLED_PROOF_DIGEST

PROOF_FILENAME = "verified-replay.proof.json"
PROOF_TYPE = "verified-replay-controlled-proof"


def subject_digest() -> str:
    package = Path(__file__).parent
    subjects = sorted(path for path in package.glob("*.py") if path.name != "replay_proof_lock.py")
    digests = {path.name: digest_bytes(path.read_bytes()) for path in subjects}
    return digest_bytes(canonical_bytes(digests))


def load_controlled_proof() -> dict[str, Any]:
    path = Path(__file__).with_name(PROOF_FILENAME)
    try:
        raw = path.read_bytes()
        proof = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("controlled replay proof is unavailable") from error
    if digest_bytes(raw) != CONTROLLED_PROOF_DIGEST:
        raise InvalidReceiptError("controlled replay proof differs from its reviewed artifact")
    if not isinstance(proof, dict) or raw != canonical_bytes(proof) + b"\n":
        raise InvalidReceiptError("controlled replay proof is not canonical JSON")
    if proof.get("schema_version") != 1 or proof.get("proof_type") != PROOF_TYPE:
        raise InvalidReceiptError("controlled replay proof has an unsupported contract")
    if proof.get("subject_digest") != subject_digest():
        raise InvalidReceiptError("controlled replay proof does not cover this implementation")
    return proof
