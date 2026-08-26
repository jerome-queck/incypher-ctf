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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from solver.codex import CODEX_HOME, Credential
from solver.credentials import NOT_SECRETS, SECRETS

MARK = "[boot]"

# What the CLI writes when a login succeeds, and therefore the only evidence a rung is usable. Its
# presence is checked and its contents are never read: the file is the vendor's, and a credential
# this module opened is a credential this module could log.
AUTH = "auth.json"

# The chain, in ADR-0010's order — subscription first, metered last — as two directories rather than
# two keys. The CLI authenticates by file, so a rung *is* a `CODEX_HOME` that has been logged in;
# a metered rung that nobody logged in simply is not in the chain, which is what makes
# "the metered credential lives only in the scored Board's overlay" enforceable by absence.
SUBSCRIPTION = "codex-subscription"
METERED = "codex-metered"
METERED_HOME = Path("/state/codex-metered")

# ADR-0014 gives a Run one brain and switches on exhaustion alone, so this is one value for every
# rung rather than one per rung. `gpt-5-codex` is rejected on the subscription we hold, which is
# account state rather than a fact about the model, and so is config rather than a constant.
DEFAULT_MODEL = "gpt-5"

# Read here and not in `.env` alone, because `docker run --env-file` and a `-e` on the command line
# reach the same place. Every one of them is declared in `.env.example` and classified in
# `solver/credentials.py`, so a name added to the template is a name this check already knows.
RUN_ID = "RUN_ID"
RUN_SECONDS = "RUN_SECONDS"
CODEX_MODEL = "CODEX_MODEL"
CTFD_URL = "CTFD_URL"
CTFD_API_TOKEN = "CTFD_API_TOKEN"

# The two that must be there for a Run to mean anything: the Board's address, and the token that
# reads it. Everything else in `SECRETS` is optional and is checked for emptiness rather than for
# presence — a Board that answers an unauthenticated read is still refused here, because a Run that
# cannot submit is not a Run.
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
class Settings:
    """What one Run was pointed at, read once from the environment and never re-read.

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

    def recorded(self) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "model": self.model,
            "chain": [rung.slot for rung in self.chain],
            "run_seconds": self.run_seconds,
            "credentials_held": self.holdings,
        }


def settings(environ: Mapping[str, str], *, homes: Mapping[str, Path] | None = None) -> Settings:
    """Read the environment once, and refuse the Run rather than start it short a credential.

    `homes` is the chain's directories, injected so a test can stand one somewhere real; in the
    container they are the two constants above and nobody passes anything.
    """
    holdings = holdings_of(environ)
    if empties := sorted(name for name, held in holdings.items() if held == EMPTY):
        raise Refusal(
            f"{MARK} {', '.join(empties)} is set to an empty value, which is not the same as unset — "
            f"an empty key occupies its slot in a client's credential search and authenticates with "
            f"nothing. Delete the line rather than blanking it"
        )
    if missing := [name for name in REQUIRED if not environ.get(name, "").strip()]:
        raise Refusal(
            f"{MARK} {', '.join(missing)} {'are' if len(missing) > 1 else 'is'} not set, and a Run "
            f"cannot be pointed at a Board without it"
        )
    chain = _chain(
        environ.get(CODEX_MODEL, "").strip() or DEFAULT_MODEL,
        homes or {SUBSCRIPTION: CODEX_HOME, METERED: METERED_HOME},
    )
    return Settings(
        url=environ[CTFD_URL].strip().rstrip("/"),
        token=environ[CTFD_API_TOKEN].strip(),
        run_id=_run_id(environ),
        model=chain[0].model,
        chain=chain,
        run_seconds=_run_seconds(environ),
        holdings=holdings,
    )


def holdings_of(environ: Mapping[str, str]) -> dict[str, str]:
    """Every declared name and whether this environment holds it — three states, never a value.

    The set is `solver/credentials.py`'s and is not re-listed here, so a credential added for the
    redactor is a credential this check already reads. That is the whole of the criterion: one list,
    two readers, and no way for a name to be declared in one place and forgotten in the other.
    """
    return {name: _holding(environ, name) for name in (*SECRETS, *NOT_SECRETS)}


def _holding(environ: Mapping[str, str], name: str) -> str:
    if name not in environ:
        return ABSENT
    return SET if environ[name].strip() else EMPTY


def _run_id(environ: Mapping[str, str]) -> str:
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
    """The credential chain, in ADR-0010's order, holding only the rungs that are logged in.

    A rung nobody logged in is left out rather than carried and failed over: the switch costs an
    invocation, and an empty chain is a Run that will spend its whole window discovering it has no
    brain. Which is why no rung at all is a refusal — ADR-0011 puts the `codex login` minutes
    before the Run, with a human present, and this is what makes forgetting it loud.
    """
    rungs = tuple(
        Credential(slot=slot, model=model, home=home) for slot, home in homes.items() if (home / AUTH).is_file()
    )
    if not rungs:
        listed = ", ".join(str(home / AUTH) for home in homes.values())
        raise Refusal(
            f"{MARK} no inference credential is logged in — none of {listed} exists. Run "
            f"`codex login --device-auth` inside the container before the Run starts (ADR-0011)"
        )
    return rungs


def lasting(closes_at: dt.datetime | None, window_seconds: float, run_seconds: float | None, now: dt.datetime) -> float:
    """How long this Run's window is, as the smallest of the three things that bound it.

    The event's own duration is the default; a `closes_at` the Board's rules state is a ceiling the
    duration cannot cross; and `RUN_SECONDS` may **shorten** it and may never lengthen it, so a
    practice Run can be half an hour and no environment variable can buy a Run past the event it is
    playing. A window with nothing left in it is a refusal rather than a Run that opens and
    immediately runs its own tail.
    """
    bounds = [window_seconds]
    if closes_at is not None:
        bounds.append((closes_at - now).total_seconds())
    if run_seconds is not None:
        bounds.append(run_seconds)
    left = min(bounds)
    if left <= 0:
        raise Refusal(
            f"{MARK} the window this Board's rules state closed at {closes_at} and it is now {now} — "
            f"there is no window left to open"
        )
    return left
