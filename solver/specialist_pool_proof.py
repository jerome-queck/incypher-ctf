"""Load the reviewed deterministic Specialist mechanism proof."""

import json
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes

PROOF_DIGEST = "2ac6645f4def8d73d0312b48c46eaacfec1696b5e60a2194da81226d76b20930"
SUBJECTS = (
    "lead_controller.py",
    "specialist_contracts.py",
    "specialist_pool.py",
    "specialist_receipt.py",
)
SCENARIOS = {
    "bounds-and-quota",
    "canonical-authority-fencing",
    "cancel-and-late-output",
    "carry-acceptance-replay",
    "crash-replay",
    "generation-bound-evidence",
    "global-capacity",
    "lead-carry-integration",
    "manifest-coexistence",
    "multi-item-crash",
    "replay-preserves-verdict",
    "scoped-ownership",
    "surviving-owner",
    "unsettled-replay",
}


def subject_digest():
    root = Path(__file__).parent
    return digest_bytes(canonical_bytes({name: digest_bytes((root / name).read_bytes()) for name in SUBJECTS}))


def load_controlled_proof():
    raw = Path(__file__).with_name("specialist-pool.proof.json").read_bytes()
    document = json.loads(raw)
    if raw != canonical_bytes(document) + b"\n" or digest_bytes(raw) != PROOF_DIGEST:
        raise ValueError("Specialist controlled proof differs from its reviewed artifact")
    if document.get("subject_digest") != subject_digest() or set(document.get("scenarios", ())) != SCENARIOS:
        raise ValueError("Specialist controlled proof does not bind the exact implementation and scenarios")
    return document


__all__ = ["load_controlled_proof", "subject_digest"]
