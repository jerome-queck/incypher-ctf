import hashlib
import json
import subprocess
import sys
from pathlib import Path


ASSEMBLER = Path(__file__).resolve().parent.parent / "scripts" / "assemble_tool_supply.py"


def write_sample_fragment(source: Path) -> None:
    fixture = source / "fixtures" / "resident-fixture" / "identity.txt"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("tool-supply-probe\n")
    tool = fixture.parent / "tool.py"
    licence = fixture.parent / "LICENSE.txt"
    fixture_input = fixture.parent / "input.txt"
    tool.write_text("#!/usr/bin/python3\nprint('1.0.0')\n")
    licence.write_text("MIT License\n")
    fixture_input.write_text("alpha beta\n")
    (source / "locks").mkdir()
    (source / "locks" / "resident.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "resident-fixture",
                "components": [
                    {
                        "component_id": "fixture.identity",
                        "capability_ids": ["fixture.identity"],
                        "version": "1.0.0",
                        "license_expression": "MIT",
                        "license_classification": "free-redistributable",
                        "source": {
                            "uri": "repo:tool-supply/fixtures/resident-fixture/tool.py",
                            "file": "fixtures/resident-fixture/tool.py",
                        },
                        "license": {
                            "authority": "repository-notice",
                            "file": "fixtures/resident-fixture/LICENSE.txt",
                        },
                        "entrypoint": "/usr/local/bin/fixture-tool",
                        "version_argv": ["--version"],
                        "fixture": {
                            "fixture_id": "fixture.identity.transform-v1",
                            "argv": ["/opt/solver/tool-supply/input.txt"],
                            "input_file": "fixtures/resident-fixture/input.txt",
                            "expected_stdout_sha256": "2067e3727fec495781f370c84a7d82111db1cc77c3b52ccfdcb4d2d71d02e650",
                            "timeout_seconds": 5,
                        },
                        "platforms": ["amd64", "arm64"],
                        "packages": [],
                        "files": [
                            {
                                "source": "fixtures/resident-fixture/identity.txt",
                                "destination": "/opt/solver/tool-supply/identity.txt",
                                "sha256": "b687f188329f78bf0347339c661112c65a2540ed682b322e26f7f36c3c69e15d",
                                "mode": "0644",
                            },
                            {
                                "source": "fixtures/resident-fixture/tool.py",
                                "destination": "/usr/local/bin/fixture-tool",
                                "sha256": hashlib.sha256(tool.read_bytes()).hexdigest(),
                                "mode": "0755",
                            },
                            {
                                "source": "fixtures/resident-fixture/LICENSE.txt",
                                "destination": "/opt/solver/tool-supply/LICENSE.txt",
                                "sha256": hashlib.sha256(licence.read_bytes()).hexdigest(),
                                "mode": "0444",
                            },
                            {
                                "source": "fixtures/resident-fixture/input.txt",
                                "destination": "/opt/solver/tool-supply/input.txt",
                                "sha256": hashlib.sha256(fixture_input.read_bytes()).hexdigest(),
                                "mode": "0444",
                            },
                        ],
                    }
                ],
            }
        )
        + "\n"
    )


def assemble(source: Path, output: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ASSEMBLER), "--source", str(source), "--output", str(output)],
        capture_output=True,
        text=True,
    )


def rehome_fragment(source: Path, fragment: dict[str, object], profile_id: str) -> None:
    original = source / "fixtures" / "resident-fixture"
    target = source / "fixtures" / profile_id
    target.mkdir(parents=True)
    for path in original.iterdir():
        (target / path.name).write_bytes(path.read_bytes())
    fragment["profile_id"] = profile_id
    component = fragment["components"][0]
    component["source"]["uri"] = f"repo:tool-supply/fixtures/{profile_id}/tool.py"
    for declared in component["files"]:
        declared["source"] = declared["source"].replace("resident-fixture", profile_id)
    component["source"]["file"] = component["source"]["file"].replace("resident-fixture", profile_id)
    component["license"]["file"] = component["license"]["file"].replace("resident-fixture", profile_id)
    component["fixture"]["input_file"] = component["fixture"]["input_file"].replace("resident-fixture", profile_id)


