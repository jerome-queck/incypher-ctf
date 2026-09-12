import json
import subprocess
import sys
from pathlib import Path

from solver.tool_supply_receipt import issue_manifest_receipt, validate_receipt


REPO_ROOT = Path(__file__).resolve().parent.parent
ASSEMBLER = REPO_ROOT / "scripts" / "assemble_tool_supply.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"

RESIDENT_CAPABILITY_IDS = {
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


def test_the_repository_fragment_assembles_once_and_is_copied_into_the_image(tmp_path: Path):
    output = tmp_path / "generated"
    result = subprocess.run(
        [
            sys.executable,
            str(ASSEMBLER),
            "--source",
            str(REPO_ROOT / "tool-supply"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    inventory = json.loads((output / "inventory.json").read_text())
    components = inventory["components"]
    assert [component["component_id"] for component in components].count("fixture.identity") == 1
    assert (output / "rootfs" / "opt" / "solver" / "tool-supply" / "identity.txt").read_bytes() == (
        b"tool-supply-probe\n"
    )
    assert "7zip=26.02+dfsg-2\n" in (output / "apt-packages.txt").read_text()
    assert (output / "receipt.json").is_file()

    dockerfile = DOCKERFILE.read_text()
    copy_lines = [line.strip() for line in dockerfile.splitlines() if line.strip().startswith("COPY ")]
    assert copy_lines.count("COPY tool-supply/generated/rootfs/ /") == 1
    assert copy_lines.count("COPY tool-supply/generated/apt-packages.txt /tmp/tool-supply-apt-packages.txt") == 1
    assert (
        copy_lines.count(
            "COPY tool-supply/generated/inventory.json tool-supply/generated/receipt.json /opt/solver/tool-supply/"
        )
        == 1
    )
    assert copy_lines.count("COPY scripts/apply_tool_supply_modes.py scripts/apply_tool_supply_modes.py") == 1
    install_position = dockerfile.index("xargs apt-get install --yes --no-install-recommends")
    assert install_position < dockerfile.index("COPY tool-supply/generated/rootfs/ /")
    assert install_position < dockerfile.index(
        "COPY tool-supply/generated/inventory.json tool-supply/generated/receipt.json"
    )
    assert dockerfile.index("COPY tool-supply/generated/rootfs/ /") < dockerfile.index(
        "python3 -m scripts.apply_tool_supply_modes"
    )

    committed = REPO_ROOT / "tool-supply" / "generated"
    assembled_files = {
        path.relative_to(output): (path.read_bytes(), bool(path.stat().st_mode & 0o111))
        for path in output.rglob("*")
        if path.is_file()
    }
    committed_files = {
        path.relative_to(committed): (path.read_bytes(), bool(path.stat().st_mode & 0o111))
        for path in committed.rglob("*")
        if path.is_file()
    }
    assert assembled_files == committed_files


def test_resident_floor_declares_its_public_capabilities_and_locked_packages(tmp_path: Path) -> None:
    output = tmp_path / "generated"
    result = subprocess.run(
        [sys.executable, str(ASSEMBLER), "--source", str(REPO_ROOT / "tool-supply"), "--output", str(output)],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    inventory = json.loads((output / "inventory.json").read_text())
    resident = [item for item in inventory["components"] if "resident" in item["profiles"]]
    assert {capability for item in resident for capability in item["capability_ids"]} == RESIDENT_CAPABILITY_IDS
    assert all(item["packages"] for item in resident)
    assert all(package["version"] not in {"*", "latest"} for item in resident for package in item["packages"])


def test_the_promoted_sample_receipt_is_self_contained_and_manifest_addressable() -> None:
    path = REPO_ROOT / "tool-supply" / "receipts" / "fixture.identity.json"
    receipt = json.loads(path.read_text())

    validate_receipt(receipt, expected_image_manifest_digest=receipt["image"]["manifest_digest"])

    assert set(receipt["materials"]) == {
        "lock",
        "inventory",
        "supply_receipt",
        "closure",
        "source",
        "licence",
        "fixture",
    }
    assert issue_manifest_receipt(receipt)["ref"] == "receipt:tool-supply:fixture.identity"
    assert receipt["component_admission"]["outcome"] == "pass"
    assert receipt["handle_solve"] is None
    assert "/Users/" not in path.read_text()
