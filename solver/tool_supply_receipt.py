"""Versioned, independently verifiable Tool-supply qualification receipts."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solver.isolation import STRICT_CONTROLS, STRICT_PROFILE_DIGEST, STRICT_PROFILE_ID, STRICT_RUNTIME_PIN
from solver.redaction import Redactor

SCHEMA_VERSION = 1
RECEIPT_TYPE = "tool-supply-receipt"


class ReceiptInvalid(ValueError):
    """A Tool-supply receipt cannot support its claimed observation."""


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_receipt_bytes(receipt: Mapping[str, object], *, without_identity: bool = False) -> bytes:
    document = dict(receipt)
    if without_identity:
        document.pop("identity", None)
    return (json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ReceiptInvalid(f"{label} must be an object")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReceiptInvalid(f"{label} must be a non-empty string")
    return value


def _digest(value: object, label: str, *, prefixed: bool = False) -> str:
    digest = _string(value, label)
    raw = digest.removeprefix("sha256:") if prefixed else digest
    if len(raw) != 64 or any(character not in "0123456789abcdef" for character in raw):
        raise ReceiptInvalid(f"{label} must be a sha256 digest")
    if prefixed and not digest.startswith("sha256:"):
        raise ReceiptInvalid(f"{label} must use the sha256: prefix")
    return digest


def _material_bytes(record: Mapping[str, Any], label: str) -> bytes:
    encoded = _string(record.get("bytes"), f"{label}.bytes")
    try:
        content = bytes.fromhex(encoded)
    except ValueError as error:
        raise ReceiptInvalid(f"{label}.bytes is not hexadecimal") from error
    supplied = _digest(record.get("sha256"), f"{label}.sha256")
    if hashlib.sha256(content).hexdigest() != supplied:
        raise ReceiptInvalid(f"{label} digest does not match embedded bytes")
    return content


def _inventory_projection(component: Mapping[str, Any], profile_id: str) -> dict[str, Any]:
    projected = json.loads(json.dumps(component))
    projected["profiles"] = [profile_id]
    projected["platforms"] = sorted(projected["platforms"])
    projected["packages"] = sorted(projected["packages"], key=lambda item: item["name"])
    projected["files"] = sorted(projected["files"], key=lambda item: item["destination"])
    return projected


@dataclass(frozen=True)
class _BuildEvidence:
    component: Mapping[str, Any]
    closure: Mapping[str, tuple[Mapping[str, Any], bytes]]


def _validate_document(document: Mapping[str, Any]) -> None:
    required = {
        "schema_version",
        "receipt_type",
        "component_id",
        "profile_id",
        "image",
        "strict_profile_digest",
        "isolation",
        "materials",
        "sbom",
        "semantic_fixture",
        "identity",
    }
    if set(document) != required:
        raise ReceiptInvalid("receipt has unknown or missing fields")
    if document["schema_version"] != SCHEMA_VERSION or document["receipt_type"] != RECEIPT_TYPE:
        raise ReceiptInvalid("unsupported Tool-supply receipt schema")
    _string(document["component_id"], "component_id")
    _string(document["profile_id"], "profile_id")


def _validate_image(document: Mapping[str, Any], expected_manifest: str | None) -> str:
    image = _mapping(document["image"], "image")
    if set(image) != {"platform", "manifest_digest", "config_digest"}:
        raise ReceiptInvalid("image binding must contain platform, manifest_digest and config_digest")
    _string(image["platform"], "image.platform")
    manifest = _digest(image["manifest_digest"], "image.manifest_digest", prefixed=True)
    _digest(image["config_digest"], "image.config_digest", prefixed=True)
    if expected_manifest is not None and manifest != expected_manifest:
        raise ReceiptInvalid("receipt names a different image manifest digest")
    return manifest


def _validate_isolation(document: Mapping[str, Any], manifest_digest: str) -> None:
    isolation = _mapping(document["isolation"], "isolation")
    required = {
        "profile_id",
        "profile_digest",
        "image_id",
        "runtime_pin",
        "outer_capabilities",
        "checks",
        "broker_peer_uid",
        "processes_before_kill",
        "processes_after_kill",
        "owned_residue",
    }
    if set(isolation) != required:
        raise ReceiptInvalid("isolation receipt has unknown or missing fields")
    expected = {
        "profile_id": STRICT_PROFILE_ID,
        "profile_digest": STRICT_PROFILE_DIGEST,
        "image_id": manifest_digest,
        "runtime_pin": STRICT_RUNTIME_PIN,
        "outer_capabilities": "empty",
        "checks": {control: "pass" for control in STRICT_CONTROLS},
        "broker_peer_uid": 20000,
        "processes_after_kill": 0,
        "owned_residue": [],
    }
    if any(isolation.get(key) != value for key, value in expected.items()):
        raise ReceiptInvalid("isolation receipt does not prove the strict profile for this image")
    if not isinstance(isolation.get("processes_before_kill"), int) or isolation["processes_before_kill"] < 3:
        raise ReceiptInvalid("isolation receipt does not prove hostile process teardown")
    if document["strict_profile_digest"] != isolation["profile_digest"]:
        raise ReceiptInvalid("strict profile digest is not bound to the isolation receipt")


def _embedded_build_documents(document: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    materials = _mapping(document["materials"], "materials")
    if set(materials) != {"lock", "inventory", "supply_receipt", "closure", "source", "licence", "fixture"}:
        raise ReceiptInvalid("materials have unknown or missing fields")
    lock_bytes = _material_bytes(_mapping(materials["lock"], "materials.lock"), "lock")
    inventory_bytes = _material_bytes(_mapping(materials["inventory"], "materials.inventory"), "inventory")
    supply_bytes = _material_bytes(_mapping(materials["supply_receipt"], "materials.supply_receipt"), "supply receipt")
    try:
        lock = json.loads(lock_bytes)
        inventory = json.loads(inventory_bytes)
        supply = json.loads(supply_bytes)
        component_id = document["component_id"]
        component = next(item for item in lock["components"] if item["component_id"] == component_id)
        inventory_component = next(item for item in inventory["components"] if item["component_id"] == component_id)
    except (KeyError, StopIteration, TypeError, json.JSONDecodeError) as error:
        raise ReceiptInvalid("embedded build materials do not contain the receipt component") from error
    if lock.get("profile_id") != document["profile_id"]:
        raise ReceiptInvalid("embedded lock profile does not match the receipt")
    if not any(
        item.get("sha256") == _sha(lock_bytes)
        for item in supply.get("fragment_digests", [])
        if isinstance(item, Mapping)
    ):
        raise ReceiptInvalid("embedded supply receipt does not bind the lock")
    if supply.get("assembly_inventory_digest") != _sha(inventory_bytes):
        raise ReceiptInvalid("embedded supply receipt does not bind the inventory")
    if inventory_component != _inventory_projection(component, lock["profile_id"]):
        raise ReceiptInvalid("embedded inventory does not equal the lock")
    return materials, component


def _validate_closure(
    materials: Mapping[str, Any], component: Mapping[str, Any]
) -> Mapping[str, tuple[Mapping[str, Any], bytes]]:
    closure_value = materials["closure"]
    if not isinstance(closure_value, list) or not closure_value:
        raise ReceiptInvalid("material closure must embed every locked file")
    closure: dict[str, tuple[Mapping[str, Any], bytes]] = {}
    for index, value in enumerate(closure_value):
        record = _mapping(value, f"materials.closure[{index}]")
        if set(record) != {"source", "destination", "sha256", "bytes"}:
            raise ReceiptInvalid("closure material has unknown or missing fields")
        _string(record["source"], "closure source")
        destination = _string(record["destination"], "closure destination")
        content = _material_bytes(record, f"closure {destination}")
        if destination in closure:
            raise ReceiptInvalid("material closure contains duplicate destinations")
        closure[destination] = (record, content)
    locked = {
        item["destination"]: (item["source"], item["sha256"])
        for item in component.get("files", [])
        if isinstance(item, Mapping)
    }
    observed = {destination: (record["source"], record["sha256"]) for destination, (record, _) in closure.items()}
    if observed != locked:
        raise ReceiptInvalid("material closure does not equal every locked file")
    return closure


def _validate_authorities(
    materials: Mapping[str, Any], component: Mapping[str, Any], closure: Mapping[str, tuple[Mapping[str, Any], bytes]]
) -> str:
    source = _mapping(materials["source"], "materials.source")
    licence = _mapping(materials["licence"], "materials.licence")
    fixture = _mapping(materials["fixture"], "materials.fixture")
    if set(source) != {"uri", "sha256"} or set(licence) != {"expression", "authority", "sha256"}:
        raise ReceiptInvalid("source or licence authority has unknown or missing fields")
    if set(fixture) != {"fixture_id", "input_sha256", "expected_stdout_sha256"}:
        raise ReceiptInvalid("fixture authority has unknown or missing fields")
    _string(source["uri"], "source.uri")
    _string(licence["expression"], "licence.expression")
    _string(licence["authority"], "licence.authority")
    _string(fixture["fixture_id"], "fixture.fixture_id")
    expected_stdout = _digest(fixture["expected_stdout_sha256"], "fixture.expected_stdout_sha256")
    locked_by_source = {item["source"]: item["destination"] for item in component["files"]}
    source_destination = locked_by_source.get(component["source"]["file"])
    licence_destination = locked_by_source.get(component["license"]["file"])
    fixture_destination = locked_by_source.get(component["fixture"]["input_file"])
    if not all((source_destination, licence_destination, fixture_destination)):
        raise ReceiptInvalid("authority evidence is unrelated to the locked component")
    source_bytes = closure[source_destination][1]
    licence_bytes = closure[licence_destination][1]
    fixture_bytes = closure[fixture_destination][1]
    if not source_bytes or not licence_bytes or not fixture_bytes:
        raise ReceiptInvalid("source, licence and fixture evidence must not be empty")
    if source.get("uri") != component["source"]["uri"] or source.get("sha256") != _sha(source_bytes):
        raise ReceiptInvalid("source authority does not match the lock")
    if (
        licence.get("expression") != component["license_expression"]
        or licence.get("authority") != component["license"]["authority"]
        or licence.get("sha256") != _sha(licence_bytes)
    ):
        raise ReceiptInvalid("licence authority does not match the lock")
    locked_fixture = component["fixture"]
    if (
        fixture.get("fixture_id") != locked_fixture["fixture_id"]
        or fixture.get("input_sha256") != _sha(fixture_bytes)
        or fixture.get("expected_stdout_sha256") != locked_fixture["expected_stdout_sha256"]
    ):
        raise ReceiptInvalid("fixture evidence does not match the lock")
    return expected_stdout


def _validate_sbom(document: Mapping[str, Any], evidence: _BuildEvidence) -> tuple[str, str, str]:
    sbom = _mapping(document["sbom"], "sbom")
    if set(sbom) != {"component_version", "entrypoint", "entrypoint_sha256", "files", "packages"}:
        raise ReceiptInvalid("SBOM has unknown or missing fields")
    version = _string(sbom["component_version"], "sbom.component_version")
    entrypoint = _string(sbom["entrypoint"], "sbom.entrypoint")
    entrypoint_digest = _digest(sbom["entrypoint_sha256"], "sbom.entrypoint_sha256")
    files = sbom["files"]
    if not isinstance(files, list) or not files or not isinstance(sbom["packages"], list):
        raise ReceiptInvalid("SBOM must enumerate the complete file and package closure")
    observed: dict[str, tuple[str, int]] = {}
    for index, value in enumerate(files):
        record = _mapping(value, f"sbom.files[{index}]")
        if set(record) != {"destination", "sha256", "size"}:
            raise ReceiptInvalid("SBOM file has unknown or missing fields")
        destination = _string(record["destination"], "SBOM destination")
        digest = _digest(record["sha256"], "SBOM file digest")
        if destination in observed or not isinstance(record["size"], int):
            raise ReceiptInvalid("SBOM contains a duplicate destination or invalid size")
        observed[destination] = (digest, record["size"])
    expected = {
        destination: (record["sha256"], len(content)) for destination, (record, content) in evidence.closure.items()
    }
    if observed != expected:
        raise ReceiptInvalid("SBOM digests and sizes do not equal the embedded locked closure")
    component = evidence.component
    if (
        sbom["packages"] != component["packages"]
        or version != component["version"]
        or entrypoint != component["entrypoint"]
    ):
        raise ReceiptInvalid("SBOM identity does not equal the locked component")
    if entrypoint not in observed or observed[entrypoint][0] != entrypoint_digest:
        raise ReceiptInvalid("SBOM does not bind the locked entrypoint")
    return entrypoint, entrypoint_digest, version


def _validate_semantic(
    document: Mapping[str, Any], entrypoint: str, entrypoint_digest: str, version: str, expected_stdout: str
) -> None:
    semantic = _mapping(document["semantic_fixture"], "semantic_fixture")
    if set(semantic) != {
        "entrypoint",
        "entrypoint_sha256",
        "observed_version",
        "exit_code",
        "stdout_sha256",
        "stderr_sha256",
        "outcome",
    }:
        raise ReceiptInvalid("semantic fixture has unknown or missing fields")
    if semantic["entrypoint"] != entrypoint or semantic["entrypoint_sha256"] != entrypoint_digest:
        raise ReceiptInvalid("semantic fixture did not execute the locked entrypoint")
    if semantic["observed_version"] != version:
        raise ReceiptInvalid("observed installed version does not match the SBOM")
    if semantic["exit_code"] != 0 or semantic["outcome"] != "pass":
        raise ReceiptInvalid("semantic fixture did not pass")
    if _digest(semantic["stdout_sha256"], "semantic stdout") != expected_stdout:
        raise ReceiptInvalid("semantic fixture output does not match the independent expected result")
    _digest(semantic["stderr_sha256"], "semantic stderr")


def validate_receipt(receipt: Mapping[str, object], *, expected_image_manifest_digest: str | None = None) -> None:
    """Validate a promoted receipt using only itself and the expected distributable image identity."""

    document = _mapping(receipt, "receipt")
    _validate_document(document)
    manifest_digest = _validate_image(document, expected_image_manifest_digest)
    _validate_isolation(document, manifest_digest)
    materials, component = _embedded_build_documents(document)
    closure = _validate_closure(materials, component)
    evidence = _BuildEvidence(component, closure)
    expected_stdout = _validate_authorities(materials, component, closure)
    entrypoint, entrypoint_digest, version = _validate_sbom(document, evidence)
    _validate_semantic(document, entrypoint, entrypoint_digest, version, expected_stdout)
    identity = _digest(document["identity"], "identity", prefixed=True)
    expected_identity = "sha256:" + hashlib.sha256(canonical_receipt_bytes(document, without_identity=True)).hexdigest()
    if identity != expected_identity:
        raise ReceiptInvalid("receipt identity does not match its evidence")


def _creation_component(
    observation: Mapping[str, object], lock_fragment: bytes, inventory: bytes, supply_receipt: bytes
) -> tuple[Mapping[str, Any], Mapping[str, Any], str]:
    try:
        lock = json.loads(lock_fragment)
        inventory_document = json.loads(inventory)
        supply_document = json.loads(supply_receipt)
        component_id = observation["component_id"]
        component = next(item for item in lock["components"] if item["component_id"] == component_id)
        inventory_component = next(
            item for item in inventory_document["components"] if item["component_id"] == component_id
        )
    except (KeyError, StopIteration, TypeError, json.JSONDecodeError) as error:
        raise ReceiptInvalid("locked build materials do not contain the observed component") from error
    fragment_digest = _sha(lock_fragment)
    if not any(
        item.get("sha256") == fragment_digest
        for item in supply_document.get("fragment_digests", [])
        if isinstance(item, Mapping)
    ):
        raise ReceiptInvalid("supply receipt does not bind the lock fragment")
    if supply_document.get("assembly_inventory_digest") != _sha(inventory):
        raise ReceiptInvalid("supply receipt does not bind the inventory")
    if inventory_component != _inventory_projection(component, lock["profile_id"]):
        raise ReceiptInvalid("inventory component does not equal the locked component")
    return lock, component, fragment_digest


def _embed_closure(component: Mapping[str, Any], source_root: Path) -> list[dict[str, object]]:
    closure = []
    for locked in component["files"]:
        try:
            content = (source_root / locked["source"]).read_bytes()
        except OSError as error:
            raise ReceiptInvalid(f"locked material is absent: {locked['source']}") from error
        if _sha(content) != locked["sha256"]:
            raise ReceiptInvalid(f"locked material digest mismatch: {locked['source']}")
        closure.append(
            {
                "source": locked["source"],
                "destination": locked["destination"],
                "sha256": locked["sha256"],
                "bytes": content.hex(),
            }
        )
    return sorted(closure, key=lambda item: str(item["destination"]))


def create_receipt(
    observation: Mapping[str, object],
    *,
    lock_fragment: bytes,
    inventory: bytes,
    supply_receipt: bytes,
    source_root: Path,
) -> dict[str, object]:
    """Bind a strict-image observation to the exact locked and embedded build materials."""

    lock, component, fragment_digest = _creation_component(observation, lock_fragment, inventory, supply_receipt)
    closure = _embed_closure(component, source_root)
    by_source = {item["source"]: item["sha256"] for item in closure}
    component_id = observation["component_id"]
    receipt: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "component_id": component_id,
        "profile_id": lock["profile_id"],
        "image": observation["image"],
        "strict_profile_digest": observation["strict_profile_digest"],
        "isolation": observation["isolation"],
        "materials": {
            "lock": {"sha256": fragment_digest, "bytes": lock_fragment.hex()},
            "inventory": {"sha256": _sha(inventory), "bytes": inventory.hex()},
            "supply_receipt": {"sha256": _sha(supply_receipt), "bytes": supply_receipt.hex()},
            "closure": closure,
            "source": {
                "uri": component["source"]["uri"],
                "sha256": by_source[component["source"]["file"]],
            },
            "licence": {
                "expression": component["license_expression"],
                "authority": component["license"]["authority"],
                "sha256": by_source[component["license"]["file"]],
            },
            "fixture": {
                "fixture_id": component["fixture"]["fixture_id"],
                "input_sha256": by_source[component["fixture"]["input_file"]],
                "expected_stdout_sha256": component["fixture"]["expected_stdout_sha256"],
            },
        },
        "sbom": observation["sbom"],
        "semantic_fixture": observation["semantic_fixture"],
        "identity": "",
    }
    receipt["identity"] = "sha256:" + _sha(canonical_receipt_bytes(receipt, without_identity=True))
    validate_receipt(receipt)
    return receipt


def issue_manifest_receipt(receipt: Mapping[str, object]) -> dict[str, str]:
    """Return the bounded manifest reference; callers decide which provisional row may use it."""

    validate_receipt(receipt)
    component_id = _string(receipt["component_id"], "component_id")
    identity = _string(receipt["identity"], "identity").removeprefix("sha256:")
    return {"ref": f"receipt:tool-supply:{component_id}", "kind": RECEIPT_TYPE, "digest": identity}


def _contains_host_path(payload: bytes, roots: Sequence[Path]) -> bool:
    paths = [b"/Users/", b"/home/"]
    paths.extend(str(root.resolve()).encode() for root in roots)
    return any(path and path in payload for path in paths)


def _embedded_payloads(value: object) -> list[bytes]:
    payloads: list[bytes] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "bytes" and isinstance(child, str):
                try:
                    payloads.append(bytes.fromhex(child))
                except ValueError:
                    pass
            else:
                payloads.extend(_embedded_payloads(child))
    elif isinstance(value, list):
        for child in value:
            payloads.extend(_embedded_payloads(child))
    return payloads


def _sanitized_payloads(receipt: Mapping[str, object]) -> list[bytes]:
    return [canonical_receipt_bytes(receipt), *_embedded_payloads(receipt.get("materials"))]


def promote_receipt(
    receipt: Mapping[str, object],
    destination: Path,
    *,
    secrets: Mapping[str, str] | None = None,
    host_roots: Sequence[Path] = (),
) -> None:
    """Sanitize and atomically promote one immutable passing receipt."""

    payload = canonical_receipt_bytes(receipt)
    sanitized_payloads = _sanitized_payloads(receipt)
    if any(_contains_host_path(candidate, host_roots) for candidate in sanitized_payloads):
        raise ReceiptInvalid("receipt contains an absolute host path")
    redactor = Redactor(secrets or {})
    if any(redactor.redact(candidate) != candidate for candidate in sanitized_payloads):
        raise ReceiptInvalid("receipt contains a declared secret or encoded form")
    validate_receipt(receipt)
    destination = Path(destination)
    if destination.exists():
        existing = json.loads(destination.read_text())
        validate_receipt(existing)
        if existing["image"]["manifest_digest"] != receipt["image"]["manifest_digest"]:
            raise ReceiptInvalid("a receipt for a different image is already promoted")
        if canonical_receipt_bytes(existing) != payload:
            raise ReceiptInvalid("the promoted receipt is immutable")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as staged:
        staged.write(payload)
        staged.flush()
        os.fsync(staged.fileno())
        staged_path = Path(staged.name)
    try:
        try:
            os.link(staged_path, destination)
        except FileExistsError:
            existing = json.loads(destination.read_text())
            validate_receipt(existing)
            if canonical_receipt_bytes(existing) != payload:
                raise ReceiptInvalid("a concurrent promotion published different evidence") from None
        directory = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staged_path.unlink(missing_ok=True)


__all__ = [
    "ReceiptInvalid",
    "canonical_receipt_bytes",
    "create_receipt",
    "issue_manifest_receipt",
    "promote_receipt",
    "validate_receipt",
]
