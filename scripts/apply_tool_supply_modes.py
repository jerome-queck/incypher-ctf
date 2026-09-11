"""Restore locked Tool file modes after Git transports the generated rootfs."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from solver.image_path import image_path


def apply_modes(inventory_path: Path, root: Path) -> int:
    inventory = json.loads(inventory_path.read_text())
    changed = 0
    try:
        files = [item for component in inventory["components"] for item in component["files"]]
        for item in files:
            destination = item["destination"]
            mode = item["mode"]
            if not isinstance(destination, str) or not isinstance(mode, str) or not re.fullmatch(r"0[0-7]{3}", mode):
                raise ValueError("Tool inventory contains an invalid destination or mode")
            target = image_path(root, destination)
            if not target.is_file() or target.is_symlink():
                raise ValueError(f"Tool destination is not a regular file: {destination}")
            os.chmod(target, int(mode, 8))
            changed += 1
    except (KeyError, TypeError) as error:
        raise ValueError("Tool inventory does not contain a file-mode closure") from error
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/"))
    arguments = parser.parse_args()
    if apply_modes(arguments.inventory, arguments.root) < 1:
        raise ValueError("Tool inventory contains no files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
