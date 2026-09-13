"""Retained runtime evidence is independently verifiable from a clean checkout."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
VERIFY = ROOT / "scripts" / "verify_runtime_evidence.py"
SOURCE = ROOT / "docs/evidence/runtime-qualification-v1/269-strict-isolation-preflight"


def clean_checkout(tmp_path: Path, capsule: Path) -> Path:
    checkout = tmp_path / "checkout"
    document = json.loads((capsule / "capsule.json").read_bytes())
    for name in document["input_files"]:
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    return checkout


def verify(capsule: Path, checkout: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(VERIFY), str(capsule), "--checkout", str(checkout)],
        capture_output=True,
        text=True,
        check=False,
    )


def resign_with_substitute_key(capsule: Path, tmp_path: Path) -> None:
    private = tmp_path / "substitute-private.pem"
    public = capsule / "evaluator-public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private], check=True)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True)
    document = json.loads((capsule / "capsule.json").read_bytes())
    document["signer_public_key_digest"] = hashlib.sha256(public.read_bytes()).hexdigest()
    manifest = capsule / "capsule.json"
    manifest.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            str(private),
            "-in",
            str(manifest),
            "-out",
            str(capsule / "capsule.sig"),
        ],
        check=True,
    )


def test_clean_checkout_verifier_rejects_artifact_input_signature_and_key_tampering(tmp_path: Path):
    capsule = tmp_path / SOURCE.name
    shutil.copytree(SOURCE, capsule)
    checkout = clean_checkout(tmp_path, capsule)
    assert verify(capsule, checkout).returncode == 0

    artifact_copy = tmp_path / "artifact-copy"
    shutil.copytree(capsule, artifact_copy)
    artifact = artifact_copy / "strict-isolation-preflight.receipt.json"
    artifact.write_bytes(artifact.read_bytes() + b"changed")
    assert verify(artifact_copy, checkout).returncode != 0

    input_copy = tmp_path / "input-copy"
    shutil.copytree(checkout, input_copy)
    dockerfile = input_copy / "Dockerfile"
    dockerfile.write_bytes(dockerfile.read_bytes() + b"changed")
    assert verify(capsule, input_copy).returncode != 0

    signature_copy = tmp_path / "signature-copy"
    shutil.copytree(capsule, signature_copy)
    signature = signature_copy / "capsule.sig"
    signature.write_bytes(signature.read_bytes() + b"changed")
    assert verify(signature_copy, checkout).returncode != 0

    key_copy = tmp_path / "key-copy"
    shutil.copytree(capsule, key_copy)
    resign_with_substitute_key(key_copy, tmp_path)
    assert verify(key_copy, checkout).returncode != 0
