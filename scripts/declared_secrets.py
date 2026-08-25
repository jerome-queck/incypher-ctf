"""The one list of credential variable names, and which of them must never be shown.

[ADR-0010](../docs/adr/0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)
requires a test asserting the redactor's declared set covers *"every secret variable in
`.env.example`"*, so that adding a credential to the template and forgetting the redactor is a
failing check rather than a leaked run. That requirement is only as strong as the phrase is
unambiguous, and the template alone answers it three ways: `^[A-Z_]+=` finds four names, counting
the commented-out overlay-only keys finds six, and neither count is right, because `CTFD_URL` is a
variable and not a secret. The two keys the obvious parser drops are the two that spend money.

So the list lives here rather than in whatever reads the template next, and the reporter, the
redactor and ADR-0010's boot check all read this one ([#61](https://github.com/jerome-queck/incypher-ctf/issues/61)).
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Never printed, never logged, never committed. Broader than the template on purpose: over-covering
# costs a mangled Observation, under-covering costs a leaked run, so a name that is plausibly in
# play belongs here even where `.env.example` does not declare it. `ANTHROPIC_AUTH_TOKEN` is the
# standing example — the template names it in prose as the variable to use when the Solver calls the
# Messages API directly, and prose is not a declaration.
SECRETS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CTFD_API_TOKEN",
    "OPENAI_API_KEY",
    "TEAM_KEY",
)

# Declared in the template and deliberately *not* secret. `CTFD_URL` is the guard that decides which
# competition the Solver enters, so a record that redacted it would hide the one field a post-mortem
# needs to tell "played badly" from "played the wrong board".
NOT_SECRETS = ("CTFD_URL",)

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
