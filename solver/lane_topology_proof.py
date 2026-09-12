"""Loader for the reviewed controlled Lane-topology proof."""

import json
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lane_topology_proof_lock import CONTROLLED_PROOF_DIGEST


PROOF_PATH = Path(__file__).with_name("lane-topology.proof.json")
SUBJECTS = (
    "lane_topology.py",
    "lane_topology_contracts.py",
    "lane_topology_journal.py",
    "lane_topology_receipt.py",
    "run.py",
    "schedule.py",
)


def subject_digest() -> str:
    root = Path(__file__).parent
    return digest_bytes(canonical_bytes({name: digest_bytes((root / name).read_bytes()) for name in SUBJECTS}))


def load_controlled_proof() -> dict[str, object]:
    raw = PROOF_PATH.read_bytes()
    if digest_bytes(raw) != CONTROLLED_PROOF_DIGEST:
        raise ValueError("Lane-topology controlled proof changed without review")
    proof = json.loads(raw)
    required_scenarios = {
        "close-replay": "pass",
        "duplicate-claim": "refused",
        "failure-isolation": "pass",
        "global-clock-overrun": "refused",
        "manifest-link": "pass",
        "no-queue-order-recompute": "pass",
        "restart-fence": "pass",
        "rolling-refill": "pass",
        "stall-isolation": "pass",
    }
    if (
        proof.get("proof_type") != "lane-topology-controlled-proof"
        or proof.get("schema_version") != 1
        or proof.get("manifest_row_id") != "core.lane-specialist-topology"
        or proof.get("profiles") != {"one-lane": "pass", "two-lane": "pass"}
        or proof.get("scenarios") != required_scenarios
        or proof.get("subject_digest") != subject_digest()
        or proof.get("verdict") != "pass"
    ):
        raise ValueError("Lane-topology controlled proof does not qualify this implementation")
    return proof


__all__ = ["load_controlled_proof", "subject_digest"]
