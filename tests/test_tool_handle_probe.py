"""Built-image Tool fixture qualification through its public handle."""

import hashlib
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from solver.isolation import (
    STRICT_CONTROLS,
    STRICT_PROFILE_DIGEST,
    STRICT_PROFILE_ID,
    STRICT_RUNTIME_PIN,
    IsolationReceipt,
)
from solver.tool_handle_probe import _fixture_protocol, qualify
from solver.target_broker_contracts import TargetProtocol
from solver.attempt_executor_contracts import RuntimeObservation
from test_attempt_executor import IMAGE_ID, ImmediateRuntime


def admitted() -> IsolationReceipt:
    return IsolationReceipt(
        profile_id=STRICT_PROFILE_ID,
        profile_digest=STRICT_PROFILE_DIGEST,
        image_id=IMAGE_ID,
        runtime_pin=tuple(STRICT_RUNTIME_PIN.items()),
        outer_capabilities="empty",
        checks=tuple((control, "pass") for control in STRICT_CONTROLS),
        broker_peer_uid=20000,
        processes_before_kill=4,
        processes_after_kill=0,
        owned_residue=(),
    )


def test_web_capability_fixtures_receive_an_http_target() -> None:
    assert _fixture_protocol("web.discovery") is TargetProtocol.HTTP
    assert _fixture_protocol("web.browser") is TargetProtocol.HTTP
    assert _fixture_protocol("network.http") is TargetProtocol.HTTP
    assert _fixture_protocol("protocol.relay") is TargetProtocol.TCP


