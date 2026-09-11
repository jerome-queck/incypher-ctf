"""Verify the promoted external competition Rig seed evidence."""

import base64
import datetime
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path


DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
SANITIZATION = {
    "hidden_fixture_content_excluded": True,
    "host_paths_excluded": True,
    "secrets_excluded": True,
}


def verify_lock(lock_path: Path) -> int:
    lock = json.loads(lock_path.read_text())
    _verify_lock_identity(lock)
    evidence_directory = lock_path.resolve().parent
    proofs = lock["proofs"]
    if [proof["name"] for proof in proofs] != ["solver-failure", "rig-failure"]:
        raise ValueError("Rig seed requires one Solver failure and one Rig failure proof")
    for proof in proofs:
        _verify_proof(proof, lock, evidence_directory)
    return len(proofs)


def _verify_lock_identity(lock: dict[str, object]) -> None:
    expected_fields = {
        "schema",
        "repository",
        "rig_commit",
        "candidate_digest",
        "image_digests",
        "proofs",
    }
    if set(lock) != expected_fields or lock["schema"] != "rig-seed-lock/v1":
        raise ValueError("Rig seed lock schema is invalid")
    if lock["repository"] != "https://github.com/jerome-queck/incypher-practice-rig":
        raise ValueError("Rig repository is not the controlling sibling")
    if not isinstance(lock["rig_commit"], str) or not COMMIT.fullmatch(lock["rig_commit"]):
        raise ValueError("Rig commit is not exact")
    if not _is_digest(lock["candidate_digest"]):
        raise ValueError("candidate digest is invalid")
    if not _valid_image_digests(lock["image_digests"]):
        raise ValueError("public service image digests are invalid")
    if not isinstance(lock["proofs"], list):
        raise ValueError("proof list is invalid")


def _verify_proof(
    proof: dict[str, object],
    lock: dict[str, object],
    evidence_directory: Path,
) -> None:
    receipt_path = _contained_path(evidence_directory, proof["receipt"])
    public_key_path = _contained_path(evidence_directory, proof["public_key"])
    signature_path = _contained_path(evidence_directory, proof["signature"])
    receipt_bytes = receipt_path.read_bytes()
    public_key_bytes = public_key_path.read_bytes()
    signature = base64.b64decode(signature_path.read_bytes().strip(), validate=True)
    expected_hashes = proof["sha256"]
    _verify_hash("receipt", receipt_bytes, expected_hashes)
    _verify_hash("public_key", public_key_bytes, expected_hashes)
    _verify_hash("signature", signature, expected_hashes)
    _verify_signature(receipt_path, public_key_path, signature)

    receipt = json.loads(receipt_bytes)
    canonical = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if canonical != receipt_bytes:
        raise ValueError("receipt is not canonical JSON")
    _verify_receipt(receipt, proof["expected"], lock)


def _verify_receipt(
    receipt: dict[str, object],
    expected: dict[str, object],
    lock: dict[str, object],
) -> None:
    if receipt["schema"] != "rig-seed/v1":
        raise ValueError("receipt schema is invalid")
    if receipt["candidate_digest"] != lock["candidate_digest"]:
        raise ValueError("receipt candidate digest does not match the lock")
    if receipt["image_digests"] != lock["image_digests"]:
        raise ValueError("receipt image digests do not match the lock")
    if receipt["solver_surface"] != ["board", "target"]:
        raise ValueError("receipt exposes an undeclared Solver surface")
    if receipt["control_plane_reachable"] is not False:
        raise ValueError("receipt reports reachable control authority")
    if receipt["sanitization"] != SANITIZATION:
        raise ValueError("receipt sanitization is incomplete")
    if any(receipt[field] != value for field, value in expected.items()):
        raise ValueError("receipt outcome does not match its declared proof")
    if str(uuid.UUID(receipt["fixture_id"])) != receipt["fixture_id"]:
        raise ValueError("fixture identity is not an opaque canonical UUID")
    started_at = _timestamp(receipt["started_at"])
    finished_at = _timestamp(receipt["finished_at"])
    if finished_at < started_at:
        raise ValueError("receipt timestamps are reversed")


def _verify_signature(receipt_path: Path, public_key_path: Path, signature: bytes) -> None:
    with tempfile.NamedTemporaryFile() as signature_file:
        signature_file.write(signature)
        signature_file.flush()
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(public_key_path),
                "-sigfile",
                signature_file.name,
                "-in",
                str(receipt_path),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
    if result.returncode:
        raise ValueError("receipt signature is invalid")


def _contained_path(directory: Path, relative: object) -> Path:
    if not isinstance(relative, str):
        raise ValueError("evidence path is invalid")
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory):
        raise ValueError("evidence path escapes its directory")
    return path


def _verify_hash(name: str, contents: bytes, expected_hashes: object) -> None:
    if not isinstance(expected_hashes, dict):
        raise ValueError("proof hashes are invalid")
    if hashlib.sha256(contents).hexdigest() != expected_hashes.get(name):
        raise ValueError(f"{name} hash is invalid")


def _valid_image_digests(value: object) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == {"board", "target"}
        and all(_is_digest(digest) for digest in value.values())
    )


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _timestamp(value: object) -> datetime.datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("receipt timestamp is not UTC")
    return datetime.datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python3 scripts/verify_rig_seed.py <lock.json>", file=sys.stderr)
        return 2
    try:
        proof_count = verify_lock(Path(argv[1]))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"invalid rig-seed evidence: {error}", file=sys.stderr)
        return 1
    print(f"verified {proof_count} signed rig-seed receipts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
