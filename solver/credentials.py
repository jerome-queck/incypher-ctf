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
# needs to tell "played badly" from "played the wrong board". The other three are the same class of
# thing — which Run this is, how long it may last, and which model it thinks with — and every one of
# them is a field a post-mortem reads rather than a value anyone could spend.
#
# They are here rather than in a second list beside the boot check for the reason the secrets are:
# `solver/boot.py` reads *this* tuple to decide what it is looking at, so a name declared in
# `.env.example` and forgotten in one of the two places is a failing test rather than a Run that
# started short a value (ADR-0010).
NOT_SECRETS = ("CTFD_URL", "RUN_ID", "RUN_SECONDS", "CODEX_MODEL")

# The complement, and the reason it lives beside the two lists above rather than in either module
# that spawns a child: **every** child gets exactly these four names and nothing else. The recon
# cascade and the vendor's agent both run challenge-supplied code as root in this container, and an
# allowlist written twice is an allowlist that will one day differ — in the direction where one of
# them inherits a credential. A name is added here or it reaches no child at all.
#
# `CTFD_URL` is deliberately absent even though it is not a secret: it is the address of the Board,
# and the model holding it is half of what "Codex never touches the Board" rules out (ADR-0014).
#
# What this does **not** cover is a credential on disk. Root walks through any file-permission fix,
# so `$CODEX_HOME/auth.json` is readable by challenge code no matter what is in this tuple; that
# hole is accepted for v1 and named in `docs/credentials.md`, and the boundary that closes it is
# v2's uid separation.
CHILD_ENVIRONMENT = ("PATH", "HOME", "TERM", "LANG")
