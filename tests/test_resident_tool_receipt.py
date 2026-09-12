import hashlib
import copy
import json
from pathlib import Path

import pytest

from solver.manifest import generate_manifest
from solver.resident_tool_receipt import create_receipt, link_manifest, verify_receipt, write_receipt
from solver.tool_supply_receipt import validate_receipt as validate_supply_receipt
from test_manifest import release_candidate_profile


ROOT = Path(__file__).resolve().parent.parent
SUPPLY = ROOT / "tool-supply"


def resident_receipts() -> list[dict[str, object]]:
    inventory = json.loads((SUPPLY / "generated" / "inventory.json").read_text())
    return [
        json.loads((SUPPLY / "receipts" / f"{component['component_id']}.json").read_text())
        for component in inventory["components"]
        if "resident" in component["profiles"]
    ]


def test_resident_receipt_binds_the_exact_catalogue_image_and_all_green_proofs() -> None:
    inventory = (SUPPLY / "generated" / "inventory.json").read_bytes()
    receipt = create_receipt(resident_receipts(), inventory)

    verify_receipt(receipt, inventory)
    assert receipt["catalogue_digest"] == hashlib.sha256(inventory).hexdigest()
    assert set(receipt["capability_ids"]) == {
        "archive.extract",
        "crypto.primitive",
        "data.sqlite",
        "document.pdf",
        "firmware.rootfs",
        "image.inspect",
        "math.symbolic",
        "network.http",
        "network.tcp",
        "process.inspect",
        "recognition.barcode",
        "recognition.ocr",
        "recon.bytes",
        "recon.mime",
        "recon.repository",
        "solver.smt",
    }
    assert all(proof["solve"] == "pass" and proof["isolation"] == "pass" for proof in receipt["proofs"])

    manifest = generate_manifest(
        image_digest=receipt["image_digest"],
        release_candidate_profile=release_candidate_profile(),
    )
    linked = link_manifest(manifest, receipt, inventory)
    row = next(item for item in linked["requirements"] if item["row_id"] == "core.tool-surface")
    assert row["receipt_ref"] == "receipt:tool-resident"


def test_resident_receipt_rejects_one_proof_from_another_catalogue() -> None:
    inventory = (SUPPLY / "generated" / "inventory.json").read_bytes()
    receipts = resident_receipts()
    receipts[0]["materials"]["inventory"]["sha256"] = "0" * 64

    with pytest.raises(ValueError):
        create_receipt(receipts, inventory)


@pytest.mark.parametrize(
    ("component_id", "capability_id", "field", "replacement", "match"),
    [
        ("resident.recon", "recon.mime", "policy_sha256", "0" * 64, "invocation policy"),
        ("resident.recon", "recon.mime", "input_after", "0" * 64, "input confinement"),
        ("resident.recon", "recon.mime", "output", "00", "semantic output"),
        ("resident.network-data", "network.http", "protocol", "tcp", "broker provenance"),
    ],
)
def test_promoted_handle_solve_rejects_policy_input_output_or_broker_tampering(
    component_id: str, capability_id: str, field: str, replacement: str, match: str
) -> None:
    receipt = copy.deepcopy(next(item for item in resident_receipts() if item["component_id"] == component_id))
    proof = next(item for item in receipt["handle_solve"]["capabilities"] if item["capability_id"] == capability_id)
    if field == "input_after":
        proof["input_snapshot"]["after_sha256"] = replacement
    elif field == "output":
        proof["output"]["bytes"] = replacement
    elif field == "protocol":
        proof["target"]["classified"]["protocol"] = replacement
    else:
        proof[field] = replacement

    with pytest.raises(ValueError, match=match):
        validate_supply_receipt(receipt)


def test_resident_receipt_rejects_duplicate_or_empty_isolation_proofs() -> None:
    inventory = (SUPPLY / "generated" / "inventory.json").read_bytes()
    receipts = resident_receipts()
    receipts[-1] = receipts[0]
    with pytest.raises(ValueError, match="duplicate|incomplete"):
        create_receipt(receipts, inventory)

    receipts = resident_receipts()
    receipts[0]["isolation"]["checks"] = {}
    with pytest.raises(ValueError, match="isolation"):
        create_receipt(receipts, inventory)


def test_repository_aggregate_rebuilds_from_promoted_proofs(tmp_path: Path) -> None:
    supply = tmp_path / "tool-supply"
    (supply / "generated").mkdir(parents=True)
    (supply / "receipts").mkdir()
    (supply / "generated" / "inventory.json").write_bytes((SUPPLY / "generated" / "inventory.json").read_bytes())
    for receipt in resident_receipts():
        component_id = receipt["component_id"]
        (supply / "receipts" / f"{component_id}.json").write_bytes(
            (SUPPLY / "receipts" / f"{component_id}.json").read_bytes()
        )

    path = write_receipt(supply)

    verify_receipt(json.loads(path.read_text()), (supply / "generated" / "inventory.json").read_bytes())