def test_two_clean_assemblies_from_one_lock_have_one_ordered_inventory(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)

    first = tmp_path / "first"
    second = tmp_path / "second"
    assert assemble(source, first).returncode == 0
    assert assemble(source, second).returncode == 0

    assert (first / "inventory.json").read_bytes() == (second / "inventory.json").read_bytes()
    inventory = json.loads((first / "inventory.json").read_text())
    assert [component["component_id"] for component in inventory["components"]] == ["fixture.identity"]
    assert inventory["components"][0]["profiles"] == ["resident-fixture"]
    assert inventory["components"][0]["entrypoint"] == "/usr/local/bin/fixture-tool"
    assert inventory["components"][0]["source"]["file"] == "fixtures/resident-fixture/tool.py"
    assert inventory["components"][0]["license"]["authority"] == "repository-notice"
    assert inventory["components"][0]["fixture"]["fixture_id"] == "fixture.identity.transform-v1"
    assert (first / "apt-packages.txt").read_text() == ""
    assert (first / "rootfs" / "opt" / "solver" / "tool-supply" / "identity.txt").read_text() == ("tool-supply-probe\n")

    receipt = json.loads((first / "receipt.json").read_text())
    assert receipt["receipt_type"] == "tool-supply-fragments"
    assert receipt["reproducibility"] == {
        "assemblies": 2,
        "comparison": "identical",
        "first_inventory_digest": receipt["assembly_inventory_digest"],
        "second_inventory_digest": receipt["assembly_inventory_digest"],
    }


