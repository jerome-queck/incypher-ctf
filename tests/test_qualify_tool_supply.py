import json
from pathlib import Path
from types import SimpleNamespace

import qualify_tool_supply
import strict_runtime


CONFIG = "sha256:" + "a" * 64
MANIFEST = "sha256:" + "b" * 64


class Runner:
    def __init__(self, observation: dict[str, object] | None = None) -> None:
        self.commands: list[list[str]] = []
        self.observation = observation or {"component_id": "fixture.identity"}

    def __call__(self, command: list[str], **_options: object) -> SimpleNamespace:
        self.commands.append(command)
        if command[:3] == ["docker", "buildx", "build"]:
            metadata = Path(command[command.index("--metadata-file") + 1])
            metadata.write_text(json.dumps({"containerimage.digest": MANIFEST, "containerimage.config.digest": CONFIG}))
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout=f"{MANIFEST} linux/arm64\n", returncode=0, stderr="")
        if "solver.tool_supply_probe" in command:
            return SimpleNamespace(stdout=json.dumps(self.observation), returncode=0, stderr="")
        return SimpleNamespace(stdout="", returncode=0, stderr="")


def test_build_metadata_binds_the_loaded_config_to_the_oci_manifest(monkeypatch) -> None:
    runner = Runner()
    monkeypatch.setattr(qualify_tool_supply.runtime, "verify", lambda: 0)

    assert qualify_tool_supply.build_image(runner) == ("linux/arm64", MANIFEST, CONFIG)
    assert "--load" in runner.commands[0]
    assert "--provenance=false" in runner.commands[0]
    assert runner.commands[1][-1] == strict_runtime.IMAGE_TAG
    assert runner.commands[2] == [
        qualify_tool_supply.sys.executable,
        str(qualify_tool_supply.REPO_ROOT / "scripts/check_host_storage.py"),
        "development",
    ]


def test_strict_observation_cleans_only_its_cgroup() -> None:
    observation = {"component_id": "fixture.identity", "outcome": "pass"}
    runner = Runner(observation)

    assert (
        qualify_tool_supply.strict_observation(
            "fixture.identity",
            platform="linux/arm64",
            manifest_digest=MANIFEST,
            config_digest=CONFIG,
            runner=runner,
        )
        == observation
    )
    assert runner.commands[1] == strict_runtime.tool_probe_command(
        MANIFEST, MANIFEST, CONFIG, "linux/arm64", "fixture.identity"
    )
    assert runner.commands[-1] == ["colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE]
