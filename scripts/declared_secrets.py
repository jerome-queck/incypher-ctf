"""Bind `.env.example` to the declared credential set, which lives in `solver/credentials.py`.

The names are not here because the redactor that reads them runs inside the image, and the image
copies `solver/` alone. What is here is the half that only makes sense in a checkout: the template
is a repository file, and no container ever sees one.

[ADR-0010](../docs/adr/0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)'s
requirement is only as strong as its phrase is unambiguous, and the template alone answers it three
ways: `^[A-Z_]+=` finds four names, counting the commented-out overlay-only keys finds six, and
neither count is right, because `CTFD_URL` is a variable and not a secret. The two keys the obvious
parser drops are the two that spend money. So what a declaration *is* is decided once, here
([#61](https://github.com/jerome-queck/incypher-ctf/issues/61)).
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# `python3 scripts/credentials_held.py` puts `scripts/` on the import path and not the repository
# root, so the package this module consumes has to be pointed at — the same reason
# `ctfd_probe.py` does it, and the same payoff: the reporter and the redactor read one list.
sys.path.insert(0, str(REPO_ROOT))

from solver.credentials import NOT_SECRETS, SECRETS  # noqa: E402

__all__ = ["NOT_SECRETS", "REPO_ROOT", "SECRETS", "template_declarations"]

# A declaration is an assignment, live or commented out — `NAME=` at the start of a line, or behind
# a `#`. It is deliberately not "a line mentioning a name": the template explains itself at length
# and several of its sentences name variables, so matching prose would declare credentials that do
# not exist.
_DECLARATION = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=")


def template_declarations(path: Path) -> set[str]:
    """Every variable `.env.example` declares, counting the ones commented out to keep them empty."""
    if not path.exists():
        return set()
    return {match.group(1) for line in path.read_text().splitlines() if (match := _DECLARATION.match(line))}
