"""The one list of credential variable names, and which of them must never be shown.

It lives here rather than in `scripts/` because of who reads it: the redactor runs *inside* the
image, and the image copies `solver/` and nothing else. A second copy beside the container would
be a rule that exists in two places, which is a rule that will disagree — and the direction it
would disagree in is a credential declared for the reporter and not for the redactor.

Both tuples travel together even though only the first is read in the image. They are one
classification rather than two lists — a name belongs to exactly one of them, and splitting them
across files is what would let a name end up in both or in neither.

[ADR-0010](../docs/adr/0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)
requires a test asserting this set covers *"every secret variable in `.env.example`"*. Binding it
to the template is `scripts/declared_secrets.py`, which reads a file that exists in the repository
and not in the image; the names themselves are here, so both readers have one source.
"""

from __future__ import annotations

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
