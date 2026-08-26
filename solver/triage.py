"""Triage — what each Challenge is worth, extracted rather than predicted.

**Extraction is the whole design, and it is a measurement rather than a preference.** A model asked
for difficulty from a description alone returns correlations *consistently near zero* (−0.139 to
0.137), and coarsening does not rescue it: a 3-tier version scored 37.75% while mislabelling **83%
of Hard problems as Easy** (ADR-0006). So the order of preference is fixed and the model is last:

1. **A stated difficulty**, where the Board publishes one — 22 of 25 sampled Brunner descriptions
   carried `**Difficulty:** …`. This is not a guess about the Challenge; it is the Challenge's
   author saying so.
2. **`solves`**, where nothing is stated. The Board's own population has already attempted these,
   and how many got through carries the ordering for free.
3. **A model**, over what is left, **with the file manifest in front of it** — the one condition
   under which that judgement beats noise, because artefacts are evidence the description is not.

Three refusals shape the rest:

- **Triage is one callable thing, not a pipeline stage.** It runs at Run start, again over what is
  unsolved, and over whatever arrives in between (`CONTEXT.md`, *Triage*), so it is a function over
  the set it is handed and holds nothing between calls.
- **It reads the manifest, never the contents.** Nothing here opens a downloaded file, deploys an
  Instance or submits anything — the point of a Tier is to be cheap enough to recompute over the
  whole Board whenever the Board moves.
- **`strength(category)` is absent by decision, not stubbed.** Category is an open string read from
  the Board, so a table keyed by name has no entry for the categories that will actually be scored
  (`CONTEXT.md`, *Tier*). A stub with the categories we happen to have met would look like a signal
  and be a guess about which Board we are playing.

A Tier is an **effort budget**, not a difficulty grade: `L(c) = L* × tier_weight(c)`, so a higher
Tier is more of the Run spent before an Attempt is cut. Nothing here lowers one — Tier rises on
Checkpoints earned and never falls, which is the scheduler's to apply and not Triage's to undo.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from solver.intake import Sighting
from solver.observation import elide
from solver.record import Recorder

MARK = "[triage]"

# The Tiers, low to high, where high is **more of the Run spent** rather than harder. Four bands
# because that is what a stated difficulty tends to carry and what `solves` can be split into
# without inventing precision; the weights that turn one into minutes are the scheduler's.
TIERS = 4
# What a Challenge nothing said anything about is worth. Deliberately not the lowest: a Challenge
# that was never judged is unknown rather than easy, and the Tier that follows from "unknown" being
# read as "cheap" is one that cuts every Attempt on it before anything is learned.
FLOOR = 2

# Where a Tier came from, which is recorded beside it and is the point of recording it at all: an
# extracted Tier, an inferred one and a judged one are three different qualities of evidence, and
# an analysis that cannot tell them apart cannot say whether asking the model was worth anything.
EXTRACTED = "extracted"
SOLVES = "solves"
JUDGED = "judged"
# The model was asked and did not answer for this Challenge — it named none, named it unreadably,
# or there was no judge to ask. Named rather than folded into `judged`, because a Run where every
# judged Tier is really the floor is a Run whose judge never worked, and that has to be visible.
UNJUDGED = "unjudged"

PROVENANCE = (EXTRACTED, SOLVES, JUDGED, UNJUDGED)

# The line a Board states a difficulty on. Written to survive the markup rather than to match one
# Board's: `**Difficulty:** Easy`, `Difficulty: easy`, `**Difficulty**: Easy` and the same with a
# dash all reach the same capture, because the thing being read is a convention among authors and
# not a field in any API.
STATES = re.compile(r"difficulty\W{0,4}[:\-–—]\W{0,4}\s*([A-Za-z][A-Za-z \-]{0,24})", re.IGNORECASE)

# The words a stated difficulty is stated in, and the Tier each buys. **Open in the same way
# Category is**: a word that is not here is not an extraction, and the Challenge falls through to
# `solves` rather than being forced onto a number nobody stated. That is the difference between
# reading a Board and assuming one — `#hard` on a Board whose scale runs to `nightmare` means
# something this table cannot know.
STATED = {
    "trivial": 1,
    "beginner": 1,
    "very easy": 1,
    "easy": 1,
    "baby": 1,
    "medium": 2,
    "moderate": 2,
    "intermediate": 2,
    "normal": 2,
    "hard": 3,
    "difficult": 3,
    "advanced": 3,
    "very hard": 4,
    "insane": 4,
    "expert": 4,
    "extreme": 4,
    "impossible": 4,
}

# How much of a description the judge is shown per Challenge. The prompt carries every Challenge in
# the remainder, so an uncapped one is a prompt whose size is the Board's to choose — and the
# elision is visible, because silent truncation is a lie the model then reasons over (ADR-0009).
DESCRIPTION_SHOWN_BYTES = 800

# What the judge is asked for, in the shape that is cheapest to read back. Prose is not parsed for
# meaning anywhere below: a line is looked at for an id we asked about and a digit in range, and a
# line that carries neither is skipped rather than guessed at.
QUESTION = (
    "You are grading effort, not solving anything. For each Challenge below, answer how much of a "
    f"fixed competition window is worth spending on it, as a Tier from 1 (least) to {TIERS} (most).\n"
    f"Answer one line per Challenge, `<id> <tier>`, and nothing else. Ids you omit take Tier {FLOOR}.\n"
    "You have the file manifest — names and sizes — and the Board's own prose. You do not have the "
    "files, and you are not being asked to solve anything.\n"
)


@dataclass(frozen=True)
class Judgement:
    """One Challenge's Tier, and where the Tier came from.

    `stated` is what the Board actually said where it said anything, kept verbatim even when it is
    a word `STATED` has no entry for — that is the case a Board with its own scale produces, and
    seeing the word is how the table gets a new row rather than the Challenge getting a wrong Tier.
    """

    challenge_id: int | str
    name: str
    tier: int
    provenance: str
    stated: str = ""


# The one way Triage reaches a model: a prompt in, prose out, and no route back to the Board.
#
# A seam rather than a call, for three reasons: the default is *not asking*, so nothing here depends
# on a model; a Run can be replayed with the model's half removed entirely; and **how little the
# judge can act is the judge's own property rather than a claim this module makes**. The one that
# exists is `solver/codex.py`'s `asking`, whose docstring says exactly what it withholds and what it
# cannot.
Judge = Callable[[str], str]


def unasked(_prompt: str) -> str:
    """The default judge: nothing is asked, so nothing is claimed.

    A Triage with no judge is a Triage that still runs and still assigns every Challenge a Tier —
    which is what makes the model the last resort rather than a dependency.
    """
    return ""


def triage(challenges: Sequence[Sighting], *, recorder: Recorder, judge: Judge = unasked) -> tuple[Judgement, ...]:
    """Assign every Challenge in `challenges` a Tier, in the order they were handed over.

    The three sources are tried in the order the evidence deserves, and each one narrows what the
    next is asked about: what the Board states, then what its solves say, then — over what is left
    and only over what is left — the model, in **one** prompt carrying the whole remainder.

    Total by construction: every Challenge leaves with a Tier and a provenance, including one the
    Board says nothing about and the judge never answers for. A Challenge that quietly received no
    Tier would be a Challenge the scheduler cannot budget, which is a Challenge never worked.
    """
    extracted = {one.challenge_id: found for one in challenges if (found := _extracted(one))}
    remainder = [one for one in challenges if one.challenge_id not in extracted]
    inferred = _by_solves([one for one in remainder if one.solves > 0])
    unknown = [one for one in remainder if one.challenge_id not in inferred]
    # One prompt for the whole remainder rather than one per Challenge: it is one invocation, and a
    # model comparing twelve Challenges against each other is doing the only thing this judgement is
    # for, which is ordering them.
    judged = _judged(judge(_prompt(unknown)), unknown) if unknown else {}
    judgements = tuple(
        extracted.get(one.challenge_id) or inferred.get(one.challenge_id) or _from_the_judge(one, judged)
        for one in challenges
    )
    recorder.triage(tiers=[_as_record(one) for one in judgements])
    return judgements


def render(judgements: Sequence[Judgement]) -> str:
    """Every Tier with its provenance, one line each — what a Run start prints and what a second
    pass is compared against."""
    return "\n".join(
        f"{MARK} tier {one.tier} · {one.provenance:<9} · {one.challenge_id} {one.name!r}"
        + (f" · the Board says {one.stated!r}" if one.stated else "")
        for one in judgements
    )


def _extracted(sighting: Sighting) -> Judgement | None:
    """The Tier the Board itself stated, or `None` where it stated nothing this table can read."""
    stated = _stated(sighting.description)
    tier = STATED.get(_word(stated))
    return Judgement(sighting.challenge_id, sighting.name, tier, EXTRACTED, stated) if tier else None


def _from_the_judge(sighting: Sighting, judged: dict[int | str, int]) -> Judgement:
    tier = judged.get(sighting.challenge_id)
    return Judgement(
        sighting.challenge_id,
        sighting.name,
        tier or FLOOR,
        JUDGED if tier else UNJUDGED,
        _stated(sighting.description),
    )


def _by_solves(solved_at_least_once: Sequence[Sighting]) -> dict[int | str, Judgement]:
    """`solves` carries the ordering — where there is an ordering in it to carry.

    Two conditions, and both are the same rule twice. Only Challenges with at least one solve are
    passed in, because at Run start every Challenge on a fresh Board has zero; and a set whose solve
    counts are **all the same** is turned down here, because a Board where everything is on five
    solves has ranked nothing. Either way the Challenges fall through to the judge, and a judge that
    says nothing leaves them at `FLOOR` — which is the point: unknown must not read as easy, and
    handing an unordered set the *cheapest* Tier is exactly that mistake.

    Where there is an ordering, the band is taken from **how many Challenges are solved more often
    than this one**, so equal solve counts buy equal budget and the Board's own list order never
    decides how long an Attempt gets.

    `solves` is read as an ordering and never as a distance: 50, 49 and 48 solves are three ranks
    here rather than three nearly-equal numbers. Turning the gap into the Tier needs a scoring curve
    nobody has fitted, which is why every cycle stores the `(solves, value)` pair instead
    ([ADR-0015](../docs/adr/0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)).
    """
    counts = sorted((one.solves for one in solved_at_least_once), reverse=True)
    if len(set(counts)) < 2:
        return {}
    return {
        one.challenge_id: Judgement(
            one.challenge_id,
            one.name,
            1 + (counts.index(one.solves) * TIERS) // len(counts),
            SOLVES,
            _stated(one.description),
        )
        for one in solved_at_least_once
    }


def _prompt(unknown: Sequence[Sighting]) -> str:
    """What the judge is shown: the Board's prose, and the manifest — **names and sizes, never
    bytes**. Opening a file here would be Triage doing the Attempt's job with none of the Attempt's
    budget, over every Challenge on the Board."""
    return QUESTION + "\n" + "\n\n".join(_manifest(one) for one in unknown)


def _manifest(sighting: Sighting) -> str:
    files = ", ".join(f"{one.name} ({one.nbytes} bytes)" for one in sighting.attachments if one.held)
    refused = ", ".join(one.name for one in sighting.attachments if not one.held)
    return "\n".join(
        line
        for line in (
            f"id {sighting.challenge_id} · {sighting.name!r} · category {sighting.category!r} "
            f"· type {sighting.challenge_type!r} · {sighting.value} points · {sighting.solves} solves",
            f"files: {files}" if files else "files: none",
            f"listed but not held: {refused}" if refused else "",
            elide(sighting.description.encode(), DESCRIPTION_SHOWN_BYTES),
        )
        if line
    )


def _judged(answer: str, unknown: Sequence[Sighting]) -> dict[int | str, int]:
    """The Tiers the judge named, read out of whatever prose it answered in.

    A line is searched for an id we asked about and a digit in range after it; anything else on the
    line is ignored and a line carrying neither is skipped. Nothing is inferred from the model's
    words: a Challenge it did not answer for keeps the floor and is recorded as `unjudged`, which is
    what makes a judge that has stopped working visible rather than silently unanimous.
    """
    wanted = {str(one.challenge_id): one.challenge_id for one in unknown}
    given: dict[int | str, int] = {}
    for line in answer.splitlines():
        tokens = re.findall(r"[A-Za-z0-9_-]+", line)
        named = next((at for at, token in enumerate(tokens) if token in wanted), None)
        if named is None:
            continue
        tier = next((int(token) for token in tokens[named + 1 :] if token.isdigit() and 1 <= int(token) <= TIERS), None)
        if tier:
            given.setdefault(wanted[tokens[named]], tier)
    return given


def _stated(description: str) -> str:
    """What the Board said about difficulty, verbatim and whether or not we can read it.

    Kept even when `STATED` has no row for it, because that is exactly the case a Board with its own
    scale produces — and seeing the word in the record is how the table gains a row, rather than the
    Challenge quietly getting a Tier nobody stated.
    """
    found = STATES.search(description or "")
    return " ".join(found.group(1).split()) if found else ""


def _word(stated: str) -> str:
    """A stated difficulty as a lookup key.

    A phrase the table has no row for is tried by its first word before it is given up on, because
    `medium to hard` is a Board being precise rather than a Board using a scale we cannot read.
    """
    phrase = stated.lower()
    return phrase if phrase in STATED else phrase.split(" ")[0]


def _as_record(judgement: Judgement) -> dict[str, object]:
    return {
        "challenge_id": judgement.challenge_id,
        "name": judgement.name,
        "tier": judgement.tier,
        "provenance": judgement.provenance,
        "stated": judgement.stated,
    }
