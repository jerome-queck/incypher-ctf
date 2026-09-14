import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_tool_adapters_are_invoked_through_their_declared_interpreters() -> None:
    adapters = (
        "osint/osint.py",
        "osint/osint.sh",
        "web/browser_driver.py",
        "web/tool.py",
        "web/tool.sh",
    )

    for adapter in adapters:
        source = (REPO_ROOT / "tool-supply" / "fixtures" / adapter).read_bytes()
        assert not source.startswith(b"#!"), adapter


def test_osint_self_check_names_bounded_functional_fixture_probes() -> None:
    source = (REPO_ROOT / "tool-supply" / "fixtures" / "osint" / "osint.py").read_text()

    assert "osint-functional-fixture-v1" in source
    assert "fixture-user" in source
    assert "fixture@example.test" in source
    assert "fixture.example" in source
    assert "sherlock_module.sherlock(" in source
    assert "gravatar(" in source
    assert "SearchCrtsh" in source
    assert "Point(" in source
    self_check = source[source.index("def self_check") : source.index("def main")]
    assert "--version" not in self_check
    assert "--help" not in self_check


def test_misc_lock_maps_shared_interpreters_and_declares_runtime_fixture() -> None:
    lock = json.loads((REPO_ROOT / "tool-supply" / "locks" / "misc-protocols.json").read_text())
    component = lock["components"][0]
    packages = {(item["name"], item["version"]) for item in component["packages"]}

    assert ("python3", "3.14.6-1") in packages
    assert ("dash", "0.5.12-12+b1") in packages
    assert any(item["source"] == "fixtures/misc-protocols/runtime.json" for item in component["files"])

    runtime = (REPO_ROOT / "tool-supply" / "fixtures" / "misc-protocols" / "runtime.json").read_text()
    assert '"/usr/bin/python3"' in runtime
    assert '"/bin/dash"' in runtime
    assert "shared-python3" in runtime
    assert "shared-dash" in runtime


def test_shared_interpreter_packages_are_deduplicated_in_the_assembled_closure() -> None:
    package_lines = (REPO_ROOT / "tool-supply" / "generated" / "apt-packages.txt").read_text().splitlines()
    assert package_lines.count("dash=0.5.12-12*") == 1
    assert package_lines.count("python3=3.14.6-1") == 1
