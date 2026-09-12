"""Solver-side admission of externally signed Evaluator evidence."""

import base64
import hashlib
import subprocess
from pathlib import Path

import pytest

from solver.evaluator_receipt_contract import capsule_contract, link_manifest, verify_document
from solver.event_store_storage import canonical_bytes
from solver.manifest import generate_manifest, manifest_digest
from test_manifest import release_candidate_profile


def candidate_and_receipt(tmp_path: Path) -> tuple[dict[str, object], dict[str, object]]:
    private = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    body = tmp_path / "body.json"
    signature = tmp_path / "body.sig"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(private)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["openssl", "pkey", "-in", str(private), "-pubout", "-out", str(public)],
        check=True,
        capture_output=True,
    )
    key = public.read_bytes()
    key_digest = hashlib.sha256(key).hexdigest()
    profile = release_candidate_profile()
    manifest = generate_manifest(
        image_digest="sha256:" + "2" * 64,
        release_candidate_profile=profile,
        receipts=[
            {
                "ref": "trust:evaluator-key",
                "kind": "evaluator-trust-anchor",
                "digest": key_digest,
            }
        ],
    )
    receipt: dict[str, object] = {
        "schema_version": 1,
        "kind": "evaluator-framework",
        "producer": "external-evaluator",
        "binding": {
            "manifest_digest": manifest_digest(manifest),
            "image_digest": manifest["candidate"]["image_digest"],
            "profile_digest": manifest["selected_profile"]["profile_digest"],
            "isolation_profile_digest": manifest["selected_profile"]["isolation"]["profile_digest"],
            "evaluator_key_digest": key_digest,
            "fixture_id": "fixture.identity.transform-v1",
            "fixture_digest": "4" * 64,
            "component_id": "fixture.identity",
            "component_version": "1.0.0",
            "expected_output_digest": "5" * 64,
            "arguments_digest": "a" * 64,
            "invocation_input_digest": "b" * 64,
        },
        "source_receipts": {
            "tool_handle_digest": "6" * 64,
            "strict_isolation_digest": "7" * 64,
            "attempt_resource_digest": "8" * 64,
            "tool_supply_digest": "c" * 64,
            "boundary_trace_digest": "9" * 64,
        },
        "verdicts": {"solve": "pass", "isolation": "pass"},
        "sanitization": {
            "fixture_bytes_excluded": True,
            "hidden_solution_excluded": True,
            "host_paths_excluded": True,
        },
        "signer_public_key_digest": key_digest,
        "signer_public_key": base64.b64encode(key).decode("ascii"),
    }
    body.write_bytes(canonical_bytes(receipt) + b"\n")
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            str(private),
            "-in",
            str(body),
            "-out",
            str(signature),
        ],
        check=True,
        capture_output=True,
    )
    receipt["signature"] = base64.b64encode(signature.read_bytes()).decode("ascii")
    return manifest, receipt


def test_signed_external_receipt_enters_capsule_and_matching_manifest_contracts(tmp_path: Path) -> None:
    manifest, receipt = candidate_and_receipt(tmp_path)
    key_digest = receipt["signer_public_key_digest"]

    verify_document(receipt)
    assert capsule_contract(key_digest).validate(receipt, object()) == ()
    linked = link_manifest(manifest, receipt)
    row = next(item for item in linked["requirements"] if item["row_id"] == "core.controlled-proofs")
    assert row["receipt_ref"] == "receipt:evaluator-framework"


def test_external_receipt_tampering_is_rejected(tmp_path: Path) -> None:
    _manifest, receipt = candidate_and_receipt(tmp_path)
    receipt["verdicts"] = {"solve": "pass", "isolation": "fail"}

    with pytest.raises(ValueError, match="signature"):
        verify_document(receipt)


def test_receipt_for_another_candidate_or_signer_cannot_link(tmp_path: Path) -> None:
    manifest, receipt = candidate_and_receipt(tmp_path)
    other = {
        **manifest,
        "candidate": {"identity": "sha256:" + "d" * 64, "image_digest": "sha256:" + "d" * 64},
    }

    with pytest.raises(ValueError, match="different candidate"):
        link_manifest(other, receipt)
    with pytest.raises(ValueError, match="trusted authority"):
        capsule_contract("e" * 64).validate(receipt, object())
