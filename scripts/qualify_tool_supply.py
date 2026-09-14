"""Build, exercise and promote one Tool component's strict-image receipt.

The handle-qualification state remains available for review until the ticket is merged.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import promote_run  # noqa: E402
import runtime  # noqa: E402
import strict_runtime  # noqa: E402
from qualification_retention import new_retained_directory  # noqa: E402
from solver.tool_supply_receipt import ReceiptInvalid, create_receipt, promote_receipt  # noqa: E402
from solver.resident_handle_receipt import create_receipt as create_handle_receipt  # noqa: E402

Runner = Callable[..., Any]
RUNTIME_HOSTS = ("colima", "native-docker")
PLATFORMS = ("linux/amd64", "linux/arm64")
NATIVE_CGROUP_PARENT = "incypher-v2-strict.slice"
NATIVE_CGROUP_SOURCE = "/sys/fs/cgroup/incypher-v2-strict.slice"


def _run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **options)


def _cleanup_cgroup(runtime_host: str, runner: Runner) -> None:
    if runtime_host == "colima":
        command = ["colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE]
    elif runtime_host == "native-docker":
        command = ["sudo", "rmdir", NATIVE_CGROUP_SOURCE]
    else:
        raise ReceiptInvalid(f"unsupported qualification runtime host: {runtime_host}")
    runner(command, check=True)


def _strict_command(command: list[str], runtime_host: str) -> list[str]:
    if runtime_host == "colima":
        return command
    if runtime_host != "native-docker":
        raise ReceiptInvalid(f"unsupported qualification runtime host: {runtime_host}")
    return [
        NATIVE_CGROUP_PARENT
        if item == strict_runtime.CGROUP_PARENT
        else item.replace(strict_runtime.CGROUP_SOURCE, NATIVE_CGROUP_SOURCE)
        for item in command
    ]


def build_image(
    runner: Runner = _run,
    *,
    platform: str = "linux/arm64",
    runtime_host: str = "colima",
) -> tuple[str, str, str]:
    """Return platform, OCI manifest digest and config digest for the one loaded build."""

    if runtime_host not in RUNTIME_HOSTS:
        raise ReceiptInvalid(f"unsupported qualification runtime host: {runtime_host}")
    if platform not in PLATFORMS:
        raise ReceiptInvalid(f"unsupported qualification platform: {platform}")
    if runtime_host == "colima" and runtime.verify() != 0:
        raise ReceiptInvalid("the pinned container runtime is unavailable")
    metadata_path = new_retained_directory("image-metadata", "tool-") / "metadata.json"
    tag = strict_runtime.IMAGE_TAG
    command = ["docker", "buildx", "build", "--load", "--provenance=false", "--platform", platform]
    command.extend(("--metadata-file", str(metadata_path), "--tag", tag, "."))
    runner(command, check=True)
    metadata = json.loads(metadata_path.read_text())
    manifest_digest = metadata.get("containerimage.digest")
    config_digest = metadata.get("containerimage.config.digest")
    if not isinstance(manifest_digest, str) or not manifest_digest.startswith("sha256:"):
        raise ReceiptInvalid("BuildKit did not return the OCI manifest digest")
    inspected = (
        runner(
            ["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", tag],
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .split()
    )
    if not isinstance(config_digest, str) or not config_digest.startswith("sha256:"):
        raise ReceiptInvalid("BuildKit did not return the OCI config digest")
    if len(inspected) != 2 or inspected[0] != manifest_digest or inspected[1] != platform:
        raise ReceiptInvalid("the loaded strict image is not the requested platform and OCI manifest")
    if runtime_host == "colima":
        strict_runtime.enforce_host_storage("development", runner)
    return platform, manifest_digest, config_digest


def strict_observation(
    component_id: str,
    *,
    platform: str,
    manifest_digest: str,
    config_digest: str,
    runtime_host: str = "colima",
    runner: Runner = _run,
) -> dict[str, object]:
    """Execute the fixture under the pinned strict command and always clean its cgroup."""

    runner(
        [
            "docker",
            "run",
            "--rm",
            "--cgroup-parent",
            NATIVE_CGROUP_PARENT if runtime_host == "native-docker" else strict_runtime.CGROUP_PARENT,
            "--entrypoint",
            "/bin/true",
            manifest_digest,
        ],
        check=True,
    )
    try:
        result = runner(
            _strict_command(
                strict_runtime.tool_probe_command(
                    manifest_digest,
                    manifest_digest,
                    config_digest,
                    platform,
                    component_id,
                ),
                runtime_host,
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ReceiptInvalid(f"strict semantic fixture refused: {result.stderr.strip() or result.returncode}")
        observation = json.loads(result.stdout)
        if not isinstance(observation, dict):
            raise ReceiptInvalid("strict semantic fixture returned no observation object")
        return observation
    finally:
        _cleanup_cgroup(runtime_host, runner)


def qualify(
    component_id: str,
    destination: Path,
    *,
    platform: str = "linux/arm64",
    runtime_host: str = "colima",
    runner: Runner = _run,
) -> dict[str, object]:
    binding = build_image(runner, platform=platform, runtime_host=runtime_host)
    platform, manifest_digest, config_digest = binding
    observation = strict_observation(
        component_id,
        platform=platform,
        manifest_digest=manifest_digest,
        config_digest=config_digest,
        runtime_host=runtime_host,
        runner=runner,
    )
    supply = REPO_ROOT / "tool-supply"
    inventory = (supply / "generated" / "inventory.json").read_bytes()
    inventory_document = json.loads(inventory)
    try:
        component = next(item for item in inventory_document["components"] if item["component_id"] == component_id)
        profile = component["profiles"][0]
    except (KeyError, StopIteration, TypeError) as error:
        raise ReceiptInvalid(f"no generated component named {component_id}") from error
    handle_solve = None
    if component.get("capability_policies"):
        runner(
            [
                "docker",
                "run",
                "--rm",
                "--cgroup-parent",
                NATIVE_CGROUP_PARENT if runtime_host == "native-docker" else strict_runtime.CGROUP_PARENT,
                "--entrypoint",
                "/bin/true",
                manifest_digest,
            ],
            check=True,
        )
        try:
            state = new_retained_directory("tool-handles", f"{component_id}-")
            result = runner(
                _strict_command(
                    strict_runtime.tool_handle_probe_command(
                        strict_runtime.RuntimeBinding(manifest_digest, manifest_digest, config_digest, platform),
                        state,
                        component_id,
                    ),
                    runtime_host,
                ),
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise ReceiptInvalid(f"strict Tool-handle Solve refused: {result.stderr.strip() or result.returncode}")
            closure = {str(item["source"]): (supply / item["source"]).read_bytes() for item in component["files"]}
            handle_solve = create_handle_receipt(state, component, inventory, closure)
        finally:
            _cleanup_cgroup(runtime_host, runner)
    receipt = create_receipt(
        observation,
        lock_fragment=(supply / "locks" / f"{profile}.json").read_bytes(),
        inventory=inventory,
        supply_receipt=(supply / "generated" / "receipt.json").read_bytes(),
        source_root=supply,
        handle_solve=handle_solve,
    )
    promote_receipt(
        receipt,
        destination,
        secrets={f"{name}[{index}]": value for index, (name, value) in enumerate(promote_run.held(REPO_ROOT))},
        host_roots=(REPO_ROOT, Path.home()),
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component_id")
    parser.add_argument("--into", type=Path)
    parser.add_argument("--platform", choices=PLATFORMS, default="linux/arm64")
    parser.add_argument("--runtime-host", choices=RUNTIME_HOSTS, default="colima")
    arguments = parser.parse_args(argv)
    receipt_root = REPO_ROOT / "tool-supply" / "receipts"
    if arguments.platform == "linux/amd64":
        receipt_root /= "amd64"
    destination = arguments.into or receipt_root / f"{arguments.component_id}.json"
    try:
        receipt = qualify(
            arguments.component_id,
            destination,
            platform=arguments.platform,
            runtime_host=arguments.runtime_host,
        )
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"Tool qualification refused: {error}", file=sys.stderr)
        return 2
    print(f"promoted {receipt['identity']} to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
