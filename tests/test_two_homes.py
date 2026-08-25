"""`/state` holds everything, and the Solver never touches git.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md)'s *Two homes*: a
commit mid-Run costs resources during the scored 5.5 hours and writes outside the one-way pipe
ADR-0008 established. Promotion to `runs/<run_id>.jsonl` is a separate step afterwards, run by a
human against a container that is already dead.

The rule is kept mechanically at the image — a binary that is not installed cannot be invoked at
14:00 — and structurally in the package, so a module cannot acquire the habit before someone
notices the `Dockerfile` never gave it the tool.
"""

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOLVER_MODULES = sorted((REPO_ROOT / "solver").glob("*.py"))
_CAN_BE_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def test_the_image_installs_no_version_control():
    """The package list is the whole inventory — nothing is installed at run time, because the
    venue network at 14:00 is not a dependency this repository gets to have."""
    installed = REPO_ROOT / "Dockerfile"

    assert "git" not in installed.read_text().split(), "the Dockerfile named a git package"


def _would_run_git(literal: str) -> bool:
    """Whether a string literal could reach the binary — the program on its own, or at the head of
    a command line.

    Deliberately not a substring search. `--skip-git-repo-check` is the standing counter-example:
    it is the flag telling the vendor's CLI that it does **not** need a worktree
    (`solver/codex.py`), which is this rule being kept rather than broken, and a substring search
    reads it as the opposite of what it is.
    """
    return any(word == "git" or word.endswith("/git") for word in literal.split())


@pytest.mark.parametrize("module", SOLVER_MODULES, ids=lambda path: path.name)
def test_no_solver_module_reaches_for_git(module: Path):
    """Read from the syntax tree rather than by grepping the file: this repository's modules
    explain *why* they never commit, and a text search would find the explanation."""
    tree = ast.parse(module.read_text())
    for node in list(ast.walk(tree)):
        if isinstance(node, _CAN_BE_DOCUMENTED) and ast.get_docstring(node):
            node.body = node.body[1:]

    named = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]

    assert not [literal for literal in named if _would_run_git(literal)]
