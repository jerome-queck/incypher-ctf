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


def test_native_docker_build_does_not_require_colima(monkeypatch) -> None:
    runner = Runner()

    def unexpected_colima_verification() -> int:
        raise AssertionError("native Docker qualification must not verify Colima")

    monkeypatch.setattr(qualify_tool_supply.runtime, "verify", unexpected_colima_verification)

    assert qualify_tool_supply.build_image(runner, runtime_host="native-docker") == (
        "linux/arm64",
        MANIFEST,
        CONFIG,
    )


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


def test_native_docker_observation_cleans_its_host_cgroup_directly() -> None:
    runner = Runner({"component_id": "fixture.identity", "outcome": "pass"})

    qualify_tool_supply.strict_observation(
        "fixture.identity",
        platform="linux/arm64",
        manifest_digest=MANIFEST,
        config_digest=CONFIG,
        runtime_host="native-docker",
        runner=runner,
    )

    assert runner.commands[0][runner.commands[0].index("--cgroup-parent") + 1] == "incypher-v2-strict.slice"
    probe = runner.commands[1]
    assert "type=bind,source=/sys/fs/cgroup/incypher-v2-strict.slice,target=/run/cgroup-parent" in probe
    assert runner.commands[-1] == ["sudo", "rmdir", "/sys/fs/cgroup/incypher-v2-strict.slice"]


def test_tool_handle_qualification_retains_state_until_merge(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    supply = root / "tool-supply"
    (supply / "generated").mkdir(parents=True)
    (supply / "locks").mkdir()
    (supply / "generated" / "inventory.json").write_text(
        json.dumps(
            {
                "components": [
                    {
                        "component_id": "fixture.identity",
                        "profiles": ["fixture-profile"],
                        "capability_policies": {"fixture": {}},
                        "files": [],
                    }
                ]
            }
        )
    )
    (supply / "locks" / "fixture-profile.json").write_bytes(b"lock")
    (supply / "generated" / "receipt.json").write_bytes(b"receipt")
    retained = root / ".cache" / "qualification" / "tool-handles" / "fixture.identity-1"

    def retain(kind: str, prefix: str) -> Path:
        assert kind == "tool-handles"
        assert prefix == "fixture.identity-"
        retained.mkdir(parents=True)
        return retained

    runner = Runner()
    monkeypatch.setattr(qualify_tool_supply, "REPO_ROOT", root)
    monkeypatch.setattr(qualify_tool_supply, "new_retained_directory", retain)
    monkeypatch.setattr(qualify_tool_supply, "build_image", lambda _runner: ("linux/arm64", MANIFEST, CONFIG))
    monkeypatch.setattr(qualify_tool_supply, "strict_observation", lambda *args, **kwargs: {"outcome": "pass"})
    monkeypatch.setattr(
        qualify_tool_supply,
        "create_handle_receipt",
        lambda state, *_args: {"state": str(state)},
    )
    monkeypatch.setattr(qualify_tool_supply, "create_receipt", lambda *args, **kwargs: {"identity": "receipt"})
    monkeypatch.setattr(qualify_tool_supply, "promote_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(qualify_tool_supply.promote_run, "held", lambda _root: [])

    result = qualify_tool_supply.qualify("fixture.identity", tmp_path / "promoted.json", runner=runner)

    assert result == {"identity": "receipt"}
    assert retained.is_dir()
    assert any("solver.tool_handle_probe" in command for command in runner.commands)
