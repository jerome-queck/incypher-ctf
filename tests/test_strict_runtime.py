"""Host-side contract for the strict Colima launcher."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import strict_runtime
from solver.attempt_executor_contracts import RuntimeBinding


IMAGE_ID = "sha256:" + "a" * 64
MANIFEST = IMAGE_ID
CONFIG = "sha256:" + "c" * 64


def image_binding() -> RuntimeBinding:
    return RuntimeBinding(IMAGE_ID, MANIFEST, CONFIG, "linux/arm64")


def test_container_command_has_the_fixed_strict_profile() -> None:
    command = strict_runtime.container_command(
        IMAGE_ID,
        env_file=Path("/home/jerome/.env"),
        state=Path("/home/jerome/state"),
        preflight_only=False,
    )

    assert command[:4] == ["docker", "run", "--restart", "unless-stopped"]
    assert ["--cap-drop", "ALL"] == command[4:6]
    assert ["--cap-add", "SYS_ADMIN"] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--cap-add", "NET_ADMIN"] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--cap-add", "SETPCAP"] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--cap-add", "SETUID"] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--cap-add", "SETGID"] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--security-opt", "seccomp=unconfined"] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--security-opt", "systempaths=unconfined"] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert ["--security-opt", "apparmor=unconfined"] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert ["--cgroup-parent", strict_runtime.CGROUP_PARENT] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert ["--cgroupns", "private"] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert [
        "--mount",
        f"type=bind,source={strict_runtime.CGROUP_SOURCE},target=/run/cgroup-parent",
    ] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--env", f"INCYPHER_STRICT_IMAGE={IMAGE_ID}"] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert ["--env-file", "/home/jerome/.env"] in [command[index : index + 2] for index in range(len(command) - 1)]
    assert ["--mount", "type=bind,source=/home/jerome/state,target=/state"] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert command[-1] == IMAGE_ID


def test_preflight_uses_the_image_entrypoint_and_never_mounts_run_inputs() -> None:
    command = strict_runtime.container_command(
        IMAGE_ID,
        env_file=Path("/home/jerome/.env"),
        state=Path("/home/jerome/state"),
        preflight_only=True,
    )

    assert command[-5:] == ["--entrypoint", "python3", IMAGE_ID, "-m", "solver.isolation_preflight"]
    assert command[:3] == ["docker", "run", "--rm"]
    assert "--env-file" not in command
    assert "/state" not in command


def test_tool_probe_uses_the_same_strict_profile_and_binds_the_distributable_image() -> None:
    command = strict_runtime.tool_probe_command(
        IMAGE_ID,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
        "linux/arm64",
        "fixture.identity",
    )

    strict_prefix = strict_runtime.container_command(IMAGE_ID, env_file=None, state=None, preflight_only=True)[:-5]
    assert command[: len(strict_prefix)] == strict_prefix
    assert ["--env", "INCYPHER_TOOL_IMAGE_MANIFEST=sha256:" + "b" * 64] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert ["--env", "INCYPHER_TOOL_IMAGE_CONFIG=sha256:" + "c" * 64] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert command[-6:] == [
        "--entrypoint",
        "python3",
        IMAGE_ID,
        "-m",
        "solver.tool_supply_probe",
        "fixture.identity",
    ]


def test_attempt_resource_probe_mounts_only_its_disposable_state(tmp_path: Path) -> None:
    command = strict_runtime.attempt_resource_probe_command(
        image_binding(),
        tmp_path,
    )

    assert ["--mount", f"type=bind,source={tmp_path},target=/state"] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert ["--env", "INCYPHER_DENY_PROBE_SECRET=qualification-secret"] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert command[-5:] == [
        "--entrypoint",
        "python3",
        IMAGE_ID,
        "-m",
        "solver.attempt_resource_probe",
    ]


def test_tool_handle_probe_runs_the_catalogued_fixture_in_the_strict_image(tmp_path: Path) -> None:
    command = strict_runtime.tool_handle_probe_command(
        image_binding(),
        tmp_path,
        "fixture.identity",
    )

    assert ["--mount", f"type=bind,source={tmp_path},target=/state"] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert command[-6:] == [
        "--entrypoint",
        "python3",
        IMAGE_ID,
        "-m",
        "solver.tool_handle_probe",
        "fixture.identity",
    ]


def test_target_broker_probe_runs_inside_the_strict_image(tmp_path: Path) -> None:
    command = strict_runtime.target_broker_probe_command(image_binding(), tmp_path)

    assert ["--mount", f"type=bind,source={tmp_path},target=/state"] in [
        command[index : index + 2] for index in range(len(command) - 1)
    ]
    assert command[-5:] == [
        "--entrypoint",
        "python3",
        IMAGE_ID,
        "-m",
        "solver.target_broker_probe",
    ]


class Runner:
    def __init__(
        self,
        *,
        fail_on: tuple[str, ...] | None = None,
        image_id: str = IMAGE_ID,
    ) -> None:
        self.commands: list[list[str]] = []
        self.fail_on = fail_on
        self.image_id = image_id

    def __call__(self, command: list[str], **kwargs: object) -> SimpleNamespace:
        self.commands.append(command)
        if self.fail_on is not None and tuple(command) == self.fail_on:
            raise RuntimeError("command failed")
        if command[:3] == ["docker", "buildx", "build"]:
            metadata = Path(command[command.index("--metadata-file") + 1])
            metadata.write_text(
                __import__("json").dumps({"containerimage.digest": MANIFEST, "containerimage.config.digest": CONFIG})
            )
        if command[:3] == ["docker", "image", "inspect"]:
            return SimpleNamespace(stdout=f"{self.image_id} linux/arm64\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)


def test_launch_builds_then_uses_the_immutable_image_id_and_cleans_its_parent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = Runner()
    monkeypatch.setattr(strict_runtime.runtime, "verify", lambda: 0)
    env_file = tmp_path / ".env"
    state = tmp_path / "state"
    env_file.write_text("TEAM_KEY=test\n")
    state.mkdir()

    assert (
        strict_runtime.main(
            ["run", "--env-file", str(env_file), "--state", str(state)],
            runner=runner,
            home=tmp_path,
        )
        == 0
    )

    assert runner.commands[0][:3] == ["docker", "buildx", "build"]
    assert runner.commands[1] == [
        "docker",
        "image",
        "inspect",
        "--format",
        "{{.Id}} {{.Os}}/{{.Architecture}}",
        strict_runtime.IMAGE_TAG,
    ]
    assert runner.commands[2] == [
        "docker",
        "run",
        "--rm",
        "--cgroup-parent",
        strict_runtime.CGROUP_PARENT,
        "--entrypoint",
        "/bin/true",
        IMAGE_ID,
    ]
    assert runner.commands[3] == strict_runtime.container_command(
        IMAGE_ID,
        env_file=env_file,
        state=state,
        preflight_only=False,
        binding=image_binding(),
    )
    assert runner.commands[4] == ["colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE]
    assert all(IMAGE_ID in command for command in (runner.commands[3],))


def test_preflight_requires_no_env_or_state_and_runs_the_same_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = Runner()
    monkeypatch.setattr(strict_runtime.runtime, "verify", lambda: 0)

    assert strict_runtime.main(["preflight"], runner=runner) == 0
    assert runner.commands[3] == strict_runtime.container_command(
        IMAGE_ID,
        env_file=None,
        state=None,
        preflight_only=True,
        binding=image_binding(),
    )


def test_build_accepts_a_config_digest_as_the_local_image_identity() -> None:
    binding = strict_runtime.build_image(Runner(image_id=CONFIG))

    assert binding == RuntimeBinding(CONFIG, MANIFEST, CONFIG, "linux/arm64")
    assert binding.image_manifest_digest != binding.image_config_digest


def test_a_failed_strict_run_still_removes_only_the_owned_cgroup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup = ("colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE)
    runner = Runner(
        fail_on=tuple(
            strict_runtime.container_command(
                IMAGE_ID,
                env_file=None,
                state=None,
                preflight_only=True,
                binding=image_binding(),
            )
        )
    )
    monkeypatch.setattr(strict_runtime.runtime, "verify", lambda: 0)

    with pytest.raises(RuntimeError, match="command failed"):
        strict_runtime.main(["preflight"], runner=runner)

    assert runner.commands[-1] == list(cleanup)
    assert not any(command[:2] == ["rm", "-rf"] for command in runner.commands)


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--env-file", "relative.env"),
        ("--state", "relative-state"),
    ],
)
def test_run_rejects_non_absolute_paths_before_any_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, option: str, value: str
) -> None:
    runner = Runner()
    monkeypatch.setattr(strict_runtime.runtime, "verify", lambda: 0)

    arguments = ["run", "--env-file", str(tmp_path / ".env"), "--state", str(tmp_path / "state")]
    if option == "--env-file":
        arguments[2] = value
    else:
        arguments[4] = value

    assert strict_runtime.main(arguments, runner=runner, home=tmp_path) == 2
    assert runner.commands == []


def test_run_rejects_missing_and_outside_paths_before_any_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runner = Runner()
    monkeypatch.setattr(strict_runtime.runtime, "verify", lambda: 0)
    env_file = tmp_path / ".env"
    outside = tmp_path.parent / "outside-state"
    env_file.write_text("TEAM_KEY=test\n")
    outside.mkdir()

    assert (
        strict_runtime.main(
            ["run", "--env-file", str(env_file), "--state", str(tmp_path / "missing")],
            runner=runner,
            home=tmp_path,
        )
        == 2
    )
    assert (
        strict_runtime.main(
            ["run", "--env-file", str(env_file), "--state", str(outside)],
            runner=runner,
            home=tmp_path,
        )
        == 2
    )
    assert runner.commands == []


def test_runtime_drift_stops_before_build(monkeypatch: pytest.MonkeyPatch) -> None:
    runner = Runner()
    monkeypatch.setattr(strict_runtime.runtime, "verify", lambda: 1)

    assert strict_runtime.main(["preflight"], runner=runner) == 1
    assert runner.commands == []


@pytest.mark.parametrize(
    "image_id",
    ["sha256:abc", "sha256:" + "a" * 63, "sha256:" + "g" * 64, "image:latest"],
)
def test_launcher_rejects_any_non_exact_image_identity(
    monkeypatch: pytest.MonkeyPatch,
    image_id: str,
) -> None:
    runner = Runner(image_id=image_id)
    monkeypatch.setattr(strict_runtime.runtime, "verify", lambda: 0)

    with pytest.raises(RuntimeError, match="immutable sha256 image ID"):
        strict_runtime.main(["preflight"], runner=runner)

    assert len(runner.commands) == 2


def test_solver_profile_and_host_launcher_share_one_runtime_pin() -> None:
    from solver.isolation import STRICT_RUNTIME_PIN

    pin = strict_runtime.runtime.PIN
    assert STRICT_RUNTIME_PIN == {
        "colima": pin.colima,
        "docker": pin.docker,
        "cpu": pin.cpu,
        "memory_gib": pin.memory_gib,
        "disk_gib": pin.disk_gib,
    }
