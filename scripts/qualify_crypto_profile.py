"""Build the clean-main baseline and bind Crypto's ADR-0057 size delta."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from solver.crypto_tool_contract import PROFILE_SIZE_BUDGET  # noqa: E402
from solver.crypto_tool_receipt import write_receipt  # noqa: E402


def command(*arguments: str) -> str:
    completed = subprocess.run(arguments, check=True, capture_output=True, text=True, cwd=REPO_ROOT)
    return completed.stdout.strip()


def image_identity(reference: str) -> str:
    return command("docker", "image", "inspect", "--format", "{{.Id}}", reference)


def image_platform(reference: str) -> str:
    return command("docker", "image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", reference)


def unpacked_size(reference: str) -> int:
    output = command("docker", "history", "--human=false", "--format", "{{.Size}}", reference)
    sizes = [int(line) for line in output.splitlines()]
    if not sizes:
        raise ValueError("Docker returned no image layers")
    return sum(sizes)


def git_object(commit: str, suffix: str) -> str:
    return command("git", "rev-parse", commit + suffix)


def git_blob(commit: str, path: str) -> bytes:
    return subprocess.run(["git", "show", f"{commit}:{path}"], check=True, capture_output=True, cwd=REPO_ROOT).stdout


def qualified_baseline_commit(requested: str | None) -> str:
    merge_base = command("git", "merge-base", "origin/main", "HEAD")
    selected = git_object(requested or merge_base, "^{commit}")
    if selected != merge_base:
        raise ValueError("Crypto baseline must be the branch's exact origin/main merge-base")
    return selected


@contextlib.contextmanager
def baseline_image(commit: str, platform: str) -> Iterator[str]:
    tag = f"incypher-crypto-baseline-{commit[:12]}-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="incypher-crypto-baseline-") as directory:
        checkout = Path(directory) / "source"
        command("git", "worktree", "add", "--detach", str(checkout), commit)
        try:
            subprocess.run(["docker", "build", "--platform", platform, "--tag", tag, "."], check=True, cwd=checkout)
            yield tag
        finally:
            subprocess.run(["docker", "image", "rm", tag], check=False, capture_output=True)
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(checkout)],
                check=False,
                capture_output=True,
                cwd=REPO_ROOT,
            )


def measurement(candidate: str, requested_baseline: str | None = None) -> dict[str, object]:
    commit = qualified_baseline_commit(requested_baseline)
    platform = image_platform(candidate)
    with baseline_image(commit, platform) as baseline:
        baseline_size = unpacked_size(baseline)
        candidate_size = unpacked_size(candidate)
        return {
            "baseline_image_digest": image_identity(baseline),
            "baseline_size_bytes": baseline_size,
            "baseline_source_commit": commit,
            "baseline_source_tree_digest": git_object(commit, "^{tree}"),
            "baseline_dockerfile_sha256": hashlib.sha256(git_blob(commit, "Dockerfile")).hexdigest(),
            "baseline_platform": platform,
            "candidate_image_digest": image_identity(candidate),
            "candidate_size_bytes": candidate_size,
            "delta_bytes": candidate_size - baseline_size,
            "budget_bytes": PROFILE_SIZE_BUDGET,
            "method": "docker-build-git-merge-base-history-unpacked-layer-sum-v1",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--baseline-commit")
    arguments = parser.parse_args()
    size = measurement(arguments.candidate_image, arguments.baseline_commit)
    destination = write_receipt(REPO_ROOT / "tool-supply", size)
    print(json.dumps({"receipt": str(destination), "profile_size": size}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
