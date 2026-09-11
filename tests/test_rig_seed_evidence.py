import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "docs/evidence/rig-seed/lock.json"


def test_promoted_rig_seed_evidence_is_independently_verifiable():
    result = subprocess.run(
        ["python3", "scripts/verify_rig_seed.py", str(LOCK)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "verified 2 signed rig-seed receipts\n"


def test_signature_rejects_receipt_tampering_even_with_an_updated_file_hash():
    with tempfile.TemporaryDirectory() as directory:
        evidence = Path(directory) / "rig-seed"
        shutil.copytree(LOCK.parent, evidence)
        lock_path = evidence / "lock.json"
        lock = json.loads(lock_path.read_text())
        receipt_path = evidence / lock["proofs"][0]["receipt"]
        receipt = json.loads(receipt_path.read_text())
        receipt["infrastructure_valid"] = False
        receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
        lock["proofs"][0]["sha256"]["receipt"] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        lock_path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")

        result = subprocess.run(
            ["python3", "scripts/verify_rig_seed.py", str(lock_path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "signature" in result.stderr


def test_receipts_distinguish_solver_failure_from_invalid_infrastructure():
    lock = json.loads(LOCK.read_text())
    receipts = {proof["name"]: json.loads((LOCK.parent / proof["receipt"]).read_text()) for proof in lock["proofs"]}

    solver_failure = receipts["solver-failure"]
    assert solver_failure["infrastructure_valid"] is True
    assert solver_failure["solver_outcome"] == "failed"
    rig_failure = receipts["rig-failure"]
    assert rig_failure["infrastructure_valid"] is False
    assert rig_failure["infrastructure_failure"] == "target_unavailable"
    assert rig_failure["solver_outcome"] == "not_observed"
