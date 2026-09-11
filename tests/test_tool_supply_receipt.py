import copy
import hashlib
import json
from pathlib import Path

import pytest

from solver.isolation import STRICT_CONTROLS, STRICT_PROFILE_DIGEST, STRICT_PROFILE_ID, STRICT_RUNTIME_PIN
from solver.tool_supply_receipt import (
    ReceiptInvalid,
    canonical_receipt_bytes,
    issue_manifest_receipt,
    promote_receipt,
    validate_receipt,
)


def sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def receipt() -> dict[str, object]:
    source = b"#!/usr/bin/python3\nprint('fixture')\n"
    licence = b"MIT License\n"
    fixture_input = b"alpha beta\n"
    expected = b"ATEB AHPLA\n"
    files = [
        {
            "source": "fixtures/resident-fixture/LICENSE.txt",
            "destination": "/opt/solver/tool-supply/LICENSE.fixture.identity",
            "sha256": sha(licence),
            "mode": "0444",
        },
        {
            "source": "fixtures/resident-fixture/input.txt",
            "destination": "/opt/solver/tool-supply/fixture-input.txt",
            "sha256": sha(fixture_input),
            "mode": "0444",
        },
        {
            "source": "fixtures/resident-fixture/tool.py",
            "destination": "/usr/local/bin/incypher-fixture-tool",
            "sha256": sha(source),
            "mode": "0755",
        },
    ]
    component = {
        "component_id": "fixture.identity",
        "version": "1.0.0",
        "license_expression": "MIT",
        "license_classification": "free-redistributable",
        "source": {
            "uri": "repo:tool-supply/fixtures/resident-fixture/tool.py",
            "file": "fixtures/resident-fixture/tool.py",
        },
        "license": {"authority": "repository-notice", "file": "fixtures/resident-fixture/LICENSE.txt"},
        "entrypoint": "/usr/local/bin/incypher-fixture-tool",
        "version_argv": ["--version"],
        "fixture": {
            "fixture_id": "fixture.identity.transform-v1",
            "argv": ["/opt/solver/tool-supply/fixture-input.txt"],
            "input_file": "fixtures/resident-fixture/input.txt",
            "expected_stdout_sha256": sha(expected),
            "timeout_seconds": 5,
        },
        "platforms": ["amd64", "arm64"],
        "packages": [],
        "files": files,
    }
    lock = (
        json.dumps(
            {"schema_version": 1, "profile_id": "resident-fixture", "components": [component]},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    inventory_component = copy.deepcopy(component)
    inventory_component["profiles"] = ["resident-fixture"]
    inventory = (
        json.dumps({"schema_version": 1, "components": [inventory_component]}, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode()
    supply = (
        json.dumps(
            {
                "schema_version": 1,
                "receipt_type": "tool-supply-fragments",
                "fragment_digests": [{"path": "locks/resident-fixture.json", "sha256": sha(lock)}],
                "assembly_inventory_digest": sha(inventory),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
    document: dict[str, object] = {
        "schema_version": 1,
        "receipt_type": "tool-supply-receipt",
        "component_id": "fixture.identity",
        "profile_id": "resident-fixture",
        "image": {
            "platform": "linux/arm64",
            "manifest_digest": "sha256:" + "1" * 64,
            "config_digest": "sha256:" + "2" * 64,
        },
        "strict_profile_digest": STRICT_PROFILE_DIGEST,
        "isolation": {
            "profile_id": STRICT_PROFILE_ID,
            "profile_digest": STRICT_PROFILE_DIGEST,
            "image_id": "sha256:" + "1" * 64,
            "runtime_pin": STRICT_RUNTIME_PIN,
            "outer_capabilities": "empty",
            "checks": {control: "pass" for control in STRICT_CONTROLS},
            "broker_peer_uid": 20000,
            "processes_before_kill": 3,
            "processes_after_kill": 0,
            "owned_residue": [],
        },
        "materials": {
            "lock": {"sha256": sha(lock), "bytes": lock.hex()},
            "inventory": {"sha256": sha(inventory), "bytes": inventory.hex()},
            "supply_receipt": {"sha256": sha(supply), "bytes": supply.hex()},
            "closure": [
                {
                    "source": item["source"],
                    "destination": item["destination"],
                    "sha256": item["sha256"],
                    "bytes": {
                        sha(licence): licence.hex(),
                        sha(fixture_input): fixture_input.hex(),
                        sha(source): source.hex(),
                    }[item["sha256"]],
                }
                for item in files
            ],
            "source": {
                "uri": "repo:tool-supply/fixtures/resident-fixture/tool.py",
                "sha256": sha(source),
            },
            "licence": {
                "expression": "MIT",
                "authority": "repository-notice",
                "sha256": sha(licence),
            },
            "fixture": {
                "fixture_id": "fixture.identity.transform-v1",
                "input_sha256": sha(fixture_input),
                "expected_stdout_sha256": sha(expected),
            },
        },
        "sbom": {
            "component_version": "1.0.0",
            "entrypoint": "/usr/local/bin/incypher-fixture-tool",
            "entrypoint_sha256": sha(source),
            "files": [
                {
                    "destination": item["destination"],
                    "sha256": item["sha256"],
                    "size": {
                        sha(licence): len(licence),
                        sha(fixture_input): len(fixture_input),
                        sha(source): len(source),
                    }[item["sha256"]],
                }
                for item in files
            ],
            "packages": [],
        },
        "semantic_fixture": {
            "entrypoint": "/usr/local/bin/incypher-fixture-tool",
            "entrypoint_sha256": sha(source),
            "observed_version": "1.0.0",
            "exit_code": 0,
            "stdout_sha256": sha(expected),
            "stderr_sha256": sha(b""),
            "outcome": "pass",
        },
        "identity": "",
    }
    document["identity"] = "sha256:" + sha(canonical_receipt_bytes(document, without_identity=True))
    return document


def test_one_receipt_carries_supply_chain_semantics_and_manifest_link() -> None:
    document = receipt()

    validate_receipt(document, expected_image_manifest_digest="sha256:" + "1" * 64)

    assert issue_manifest_receipt(document) == {
        "ref": "receipt:tool-supply:fixture.identity",
        "kind": "tool-supply-receipt",
        "digest": document["identity"].removeprefix("sha256:"),
    }


@pytest.mark.parametrize(
    ("path", "replacement", "match"),
    [
        (("image", "manifest_digest"), "sha256:" + "9" * 64, "strict profile"),
        (("materials", "lock", "bytes"), b"changed lock".hex(), "lock digest"),
        (("semantic_fixture", "stdout_sha256"), "9" * 64, "semantic fixture output"),
        (("materials", "closure", "0", "bytes"), b"not MIT".hex(), "closure.*digest"),
    ],
)
def test_image_lock_fixture_or_licence_tampering_invalidates(
    path: tuple[str, ...], replacement: str, match: str
) -> None:
    changed = copy.deepcopy(receipt())
    target = changed
    for key in path[:-1]:
        target = target[int(key)] if isinstance(target, list) else target[key]  # type: ignore[assignment,index]
    target[path[-1]] = replacement  # type: ignore[index]

    with pytest.raises(ReceiptInvalid, match=match):
        validate_receipt(changed)


def test_presence_without_the_locked_entrypoint_semantic_result_is_rejected() -> None:
    changed = copy.deepcopy(receipt())
    changed["semantic_fixture"]["entrypoint"] = "/bin/true"  # type: ignore[index]
    changed["identity"] = "sha256:" + sha(canonical_receipt_bytes(changed, without_identity=True))

    with pytest.raises(ReceiptInvalid, match="locked entrypoint"):
        validate_receipt(changed)


def test_promotion_refuses_host_paths_and_secret_forms_then_writes_canonical_evidence(tmp_path: Path) -> None:
    document = receipt()
    leaking = copy.deepcopy(document)
    leaking["materials"]["source"]["uri"] = str(tmp_path / "checkout" / "tool.py")  # type: ignore[index]
    leaking["identity"] = "sha256:" + sha(canonical_receipt_bytes(leaking, without_identity=True))
    with pytest.raises(ReceiptInvalid, match="host path"):
        promote_receipt(leaking, tmp_path / "promoted.json", host_roots=(tmp_path,))

    leaking = copy.deepcopy(document)
    leaking["materials"]["closure"][0]["bytes"] = str(tmp_path / "encoded-in-material").encode().hex()  # type: ignore[index]
    with pytest.raises(ReceiptInvalid, match="host path"):
        promote_receipt(leaking, tmp_path / "promoted.json", host_roots=(tmp_path,))

    secret = "ctfd_" + "a1b2" * 16
    leaking = copy.deepcopy(document)
    leaking["materials"]["source"]["uri"] = secret  # type: ignore[index]
    leaking["identity"] = "sha256:" + sha(canonical_receipt_bytes(leaking, without_identity=True))
    with pytest.raises(ReceiptInvalid, match="secret"):
        promote_receipt(leaking, tmp_path / "promoted.json", secrets={"CTFD_API_TOKEN": secret})

    destination = tmp_path / "promoted.json"
    promote_receipt(document, destination, host_roots=(tmp_path,))
    assert destination.read_bytes() == canonical_receipt_bytes(document)
    validate_receipt(json.loads(destination.read_text()))


def test_changed_image_cannot_silently_replace_a_promoted_receipt(tmp_path: Path) -> None:
    destination = tmp_path / "promoted.json"
    promote_receipt(receipt(), destination)
    changed = copy.deepcopy(receipt())
    changed["image"]["manifest_digest"] = "sha256:" + "7" * 64  # type: ignore[index]
    changed["isolation"]["image_id"] = "sha256:" + "7" * 64  # type: ignore[index]
    changed["identity"] = "sha256:" + sha(canonical_receipt_bytes(changed, without_identity=True))

    with pytest.raises(ReceiptInvalid, match="different image"):
        promote_receipt(changed, destination)


def test_reidentified_profile_or_file_size_assertions_are_not_trusted() -> None:
    changed = copy.deepcopy(receipt())
    changed["strict_profile_digest"] = "9" * 64
    changed["isolation"]["profile_digest"] = "9" * 64  # type: ignore[index]
    changed["identity"] = "sha256:" + sha(canonical_receipt_bytes(changed, without_identity=True))
    with pytest.raises(ReceiptInvalid, match="strict profile"):
        validate_receipt(changed)

    changed = copy.deepcopy(receipt())
    changed["sbom"]["files"][0]["size"] += 1  # type: ignore[index,operator]
    changed["identity"] = "sha256:" + sha(canonical_receipt_bytes(changed, without_identity=True))
    with pytest.raises(ReceiptInvalid, match="sizes"):
        validate_receipt(changed)