def test_a_floating_component_version_fails_before_replacing_the_last_assembly(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    output = tmp_path / "assembled"
    assert assemble(source, output).returncode == 0
    previous_inventory = (output / "inventory.json").read_bytes()

    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["components"][0]["version"] = "latest"
    lock_path.write_text(json.dumps(lock) + "\n")

    result = assemble(source, output)
    assert result.returncode != 0
    assert "floating component version: fixture.identity=latest" in result.stderr
    assert (output / "inventory.json").read_bytes() == previous_inventory


def test_component_version_ranges_fail_before_assembly(tmp_path: Path):
    for index, range_version in enumerate(("^1.2.3", "~1.2", ">=1", "1.2.x", "1.X")):
        source = tmp_path / str(index) / "tool-supply"
        write_sample_fragment(source)
        lock_path = source / "locks" / "resident.json"
        lock = json.loads(lock_path.read_text())
        lock["components"][0]["version"] = range_version
        lock_path.write_text(json.dumps(lock) + "\n")

        result = assemble(source, tmp_path / str(index) / "assembled")

        assert result.returncode != 0
        assert f"floating component version: fixture.identity={range_version}" in result.stderr


def test_duplicate_component_ids_across_profile_locks_fail_before_assembly(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    duplicate = json.loads((source / "locks" / "resident.json").read_text())
    rehome_fragment(source, duplicate, "second-profile")
    (source / "locks" / "second.json").write_text(json.dumps(duplicate) + "\n")

    output = tmp_path / "assembled"
    result = assemble(source, output)

    assert result.returncode != 0
    assert "duplicate component_id: fixture.identity" in result.stderr
    assert not output.exists()


def test_unsupported_licence_classification_fails_before_assembly(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["components"][0]["license_classification"] = "proprietary"
    lock_path.write_text(json.dumps(lock) + "\n")

    output = tmp_path / "assembled"
    result = assemble(source, output)

    assert result.returncode != 0
    assert "unsupported licence classification: fixture.identity=proprietary" in result.stderr
    assert not output.exists()


def test_a_floating_package_version_fails_before_assembly(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["components"][0]["packages"] = [{"name": "fixture-package", "version": "*"}]
    lock_path.write_text(json.dumps(lock) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "floating package version: fixture.identity/fixture-package=*" in result.stderr


def test_architecture_local_binary_revisions_share_one_exact_source_lock(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["components"][0]["packages"] = [{"name": "fixture-package", "version": "1.0-1+b2"}]
    lock_path.write_text(json.dumps(lock) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "assembled" / "apt-packages.txt").read_text() == "fixture-package=1.0-1*\n"


def test_an_unknown_lock_schema_fails_before_assembly(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["schema_version"] = 2
    lock_path.write_text(json.dumps(lock) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "unsupported lock schema_version: locks/resident.json=2" in result.stderr


def test_an_empty_lock_set_cannot_replace_a_valid_image_recipe(tmp_path: Path):
    source = tmp_path / "tool-supply"
    (source / "locks").mkdir(parents=True)

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "no Tool-supply locks found" in result.stderr


def test_two_components_cannot_claim_one_image_destination(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    second = json.loads((source / "locks" / "resident.json").read_text())
    rehome_fragment(source, second, "second-profile")
    second["components"][0]["component_id"] = "fixture.second"
    (source / "locks" / "second.json").write_text(json.dumps(second) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "duplicate image destination: /opt/solver/tool-supply/" in result.stderr


def test_a_lock_cannot_copy_a_file_from_outside_its_source_tree(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    outside = tmp_path / "outside.txt"
    outside.write_text("tool-supply-probe\n")
    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["components"][0]["files"][0]["source"] = "../outside.txt"
    lock_path.write_text(json.dumps(lock) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "source escapes Tool-supply tree: ../outside.txt" in result.stderr


def test_conflicting_versions_of_one_package_fail_before_assembly(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    first_path = source / "locks" / "resident.json"
    first = json.loads(first_path.read_text())
    first["components"][0]["packages"] = [{"name": "fixture-package", "version": "1.0-1"}]
    first_path.write_text(json.dumps(first) + "\n")
    second = json.loads(json.dumps(first))
    rehome_fragment(source, second, "second-profile")
    second["components"][0]["component_id"] = "fixture.second"
    second["components"][0]["packages"][0]["version"] = "2.0-1"
    second["components"][0]["files"][0]["destination"] = "/opt/solver/tool-supply/second.txt"
    (source / "locks" / "second.json").write_text(json.dumps(second) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "conflicting package versions: fixture-package=1.0-1,2.0-1" in result.stderr


def test_a_package_name_cannot_become_an_installer_option(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["components"][0]["packages"] = [{"name": "--allow-unauthenticated", "version": "1.0"}]
    lock_path.write_text(json.dumps(lock) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "invalid package name: --allow-unauthenticated" in result.stderr


def test_package_and_file_inventory_order_is_canonical(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    (source / "fixtures" / "resident-fixture" / "second.txt").write_text("tool-supply-probe\n")
    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["components"][0]["packages"] = [
        {"name": "zeta-package", "version": "1.0"},
        {"name": "alpha-package", "version": "1.0"},
    ]
    lock["components"][0]["files"].insert(
        0,
        {
            "source": "fixtures/resident-fixture/second.txt",
            "destination": "/opt/solver/tool-supply/zeta.txt",
            "sha256": "b687f188329f78bf0347339c661112c65a2540ed682b322e26f7f36c3c69e15d",
            "mode": "0644",
        },
    )
    lock_path.write_text(json.dumps(lock) + "\n")

    output = tmp_path / "assembled"
    assert assemble(source, output).returncode == 0
    component = json.loads((output / "inventory.json").read_text())["components"][0]

    assert [package["name"] for package in component["packages"]] == ["alpha-package", "zeta-package"]
    assert [declared["destination"] for declared in component["files"]] == sorted(
        [
            "/opt/solver/tool-supply/LICENSE.txt",
            "/opt/solver/tool-supply/identity.txt",
            "/opt/solver/tool-supply/input.txt",
            "/opt/solver/tool-supply/zeta.txt",
            "/usr/local/bin/fixture-tool",
        ]
    )


def test_the_output_cannot_replace_the_source_or_its_locks(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)

    replacing = assemble(source, source)
    replacing_locks = assemble(source, source / "locks")

    assert replacing.returncode != 0
    assert replacing_locks.returncode != 0
    assert "output overlaps Tool-supply inputs" in replacing.stderr
    assert "output overlaps Tool-supply inputs" in replacing_locks.stderr
    assert (source / "locks" / "resident.json").is_file()


def test_each_profile_id_owns_exactly_one_lock_fragment(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    second = json.loads((source / "locks" / "resident.json").read_text())
    second["components"][0]["component_id"] = "fixture.second"
    second["components"][0]["files"][0]["destination"] = "/opt/solver/tool-supply/second.txt"
    (source / "locks" / "second.json").write_text(json.dumps(second) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "duplicate profile_id: resident-fixture" in result.stderr


def test_a_profile_can_copy_only_its_own_fixture_tree(tmp_path: Path):
    source = tmp_path / "tool-supply"
    write_sample_fragment(source)
    other_fixture = source / "fixtures" / "other-profile" / "identity.txt"
    other_fixture.parent.mkdir(parents=True)
    other_fixture.write_text("tool-supply-probe\n")
    lock_path = source / "locks" / "resident.json"
    lock = json.loads(lock_path.read_text())
    lock["components"][0]["files"][0]["source"] = "fixtures/other-profile/identity.txt"
    lock_path.write_text(json.dumps(lock) + "\n")

    result = assemble(source, tmp_path / "assembled")

    assert result.returncode != 0
    assert "fixture is not owned by profile resident-fixture" in result.stderr
