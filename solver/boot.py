"""Everything the environment must hold before a Run starts, and the refusal when it does not.

The failure this exists to prevent is a container that comes up at 10:30 with one variable quietly
unset and runs the whole window on a credential that was never there — silent from inside, and
afterwards indistinguishable from bad luck. **A loud refusal at 10:15, with a human standing there,
is setup rather than Intervention** (`CONTEXT.md`, *Intervention*; ADR-0011).

So every credential is read **once**, here, before anything is spent. `Refusal` lives in this module
rather than beside the Board because this is where the first one is raised and because both halves
of the boot — what we hold and what the Board is — end the same way: the process exits before the
loop, saying which fact was missing.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from solver.codex import CODEX_HOME, Credential
from solver.board_broker_contracts import BOARD_BROKER_HOLDINGS_ENV, BOARD_BROKER_SOCKET_ENV, BOARD_PROFILE_HANDLE_ENV
from solver.credentials import FORBIDDEN_ENVIRONMENT, NOT_SECRETS, SECRETS

MARK = "[boot]"

# What the CLI writes when a login succeeds, and therefore the only evidence a rung is usable. Its
# presence is checked and its contents are never read: the file is the vendor's, and a credential
# this module opened is a credential this module could log.
AUTH = "auth.json"

SUBSCRIPTION = "codex-subscription"

# Native Codex uses one model for the Run; `CODEX_MODEL` overrides it without a rebuild. CPA model
# policy is owned by its separate Harness and route controller.
#
# **Which ids exist at all is account state, and it moves.** Read live from the container on
# 26 Aug 2026: `gpt-5` is refused outright — *"The 'gpt-5' model is not supported when using
# Codex"* — as `gpt-5-codex` was before it, and what the subscription actually serves is the 5.4
# through 5.6 families. So a default nobody checked against a live login is a default that fails
# every Attempt of a Run with nobody there, which is why this one was.
#
# Daybreak Blue rather than `gpt-5.6-sol` because of what the vendor says it is for — *"Latest
# frontier agentic coding model for broad defensive cybersecurity work"* — which is the work. The
# cost is honest: the id floats where a versioned one would not, so the model can move under us
# between the gate image and the run-day image in a way the base image's digest pin does not allow.
# It is a dial rather than an artefact, and one a practice Run is meant to measure.
DEFAULT_MODEL = "gpt-daybreak-blue-latest"

# Read here and not in `.env` alone, because `docker run --env-file` and a `-e` on the command line
# reach the same place. Every one of them is declared in `.env.example` and classified in
# `solver/credentials.py`, so a name added to the template is a name this check already knows.
RUN_ID = "RUN_ID"
RUN_SECONDS = "RUN_SECONDS"
CODEX_MODEL = "CODEX_MODEL"
CTFD_URL = "CTFD_URL"
CTFD_API_TOKEN = "CTFD_API_TOKEN"

# The two that must be there whatever Board this is: its address, and the token that reads it. A
# Board that answers an unauthenticated read is still refused here, because a Run that cannot submit
# is not a Run. Everything else in `SECRETS` is checked for **emptiness** rather than for presence,
# and is demanded only where that Board's own rules demand it — `Setup.must_hold`, from the profile.
REQUIRED = (CTFD_URL, CTFD_API_TOKEN)

# A `run_id` becomes a path under `/state/runs`, so a name that is not one component is refused
# rather than resolved: `..` would put a Run's window somewhere no restart would look for it.
NOT_A_COMPONENT = ("/", "\\", "..")

SET = "set"
EMPTY = "empty"
ABSENT = "absent"


class Refusal(Exception):
    """The Solver declining to start, before anything is spent.

    Its own type because it is the one exception the entry point is allowed to turn into a quiet
    exit code with a sentence: everything else is a crash and is recorded as one. It is never a
    **Cut** — a Cut ends an Attempt and says what stopped it (`CONTEXT.md`, *Cut*), and nothing has
    been attempted yet.
    """


@dataclass(frozen=True)
class Setup:
    """What one Run was pointed at, read once from the environment and never re-read.

    Named for what it is: **setup** is the repository's word for preparing a Run, and preparing one
    is explicitly not Intervention however manual it is (`CONTEXT.md`, *Intervention*). Not
    *settings* — that word is reserved away from the Board profile, and half the fields here are the
    Board's.

    Carries no credential value except the Board token, which is the one secret this process itself
    spends — the inference credentials are directories on disk, and the rest are names whose
    *holding* is recorded and whose values are never read at all.
    """

    url: str
    token: str
    run_id: str
    model: str
    chain: tuple[Credential, ...]
    # Set only where the operator shortened the window. It may never lengthen one, so a Run pointed
    # at an event is bounded by the event whatever this says.
    run_seconds: float | None
    # Per declared name: `set`, `empty` or `absent` — never a value. This is what makes ADR-0010's
    # "absence is the control" a thing a post-mortem can read: a practice Run that spent no money
    # and one that could not have is the same record only if the holding is written down.
    holdings: dict[str, str]

    def must_hold(self, needed: Sequence[str]) -> None:
        """Refuse where this Board's rules name a credential this environment does not hold.

        Which credentials a Run needs is not the same question on every Board — the team key gates
        IN-CYPHER's raw-TCP Challenges and Brunner has never heard of it — so *what* is required is a
        Board profile value and *whether we hold it* is this. Without the pairing, a Run on the Board
        that needs one starts anyway and discovers it three hours in, with nobody there.
        """
        short = sorted(name for name in needed if self.holdings.get(name, ABSENT) != SET)
        if short:
            raise Refusal(
                f"{MARK} this Board's rules require {', '.join(short)}, and this environment does not "
                f"hold {'them' if len(short) > 1 else 'it'}"
            )

    def recorded(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "model": self.model,
            "chain": [rung.slot for rung in self.chain],
            "run_seconds": self.run_seconds,
            "credentials_held": self.holdings,
        }


def setup(environ: Mapping[str, str], *, homes: Mapping[str, Path] | None = None) -> Setup:
    """Read the environment once, and refuse the Run rather than start it short a credential.

    The order of the refusals below is the design: everything the environment alone decides is read
    out first, so the refusal a human meets is one they can fix from the file already in front of
    them, and only then is the disk looked at. `homes` is the chain's directories, injected so a
    test can stand one somewhere real; production supplies only native Codex's private home.
    """
    if forbidden := sorted(name for name in FORBIDDEN_ENVIRONMENT if name in environ):
        raise Refusal(f"{MARK} unsupported inference credential configuration: {', '.join(forbidden)}")
    holdings = holdings_of(environ)
    if empties := sorted(name for name, held in holdings.items() if held == EMPTY):
        raise Refusal(
            f"{MARK} {', '.join(empties)} is set to an empty value, which is not the same as unset — "
            f"an empty key occupies its slot in a client's credential search and authenticates with "
            f"nothing. Delete the line rather than blanking it"
        )
    brokered = _broker_holdings(environ)
    if environ.get(BOARD_BROKER_SOCKET_ENV, "").strip() and not environ.get(BOARD_PROFILE_HANDLE_ENV, "").strip():
        raise Refusal(f"{MARK} Board-profile authority is absent")
    if missing := [name for name in REQUIRED if not environ.get(name, "").strip() and name not in brokered]:
        raise Refusal(
            f"{MARK} unset: {', '.join(missing)}. A Run cannot be pointed at a Board without both a "
            f"URL and a token, and an unauthenticated read would be a Run that cannot submit"
        )
    run_id, run_seconds = run_identity(environ), _run_seconds(environ)
    chain = _chain(environ.get(CODEX_MODEL, "").strip() or DEFAULT_MODEL, homes or {SUBSCRIPTION: CODEX_HOME})
    return Setup(
        url=environ[CTFD_URL].strip().rstrip("/"),
        token=environ.get(CTFD_API_TOKEN, "").strip(),
        run_id=run_id,
        model=chain[0].model,
        chain=chain,
        run_seconds=run_seconds,
        holdings=holdings,
    )


def holdings_of(environ: Mapping[str, str]) -> dict[str, str]:
    """Every declared name and whether this environment holds it — three states, never a value.

    The set is `solver/credentials.py`'s and is not re-listed here, so a credential added for the
    redactor is a credential this check already reads. That is the whole of the criterion: one list,
    two readers, and no way for a name to be declared in one place and forgotten in the other.
    """
    holdings = {name: _holding(environ, name) for name in (*SECRETS, *NOT_SECRETS)}
    for name in _broker_holdings(environ):
        holdings[name] = SET
    return holdings


def _broker_holdings(environ: Mapping[str, str]) -> frozenset[str]:
    if not environ.get(BOARD_BROKER_SOCKET_ENV, "").strip():
        return frozenset()
    named = {name for name in environ.get(BOARD_BROKER_HOLDINGS_ENV, "").split(",") if name}
    return frozenset(named & set(SECRETS))


def _holding(environ: Mapping[str, str], name: str) -> str:
    if name not in environ:
        return ABSENT
    return SET if environ[name].strip() else EMPTY


def run_identity(environ: Mapping[str, str]) -> str:
    """The Run's identity, from something that survives a boot — never minted here.

    `schedule.Window` stamps its absolute deadline under `state/runs/<run_id>` and consults the
    configured duration **only when there is no window on disk**, so a `run_id` generated at startup
    hands every restart a fresh window and the absolute-deadline guarantee evaporates with nobody
    there to see it (`CONTEXT.md`, *Run* — a Run survives a restart). A timestamp and a uuid are
    both generated at startup, so neither is available here: the value comes from the env file or
    the command line, or the Run does not start.
    """
    run_id = environ.get(RUN_ID, "").strip()
    if not run_id:
        raise Refusal(
            f"{MARK} {RUN_ID} is not set. It cannot be minted here — a fresh one on a restart opens a "
            f"fresh window under a fresh run_dir, which silently undoes the absolute deadline. Set it "
            f"in the env file so a restarted Solver rejoins its own Run"
        )
    if any(part in run_id for part in NOT_A_COMPONENT):
        raise Refusal(
            f"{MARK} {RUN_ID}={run_id!r} is not one path component, and it becomes a directory under /state/runs"
        )
    return run_id


def _run_seconds(environ: Mapping[str, str]) -> float | None:
    stated = environ.get(RUN_SECONDS, "").strip()
    if not stated:
        return None
    try:
        seconds = float(stated)
    except ValueError:
        raise Refusal(f"{MARK} {RUN_SECONDS}={stated!r} is not a number of seconds") from None
    if seconds <= 0:
        raise Refusal(f"{MARK} {RUN_SECONDS}={stated!r} buys no time at all")
    return seconds


def _chain(model: str, homes: Mapping[str, Path]) -> tuple[Credential, ...]:
    """Construct the available native credential; injected homes keep this seam deterministic.

    No logged-in native home is a Refusal. CPA authentication belongs to its separate service and
    never enters this structure.
    """
    rungs = tuple(
        Credential(slot=slot, model=model, home=home) for slot, home in homes.items() if (home / AUTH).is_file()
    )
    if not rungs:
        listed = ", ".join(str(home / AUTH) for home in homes.values())
        raise Refusal(
            f"{MARK} no inference credential is logged in: nothing at {listed}. Run "
            f"`codex login --device-auth` inside the container before the Run starts (ADR-0011)"
        )
    return rungs


def lasting(closes_at: dt.datetime | None, window_seconds: float, run_seconds: float | None, now: dt.datetime) -> float:
    """How long this Run's window is, as the smallest of the three things that bound it.

    The event's own duration is the default; `RUN_SECONDS` may **shorten** it and may never lengthen
    it, so a practice Run can be half an hour and no environment variable can buy a Run past the
    event it is playing; and a close the Board's rules state is a ceiling neither can cross.

    The close is the only one of the three that can have run out already, because it is the only one
    that is a moment rather than a length — a profile that states no window is refused when it is
    read, and `RUN_SECONDS` is refused at zero. So it is the only bound this can refuse on, and the
    sentence names it rather than guessing.
    """
    left = window_seconds if run_seconds is None else min(window_seconds, run_seconds)
    if closes_at is None:
        return left
    until_close = (closes_at - now).total_seconds()
    if until_close <= 0:
        raise Refusal(
            f"{MARK} the close this Board's rules state, {closes_at.isoformat()}, passed at "
            f"{now.isoformat()} — there is no window left to open"
        )
    return min(left, until_close)
