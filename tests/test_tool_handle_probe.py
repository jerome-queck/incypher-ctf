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


def test_catalogued_fixture_runs_through_a_verified_tool_handle(tmp_path: Path) -> None:
    output = b"application/octet-stream\n"
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "components": [
                    {
                        "component_id": "fixture.identity",
                        "entrypoint": "file",
                        "version": "1.0.0",
                        "fixture": {
                            "argv": ["sample"],
                            "expected_stdout_sha256": hashlib.sha256(output).hexdigest(),
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
        runtime=ImmediateRuntime(),
    )

    receipt = json.loads(path.read_bytes())
    assert receipt["invocations"][0]["capability_id"] == "fixture.identity"
    assert receipt["invocations"][0]["component_id"] == "file"
    assert receipt["invocations"][0]["version"] == "1.0.0"
    assert receipt["invocations"][0]["image_digest"] == IMAGE_ID
    assert receipt["invocations"][0]["output_digest"] == hashlib.sha256(output).hexdigest()
