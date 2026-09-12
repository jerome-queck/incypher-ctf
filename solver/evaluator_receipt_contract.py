"""Minimal consumer contract for externally produced Evaluator receipts."""

from __future__ import annotations

import base64
import hashlib
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes

SCHEMA_VERSION = 1
RECEIPT_KIND = "evaluator-framework"
PRODUCER = "external-evaluator"
MANIFEST_ROW_ID = "core.controlled-proofs"
MANIFEST_RECEIPT_REF = "receipt:evaluator-framework"


def verify_document(receipt: Mapping[str, object]) -> None:
    """Verify the embedded public-key signature and closed public receipt shape."""

    required = {
        "schema_version",
        "kind",
        "producer",
        "binding",
        "source_receipts",
        "verdicts",
        "sanitization",
        "signer_public_key_digest",
        "signer_public_key",
        "signature",
    }
    if (
        set(receipt) != required
        or receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("kind") != RECEIPT_KIND
        or receipt.get("producer") != PRODUCER
        or receipt.get("verdicts")
        not in (
            {"solve": "pass", "isolation": "pass"},
            {"solve": "pass", "isolation": "fail"},
            {"solve": "fail", "isolation": "pass"},
            {"solve": "fail", "isolation": "fail"},
        )
        or receipt.get("sanitization")
        != {
            "fixture_bytes_excluded": True,
            "hidden_solution_excluded": True,
            "host_paths_excluded": True,
        }
    ):
        raise ValueError("Evaluator receipt shape is invalid")
    binding = receipt["binding"]
    sources = receipt["source_receipts"]
    if not isinstance(binding, Mapping) or set(binding) != {
        "manifest_digest",
        "image_digest",
        "profile_digest",
        "isolation_profile_digest",
        "evaluator_key_digest",
        "fixture_id",
        "fixture_digest",
        "component_id",
        "component_version",
        "expected_output_digest",
        "arguments_digest",
        "invocation_input_digest",
    }:
        raise ValueError("Evaluator receipt binding is invalid")
    if not isinstance(sources, Mapping) or set(sources) != {
        "tool_handle_digest",
        "strict_isolation_digest",
        "attempt_resource_digest",
        "tool_supply_digest",
        "boundary_trace_digest",
    }:
        raise ValueError("Evaluator receipt sources are invalid")
    digests = (
        binding["manifest_digest"],
        binding["profile_digest"],
        binding["isolation_profile_digest"],
        binding["evaluator_key_digest"],
        binding["fixture_digest"],
        binding["expected_output_digest"],
        binding["arguments_digest"],
        binding["invocation_input_digest"],
        *sources.values(),
    )
    if any(not _digest(value) for value in digests) or not _image(binding["image_digest"]):
        raise ValueError("Evaluator receipt digest binding is invalid")
    try:
        public_key = base64.b64decode(receipt["signer_public_key"], validate=True)  # type: ignore[arg-type]
        signature = base64.b64decode(receipt["signature"], validate=True)  # type: ignore[arg-type]
    except (TypeError, ValueError) as error:
        raise ValueError("Evaluator receipt signature is invalid") from error
    if hashlib.sha256(public_key).hexdigest() != receipt["signer_public_key_digest"]:
        raise ValueError("Evaluator receipt signer identity is invalid")
    unsigned = dict(receipt)
    unsigned.pop("signature")
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        body = root / "receipt.json"
        key = root / "public.pem"
        signed = root / "receipt.sig"
        body.write_bytes(canonical_bytes(unsigned) + b"\n")
        key.write_bytes(public_key)
        signed.write_bytes(signature)
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(key),
                "-in",
                str(body),
                "-sigfile",
                str(signed),
            ],
            capture_output=True,
            check=False,
        )
    if result.returncode:
        raise ValueError("Evaluator receipt signature is invalid")


def capsule_contract(trusted_signer_digest: str):
    from solver.evidence_capsule_contracts import ReceiptContract

    def validate(receipt, _source):
        verify_document(receipt)
        if receipt["signer_public_key_digest"] != trusted_signer_digest:
            raise ValueError("Evaluator receipt signer is not the trusted authority")
        return ()

    return ReceiptContract(RECEIPT_KIND, SCHEMA_VERSION, PRODUCER, validate)


def manifest_receipt(receipt: Mapping[str, object]) -> dict[str, str]:
    verify_document(receipt)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_KIND,
        "digest": digest_bytes(canonical_bytes(receipt)),
    }


def link_manifest(manifest: Mapping[str, object], receipt: Mapping[str, object]):
    from solver.manifest import attach_requirement_receipt, manifest_digest

    candidate = manifest["candidate"]  # type: ignore[index]
    profile = manifest["selected_profile"]  # type: ignore[index]
    binding = receipt["binding"]
    trusted = _trusted_key_digest(manifest)
    if (
        not isinstance(candidate, Mapping)
        or not isinstance(profile, Mapping)
        or not isinstance(binding, Mapping)
        or binding.get("manifest_digest") != manifest_digest(manifest)
        or binding.get("image_digest") != candidate.get("image_digest")
        or binding.get("profile_digest") != profile.get("profile_digest")
        or not isinstance(profile.get("isolation"), Mapping)
        or binding.get("isolation_profile_digest") != profile["isolation"].get("profile_digest")
        or binding.get("evaluator_key_digest") != trusted
        or receipt.get("signer_public_key_digest") != trusted
    ):
        raise ValueError("Evaluator receipt belongs to a different candidate manifest")

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(receipt))


def _trusted_key_digest(manifest: Mapping[str, object]) -> str:
    receipts = manifest.get("receipts")
    if not isinstance(receipts, list):
        raise ValueError("candidate manifest has no Evaluator trust anchor")
    refs = [
        item.get("digest")
        for item in receipts
        if isinstance(item, Mapping) and item.get("kind") == "evaluator-trust-anchor"
    ]
    if len(refs) != 1 or not _digest(refs[0]):
        raise ValueError("candidate manifest has no unique Evaluator trust anchor")
    return str(refs[0])


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not set(value) - set("0123456789abcdef")


def _image(value: object) -> bool:
    return isinstance(value, str) and value.startswith("sha256:") and _digest(value[7:])


__all__ = ["capsule_contract", "link_manifest", "manifest_receipt", "verify_document"]
