"""`/state` holds everything, and the Solver never touches git.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md)'s *Two homes*: a
commit mid-Run costs resources during the scored 5.5 hours and writes outside the one-way pipe
ADR-0008 established. Promotion to `runs/<run_id>.jsonl` is a separate step afterwards, run by a
human against a container that is already dead.

The rule had two enforcements and now has one.
[ADR-0024](../docs/adr/0024-the-image-carries-what-a-run-reached-for-and-a-picture-is-attached.md)
put `git` in the image for the **model**, which reached for it more often than any other binary it
could not find, so the mechanical half — a binary that is not installed cannot be invoked at 14:00
— is gone. What is left is the structural half below, and it is the half that was ever about this
rule: ADR-0009's sentence is about what *the Solver* does, and a Challenge shipping a leaked `.git`
neither commits our record nor writes outside `/state`.
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SOLVER_MODULES = sorted((REPO_ROOT / "solver").glob("*.py"))
_CAN_BE_DOCUMENTED = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _packages_installed() -> list[str]:
    """Every package name the `Dockerfile` hands to `apt-get install`, read off the continuation
    lines rather than matched as a literal — a test that pins six spaces and a backslash goes red
    the day somebody reindents the file, which teaches nobody anything."""
    listed = (REPO_ROOT / "Dockerfile").read_text().splitlines()
    return [word for line in listed if (word := line.strip().removesuffix("\\").strip()) and _is_package(line, word)]


def _is_package(line: str, word: str) -> bool:
    return (
        line.startswith("      ")
        and not word.startswith("#")
        and re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", word) is not None
    )


def test_the_image_gives_git_to_the_model_and_never_to_the_solver():
    """The inverse of what this file asserted until ADR-0024, and the reversal is pinned rather
    than merely allowed: `git` is on the list on purpose, so a later tidy-up that drops it argues
    with a red test instead of quietly closing a Challenge genre again. What still binds *us* is
    the AST guard below, which is the half of ADR-0009's rule that was ever about the Solver.

    The package list is still the whole inventory — nothing is installed at run time, because the
    venue network at 14:00 is not a dependency this repository gets to have."""
    assert "git" in _packages_installed(), "the Dockerfile no longer names the git package"


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
