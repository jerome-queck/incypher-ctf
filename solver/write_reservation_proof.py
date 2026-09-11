"""Load the controlled storage-reservation proof bound to its implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.write_reservation_contracts import ReservationConflict
from solver.write_reservation_proof_lock import CONTROLLED_PROOF_DIGEST


PROOF_FILENAME = "write-reservation.proof.json"
PROOF_TYPE = "write-reservation-controlled-proof"


def subject_digest() -> str:
    package = Path(__file__).parent
    subjects = sorted(
        path for path in package.glob("write_reservation*.py") if path.name != "write_reservation_proof_lock.py"
    )
    return digest_bytes(canonical_bytes({path.name: digest_bytes(path.read_bytes()) for path in subjects}))


def load_controlled_proof() -> dict[str, Any]:
    path = Path(__file__).with_name(PROOF_FILENAME)
    try:
        raw = path.read_bytes()
        proof = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReservationConflict("controlled write-reservation proof is unavailable") from error
    if digest_bytes(raw) != CONTROLLED_PROOF_DIGEST:
        raise ReservationConflict("controlled write-reservation proof differs from its reviewed artifact")
    if not isinstance(proof, dict) or raw != canonical_bytes(proof) + b"\n":
        raise ReservationConflict("controlled write-reservation proof is not canonical JSON")
    if proof.get("schema_version") != 1 or proof.get("proof_type") != PROOF_TYPE:
        raise ReservationConflict("controlled write-reservation proof has an unsupported contract")
    if proof.get("subject_digest") != subject_digest():
        raise ReservationConflict("controlled write-reservation proof does not cover this implementation")
    return proof
