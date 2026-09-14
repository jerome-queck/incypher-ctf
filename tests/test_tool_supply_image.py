import copy
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from solver.tool_supply_receipt import issue_manifest_receipt, validate_receipt


REPO_ROOT = Path(__file__).resolve().parent.parent
ASSEMBLER = REPO_ROOT / "scripts" / "assemble_tool_supply.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

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

REQUIRED_302_CAPABILITIES = {
    "http.session",
    "tcp.session",
    "web.discovery",
    "web.browser",
    "osint.dns",
    "osint.identity",
    "osint.email",
    "osint.domain",
    "osint.geo",
    "misc.transform",
    "misc.emulate",
    "protocol.relay",
    "jail.reason",
}


def test_osint_adapter_requires_one_recorded_and_one_live_query(tmp_path: Path) -> None:
    adapter_path = REPO_ROOT / "tool-supply" / "fixtures" / "osint" / "osint.py"
    spec = importlib.util.spec_from_file_location("osint_fixture_adapter", adapter_path)
    assert spec is not None and spec.loader is not None
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    path = tmp_path / "domain.json"
    path.write_text(
        json.dumps(
            {
                "queries": [
                    {
                        "kind": "domain",
                        "source_id": "rdap",
                        "subject": "example.test",
                        "body": "e30=",
                        "content_type": "application/json",
                    },
                    {
                        "kind": "domain",
                        "source_id": "rdap",
                        "subject": "example.test",
                        "body": "",
                        "content_type": "",
                    },
                ]
            }
        )
    )

    queries = adapter._queries(path, "osint.domain")

    assert len(queries) == 2
    assert bool(queries[0]["body"])
    assert queries[1]["body"] == ""


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
    capabilities = json.loads((output / "capabilities.json").read_text())
    required = {row["capability_id"]: row for row in capabilities["capabilities"] if row["requirement"] == "required"}
    assert REQUIRED_302_CAPABILITIES <= set(required)
    assert all(required[capability]["installed"] for capability in REQUIRED_302_CAPABILITIES)
    assert all(required[capability]["proved"] for capability in REQUIRED_302_CAPABILITIES)
    assert all(required[capability]["enabled"] for capability in REQUIRED_302_CAPABILITIES)
    assert {required[capability]["authority"] for capability in REQUIRED_302_CAPABILITIES} == {
        "tool-handle",
        "research-handle",
        "target-handle",
    }
    triggered = {row["capability_id"]: row for row in capabilities["capabilities"] if row["requirement"] == "triggered"}
    assert {
        "web.hypothesis-tools.arjun",
        "web.hypothesis-tools.jwt-tool",
        "web.hypothesis-tools.sstimap",
        "web.hypothesis-tools.sqlmap",
        "web.hypothesis-tools.dalfox",
        "web.hypothesis-tools.nuclei",
        "web.hypothesis-tools.request-smuggling",
        "web.hypothesis-tools.phpggc",
        "web.hypothesis-tools.ysoserial",
        "network.service-fingerprint",
        "web.oast",
        "jail.pyjailbreaker",
        "blockchain.toolchain",
        "cloud.provider",
        "game.extract",
        "language.extra",
    } <= set(triggered)
    assert all(row["reason"] and row["promotion_rule"] for row in triggered.values())
    assert all(not row["enabled"] for row in triggered.values())

    dockerfile = DOCKERFILE.read_text()
    copy_lines = [line.strip() for line in dockerfile.splitlines() if line.strip().startswith("COPY ")]
    assert copy_lines.count("COPY tool-supply/generated/rootfs/ /") == 1
    assert copy_lines.count("COPY tool-supply/generated/apt-packages.txt /tmp/tool-supply-apt-packages.txt") == 1
    assert (
        copy_lines.count(
            "COPY tool-supply/generated/inventory.json tool-supply/generated/receipt.json tool-supply/generated/capabilities.json /opt/solver/tool-supply/"
        )
        == 1
    )
    assert copy_lines.count("COPY scripts/apply_tool_supply_modes.py scripts/apply_tool_supply_modes.py") == 1
    install_position = dockerfile.index("xargs apt-get install --yes --no-install-recommends")
    assert install_position < dockerfile.index("COPY tool-supply/generated/rootfs/ /")
    assert install_position < dockerfile.index(
        "COPY tool-supply/generated/inventory.json tool-supply/generated/receipt.json tool-supply/generated/capabilities.json"
    )
    assert dockerfile.index("COPY tool-supply/generated/rootfs/ /") < dockerfile.index(
        "python3 -m scripts.apply_tool_supply_modes"
    )
    assert "pip install --ignore-installed --no-cache-dir --require-hashes --no-deps" in dockerfile
    assert "!tool-supply/generated/capabilities.json" in DOCKERIGNORE.read_text().splitlines()

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


