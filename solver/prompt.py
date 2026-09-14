"""The one prompt an Attempt is opened with, and the only place its wording lives.

Everything here is either a fact the model could not otherwise have — the Board's Flag wrapper, what
its rules forbid, the address of an Instance we deployed — or a rule about what authorises a
submission. Nothing here tells the model how it is doing: ADR-0005 puts the stall call outside the
solving model on the measurement that a model's own progress report is unreliable, and telling one
mid-Attempt that it appears stuck is close to an ideal prompt for inducing *impossible*.

`UNWINNABLE` is the near miss and stays on the right side of that line. It is a standing rule about
what makes a *Challenge* unwinnable, stated once at the open like `DERIVATION` — not a reading of
how this Attempt is going, which is the thing ADR-0005 keeps out. It is here because the matcher it
feeds was listening for words the model had never been asked to use (#143), and most of it is
exclusions because the first wording of it asked about the workspace and was answered truthfully
about the workspace, on two Challenges the rest of the Board had solved (#149).

The five things that cross an Attempt boundary are `solver/carry.py`'s and are not re-listed here;
this module writes the header they hang under.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from pathlib import Path

from solver.carry import Boundary
from solver.instance import Lease
from solver.intake import Sighting
from solver.profile import Rules
from solver.staging import Staged
from solver.stall import DECLARES_IMPOSSIBLE

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

# #143's contract and #149's correction, and the one place in this prompt that touches the stall
# call at all.
#
# It reads as a rule about the **Challenge** and never as a question about the Attempt, which is
# what keeps ADR-0005 intact. What that record forbids is telling a model mid-Attempt that it
# appears stuck — a running commentary on how the turn is going, and close to an ideal prompt for
# inducing the state. This is stated once when the Attempt opens, alongside `DERIVATION`, and says
# nothing about progress.
#
# The sentence is `solver/stall.py`'s so the matcher and the prompt cannot drift. Saying what it
# costs matters as much as saying the words: a model that thinks declaring this forfeits the Run
# will keep grinding, and a model that thinks it is free will reach for it the moment a Challenge
# is difficult.
#
# **The second paragraph is the whole of #149** and is longer than the trigger it qualifies, which
# is the right proportion. `compfest-2026-seg2` parked two Challenges other teams had solved 58 and
# 31 times, and both Claims gave the same reason: *no account credentials were provided in this
# environment*, *this workspace lacks its credentials*. Both were true, and neither was about the
# Challenge. The first cut of this rule offered *"a credential or an account we do not hold"* as a
# trigger and the model read *we* as itself — correctly, because ADR-0014 denies it the Board
# credential on purpose. A **designed** boundary read as a closed route, through the one door out
# of an Attempt that only ever shuts. So the exclusions are named one at a time, each of them a
# thing a live Attempt actually stopped on, rather than left to be inferred from the trigger.
UNWINNABLE = (
    f"There is one state worth declaring, and it is a fact about this **Challenge** rather than "
    f"about your workspace: no route to the Flag exists for anyone playing this Board — every team "
    f"meets the same wall — or the only route to it is one this Board's rules forbid. If that is "
    f"true, write a line beginning exactly `{DECLARES_IMPOSSIBLE}` followed by which of those it "
    f"is, and stop.\n"
    f"Almost nothing is this, and what *you* do not hold is never the evidence. The Solver that "
    f"opened this Attempt holds this Board's credential and submits the Flag on your behalf, so you "
    f"are not expected to reach the Board, to log in, or to hold an account anywhere: that boundary "
    f"is deliberate and it is not the Challenge being closed. Neither is a tool the image does not "
    f"ship, a host that will not answer, a key or an address or an endpoint you have not found yet, "
    f"or a cryptographic step you have not broken — those are all work left to do. Nor is "
    f"difficulty: a Challenge you have not cracked yet is one to keep working.\n"
    f"Declaring it parks this Challenge and spends the rest of the Run on the others, which is the "
    f"right trade when it is true and an expensive one when it is not. Only a line that starts with "
    f"`{DECLARES_IMPOSSIBLE}` counts, so you can reason about whether to write one without writing "
    f"it."
)

# What the model is told where staging could not use the Board's own name for a file (#119).
#
# Three things it has to do at once. It says what the Board calls the file, because a model
# reaching for that name is otherwise left to infer why it is not there. It says the staging was
# **ours**, because an unexplained same-named twin in a CTF working directory reads as a decoy
# and is worth a turn to a model that thinks so. And it stays in the past tense, about what was
# already true when the Attempt opened rather than about the directory now: this prompt is
# recomposed every turn from one tuple minted once, so a present-tense claim is one the model's
# own `mv` falsifies — and it would then point away from the Board's bytes rather than at them.
#
# No verdict about *whose* the other file is. That is a judgement a reader derives from the two
# digests `Run._record` wrote (ADR-0009), never one the Solver freezes into a prompt.
NAME_TAKEN = (
    "the Board calls this file {name}, and another file already had that name, so this is where "
    "the Board's copy was staged"
)


def compose(
    *,
    challenge: Sighting,
    rules: Rules,
    boundary: Boundary,
    recon: str,
    workdir: Path,
    staged: Sequence[Staged],
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
            boundary.carried(facts=_facts(challenge, staged, lease), recon=recon),
            _closing(),
        ]
    )


def _opening(challenge: Sighting, rules: Rules, workdir: Path, budget_s: int) -> str:
    return (
        f"You are solving one Capture The Flag Challenge on {rules.url}, alone and unattended, with "
        f"a root shell in a container and the forensics, crypto, reversing, Web, OSINT and protocol tooling the image "
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
    """Every shape the Board states, because a model told only the primary one would read a Flag in
    the second shape as not a Flag."""
    shapes = ", ".join(f"`{one}`" for one in rules.flag_wrappers)
    return (
        f"## The Flag\n"
        f"Flags on this Board match {shapes}.\n"
        f"Print the Flag from the command that produced it. **Do not try to submit it** — submitting "
        f"is not yours and there is nothing here to submit to; the Flag is taken from your output.\n"
        f"{DERIVATION}\n"
        f"{UNWINNABLE}"
    )


def _facts(challenge: Sighting, staged: Sequence[Staged], lease: Lease | None) -> str:
    """The Board-given facts, which are the first of the five things that cross a boundary.

    Held together in one block rather than spread through the header because that is what makes them
    re-composable at the next Attempt: a fact the Board gave us is not ours to forget, and a header
    sentence is not something `solver/carry.py` can carry.

    `staged` is handed in rather than filtered out of the manifest here: `Run._staged` decides once
    which files an Attempt holds, so the Board's name, its byte count and the landing can never be
    paired with each other's file — and the landing is the only path this module can name, which is
    what keeps the Run's own record out of a prompt (#126). The line below it asks the manifest a
    different question, and answers it with names rather than paths.
    """
    lines = [
        f"{challenge.name} · {challenge.category or 'uncategorised'} · {challenge.value} points",
        challenge.description.strip() or "— the Board gives this Challenge no description",
    ]
    if staged:
        lines.append("Files already downloaded for you:")
        lines += [_held_file(one) for one in staged]
    if unfetched := [one for one in challenge.attachments if not one.held]:
        lines.append("Files the Board lists that we do not hold: " + ", ".join(one.name for one in unfetched))
    if lease is not None:
        lines.append(_instance(lease))
    return "\n".join(lines)


def _held_file(staged: Staged) -> str:
    """One line the model can act on: where the file is, and how big it was when we downloaded it.

    *As downloaded* rather than a bare byte count, because the count is frozen when the Attempt
    stages its files while this line is recomposed every turn — and `compose` reads no filesystem to
    notice that the model has since unpacked or truncated the thing. Two words, and they are chosen
    to date the number rather than to hedge it: a size hinted at as *possibly wrong* is a stego hunt
    on a plain text file.
    """
    line = f"- {staged.landing} ({staged.nbytes} bytes as downloaded)"
    return line if staged.under_the_boards_name else f"{line} — {NAME_TAKEN.format(name=staged.name)}"


def _instance(lease: Lease) -> str:
    """The Instance's opaque access path, and its expiry as a fact rather than a deadline to race.

    The expiry is the Board's and not ours; the Attempt is already killed before it (`Lease`'s own
    submission reserve), so this is here to explain why a Target stops answering rather than to ask
    the model to hurry.
    """
    until = lease.until.isoformat() if isinstance(lease.until, dt.datetime) else "an unstated time"
    return (
        "A Target is available only through the generation-bound Target broker. Use "
        "`/target-client.py tcp '<payload>'`, `/target-client.py http <METHOD> <PATH>`, or the "
        "typed `http-session`, `http-fuzz`, `tcp-session`, and `browser` JSON operations from an "
        f"authorised Tool; its raw address is withheld. It stops answering at {until}."
    )


def _closing() -> str:
    return (
        f"## Before you stop\n"
        f"Write one short line naming what you tried, prefixed exactly `{APPROACH}`. It is the only "
        f"thing you write that reaches your next turn on this Challenge."
    )
