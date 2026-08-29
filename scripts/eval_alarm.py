"""Eval question 6 — **is the alarm firing?** The count of `cut:self-reported-impossible`, target zero.

    python3 scripts/eval_alarm.py [--event <name>] [stream ...]

[ADR-0005](../docs/adr/0005-the-stall-call-lives-outside-the-solving-model.md) gives the model one
door out of an Attempt and it only closes: a volunteered "impossible" shortens the budget and can
never lengthen one. The cause is in the closed vocabulary **because the aim is for it never to
fire** — a cause nobody records is a defect nobody can watch trending to zero
([ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md)).

So this is the one query with a target, and it is the only one that **exits non-zero on what it
finds**: a fired alarm is a finding, and a query an agent runs unattended should say so in the one
channel a caller cannot miss. Exit 0 means the alarm did not fire; exit 1 means it did, and the
Attempts it fired on are named.

What makes a fired alarm worth reading rather than merely counting is *what was said*. The matcher
in `solver/stall.py` is deliberately tight — MIRAGE-Bench measures agents fabricating an action
46–65% of the time in genuinely unachievable states rather than saying so — so a hit is either a
Challenge that really was impossible or a phrase that matched too loosely, and only the prose tells
them apart. It lives in `claims/` beside a `/state` stream and is dropped from a promoted one, so
run this against `state/runs/<id>` when the answer matters.

**The declaration is read before the phrases**, in the matcher's own order (`stall.said`). Since
#143 the marker is the way this alarm is normally reached and the phrases are the residue — a model
giving up in words nobody chose — so a query that searched the phrases alone answered *no phrase
matches the matcher as it stands today* about the two Attempts that had used the marker exactly as
asked, which is this script's own words for a false positive (#149).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import stream  # noqa: E402
from solver.record import CUT_SELF_REPORTED_IMPOSSIBLE  # noqa: E402
from solver.stall import DECLARES_IMPOSSIBLE, Thresholds, declared  # noqa: E402

# How much of the sentence to print. Enough to judge the match, short enough that a fired alarm
# stays one line per Attempt.
SHOWN = 160


def _why(run: stream.Run, attempt: stream.Attempt) -> str:
    """What tripped it, out of what the model actually said — or why we cannot show one.

    Both halves of the matcher, in the order `stall.said` reads them. The declaration first because
    it is the one the model was *asked* for: where it is present it is the whole reason, and the
    phrase search below it would either miss it or quote some unrelated sentence that happened to
    carry one of the five.
    """
    said = run.said(attempt)
    if not said:
        return "no Claim body beside this stream"
    for _, prose in said:
        if line := _declaration(prose):
            return line
    for _, prose in said:
        lowered = prose.lower()
        for phrase in Thresholds().impossible:
            if phrase in lowered:
                at = lowered.index(phrase)
                return " ".join(prose[max(0, at - 40) : at + SHOWN].split())
    return "no phrase in the Claims matches the matcher as it stands today"


def _declaration(prose: str) -> str:
    """The line the model declared on, as it wrote it — or the empty string where it did not.

    `declared` answers *whether*, which is what the stall call needs and not what a reader does, so
    the line is found again here rather than returned from there: a Watch that handed back the text
    it matched would be carrying prose into the one module ADR-0005 keeps prose out of.
    """
    if not declared(prose):
        return ""
    lines = (line.lstrip() for line in prose.splitlines())
    found = next(line for line in lines if line.upper().startswith(DECLARES_IMPOSSIBLE))
    return " ".join(found[:SHOWN].split())


def main(argv: Sequence[str] | None = None) -> int:
    parser = stream.asking(__doc__)
    arguments = parser.parse_args(argv)

    runs, said = stream.answering(arguments)
    print(said)

    attempts = [(run, one) for run in runs for one in run.attempts if one.opened]
    fired = [(run, one) for run, one in attempts if one.cause == CUT_SELF_REPORTED_IMPOSSIBLE]

    print(f"\n{len(fired)} of {len(attempts)} attempt(s) ended {CUT_SELF_REPORTED_IMPOSSIBLE} — the target is 0")
    if not fired:
        return 0

    print()
    print(
        stream.table(
            ["run", "attempt", "challenge", "category", "tier", "what was said"],
            [
                [
                    run.run_id,
                    one.attempt_id,
                    one.opened.get("challenge_name", ""),
                    one.category,
                    one.tier,
                    _why(run, one),
                ]
                for run, one in fired
            ],
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
