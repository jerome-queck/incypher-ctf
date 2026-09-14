"""Reclaim disposable Docker storage after a merged pull request.

This command is deliberately separate from build, qualification, and Run admission. It requires
the caller to name a pull request and verifies that GitHub reports the squash merge as complete
before touching Docker storage.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from typing import Any

import runtime


CommandRunner = Callable[..., Any]
REPOSITORY = "jerome-queck/incypher-ctf"
ROOT = Path(__file__).resolve().parent.parent


def reclamation_commands() -> tuple[tuple[str, ...], ...]:
    """Return the only disposable-storage operations this command may execute."""

    return (
        ("docker", "builder", "prune", "--all", "--force"),
        ("docker", "image", "prune", "--force"),
    )


def _merge_commit_oid(value: object) -> str:
    if isinstance(value, Mapping):
        value = value.get("oid")
    return value if isinstance(value, str) else ""


def _require_merged_pr(pr: str, runner: CommandRunner) -> None:
    command = ("gh", "pr", "view", pr, "--repo", REPOSITORY, "--json", "state,mergedAt,mergeCommit")
    try:
        result = runner(command, capture_output=True, text=True, check=False)
        payload = json.loads(result.stdout)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise RuntimeError(f"cannot verify pull request #{pr} merge state") from error
    if getattr(result, "returncode", 1) != 0 or not isinstance(payload, Mapping):
        raise RuntimeError(f"cannot verify pull request #{pr} merge state")
    if (
        payload.get("state") != "MERGED"
        or not payload.get("mergedAt")
        or not _merge_commit_oid(payload.get("mergeCommit"))
    ):
        raise RuntimeError(f"pull request #{pr} is not verified as merged")


def _read(command: tuple[str, ...], runner: CommandRunner) -> str:
    result = runner(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("cannot verify that ticket work is complete")
    return result.stdout


def _require_completed_work(runner: CommandRunner) -> None:
    open_prs = json.loads(
        _read(("gh", "pr", "list", "--repo", REPOSITORY, "--state", "open", "--json", "number"), runner)
    )
    if not isinstance(open_prs, list) or open_prs:
        raise RuntimeError("open pull requests still need their build cache")
    inventory = _read(("git", "-C", str(ROOT), "worktree", "list", "--porcelain"), runner)
    paths = [line.removeprefix("worktree ") for line in inventory.splitlines() if line.startswith("worktree ")]
    if not paths:
        raise RuntimeError("cannot verify repository worktrees")
    for path in paths:
        if _read(("git", "-C", path, "status", "--porcelain"), runner).strip():
            raise RuntimeError(f"unfinished work remains in {path}")
    if _read(("docker", "ps", "--quiet"), runner).strip():
        raise RuntimeError("running containers still use this Docker store")


def _run(command: Sequence[str], runner: CommandRunner) -> None:
    try:
        result = runner(command, check=False)
    except OSError as error:
        raise RuntimeError(f"reclamation command failed: {' '.join(command)}") from error
    if getattr(result, "returncode", 1) != 0:
        raise RuntimeError(f"reclamation command failed: {' '.join(command)}")


def main(argv: list[str] | None = None, *, runner: CommandRunner | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr", required=True, help="merged pull request number that authorizes reclamation")
    arguments = parser.parse_args(argv)
    if not arguments.pr.isdigit() or int(arguments.pr) < 1:
        parser.error("--pr must be a positive pull request number")

    command_runner = runner or subprocess.run
    try:
        _require_merged_pr(arguments.pr, command_runner)
        _require_completed_work(command_runner)
        if runtime.verify() != 0:
            raise RuntimeError("runtime is not on its grow-only pin; no Docker storage was reclaimed")
        for command in reclamation_commands():
            _run(command, command_runner)
    except (RuntimeError, OSError, ValueError) as error:
        print(f"storage reclamation refused: {error}", file=sys.stderr)
        return 1
    print(f"reclaimed disposable Docker storage after merged PR #{arguments.pr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
