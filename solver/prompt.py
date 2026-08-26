"""The one prompt an Attempt is opened with, and the only place its wording lives.

Everything here is either a fact the model could not otherwise have — the Board's Flag wrapper, what
its rules forbid, the address of an Instance we deployed — or a rule about what authorises a
submission. Nothing here tells the model how it is doing: ADR-0005 puts the stall call outside the
solving model on the measurement that a model's own progress report is unreliable, and telling one
mid-Attempt that it appears stuck is close to an ideal prompt for inducing *impossible*.

The five things that cross an Attempt boundary are `solver/carry.py`'s and are not re-listed here;
this module writes the header they hang under.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from solver.carry import Boundary
from solver.instance import Lease
from solver.intake import Sighting
from solver.profile import Rules

# The one line the model writes that survives the Attempt (`solver/carry.py`). Asked for by an
# explicit marker rather than inferred from the prose, so a label is something the model chose to
# write and never something a parser guessed out of a paragraph.
APPROACH = "APPROACH:"

# #71's derivation rule, in the prompt because the structural half of it is already enforced and
# cannot say *why*: a transformation the model performs in its head lands in no Observation, so
# `solver/flag.py` can only ever submit its result `unverified`. A model that is never told will
# keep doing arithmetic in its head and keep producing candidates the gate holds back.
DERIVATION = (
    "If a Flag has to be decoded, decrypted or assembled, run that transformation as a command so "
    "its result lands in real output. A Flag you only write down is recorded unverified: nothing "
    "you say authorises a submission, and a command's output is the only thing that does."
)

NO_PROHIBITIONS = "— this Board's rules name none beyond ordinary competition conduct"


def compose(
    *,
    challenge: Sighting,
    rules: Rules,
    boundary: Boundary,
    recon: str,
    workdir: Path,
    budget_s: int,
    lease: Lease | None = None,
) -> str:
    """The whole Attempt prompt: what the Solver knows, then what crossed the boundary.

    `budget_s` is stated once, as this Attempt's size rather than as a countdown. It is scoping
    information — a model with ten minutes and a model with an hour should not pick the same
    approach — and it is deliberately not refreshed: a live clock in a prompt is a running
    commentary on how the Attempt is going, which is the thing ADR-0005 keeps out.
    """
    return "\n\n".join(
        [
            _opening(challenge, rules, workdir, budget_s),
            _forbidden(rules),
            _finding_the_flag(rules),
            boundary.carried(facts=_facts(challenge, lease), recon=recon),
            _closing(),
        ]
    )


def _opening(challenge: Sighting, rules: Rules, workdir: Path, budget_s: int) -> str:
    return (
        f"You are solving one Capture The Flag Challenge on {rules.url}, alone and unattended, with "
        f"a root shell in a container and the forensics, crypto and reversing tooling the image "
        f"ships.\n"
        f"Your working directory is {workdir}. It is yours, it already holds this Challenge's files, "
        f"and it survives into your next turn on this Challenge — anything you want later, leave "
        f"there.\n"
        f"This turn is about {budget_s // 60} minute(s) of work."
    )


def _forbidden(rules: Rules) -> str:
    listed = "\n".join(f"- {one}" for one in rules.prohibitions) or NO_PROHIBITIONS
    return f"## What this Board's rules forbid\n{listed}"


def _finding_the_flag(rules: Rules) -> str:
    return (
        f"## The Flag\n"
        f"Flags on this Board match `{rules.flag_wrapper}`.\n"
        f"Print the Flag from the command that produced it. **Do not try to submit it** — submitting "
        f"is not yours and there is nothing here to submit to; the Flag is taken from your output.\n"
        f"{DERIVATION}"
    )


def _facts(challenge: Sighting, lease: Lease | None) -> str:
    """The Board-given facts, which are the first of the five things that cross a boundary.

    Held together in one block rather than spread through the header because that is what makes them
    re-composable at the next Attempt: a fact the Board gave us is not ours to forget, and a header
    sentence is not something `solver/carry.py` can carry.
    """
    lines = [
        f"{challenge.name} · {challenge.category or 'uncategorised'} · {challenge.value} points",
        challenge.description.strip() or "— the Board gives this Challenge no description",
    ]
    held = [one for one in challenge.attachments if one.held]
    if held:
        lines.append("Files already downloaded for you:")
        lines += [f"- {one.path} ({one.nbytes} bytes)" for one in held]
    if unfetched := [one for one in challenge.attachments if not one.held]:
        lines.append("Files the Board lists that we do not hold: " + ", ".join(one.name for one in unfetched))
    if lease is not None:
        lines.append(_instance(lease))
    return "\n".join(lines)


def _instance(lease: Lease) -> str:
    """The Instance's address, and its expiry as a fact rather than as a deadline to race.

    The expiry is the Board's and not ours; the Attempt is already killed before it (`Lease`'s own
    submission reserve), so this is here to explain why a Target stops answering rather than to ask
    the model to hurry.
    """
    until = lease.until.isoformat() if isinstance(lease.until, dt.datetime) else "an unstated time"
    return f"A Target was deployed for you at {lease.connection_info} and stops answering at {until}."


def _closing() -> str:
    return (
        f"## Before you stop\n"
        f"Write one short line naming what you tried, prefixed exactly `{APPROACH}`. It is the only "
        f"thing you write that reaches your next turn on this Challenge."
    )
