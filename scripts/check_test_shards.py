"""Prove the CI test shards are an exact partition of tracked test modules."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tests" / "shards.toml"
SHARDS = (
    "foundation/state",
    "authority/Board",
    "autonomy/inference",
    "tools/integration",
)


def tracked_tests(root: Path = ROOT) -> tuple[str, ...]:
    found = subprocess.run(
        ("git", "-C", str(root), "ls-files", "--", "tests"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    collected = (
        file
        for file in found
        if file.endswith(".py") and (Path(file).name.startswith("test_") or Path(file).name.endswith("_test.py"))
    )
    return tuple(sorted(collected))


def load_shards(path: Path = MANIFEST) -> dict[str, tuple[str, ...]]:
    with path.open("rb") as source:
        raw = tomllib.load(source)
    declared = raw.get("shards")
    if not isinstance(declared, dict):
        raise ValueError("manifest must contain one [shards] table")
    return {
        name: tuple(files) if isinstance(files, list) and all(isinstance(file, str) for file in files) else ()
        for name, files in declared.items()
    }


def partition_errors(shards: dict[str, tuple[str, ...]], tracked: tuple[str, ...]) -> tuple[str, ...]:
    errors: list[str] = []
    names = tuple(shards)
    if names != SHARDS:
        errors.append(f"shards must be declared in this order: {', '.join(SHARDS)}")

    listed = tuple(file for name in SHARDS for file in shards.get(name, ()))
    counts = Counter(listed)
    duplicates = sorted(file for file, count in counts.items() if count > 1)
    missing = sorted(set(tracked) - set(listed))
    unexpected = sorted(set(listed) - set(tracked))
    empty = [name for name in SHARDS if not shards.get(name)]

    if empty:
        errors.append("empty shards: " + ", ".join(empty))
    if duplicates:
        errors.append("assigned more than once: " + ", ".join(duplicates))
    if missing:
        errors.append("tracked tests not assigned: " + ", ".join(missing))
    if unexpected:
        errors.append("manifest entries are not tracked tests: " + ", ".join(unexpected))
    return tuple(errors)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", choices=SHARDS, help="print one validated shard, one path per line")
    arguments = parser.parse_args(argv)

    shards = load_shards()
    errors = partition_errors(shards, tracked_tests())
    if errors:
        for error in errors:
            print(f"test shard manifest: {error}")
        return 1

    if arguments.shard:
        print(*shards[arguments.shard], sep="\n")
    else:
        print(f"{sum(map(len, shards.values()))} tracked test modules assigned exactly once across four shards")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
