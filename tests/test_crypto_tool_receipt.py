import copy
import hashlib
import json
from pathlib import Path

import pytest

from solver.crypto_tool_contract import PROFILE_SIZE_BUDGET
from solver.crypto_tool_receipt import create_receipt, link_manifest, verify_receipt
from solver.manifest import generate_manifest
from solver.tool_control import profile_components
from test_manifest import release_candidate_profile


ROOT = Path(__file__).resolve().parent.parent
SUPPLY = ROOT / "tool-supply"


def test_crypto_profile_declares_bounded_public_handles() -> None:
    components = profile_components(SUPPLY / "generated" / "inventory.json", "tool-crypto")

    assert {item.capability_id for item in components} == {
        "crypto.asymmetric",
        "crypto.cas",
        "crypto.classical",
        "crypto.encoding",
        "crypto.lattice",
        "crypto.number-theory",
        "crypto.hash-crack",
        "crypto.symmetric-hash",
        "crypto.certificate",
    }
    assert all(item.require_network_denied and item.exact_resource_request for item in components)


def test_crypto_receipt_binds_supply_solve_isolation_and_tamper_matrix() -> None:
    inventory = (SUPPLY / "generated" / "inventory.json").read_bytes()
    source = json.loads((SUPPLY / "receipts" / "tool-crypto.core.json").read_text())
    candidate = source["image"]["manifest_digest"]
    size = {
        "baseline_image_digest": "sha256:" + "1" * 64,
        "baseline_size_bytes": 1_000,
        "baseline_source_commit": "1" * 40,
        "baseline_source_tree_digest": "2" * 40,
        "baseline_dockerfile_sha256": "3" * 64,
        "baseline_platform": "linux/arm64",
        "candidate_image_digest": candidate,
        "candidate_size_bytes": 2_000,
        "delta_bytes": 1_000,
        "budget_bytes": PROFILE_SIZE_BUDGET,
        "method": "docker-build-git-merge-base-history-unpacked-layer-sum-v1",
    }
    receipt = create_receipt(source, inventory, size)
    verify_receipt(receipt, inventory)

    assert receipt["profile_id"] == "tool-crypto"
    assert receipt["catalogue_digest"] == hashlib.sha256(inventory).hexdigest()
    assert receipt["size_bytes"] > 0
    assert receipt["profile_size"]["delta_bytes"] <= PROFILE_SIZE_BUDGET
    assert receipt["outcomes"] == {"supply": "pass", "solve": "pass", "isolation": "pass"}
    assert set(source["isolation"]["checks"]) >= {
        "control",
        "credential",
        "sibling",
        "filesystem",
        "network",
        "resource",
        "host-mount",
    }
    manifest = generate_manifest(
        image_digest=receipt["image_digest"], release_candidate_profile=release_candidate_profile()
    )
    linked = link_manifest(manifest, receipt, inventory)
    row = next(item for item in linked["requirements"] if item["row_id"] == "core.tool-surface")
    assert row["receipt_ref"] == "receipt:tool-crypto"
    assert row["status"] == "planned"

    for field in ("catalogue_digest", "image_digest", "supply_receipt_digest"):
        changed = copy.deepcopy(receipt)
        changed[field] = "0" * 64
        with pytest.raises(ValueError):
            verify_receipt(changed, inventory)

    changed = copy.deepcopy(receipt)
    changed["source_receipt"]["handle_solve"]["capabilities"][0]["output"]["bytes"] = "00"
    with pytest.raises(ValueError):
        verify_receipt(changed, inventory)

    too_large = copy.deepcopy(size)
    too_large["candidate_size_bytes"] = PROFILE_SIZE_BUDGET + 1_001
    too_large["delta_bytes"] = PROFILE_SIZE_BUDGET + 1
    with pytest.raises(ValueError, match="exceeds ADR-0057"):
        create_receipt(source, inventory, too_large)
