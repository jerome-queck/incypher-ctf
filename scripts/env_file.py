"""Reading an env file — the one place that decides what a line means.

Two callers parsed this format independently, line for line the same, and both carried the same two
faults: `export NAME=value` became a variable called `export NAME`, and `NAME=""` became two
characters of content. In the credential reporter those are the failure
[#61](https://github.com/jerome-queck/incypher-ctf/issues/61) exists to prevent, once in each
direction — a secret we hold reported absent, and one we do not hold reported set. In
`ctfd_probe.load_env` the first of them would have started the Solver without a credential that was
sitting in the file.

Matching parsers were never the goal; one parser is. What a line means is decidable offline with no
board and no network, which is what makes it worth a seam of its own
([#22](https://github.com/jerome-queck/incypher-ctf/issues/22)).
"""

import hashlib
import os
import tempfile
from pathlib import Path

from solver.env_cutover import EnvCutoverResult, completed_cutover
from solver.env_file import assignments as _assignments

assignments = _assignments

_TEMPLATE_NAME = ".env.example"


class AmbiguousEnvironmentAuthority(ValueError):
    """Raised when a legacy environment overlay still claims authority."""


def assert_unambiguous(path: Path) -> None:
    """Reject legacy env overlays that would make the active file's authority unclear."""

    if path.name != ".env":
        raise AmbiguousEnvironmentAuthority("the sanctioned environment authority must be named .env")
    legacy = sorted(
        candidate.name
        for candidate in path.parent.glob(".env.*")
        if candidate.name != _TEMPLATE_NAME and candidate.is_file()
    )
    if legacy:
        raise AmbiguousEnvironmentAuthority(f"legacy environment authority: {', '.join(legacy)}")


def _fsync_parent(directory: Path) -> None:
    """Flush the directory entry when the operating system supports directory fsync."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError:
        return
    try:
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
    finally:
        os.close(directory_fd)


def atomic_replace(path: Path, text: str) -> EnvCutoverResult:
    """Replace an active env file atomically, refusing legacy overlays first."""

    assert_unambiguous(path)

    descriptor, temporary = tempfile.mkstemp(prefix=".setup-env-replace-", dir=path.parent)
    replaced = False
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            descriptor = -1
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        replaced = True
        _fsync_parent(path.parent)
        return completed_cutover(path, hashlib.sha256(text.encode("utf-8")).hexdigest())
    finally:
        if not replaced:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        if descriptor != -1:
            os.close(descriptor)