def test_web_probe_checks_the_package_and_the_runtime_banner() -> None:
    probe = (REPO_ROOT / "tool-supply" / "fixtures" / "web" / "tool.py").read_text()
    browser_driver = (REPO_ROOT / "tool-supply" / "fixtures" / "web" / "browser_driver.py").read_text()

    assert "dpkg-query" in probe
    assert "2.2.1-1" in probe
    assert "ffuf version: 2.1.0-dev" in probe
    assert "chromium.launch" in probe
    assert 'TemporaryDirectory(prefix="target-browser-driver-")' in browser_driver


def test_misc_supply_drops_generic_smb_and_proves_one_multi_language_jail_fixture() -> None:
    lock = json.loads((REPO_ROOT / "tool-supply" / "locks" / "misc-protocols.json").read_text())
    component = lock["components"][0]
    fixture = component["fixture"]
    capabilities = json.loads((REPO_ROOT / "tool-supply" / "capabilities.json").read_text())
    smb = next(row for row in capabilities["capabilities"] if row["capability_id"] == "protocol.smb")

    assert "protocol.smb" not in component["capability_ids"]
    assert "protocol.smb" not in component["capability_policies"]
    assert "protocol.smb" not in fixture["capability_argv"]
    assert "protocol.smb" not in fixture["capability_input_files"]
    assert "protocol.smb" not in fixture["capability_expected_facts"]
    assert "protocol.smb" not in fixture["capability_stdout_sha256"]
    assert not any(package["name"] == "smbclient" for package in component["packages"])
    assert smb["requirement"] == "triggered"
    assert smb["component_ids"] == []
    assert not smb["installed"] and not smb["proved"] and not smb["enabled"]
    assert "generic TCP" in smb["reason"]
    assert smb["promotion_rule"]

    jail_sources = set(fixture["capability_input_files"]["jail.reason"])
    assert jail_sources == {
        "fixtures/misc-protocols/jail-javascript.json",
        "fixtures/misc-protocols/jail-python.json",
        "fixtures/misc-protocols/jail-dash.json",
    }
    assert fixture["capability_argv"]["jail.reason"] == ["/opt/solver/tool-supply/misc-protocols/jail"]
    assert {
        "language=javascript",
        "language=python",
        "language=dash",
        'stdout="42"',
    } <= set(fixture["capability_expected_facts"]["jail.reason"])


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


def test_required_profile_receipts_bind_typed_broker_provenance_and_cumulative_bounds() -> None:
    receipts = {
        name: json.loads((REPO_ROOT / "tool-supply" / "receipts" / f"{name}.json").read_text())
        for name in ("web.core", "osint.core", "misc-protocols.core")
    }
    for receipt in receipts.values():
        validate_receipt(receipt, expected_image_manifest_digest=receipt["image"]["manifest_digest"])

    osint = receipts["osint.core"]
    assert all(
        {observation["record"]["origin"] for observation in proof["research"]} == {"recorded", "live"}
        for proof in osint["handle_solve"]["capabilities"]
    )
    web = next(
        proof
        for proof in receipts["web.core"]["handle_solve"]["capabilities"]
        if proof["capability_id"] == "web.discovery"
    )
    expected_routes = (REPO_ROOT / "tool-supply" / "fixtures" / "web" / "routes.txt").read_text().splitlines()
    assert len(web["target"]) == len(expected_routes)
    assert all(exchange["classified"]["operation"] == "http-session" for exchange in web["target"])

    changed = copy.deepcopy(receipts["web.core"])
    discovery = next(
        proof for proof in changed["handle_solve"]["capabilities"] if proof["capability_id"] == "web.discovery"
    )
    discovery["target"][0]["classified"]["request_bytes"] = 10**12
    with pytest.raises(ValueError, match="exchange bound"):
        validate_receipt(changed)

    changed = copy.deepcopy(osint)
    changed["handle_solve"]["capabilities"][0]["research"][0]["record"]["blob_bytes"] += 1
    with pytest.raises(ValueError, match="Research provenance"):
        validate_receipt(changed)


def test_amd64_image_job_runs_every_required_profile_semantic_probe() -> None:
    workflow = CI_WORKFLOW.read_text()

    assert "--runtime-host native-docker" in workflow
    for component_id in ("web.core", "osint.core", "misc-protocols.core"):
        assert component_id in workflow
