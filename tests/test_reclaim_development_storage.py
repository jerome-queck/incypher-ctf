from types import SimpleNamespace

import reclaim_development_storage as reclaim


def test_reclamation_is_limited_to_build_cache_and_dangling_images() -> None:
    assert reclaim.reclamation_commands() == (
        ("docker", "builder", "prune", "--all", "--force"),
        ("docker", "image", "prune", "--force"),
    )


def test_reclamation_refuses_before_any_docker_command_when_pr_is_open(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command, **_kwargs):
        calls.append(tuple(command))
        return SimpleNamespace(returncode=0, stdout='{"state":"OPEN","mergedAt":null,"mergeCommit":null}', stderr="")

    monkeypatch.setattr(reclaim.runtime, "verify", lambda: 0)

    assert reclaim.main(["--pr", "376"], runner=runner) == 1
    assert calls == [("gh", "pr", "view", "376", "--repo", reclaim.REPOSITORY, "--json", "state,mergedAt,mergeCommit")]


def test_reclamation_checks_merged_pr_then_runtime_before_pruning(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command, **_kwargs):
        calls.append(tuple(command))
        if command[:3] == ("gh", "pr", "list"):
            return SimpleNamespace(returncode=0, stdout="[]")
        if "worktree" in command:
            return SimpleNamespace(returncode=0, stdout=f"worktree {reclaim.ROOT}\n")
        if command[0] == "gh":
            return SimpleNamespace(
                returncode=0,
                stdout='{"state":"MERGED","mergedAt":"2026-09-15T00:00:00Z","mergeCommit":{"oid":"abc"}}',
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(reclaim.runtime, "verify", lambda: calls.append(("runtime.verify",)) or 0)

    assert reclaim.main(["--pr", "376"], runner=runner) == 0
    assert calls == [
        ("gh", "pr", "view", "376", "--repo", reclaim.REPOSITORY, "--json", "state,mergedAt,mergeCommit"),
        ("gh", "pr", "list", "--repo", reclaim.REPOSITORY, "--state", "open", "--json", "number"),
        ("git", "-C", str(reclaim.ROOT), "worktree", "list", "--porcelain"),
        ("git", "-C", str(reclaim.ROOT), "status", "--porcelain"),
        ("docker", "ps", "--quiet"),
        ("runtime.verify",),
        ("docker", "builder", "prune", "--all", "--force"),
        ("docker", "image", "prune", "--force"),
    ]


def test_reclamation_preserves_cache_for_another_open_ticket(monkeypatch):
    calls = []

    def runner(command, **_kwargs):
        calls.append(tuple(command))
        if command[:3] == ("gh", "pr", "view"):
            output = '{"state":"MERGED","mergedAt":"2026-09-15T00:00:00Z","mergeCommit":{"oid":"abc"}}'
        elif command[:3] == ("gh", "pr", "list"):
            output = '[{"number":375}]'
        else:
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(reclaim.runtime, "verify", lambda: 0)
    assert reclaim.main(["--pr", "376"], runner=runner) == 1
    assert not any("prune" in command for command in calls)


def test_reclamation_preserves_a_dirty_sibling_worktree(monkeypatch):
    calls = []

    def runner(command, **_kwargs):
        calls.append(tuple(command))
        if command[:3] == ("gh", "pr", "view"):
            output = '{"state":"MERGED","mergedAt":"2026-09-15T00:00:00Z","mergeCommit":{"oid":"abc"}}'
        elif command[:3] == ("gh", "pr", "list"):
            output = "[]"
        elif "worktree" in command:
            output = "worktree /primary\n\nworktree /unfinished ticket\n"
        elif command[2] == "/unfinished ticket":
            output = " M solver/run.py\n"
        else:
            output = ""
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(reclaim.runtime, "verify", lambda: 0)
    assert reclaim.main(["--pr", "376"], runner=runner) == 1
    assert not any("prune" in command for command in calls)
