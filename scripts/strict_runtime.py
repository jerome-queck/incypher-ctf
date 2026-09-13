"""Run the Solver through the pinned host-side strict Colima profile."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import runtime  # noqa: E402
from solver.attempt_executor_contracts import RuntimeBinding  # noqa: E402

CGROUP_PARENT = "incypher-v2-strict"
CGROUP_SOURCE = "/sys/fs/cgroup/system.slice/incypher-v2-strict"
IMAGE_TAG = "incypher-solver:strict"
IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")

CommandRunner = Callable[..., Any]


def _subprocess_run(command: list[str], **kwargs: Any) -> Any:
    return subprocess.run(command, **kwargs)


def container_command(
    image_id: str,
    *,
    env_file: Path | None,
    state: Path | None,
    preflight_only: bool,
    binding: RuntimeBinding | None = None,
) -> list[str]:
    """Return the one Docker command allowed to start the strict Solver image."""
    command = ["docker", "run"]
    if preflight_only:
        command.append("--rm")
    else:
        command.extend(["--restart", "unless-stopped"])
    command.extend(
        [
            "--cap-drop",
            "ALL",
            "--cap-add",
            "SYS_ADMIN",
            "--cap-add",
            "NET_ADMIN",
            "--cap-add",
            "SETPCAP",
            "--cap-add",
            "SETUID",
            "--cap-add",
            "SETGID",
            "--security-opt",
            "seccomp=unconfined",
            "--security-opt",
            "systempaths=unconfined",
            "--security-opt",
            "apparmor=unconfined",
            "--cgroup-parent",
            CGROUP_PARENT,
            "--cgroupns",
            "private",
            "--mount",
            f"type=bind,source={CGROUP_SOURCE},target=/run/cgroup-parent",
            "--env",
            f"INCYPHER_STRICT_IMAGE={image_id}",
        ]
    )
    if binding is not None:
        command.extend(
            [
                "--env",
                f"INCYPHER_IMAGE_MANIFEST={binding.image_manifest_digest}",
                "--env",
                f"INCYPHER_IMAGE_CONFIG={binding.image_config_digest}",
                "--env",
                f"INCYPHER_IMAGE_PLATFORM={binding.platform}",
            ]
        )

    if not preflight_only:
        if env_file is not None:
            command.extend(["--env-file", str(env_file)])
        if state is not None:
            command.extend(["--mount", f"type=bind,source={state},target=/state"])

    if preflight_only:
        command.extend(["--entrypoint", "python3", image_id, "-m", "solver.isolation_preflight"])
    else:
        command.append(image_id)
    return command


def tool_probe_command(
    image_id: str, manifest_digest: str, config_digest: str, platform: str, component_id: str
) -> list[str]:
    """Run a fixed component fixture through the identical strict outer profile."""

    command = container_command(image_id, env_file=None, state=None, preflight_only=True)
    command[-5:] = [
        "--env",
        f"INCYPHER_TOOL_IMAGE_MANIFEST={manifest_digest}",
        "--env",
        f"INCYPHER_TOOL_IMAGE_CONFIG={config_digest}",
        "--env",
        f"INCYPHER_TOOL_PLATFORM={platform}",
        "--entrypoint",
        "python3",
        image_id,
        "-m",
        "solver.tool_supply_probe",
        component_id,
    ]
    return command


def attempt_resource_probe_command(
    binding: RuntimeBinding,
    state: Path,
) -> list[str]:
    """Run the semantic Resource fixtures in the same exact strict image."""

    command = container_command(
        binding.image_id,
        env_file=None,
        state=None,
        preflight_only=True,
        binding=binding,
    )
    command[-5:] = [
        "--mount",
        f"type=bind,source={state},target=/state",
        "--env",
        "INCYPHER_DENY_PROBE_SECRET=qualification-secret",
        "--entrypoint",
        "python3",
        binding.image_id,
        "-m",
        "solver.attempt_resource_probe",
    ]
    return command


def tool_handle_probe_command(
    binding: RuntimeBinding,
    state: Path,
    component_id: str,
) -> list[str]:
    """Exercise one catalogued component through its production Tool handle."""

    command = container_command(
        binding.image_id,
        env_file=None,
        state=None,
        preflight_only=True,
        binding=binding,
    )
    command[-5:] = [
        "--mount",
        f"type=bind,source={state},target=/state",
        "--entrypoint",
        "python3",
        binding.image_id,
        "-m",
        "solver.tool_handle_probe",
        component_id,
    ]
    return command


def target_broker_probe_command(binding: RuntimeBinding, state: Path) -> list[str]:
    """Exercise the hostile worker's sole Target port in the strict image."""

    command = container_command(
        binding.image_id,
        env_file=None,
        state=None,
        preflight_only=True,
        binding=binding,
    )
    command[-5:] = [
        "--mount",
        f"type=bind,source={state},target=/state",
        "--entrypoint",
        "python3",
        binding.image_id,
        "-m",
        "solver.target_broker_probe",
    ]
    return command


def research_broker_probe_command(binding: RuntimeBinding, state: Path) -> list[str]:
    """Exercise brokered public Research and raw-egress denial in the strict image."""

    command = container_command(
        binding.image_id,
        env_file=None,
        state=None,
        preflight_only=True,
        binding=binding,
    )
    command[-5:] = [
        "--mount",
        f"type=bind,source={state},target=/state",
        "--entrypoint",
        "python3",
        binding.image_id,
        "-m",
        "solver.research_broker_probe",
    ]
    return command


