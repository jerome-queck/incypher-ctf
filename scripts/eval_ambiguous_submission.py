"""Sign ADR-0048's three-record ambiguous-submission reconciliation on the host."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes
from solver.manifest import manifest_digest
from solver.submission.evaluator_receipt import KIND, SOURCES, verify_receipt


def evaluate(source_root: Path, manifest: dict[str, object], private_key: Path, destination: Path) -> Path:
    public = subprocess.run(["openssl", "pkey", "-in", private_key, "-pubout"], check=True, capture_output=True).stdout
    sources = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest() for name in SOURCES}
    board = json.loads((source_root / "actual-board-state.json").read_bytes())
    document = {
        "schema_version": 1,
        "kind": KIND,
        "producer": "external-evaluator",
        "binding": {"manifest_digest": manifest_digest(manifest)},
        "sources": sources,
        "sanitization": {
            "candidate_value_excluded": True,
            "credentials_excluded": True,
            "host_paths_excluded": True,
        },
        "result": {"effect_id": board["effect_id"], "disposition": "unknown-and-spent"},
        "signer_public_key": base64.b64encode(public).decode(),
        "signer_public_key_digest": hashlib.sha256(public).hexdigest(),
    }
    unsigned = source_root / ".ambiguous-evaluator-unsigned.json"
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
