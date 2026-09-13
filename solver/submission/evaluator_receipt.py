"""Independent contract for the signed host Evaluator ambiguity proof."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.manifest import attach_requirement_receipt, manifest_digest
from solver.submission.ambiguity import verify_receipt as verify_solver
from solver.submission.receipt import verify_receipt as verify_serial

KIND = "ambiguous-submission-evaluator"
REF = "receipt:ambiguous-submission"
ROW = "core.submission-tail"
SOURCES = ("intended-event.json", "actual-board-state.json", "serial-authority.json", "solver-observation.json")


def verify_receipt(path: Path, source_root: Path | None = None) -> Path:
    receipt_path = Path(path)
    document = json.loads(receipt_path.read_bytes())
    if receipt_path.read_bytes() != canonical_bytes(document) + b"\n":
        raise ValueError("Evaluator receipt is not canonical")
    required = {
        "schema_version",
        "kind",
        "producer",
        "binding",
        "sources",
        "sanitization",
        "result",
        "signer_public_key",
        "signer_public_key_digest",
        "signature",
    }
    if (
        set(document) != required
        or document["schema_version"] != 1
        or document["kind"] != KIND
        or document["producer"] != "external-evaluator"
    ):
        raise ValueError("Evaluator receipt shape is invalid")
    if document["sanitization"] != {
        "candidate_value_excluded": True,
        "credentials_excluded": True,
        "host_paths_excluded": True,
    }:
        raise ValueError("Evaluator receipt sanitization is invalid")
    root = source_root or receipt_path.parent
    sources = document["sources"]
    if not isinstance(sources, Mapping) or set(sources) != set(SOURCES):
        raise ValueError("Evaluator source set is incomplete")
    bodies = {}
    for name in SOURCES:
        source = root / name
        try:
            raw = source.read_bytes()
        except OSError as error:
            raise ValueError("Evaluator source is absent") from error
        if hashlib.sha256(raw).hexdigest() != sources[name]:
            raise ValueError("Evaluator source digest is invalid")
        bodies[name] = json.loads(raw)
    verify_solver(root / "solver-observation.json")
    verify_serial(root / "serial-authority.json")
    effect = str(document["result"]["effect_id"])
    intended, board, serial, solver = (bodies[name] for name in SOURCES)
    if intended != {"event": "post-effect-ambiguity", "effect_id": effect, "schema_version": 1}:
        raise ValueError("intended ambiguity event is unreconciled")
    if board != {"effect_id": effect, "posts": 1, "result": "outcome-unknown", "schema_version": 1}:
        raise ValueError("actual Board state is unreconciled")
    serial_rows = [row for row in serial["submissions"] if row["effect_id"] == effect]
    events = [row for row in solver["events"] if row.get("effect_id") == effect]
    if (
        len(serial_rows) != 1
        or serial_rows[0]["states"][-1] != "possibly-sent"
        or not any(row["event"] == "boot-replayed" for row in events)
        or not any(row["event"] == "fence-closed" for row in events)
    ):
        raise ValueError("three-record ambiguity reconciliation is incomplete")
    _verify_signature(document)
    return receipt_path


def _verify_signature(document):
    public = base64.b64decode(document["signer_public_key"], validate=True)
    signature = base64.b64decode(document["signature"], validate=True)
    if hashlib.sha256(public).hexdigest() != document["signer_public_key_digest"]:
        raise ValueError("Evaluator signer identity is invalid")
    unsigned = dict(document)
    unsigned.pop("signature")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        body, key, signed = root / "body", root / "key", root / "signature"
        body.write_bytes(canonical_bytes(unsigned) + b"\n")
        key.write_bytes(public)
        signed.write_bytes(signature)
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", key, "-in", body, "-sigfile", signed],
            capture_output=True,
            check=False,
        )
    if result.returncode:
        raise ValueError("Evaluator signature is invalid")


def descriptor(path: Path):
    verified = verify_receipt(path)
    return {"ref": REF, "kind": KIND, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path):
    document = json.loads(Path(path).read_bytes())
    trusted = [item["digest"] for item in manifest["receipts"] if item["kind"] == "evaluator-trust-anchor"]
    if (
        len(trusted) != 1
        or trusted[0] != document["signer_public_key_digest"]
        or document["binding"]["manifest_digest"] != manifest_digest(manifest)
    ):
        raise ValueError("Evaluator receipt belongs to another manifest or signer")
    return attach_requirement_receipt(manifest, ROW, descriptor(path))
