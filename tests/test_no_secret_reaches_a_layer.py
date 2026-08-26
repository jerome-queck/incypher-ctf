"""Nothing is baked into an image layer but the three things ADR-0008 names, and no secret ever is.

A layer is readable by anyone holding the image, survives a later `RUN rm`, and travels with every
push — unrecoverable in precisely the way the team key already is. ADR-0010 records the refusal
rather than the preference: *if `--env-file` is refused we do not compete rather than bake a key
into a layer*. These are the checks that make the allowlist keep saying so.

The `.dockerignore` model below is a model, and its job is to catch the drift the eye misses: a
re-inclusion whose parent directory is still excluded ships nothing, and a widened one ships an env
file. The build itself is proved in `.github/workflows/ci.yml`, which boots the image.
"""

import fnmatch
from pathlib import Path

import pytest
from solver import profile
from solver.credentials import SECRETS

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
TRACKED = ROOT / "docs" / "competitions"


def instructions() -> list[str]:
    """The Dockerfile with its prose removed — a secret named in a comment is documentation, and a
    secret named in an instruction is a layer."""
    return [line for line in DOCKERFILE.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")]


def patterns() -> list[str]:
    return [line.strip() for line in DOCKERIGNORE.read_text().splitlines() if line.strip() and not line.startswith("#")]


def admitted(path: str) -> bool:
    """Whether the build context would carry this path, by Docker's own rule: every pattern is
    tried, a pattern matching a prefix of the path reaches everything under it, and the **last**
    match wins."""
    kept = True
    for pattern in patterns():
        negated = pattern.startswith("!")
        if _prefix_match(path, pattern.lstrip("!")):
            kept = negated
    return kept


def _prefix_match(path: str, pattern: str) -> bool:
    parts, wanted = path.split("/"), pattern.split("/")
    if len(wanted) > len(parts):
        return False
    return all(fnmatch.fnmatchcase(part, one) for part, one in zip(parts, wanted))


@pytest.mark.parametrize("name", SECRETS)
def test_no_declared_secret_is_named_by_an_instruction(name):
    """Not an `ARG`, not an `ENV`, not a `COPY`, and never will be. Credentials arrive by
    `--env-file` at run time, which is what lets the image be handed over without handing over the
    one secret we could not rotate."""
    assert not [line for line in instructions() if name in line]


@pytest.mark.parametrize("secret", [".env", ".env.incypher", ".env.brunner", "state/runs/gate/stream.jsonl"])
def test_the_build_context_carries_no_env_file_and_no_run_state(secret):
    assert not admitted(secret)


def test_the_three_things_adr_0008_bakes_in_are_the_three_things_the_context_carries():
    assert admitted("solver/board.py")
    assert admitted("docs/competitions/brunnerctf-2026-global.board.json")
    # Our reading of an event and the verbatim rules snapshot are repository files, not image ones.
    assert not admitted("docs/competitions/brunnerctf-2026-global.md")
    assert not admitted("docs/competitions/brunnerctf-2026-global.rules.txt")


def test_every_tracked_board_profile_is_admitted_and_copied():
    """One image for every Board (ADR-0008), so every event's profile is baked and `CTFD_URL`
    selects between them — an image carrying one event's rules would be an image per event."""
    tracked = sorted(one.name for one in TRACKED.glob(f"*{profile.SUFFIX}"))

    assert tracked, "no event has a tracked Board profile"
    assert all(admitted(f"docs/competitions/{name}") for name in tracked)
    assert [line for line in instructions() if line.startswith("COPY") and profile.SUFFIX in line]


def test_the_image_looks_for_its_profiles_where_the_dockerfile_puts_them():
    copied = next(line for line in instructions() if line.startswith("COPY") and profile.SUFFIX in line)
    workdir = next(line for line in instructions() if line.startswith("WORKDIR")).split()[1]

    assert str(profile.BOARDS) == f"{workdir}/{copied.split()[-1].rstrip('/')}"


def test_pid_one_is_the_solver_itself_with_no_supervisor_in_front_of_it():
    """v1's gate has to tell a clean exit from a restart loop, and anything between the process and
    the container blurs exactly that. Exec form, so no shell sits in front of it either."""
    entrypoints = [line for line in instructions() if line.startswith("ENTRYPOINT")]

    assert entrypoints == ['ENTRYPOINT ["python3", "-m", "solver"]']
    assert not [line for line in instructions() if line.startswith("CMD")]