def _check_result(result: Any, command: list[str]) -> None:
    returncode = getattr(result, "returncode", None)
    if returncode not in (None, 0):
        raise subprocess.CalledProcessError(returncode, command)


def _checked(command: list[str], runner: CommandRunner) -> Any:
    result = runner(command, check=True)
    _check_result(result, command)
    return result


def build_image(runner: CommandRunner) -> RuntimeBinding:
    with tempfile.TemporaryDirectory(prefix="strict-image-metadata-") as temporary:
        metadata_path = Path(temporary) / "metadata.json"
        _checked(
            [
                "docker",
                "buildx",
                "build",
                "--load",
                "--provenance=false",
                "--metadata-file",
                str(metadata_path),
                "--tag",
                IMAGE_TAG,
                ".",
            ],
            runner,
        )
        metadata = json.loads(metadata_path.read_text())
    manifest = metadata.get("containerimage.digest")
    config = metadata.get("containerimage.config.digest")
    inspected = (
        runner(
            ["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", IMAGE_TAG],
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .split()
    )
    if len(inspected) != 2 or not IMAGE_ID.fullmatch(inspected[0]):
        raise RuntimeError("docker image inspect did not return an immutable sha256 image ID")
    if (
        not isinstance(manifest, str)
        or not IMAGE_ID.fullmatch(manifest)
        or not isinstance(config, str)
        or not IMAGE_ID.fullmatch(config)
        or inspected[0] not in {manifest, config}
        or inspected[1] not in {"linux/arm64", "linux/amd64"}
    ):
        raise RuntimeError("BuildKit did not bind one loaded OCI image")
    return RuntimeBinding(inspected[0], manifest, config, inspected[1])


def _validate_path(
    label: str,
    path: Path,
    allowed_roots: tuple[Path, ...],
    *,
    exact: Path | None = None,
) -> None:
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    if not path.exists():
        raise ValueError(f"{label} does not exist: {path}")

    resolved = path.resolve()
    if exact is not None and resolved != exact.resolve():
        raise ValueError(f"{label} must be the canonical competition path {exact}: {path}")
    for root in allowed_roots:
        try:
            resolved.relative_to(root.resolve())
            return
        except ValueError:
            continue
    roots = ", ".join(str(root) for root in allowed_roots)
    raise ValueError(f"{label} must be within an allowed Colima mount ({roots}): {path}")


def _production_allowed_roots(home: Path, external: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Env may remain private under home; scored state belongs only on Working."""
    return (home, external), (external,)


def _launch(
    *,
    env_file: Path | None,
    state: Path | None,
    preflight_only: bool,
    runner: CommandRunner,
) -> int:
    verified = runtime.verify()
    if verified != 0:
        return verified

    binding = build_image(runner)

    if not preflight_only:
        _checked(["docker", "builder", "prune", "--all", "--force"], runner)
        _checked([sys.executable, str(REPO_ROOT / "scripts/check_host_storage.py"), "competition"], runner)

    try:
        _checked(
            [
                "docker",
                "run",
                "--rm",
                "--cgroup-parent",
                CGROUP_PARENT,
                "--entrypoint",
                "/bin/true",
                binding.image_id,
            ],
            runner,
        )
        _checked(
            container_command(
                binding.image_id,
                env_file=env_file,
                state=state,
                preflight_only=preflight_only,
                binding=binding,
            ),
            runner,
        )
    finally:
        _checked(["colima", "ssh", "--", "sudo", "rmdir", CGROUP_SOURCE], runner)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Solver through strict Colima isolation.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("preflight", help="run the image's strict isolation preflight")
    commands.add_parser("qualify-target", help="prove the hostile worker's Target port")
    run = commands.add_parser("run", help="run the image's Solver entrypoint")
    run.add_argument("--env-file", type=Path, required=True)
    run.add_argument("--state", type=Path, required=True)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    runner: CommandRunner | None = None,
    home: Path | None = None,
) -> int:
    arguments = _parser().parse_args(argv)
    command_runner = runner or _subprocess_run

    if arguments.command == "preflight":
        return _launch(env_file=None, state=None, preflight_only=True, runner=command_runner)
    if arguments.command == "qualify-target":
        verified = runtime.verify()
        if verified != 0:
            return verified
        cache = REPO_ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        binding = build_image(command_runner)
        try:
            _checked(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--cgroup-parent",
                    CGROUP_PARENT,
                    "--entrypoint",
                    "/bin/true",
                    binding.image_id,
                ],
                command_runner,
            )
            with tempfile.TemporaryDirectory(prefix="target-qualification-", dir=cache) as state:
                _checked(target_broker_probe_command(binding, Path(state)), command_runner)
        finally:
            _checked(["colima", "ssh", "--", "sudo", "rmdir", CGROUP_SOURCE], command_runner)
        return 0

    if home is None:
        env_roots, state_roots = _production_allowed_roots(Path.home(), runtime.EXTERNAL_PROJECT_ROOT)
        exact_state = runtime.EXTERNAL_PROJECT_ROOT / "incypher-ctf" / "state"
    else:
        env_roots = state_roots = (home,)
        exact_state = None
    try:
        _validate_path("--env-file", arguments.env_file, env_roots)
        _validate_path("--state", arguments.state, state_roots, exact=exact_state)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    return _launch(
        env_file=arguments.env_file,
        state=arguments.state,
        preflight_only=False,
        runner=command_runner,
    )


if __name__ == "__main__":
    raise SystemExit(main())
