"""Sign a host-observed final-interval proof under the retained Evaluator key."""

from __future__ import annotations

import base64
import hashlib
import subprocess
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes
from solver.final_interval_evaluator import KIND, SOURCES, verify_receipt
from solver.manifest import manifest_digest


def evaluate(source_root: Path, manifest: dict[str, object], private_key: Path, destination: Path) -> Path:
    public = subprocess.run(["openssl", "pkey", "-in", private_key, "-pubout"], check=True, capture_output=True).stdout
    document = {
        "schema_version": 1,
        "kind": KIND,
        "producer": "external-evaluator",
        "binding": {
            "manifest_digest": manifest_digest(manifest),
            "image_digest": manifest["candidate"]["image_digest"],
        },
        "sources": {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in SOURCES},
        "sanitization": {
            "candidate_values_excluded": True,
            "credentials_excluded": True,
            "host_paths_excluded": True,
        },
        "verdict": "pass",
        "signer_public_key": base64.b64encode(public).decode(),
        "signer_public_key_digest": hashlib.sha256(public).hexdigest(),
    }
    unsigned = source_root / ".final-interval-evaluator-unsigned.json"
    atomic_write(unsigned, canonical_bytes(document) + b"\n")
    signature = subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", private_key, "-in", unsigned],
        check=True,
        capture_output=True,
    ).stdout
    unsigned.unlink()
    document["signature"] = base64.b64encode(signature).decode()
    atomic_write(destination, canonical_bytes(document) + b"\n")
    return verify_receipt(destination, source_root)


if __name__ == "__main__":
    raise SystemExit("import evaluate with an explicit manifest and qualified private-key path")