def test_jail_reason_adapter_executes_all_languages_from_one_directory(tmp_path: Path, monkeypatch) -> None:
    source = Path(__file__).resolve().parent.parent / "tool-supply" / "fixtures" / "misc-protocols" / "tool.py"
    inputs = tmp_path / "jail"
    inputs.mkdir()
    for language in ("javascript", "python", "dash"):
        fixture = source.parent / f"jail-{language}.json"
        (inputs / fixture.name).write_bytes(fixture.read_bytes())

    node = shutil.which("node")
    if node is None:
        pytest.skip("host Node runtime unavailable; the image uses /usr/bin/node")
    spec = importlib.util.spec_from_file_location("misc_protocols_tool", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_run = subprocess.run

    def run(arguments, *args, **kwargs):
        if arguments and arguments[0] == "/usr/bin/node":
            arguments = [node, *arguments[1:]]
            kwargs["timeout"] = 10
        return real_run(arguments, *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", run)

    output = module.jail(str(inputs))
    assert "schema=misc-protocols.jail.v1" in output
    assert "engine=bounded-jail-playbook@1.0.0" in output
    for language in ("javascript", "python", "dash"):
        assert f"language={language}" in output
    assert output.count('stdout="42"') == 3


def test_jail_reason_adapter_keeps_single_language_file_invocation(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parent.parent / "tool-supply" / "fixtures" / "misc-protocols" / "tool.py"
    fixture = tmp_path / "jail-python.json"
    fixture.write_bytes((source.parent / "jail-python.json").read_bytes())

    spec = importlib.util.spec_from_file_location("misc_protocols_tool_single", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    output = module.jail(str(fixture))
    assert "language=python" in output
    assert "language=javascript" not in output
    assert "language=dash" not in output


def test_misc_self_check_uses_the_installed_jail_fixture_directory(tmp_path: Path, monkeypatch) -> None:
    source = Path(__file__).resolve().parent.parent / "tool-supply" / "fixtures" / "misc-protocols" / "tool.py"
    (tmp_path / "runtime.json").write_bytes((source.parent / "runtime.json").read_bytes())
    spec = importlib.util.spec_from_file_location("misc_protocols_self_check", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    jail_root = tmp_path / "jail"
    jail_root.mkdir()
    for language in ("javascript", "python", "dash"):
        (jail_root / f"jail-{language}.json").write_text("{}")
    observed = []

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")

    def run_process(command, **_options):
        if "--version" in command:
            return b"v24.19.0\n"
        if command[:3] == ["/usr/bin/dpkg-query", "-W", "-f=${Version}"]:
            return {"python3": b"3.14.6-1\n", "dash": b"0.5.12-12\n"}[command[-1]]
        if command[0] == "/usr/bin/python3":
            return b"shared-python3\n"
        if command[0] == "/bin/dash":
            return b"shared-dash\n"
        return b""

    monkeypatch.setattr(module, "run_process", run_process)
    monkeypatch.setattr(module, "transform", lambda path: observed.append(("transform", path)))
    monkeypatch.setattr(module, "emulate", lambda path: observed.append(("emulate", path)))
    monkeypatch.setattr(module, "jail", lambda path: observed.append(("jail", path)))

    assert module.self_check(str(tmp_path / "input.txt")) == 0
    assert [path for kind, path in observed if kind == "jail"] == [
        str(jail_root / f"jail-{language}.json") for language in ("javascript", "python", "dash")
    ]


def test_each_catalogued_capability_gets_its_own_handle_assertion(tmp_path: Path) -> None:
    outputs = {name: f"{name}\n".encode() for name in ("recon.mime", "recon.bytes")}
    inputs = {}
    for name in outputs:
        path = tmp_path / f"{name}.txt"
        path.write_text(name)
        inputs[name] = path

    class CapabilityRuntime(ImmediateRuntime):
        def __init__(self) -> None:
            super().__init__()
            self._outputs = iter(outputs.values())

        def launch(self, envelope_id, incoming):
            super().launch(envelope_id, incoming)
            return RuntimeObservation.exited(
                exit_code=0,
                output=next(self._outputs),
                cgroup_path=f"/run/cgroup-parent/container/executors/{envelope_id}",
                executor_uid=20000,
            )

    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "components": [
                    {
                        "component_id": "fixture.identity",
                        "capability_ids": list(outputs),
                        "entrypoint": "/usr/local/bin/fixture",
                        "interpreter": "/bin/dash",
                        "version": "1.0.0",
                        "profiles": ["resident"],
                        "packages": [
                            {"name": "file", "version": "1"},
                            {"name": "xxd", "version": "1"},
                        ],
                        "files": [
                            {
                                "destination": "/usr/local/bin/fixture",
                                "sha256": "a" * 64,
                            }
                        ],
                        "capability_policies": {
                            name: {
                                "argv": ["/bin/dash", "/usr/local/bin/fixture", name, "{input}"],
                                "cpu_seconds": 5,
                                "filesystem_bytes": 1024 * 1024,
                                "input_kind": "file",
                                "max_input_bytes": 1024,
                                "max_output_bytes": 1024,
                                "memory_bytes": 1024 * 1024,
                                "network": "deny",
                                "output_schema": "",
                                "pids": 8,
                                "wall_seconds": 5,
                            }
                            for name in outputs
                        },
                        "fixture": {
                            "argv": ["sample"],
                            "capability_argv": {name: [str(inputs[name])] for name in outputs},
                            "capability_expected_facts": {name: [name] for name in outputs},
                            "expected_stdout_sha256": hashlib.sha256(b"family\n").hexdigest(),
                            "capability_stdout_sha256": {
                                name: hashlib.sha256(output).hexdigest() for name, output in outputs.items()
                            },
                            "timeout_seconds": 5,
                        },
                    }
                ]
            }
        )
    )
    environment = {
        "INCYPHER_STRICT_IMAGE": IMAGE_ID,
        "INCYPHER_IMAGE_MANIFEST": IMAGE_ID,
        "INCYPHER_IMAGE_CONFIG": "sha256:" + "c" * 64,
        "INCYPHER_IMAGE_PLATFORM": "linux/arm64",
    }

    path = qualify(
        environment,
        "fixture.identity",
        state=tmp_path / "state",
        inventory_path=inventory,
        preflight=lambda *_args, **_kwargs: admitted(),
        runtime=CapabilityRuntime(),
    )

    receipt = json.loads(path.read_bytes())
    assert [item["capability_id"] for item in receipt["invocations"]] == list(outputs)
    assert {item["component_id"] for item in receipt["invocations"]} == {"/bin/dash"}
    assert {item["version"] for item in receipt["invocations"]} == {"1.0.0"}
    assert {item["image_digest"] for item in receipt["invocations"]} == {IMAGE_ID}
    assert {item["output_digest"] for item in receipt["invocations"]} == {
        hashlib.sha256(output).hexdigest() for output in outputs.values()
    }
