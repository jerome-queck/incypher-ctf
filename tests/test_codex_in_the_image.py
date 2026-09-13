"""The vendor's agent ships in the image, at a version nobody can move under us.

`solver/codex.py` lands with a working adapter and takes the executable name as a config value, so
what was missing was never code: it was a binary. Inside a container without one, every Attempt of
a Run is `[codex] codex did not run — No such file or directory`, correctly recorded, on every
Challenge — a five-and-a-half-hour Run that does nothing and reports nothing wrong.

The build itself is what proves the CLI answers, exercised on real input beside the recon floor.
What is left for here is the part a build cannot check about itself: that the thing it installed is
pinned, and pinned for **both** architectures rather than the one whoever wrote the line was on.
"""

import re
from pathlib import Path

from solver.codex import Invocation

DOCKERFILE = Path(__file__).resolve().parent.parent / "Dockerfile"

# BuildKit's own, set for the platform actually being built. The run-day image is arm64 and CI
# proves amd64, so these are the two that have to be mapped and they are Docker's spellings.
ARCHITECTURES = ("arm64", "amd64")


def instructions() -> str:
    return "\n".join(
        line for line in DOCKERFILE.read_text().splitlines() if line.strip() and not line.lstrip().startswith("#")
    )


def installed() -> list[str]:
    """Every binary the install loop puts on `PATH`, read off the loop rather than off a literal —
    the CLI arrived in two halves once already and a third is a list entry."""
    listed = re.search(r"for part in ([^;]+); do", instructions())
    return re.findall(r'"([a-z0-9-]+):', listed.group(1)) if listed else []


def test_the_executable_the_adapter_spawns_is_the_one_the_image_installs():
    """The adapter names it as config and the image puts it on `PATH`. Two names that drifted apart
    would be an image that builds green and a Run with no agent in it."""
    assert Invocation.executable == "codex"
    assert Invocation.executable in installed()
    assert '"/usr/local/bin/${name}"' in instructions()


def test_the_tool_host_ships_beside_the_cli():
    """From 0.147.0 the shell tool routes through a separate host binary, and without it every
    command the model tries fails before it runs while `codex --version` answers perfectly. Half a
    CLI is the shape this list exists to make impossible."""
    assert "codex-code-mode-host" in installed()


def test_the_version_is_pinned_rather_than_floating():
    """A vendor release between the gate image and the run-day image is the one window nobody is
    watching — the same argument the base image's digest pin makes."""
    pinned = re.search(r"ARG CODEX_VERSION=(\S+)", instructions())

    assert pinned, "no CODEX_VERSION is pinned"
    assert re.fullmatch(r"\d+\.\d+\.\d+", pinned.group(1)), pinned.group(1)
    assert "@latest" not in instructions()


def test_every_binary_is_mapped_to_its_own_bytes_on_every_architecture():
    """A per-architecture download is the trap: a URL naming one of them builds green on the runner
    and 404s on the machine that competes. One digest per binary per architecture, all distinct,
    because a digest covering two downloads is a digest checking nothing."""
    digests = re.findall(r"ARG (?:CODEX|CODE_MODE_HOST)_SHA256_[A-Z0-9]+=([0-9a-f]{64})", instructions())
    wanted = len(installed()) * len(ARCHITECTURES)

    assert len(digests) == wanted, f"{len(installed())} binaries x {len(ARCHITECTURES)} arches wants {wanted} digests"
    assert len(set(digests)) == wanted
    for architecture in ARCHITECTURES:
        assert re.search(rf"\b{architecture}\)\s", instructions()), f"{architecture} is not a case arm"


def test_an_architecture_nobody_mapped_fails_the_build_rather_than_shipping_without_an_agent():
    """Exhaustive rather than defaulted. The failure this prevents is an image that builds, runs,
    and has nothing to spawn — which is exactly the shape being fixed here."""
    assert re.search(r"\*\)\s*echo .*TARGETARCH.*>&2; exit 1", instructions())


def test_the_build_proves_the_binary_runs_and_is_the_one_that_was_pinned():
    """`command -v` passes for a binary that cannot run, and a binary that runs is not evidence it
    is the pinned one — so the probe layer asserts the version it prints."""
    assert re.search(r"codex --version \| grep -qF \"\$CODEX_VERSION\"", instructions())


def test_no_credential_reaches_a_layer_alongside_it():
    """`auth.json` arrives at `/state/codex` at run time, so the image is still handed over without
    handing over a login (`docs/credentials.md`, ADR-0011)."""
    assert "auth.json" not in instructions()
    assert not re.search(r"^(ARG|ENV)\s+CODEX_(HOME|API|ACCESS)", instructions(), re.MULTILINE)
