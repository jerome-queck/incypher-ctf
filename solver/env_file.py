"""One parser and source inventory for host environment authority files."""

from __future__ import annotations

import re
from pathlib import Path


TEMPLATE_NAME = ".env.example"
_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def assignment_rows(text: str) -> tuple[tuple[str, str], ...]:
    """Return every assignment in source order, preserving repeated values."""

    found = []
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        if match := _ASSIGNMENT.match(line):
            found.append((match.group(1), _unquote(match.group(2).strip())))
    return tuple(found)


def assignments(text: str) -> dict[str, str]:
    """Return effective assignments, with later lines winning."""

    return dict(assignment_rows(text))


def environment_sources(directory: Path) -> tuple[Path, ...]:
    """Inventory the active env file followed by every non-template overlay."""

    active = directory / ".env"
    overlays = sorted(path for path in directory.glob(".env.*") if path.name != TEMPLATE_NAME)
    return ((active,) if active.exists() else ()) + tuple(overlays)
