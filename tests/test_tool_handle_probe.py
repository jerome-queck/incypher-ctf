"""Built-image Tool fixture qualification through its public handle."""

import hashlib
import json
from pathlib import Path

from solver.isolation import (
    STRICT_CONTROLS,
    STRICT_PROFILE_DIGEST,
    STRICT_PROFILE_ID,
    STRICT_RUNTIME_PIN,
    IsolationReceipt,
)
from solver.tool_handle_probe import qualify
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
