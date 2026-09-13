"""Verify an installed Sage environment against its exact package/licence lock."""

import argparse
import json
from pathlib import Path


def verify(prefix: Path, lock_path: Path, explicit_path: Path) -> None:
    lock = json.loads(lock_path.read_text())
    expected = {(row["name"], row["version"], row["build_string"]): row for row in lock["packages"]}
    explicit = {line for line in explicit_path.read_text().splitlines() if line and not line.startswith("@")}
    locked_explicit = {f"{row['url']}#{row['sha256']}" for row in expected.values()}
    if explicit != locked_explicit:
        raise ValueError("explicit Sage closure differs from its metadata lock")
    installed = {}
    for path in sorted((prefix / "conda-meta").glob("*.json")):
        row = json.loads(path.read_text())
        identity = (row["name"], row["version"], row["build"])
        installed[identity] = row
    if set(installed) != set(expected):
        raise ValueError("installed Sage closure differs from its exact lock")
    for identity, locked in expected.items():
        observed = installed[identity]
        for installed_field, lock_field in (
            ("license", "license"),
            ("sha256", "sha256"),
            ("url", "url"),
            ("size", "size"),
        ):
            if observed.get(installed_field) != locked.get(lock_field):
                raise ValueError(f"installed Sage {lock_field} differs from its exact lock: {identity[0]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("prefix", type=Path)
    parser.add_argument("lock", type=Path)
    parser.add_argument("explicit", type=Path)
    arguments = parser.parse_args()
    verify(arguments.prefix, arguments.lock, arguments.explicit)


if __name__ == "__main__":
    main()
