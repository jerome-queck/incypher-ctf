"""Manifest-addressable qualification receipt for the dedicated Crypto profile."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from solver.crypto_tool_contract import CryptoComponent, CryptoProfileSize, QualifiedCryptoSupply
from solver.event_store_storage import canonical_bytes
from solver.tool_supply_receipt import validate_receipt as validate_supply_receipt

SCHEMA_VERSION = 1
RECEIPT_TYPE = "tool-profile"
PROFILE_ID = "tool-crypto"
MANIFEST_ROW_ID = "core.tool-surface"
MANIFEST_RECEIPT_REF = "receipt:tool-crypto"


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _identity(document: Mapping[str, object]) -> str:
    basis = dict(document)
    basis.pop("identity", None)
    return "sha256:" + _sha(canonical_bytes(basis))


def create_receipt(
    source_receipt: Mapping[str, object], inventory: bytes, size_measurement: Mapping[str, object]
) -> dict[str, object]:
    """Verify and aggregate one promoted Crypto component proof."""

    source = json.loads(json.dumps(source_receipt))
    component = CryptoComponent.from_inventory(json.loads(inventory), PROFILE_ID)
    supply = QualifiedCryptoSupply.from_receipt(source)
    profile_size = CryptoProfileSize.from_measurement(size_measurement, supply.image_digest)
    if supply.component_id != component.component_id or supply.profile_id != PROFILE_ID:
        raise ValueError("Crypto proof names another component or profile")
    validate_supply_receipt(source, expected_image_manifest_digest=supply.image_digest)
    document: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "profile_id": PROFILE_ID,
        "component_id": component.component_id,
        "component_version": component.version,
        "capability_ids": component.capability_ids,
        "packages": component.packages,
        "source": component.source,
        "licence": {
            "expression": component.license_expression,
            "classification": component.license_classification,
            "authority": component.license_authority,
        },
        "size_bytes": supply.size_bytes,
        "profile_size": {
            "baseline_image_digest": profile_size.baseline_image_digest,
            "baseline_size_bytes": profile_size.baseline_size_bytes,
            "baseline_source_commit": profile_size.baseline_source_commit,
            "baseline_source_tree_digest": profile_size.baseline_source_tree_digest,
            "baseline_dockerfile_sha256": profile_size.baseline_dockerfile_sha256,
            "baseline_platform": profile_size.baseline_platform,
            "candidate_image_digest": profile_size.candidate_image_digest,
            "candidate_size_bytes": profile_size.candidate_size_bytes,
            "delta_bytes": profile_size.delta_bytes,
            "budget_bytes": profile_size.budget_bytes,
            "method": profile_size.method,
        },
        "catalogue_digest": _sha(inventory),
        "image_digest": supply.image_digest,
        "supply_receipt_digest": _sha(canonical_bytes(source)),
        "outcomes": {"supply": "pass", "solve": "pass", "isolation": "pass"},
        "source_receipt": source,
        "identity": "",
    }
    document["identity"] = _identity(document)
    return document


def verify_receipt(receipt: Mapping[str, object], inventory: bytes) -> None:
    """Rebuild the profile receipt, invalidating every linked-material change."""

    source = receipt.get("source_receipt")
    if not isinstance(source, Mapping):
        raise ValueError("Crypto source proof is absent")
    size = receipt.get("profile_size")
    if not isinstance(size, Mapping):
        raise ValueError("Crypto profile size proof is absent")
    rebuilt = create_receipt(source, inventory, size)
    if rebuilt != receipt or receipt.get("identity") != _identity(receipt):
        raise ValueError("Crypto profile receipt does not match its linked proof")


def manifest_receipt(receipt: Mapping[str, object], inventory: bytes) -> dict[str, str]:
    verify_receipt(receipt, inventory)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": _sha(canonical_bytes(receipt))}


def link_manifest(manifest: Mapping[str, object], receipt: Mapping[str, object], inventory: bytes):
    from solver.manifest import attach_partial_requirement_receipt

    candidate = manifest.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("image_digest") != receipt.get("image_digest"):
        raise ValueError("Crypto receipt belongs to another candidate image")
    return attach_partial_requirement_receipt(
        manifest,
        MANIFEST_ROW_ID,
        manifest_receipt(receipt, inventory),
        reason="Crypto profile qualified; remaining Core Tool profiles are not complete.",
    )


def write_receipt(supply_root: Path, size_measurement: Mapping[str, object]) -> Path:
    supply_root = Path(supply_root)
    inventory = (supply_root / "generated" / "inventory.json").read_bytes()
    source = json.loads((supply_root / "receipts" / "tool-crypto.core.json").read_text())
    document = create_receipt(source, inventory, size_measurement)
    destination = supply_root / "receipts" / "tool-crypto.json"
    handle, temporary = tempfile.mkstemp(prefix=".tool-crypto-", dir=destination.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(canonical_bytes(document) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return destination


__all__ = ["create_receipt", "link_manifest", "manifest_receipt", "verify_receipt", "write_receipt"]
