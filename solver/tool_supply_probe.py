"""Run one locked Tool fixture after admission to the strict image profile."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

from solver.isolation import IsolationReceipt, strict_preflight

INVENTORY = Path("/opt/solver/tool-supply/inventory.json")
MAX_OUTPUT = 64 * 1024


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _inside(root: Path, absolute: str) -> Path:
    path = Path(absolute)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"image path is not absolute and contained: {absolute}")
    return root.joinpath(*path.parts[1:])


def observe(
    component_id: str,
    *,
    inventory_path: Path = INVENTORY,
    image_root: Path = Path("/"),
    environ: Mapping[str, str] | None = None,
    preflight: Callable[[Mapping[str, str]], IsolationReceipt] = strict_preflight,
    execute: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> dict[str, object]:
    """Return bounded facts produced by the component's own locked entrypoint."""

    environment = os.environ if environ is None else environ
    manifest_digest = environment.get("INCYPHER_TOOL_IMAGE_MANIFEST", "")
    config_digest = environment.get("INCYPHER_TOOL_IMAGE_CONFIG", "")
    platform = environment.get("INCYPHER_TOOL_PLATFORM", "")
    if (
        not manifest_digest.startswith("sha256:")
        or not config_digest.startswith("sha256:")
        or not platform.startswith("linux/")
    ):
        raise ValueError("image manifest, config digest and platform are required")
    isolation = preflight(environment)
    if isolation.image_id != manifest_digest:
        raise ValueError("strict admission did not bind the exercised OCI manifest")
    inventory = json.loads(inventory_path.read_text())
    try:
        component = next(item for item in inventory["components"] if item["component_id"] == component_id)
    except (KeyError, StopIteration, TypeError) as error:
        raise ValueError(f"component is absent from the built image: {component_id}") from error
    observed_files = []
    for declared in component["files"]:
        installed = _inside(image_root, declared["destination"])
        content = installed.read_bytes()
        if _sha(content) != declared["sha256"]:
            raise ValueError(f"installed file digest mismatch: {declared['destination']}")
        observed_files.append(
            {"destination": declared["destination"], "sha256": declared["sha256"], "size": len(content)}
        )
    observed_files.sort(key=lambda item: item["destination"])
    entrypoint = _inside(image_root, component["entrypoint"])

    def mapped(arguments: list[str]) -> list[str]:
        return [str(_inside(image_root, value)) if value.startswith("/") else value for value in arguments]

    options: dict[str, Any] = {
        "capture_output": True,
        "check": False,
        "env": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
        "timeout": component["fixture"]["timeout_seconds"],
    }
    version = execute([str(entrypoint), *mapped(component["version_argv"])], **options)
    result = execute([str(entrypoint), *mapped(component["fixture"]["argv"])], **options)
    if any(len(output) > MAX_OUTPUT for output in (version.stdout, version.stderr, result.stdout, result.stderr)):
        raise ValueError("semantic fixture exceeded its bounded output")
    observed_version = version.stdout.decode("utf-8").strip() if version.returncode == 0 else ""
    expected = component["fixture"]["expected_stdout_sha256"]
    outcome = "pass" if result.returncode == 0 and _sha(result.stdout) == expected else "fail"
    return {
        "component_id": component_id,
        "image": {
            "platform": platform,
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
        },
        "strict_profile_digest": isolation.profile_digest,
        "isolation": {
            **asdict(isolation),
            "runtime_pin": dict(isolation.runtime_pin),
            "checks": dict(isolation.checks),
            "owned_residue": list(isolation.owned_residue),
        },
        "sbom": {
            "component_version": component["version"],
            "entrypoint": component["entrypoint"],
            "entrypoint_sha256": next(
                item["sha256"] for item in component["files"] if item["destination"] == component["entrypoint"]
            ),
            "files": observed_files,
            "packages": component["packages"],
        },
        "semantic_fixture": {
            "entrypoint": component["entrypoint"],
            "entrypoint_sha256": _sha(entrypoint.read_bytes()),
            "observed_version": observed_version,
            "exit_code": result.returncode,
            "stdout_sha256": _sha(result.stdout),
            "stderr_sha256": _sha(result.stderr),
            "outcome": outcome,
        },
    }


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m solver.tool_supply_probe COMPONENT_ID", file=sys.stderr)
        return 2
    try:
        observation = observe(arguments[0])
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
        print(f"tool fixture refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(observation, sort_keys=True, separators=(",", ":")))
    return 0 if observation["semantic_fixture"]["outcome"] == "pass" else 1  # type: ignore[index]


if __name__ == "__main__":
    raise SystemExit(main())
