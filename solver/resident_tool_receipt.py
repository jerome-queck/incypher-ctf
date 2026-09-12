"""Aggregate the proved resident Tool floor into one manifest-addressable receipt."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from solver.event_store_storage import canonical_bytes
from solver.isolation import STRICT_CONTROLS
from solver.tool_supply_receipt import validate_receipt

SCHEMA_VERSION = 1
RECEIPT_TYPE = "tool-resident"
MANIFEST_ROW_ID = "core.tool-surface"
MANIFEST_RECEIPT_REF = "receipt:tool-resident"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(document: Mapping[str, object]) -> str:
    basis = dict(document)
    basis.pop("identity", None)
    return "sha256:" + _digest(canonical_bytes(basis))


def create_receipt(supply_receipts: Sequence[Mapping[str, object]], inventory: bytes) -> dict[str, object]:
    """Return one self-contained receipt after verifying every resident proof."""

    try:
        catalogue = json.loads(inventory)
        resident = {item["component_id"]: item for item in catalogue["components"] if "resident" in item["profiles"]}
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("resident Tool catalogue is invalid") from error
    if not resident or len(supply_receipts) != len(resident):
        raise ValueError("resident Tool proof set is incomplete")

    image_digest = ""
    proofs: list[dict[str, object]] = []
    proved_components: set[str] = set()
    for source in supply_receipts:
        receipt = json.loads(json.dumps(source))
        component_id = receipt.get("component_id")
        if component_id not in resident or receipt.get("profile_id") != "resident":
            raise ValueError("resident Tool proof names an undeclared component")
        if component_id in proved_components:
            raise ValueError("resident Tool proof set contains a duplicate component")
        proved_components.add(str(component_id))
        current_image = (
            receipt.get("image", {}).get("manifest_digest") if isinstance(receipt.get("image"), dict) else None
        )
        if not isinstance(current_image, str):
            raise ValueError("resident Tool proof has no image binding")
        validate_receipt(receipt, expected_image_manifest_digest=current_image)
        material = receipt.get("materials")
        embedded = material.get("inventory") if isinstance(material, dict) else None
        if not isinstance(embedded, dict) or embedded.get("sha256") != _digest(inventory):
            raise ValueError("resident Tool proof names another catalogue")
        if image_digest and image_digest != current_image:
            raise ValueError("resident Tool proofs name different images")
        image_digest = current_image
        handle_solve = receipt.get("handle_solve")
        isolation = receipt.get("isolation")
        if not isinstance(handle_solve, dict) or handle_solve.get("receipt_type") != "resident-handle-solve":
            raise ValueError("resident Tool Solve proof did not pass")
        checks = isolation.get("checks") if isinstance(isolation, dict) else None
        if checks != {control: "pass" for control in STRICT_CONTROLS}:
            raise ValueError("resident Tool Isolation proof did not pass")
        proofs.append(
            {
                "component_id": component_id,
                "capability_ids": resident[component_id]["capability_ids"],
                "supply_receipt": receipt,
                "supply_receipt_digest": _digest(canonical_bytes(receipt)),
                "handle_solve_identity": handle_solve["identity"],
                "solve": "pass",
                "isolation": "pass",
            }
        )

    if proved_components != set(resident):
        raise ValueError("resident Tool proof set is incomplete")
    document: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "image_digest": image_digest,
        "catalogue_digest": _digest(inventory),
        "capability_ids": sorted(
            capability for component in resident.values() for capability in component["capability_ids"]
        ),
        "proofs": sorted(proofs, key=lambda item: str(item["component_id"])),
    }
    document["identity"] = _identity(document)
    return document


def verify_receipt(receipt: Mapping[str, object], inventory: bytes) -> None:
    """Rebuild and compare a resident receipt from its embedded source proofs."""

    if (
        set(receipt)
        != {
            "schema_version",
            "receipt_type",
            "image_digest",
            "catalogue_digest",
            "capability_ids",
            "proofs",
            "identity",
        }
        or receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("receipt_type") != RECEIPT_TYPE
    ):
        raise ValueError("resident Tool receipt shape is invalid")
    proofs = receipt.get("proofs")
    if not isinstance(proofs, list):
        raise ValueError("resident Tool proofs are invalid")
    sources = [item.get("supply_receipt") for item in proofs if isinstance(item, dict)]
    if len(sources) != len(proofs) or any(not isinstance(item, Mapping) for item in sources):
        raise ValueError("resident Tool source proofs are invalid")
    rebuilt = create_receipt(sources, inventory)  # type: ignore[arg-type]
    if rebuilt != receipt or receipt.get("identity") != _identity(receipt):
        raise ValueError("resident Tool receipt does not match its source proofs")


def manifest_receipt(receipt: Mapping[str, object], inventory: bytes) -> dict[str, str]:
    verify_receipt(receipt, inventory)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": _digest(canonical_bytes(receipt))}


def write_receipt(supply_root: Path) -> Path:
    """Verify promoted component proofs and atomically publish their aggregate."""

    supply_root = Path(supply_root)
    inventory = (supply_root / "generated" / "inventory.json").read_bytes()
    catalogue = json.loads(inventory)
    component_ids = sorted(item["component_id"] for item in catalogue["components"] if "resident" in item["profiles"])
    sources = [
        json.loads((supply_root / "receipts" / f"{component_id}.json").read_text()) for component_id in component_ids
    ]
    document = create_receipt(sources, inventory)
    destination = supply_root / "receipts" / "tool-resident.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".tool-resident-", dir=destination.parent)
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


def link_manifest(manifest: Mapping[str, object], receipt: Mapping[str, object], inventory: bytes):
    from solver.manifest import attach_requirement_receipt

    candidate = manifest.get("candidate")
    if not isinstance(candidate, Mapping) or candidate.get("image_digest") != receipt.get("image_digest"):
        raise ValueError("resident Tool receipt belongs to another candidate image")
    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(receipt, inventory))


__all__ = ["create_receipt", "link_manifest", "manifest_receipt", "verify_receipt", "write_receipt"]
