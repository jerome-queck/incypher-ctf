"""Refuse host storage growth outside ADR-0057's development or competition budget.

Docker's aggregate image and cache totals include Tool layers, downloads, package closures and
build intermediates. No Tool supply is exempt merely because the sparse VM disk is larger.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import runtime

GIB = 1024**3
PROJECT_ROOT = runtime.EXTERNAL_PROJECT_ROOT
WORKING = PROJECT_ROOT.parent
MINIMUM_HOST_FREE = 100 * GIB
STATE_LIMIT = 60 * GIB
IMAGE_LIMIT = 12 * GIB
LIMITS = {
    "development": {"Images": 40 * GIB, "Build Cache": 20 * GIB},
    "competition": {"Images": 24 * GIB, "Build Cache": 0},
}


def bytes_in(text: str) -> int | None:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)([KMGT]?i?B)", text)
    if not match:
        return None
    units = {
        "B": 1,
        "KB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
        "KiB": 1024,
        "MiB": 1024**2,
        "GiB": GIB,
        "TiB": 1024**4,
    }
    return round(float(match.group(1)) * units[match.group(2)])


def violations(
    mode: str,
    rows: list[Mapping[str, str]],
    free: int,
    *,
    state_size: int = 0,
    image_sizes: tuple[int, ...] = (),
) -> list[str]:
    found = []
    if free < MINIMUM_HOST_FREE:
        found.append(f"Working has {free // GIB} GiB free; 100 GiB is protected")
    if state_size > STATE_LIMIT:
        found.append(f"Run state uses {state_size // GIB} GiB; limit is 60 GiB")
    if image_sizes and max(image_sizes) > IMAGE_LIMIT:
        found.append(f"one Docker image uses {max(image_sizes) // GIB} GiB; Candidate limit is 12 GiB")
    for row in rows:
        limit = LIMITS[mode].get(row.get("Type", ""))
        size = bytes_in(row.get("Size", ""))
        if limit is not None and size is not None and size > limit:
            found.append(f"Docker {row['Type']} uses {row['Size']}; {mode} limit is {limit // GIB} GiB")
    return found


def docker_rows() -> list[Mapping[str, str]]:
    result = subprocess.run(
        ["docker", "system", "df", "--format", "{{json .}}"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("Docker storage is unavailable; start the pinned Colima VM")
    return [json.loads(line) for line in result.stdout.splitlines()]


def docker_image_sizes() -> tuple[int, ...]:
    result = subprocess.run(
        ["docker", "image", "ls", "--format", "{{json .}}"], capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise RuntimeError("Docker image inventory is unavailable")
    return tuple(
        size for line in result.stdout.splitlines() if (size := bytes_in(json.loads(line).get("Size", ""))) is not None
    )


def directory_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and not path.is_symlink())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=LIMITS)
    mode = parser.parse_args(argv).mode

    location_drift = runtime.storage_drift(
        Path.home() / ".colima",
        PROJECT_ROOT,
        volume_mounted=WORKING.is_mount(),
        mount_reaches_vm=None,
    )
    if location_drift:
        for line in location_drift:
            print(f"storage refusal: {line}", file=sys.stderr)
        return 1

    state = PROJECT_ROOT / "incypher-ctf" / "state"
    found = violations(
        mode,
        docker_rows(),
        shutil.disk_usage(WORKING).free,
        state_size=directory_size(state),
        image_sizes=docker_image_sizes(),
    )
    for line in found:
        print(f"storage refusal: {line}", file=sys.stderr)
    if found:
        return 1
    print(f"host storage is inside the {mode} budget")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
