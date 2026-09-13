"""Live credential names, historical scanner names, and non-secret Run configuration.

It lives here rather than in `scripts/` because of who reads it: the redactor runs *inside* the
image, and the image copies `solver/` and nothing else. A second copy beside the container would
be a rule that exists in two places, which is a rule that will disagree — and the direction it
would disagree in is a credential declared for the reporter and not for the redactor.

The live and historical sets are deliberately distinct: removed inference variables must be
refused at Boot while their old values remain scanner vocabulary for historical evidence.

[ADR-0010](../docs/adr/0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)
requires a test asserting this set covers *"every secret variable in `.env.example`"*. Binding it
to the template is `scripts/declared_secrets.py`, which reads a file that exists in the repository
and not in the image; the names themselves are here, so both readers have one source.
"""

from __future__ import annotations

# Live environment secrets: never printed, logged, or committed.
SECRETS = (
    "CTFD_API_TOKEN",
    "CPA_TOKEN",
    "TEAM_KEY",
)

# Names removed from the live credential surface by ADR-0040. They remain scanner vocabulary so
# promoted historical evidence can still be checked against credentials that older Runs held.
HISTORICAL_SECRETS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
)
SCANNED_SECRETS = (*SECRETS, *HISTORICAL_SECRETS)
FORBIDDEN_ENVIRONMENT = (*HISTORICAL_SECRETS, "CODEX_HOME_METERED")

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
# so `$CODEX_HOME/auth.json` is readable no matter what is in this tuple; that hole is accepted for
# v1 and named in `docs/credentials.md`.
#
# Two corrections a live Run forced, because this comment used to name the wrong actor and the wrong
# remedy (#144). The reader it should describe is not challenge code but **the model** — on
# `compfest-2026-seg1` it was the agent's own `rg` over `/state/runs` that read the Run's record,
# and a boundary drawn around challenge-supplied code would not have been in its way. And uid
# separation is not the remedy on `/state`: Colima's bind mount does not enforce modes, so a
# root-owned `0700` directory there is read by any uid while `stat` reports it as protected.
# Measured, not assumed — the same test blocks on the container filesystem, which is what makes it
# the mount and not the mode.
CHILD_ENVIRONMENT = ("PATH", "HOME", "TERM", "LANG")
