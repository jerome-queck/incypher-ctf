"""Build, exercise and promote one Tool component's strict-image receipt."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import promote_run  # noqa: E402
import runtime  # noqa: E402
import strict_runtime  # noqa: E402
from solver.tool_supply_receipt import ReceiptInvalid, create_receipt, promote_receipt  # noqa: E402
from solver.resident_handle_receipt import create_receipt as create_handle_receipt  # noqa: E402

Runner = Callable[..., Any]


def _run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, **options)


def build_image(runner: Runner = _run) -> tuple[str, str, str]:
    """Return platform, OCI manifest digest and config digest for the one loaded build."""

    if runtime.verify() != 0:
        raise ReceiptInvalid("the pinned container runtime is unavailable")
    with tempfile.TemporaryDirectory(prefix="tool-image-metadata-") as temporary:
        metadata_path = Path(temporary) / "metadata.json"
        runner(
            [
                "docker",
                "buildx",
                "build",
                "--load",
                "--provenance=false",
                "--metadata-file",
                str(metadata_path),
                "--tag",
                strict_runtime.IMAGE_TAG,
                ".",
            ],
            check=True,
        )
        metadata = json.loads(metadata_path.read_text())
    manifest_digest = metadata.get("containerimage.digest")
    config_digest = metadata.get("containerimage.config.digest")
    if not isinstance(manifest_digest, str) or not manifest_digest.startswith("sha256:"):
        raise ReceiptInvalid("BuildKit did not return the OCI manifest digest")
    inspected = (
        runner(
            ["docker", "image", "inspect", "--format", "{{.Id}} {{.Os}}/{{.Architecture}}", strict_runtime.IMAGE_TAG],
            check=True,
            capture_output=True,
            text=True,
        )
        .stdout.strip()
        .split()
    )
    if not isinstance(config_digest, str) or not config_digest.startswith("sha256:"):
        raise ReceiptInvalid("BuildKit did not return the OCI config digest")
    if len(inspected) != 2 or inspected[0] != manifest_digest:
        raise ReceiptInvalid("the loaded strict image is not the OCI manifest")
    strict_runtime.enforce_host_storage("development", runner)
    return inspected[1], manifest_digest, config_digest


def strict_observation(
    component_id: str,
    *,
    platform: str,
    manifest_digest: str,
    config_digest: str,
    runner: Runner = _run,
) -> dict[str, object]:
    """Execute the fixture under the pinned strict command and always clean its cgroup."""

    runner(
        [
            "docker",
            "run",
            "--rm",
            "--cgroup-parent",
            strict_runtime.CGROUP_PARENT,
            "--entrypoint",
            "/bin/true",
            manifest_digest,
        ],
        check=True,
    )
    try:
        result = runner(
            strict_runtime.tool_probe_command(manifest_digest, manifest_digest, config_digest, platform, component_id),
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
        runner(["colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE], check=True)


def qualify(component_id: str, destination: Path, *, runner: Runner = _run) -> dict[str, object]:
    platform, manifest_digest, config_digest = build_image(runner)
    observation = strict_observation(
        component_id,
        platform=platform,
        manifest_digest=manifest_digest,
        config_digest=config_digest,
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
        cache = REPO_ROOT / ".cache"
        cache.mkdir(exist_ok=True)
        runner(
            [
                "docker",
                "run",
                "--rm",
                "--cgroup-parent",
                strict_runtime.CGROUP_PARENT,
                "--entrypoint",
                "/bin/true",
                manifest_digest,
            ],
            check=True,
        )
        try:
            with tempfile.TemporaryDirectory(prefix="tool-handle-", dir=cache) as temporary:
                state = Path(temporary)
                result = runner(
                    strict_runtime.tool_handle_probe_command(
                        strict_runtime.RuntimeBinding(manifest_digest, manifest_digest, config_digest, platform),
                        state,
                        component_id,
                    ),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    raise ReceiptInvalid(
                        f"strict Tool-handle Solve refused: {result.stderr.strip() or result.returncode}"
                    )
                closure = {str(item["source"]): (supply / item["source"]).read_bytes() for item in component["files"]}
                handle_solve = create_handle_receipt(state, component, inventory, closure)
        finally:
            runner(["colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE], check=True)
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
    arguments = parser.parse_args(argv)
    destination = arguments.into or REPO_ROOT / "tool-supply" / "receipts" / f"{arguments.component_id}.json"
    try:
        receipt = qualify(arguments.component_id, destination)
    except (OSError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        print(f"Tool qualification refused: {error}", file=sys.stderr)
        return 2
    print(f"promoted {receipt['identity']} to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
