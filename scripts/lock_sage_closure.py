"""Resolve the minimal Sage library closure into exact per-platform locks."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

REQUESTED = "sagelib=10.9"
PLATFORM_NAMES = {"linux-aarch64": "arm64", "linux-64": "amd64"}


def solve(micromamba: Path, platform: str) -> list[dict[str, Any]]:
    completed = subprocess.run(
        [
            str(micromamba),
            "create",
            "--dry-run",
            "--json",
            "--no-rc",
            "--yes",
            "--prefix",
            "/tmp/incypher-sage-lock",
            "--channel",
            "conda-forge",
            "--platform",
            platform,
            REQUESTED,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    document = json.loads(completed.stdout)
    packages = document.get("actions", {}).get("FETCH")
    if not document.get("success") or not isinstance(packages, list) or not packages:
        raise ValueError("micromamba returned no successful package closure")
    return packages


def lock_document(packages: list[dict[str, Any]], platform: str) -> dict[str, object]:
    fields = ("name", "version", "build_string", "license", "sha256", "size", "url")
    rows = [{field: package.get(field) for field in fields} for package in packages]
    if any(not row["license"] or not row["sha256"] or not row["url"] for row in rows):
        raise ValueError("Sage dependency lacks licence, hash, or source metadata")
    return {
        "component_id": "sagelib",
        "packages": sorted(rows, key=lambda row: (str(row["name"]), str(row["build_string"]))),
        "platform": "linux/" + PLATFORM_NAMES[platform],
        "profile_id": "tool-crypto",
        "requested": REQUESTED,
        "schema_version": 1,
    }


def write_locks(packages: list[dict[str, Any]], platform: str, output: Path) -> None:
    stem = "sage-linux-" + PLATFORM_NAMES[platform]
    explicit = ["@EXPLICIT", *(f"{row['url']}#{row['sha256']}" for row in packages)]
    (output / f"{stem}.explicit").write_text("\n".join(explicit) + "\n")
    (output / f"{stem}.json").write_text(
        json.dumps(lock_document(packages, platform), sort_keys=True, separators=(",", ":")) + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--micromamba", type=Path, required=True)
    parser.add_argument("--platform", choices=tuple(PLATFORM_NAMES), required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.output.mkdir(parents=True, exist_ok=True)
    write_locks(solve(arguments.micromamba, arguments.platform), arguments.platform, arguments.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
