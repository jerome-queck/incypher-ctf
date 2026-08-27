"""Eval question 1 — **is the context system helping?** Steps to the last Checkpoint, and the rate.

    python3 scripts/eval_context.py [stream ...]

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) asks it as two
numbers: **Steps-to-last-Checkpoint over Steps-spent**, per Attempt, and **Checkpoints per thousand
tokens**. The first says whether an Attempt was still moving the environment when it ended or had
been going nowhere for a while; the second says what a Checkpoint costs, which is the only thing
that makes the first comparable across Challenges of wildly different size.

A ratio near 1 is an Attempt cut while it was still finding things. A ratio near 0 is an Attempt
that found everything it was going to find early and then spent the rest of its budget — which is
the shape a stall counter is supposed to catch, and question 2 is where the threshold that would
have caught it gets chosen.

**Checkpoints are derived here, not read.** The `checkpoint` field exists on a `step-end` and the
orchestrator never fills it, which is ADR-0009 working as designed: a Checkpoint is a *judgement*
— "this command answers differently than it did" — and a stored judgement answers for exactly the
rule that was live when it was stored. So the Checkpoints counted here come out of the same replay
question 2 runs, at the thresholds `solver/stall.py` ships with, and re-running this after the rule
changes re-reads every past Run under the new one.

**Tokens are metered per turn, and a killed turn reports none.** The vendor bills a turn on
`turn.completed`, so an Attempt whose every turn ran into the deadline carries zero tokens and no
rate can be taken over it. Those Attempts are shown with a blank rate rather than a zero, because a
rate nobody could measure and a rate that measured zero are opposite findings.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import stream  # noqa: E402
from eval_thresholds import replay  # noqa: E402
from solver.stall import Thresholds  # noqa: E402


def _ratio(marks: Sequence[int], spent: int) -> str:
    """Where the last Checkpoint fell, as a share of the Steps spent — blank where there was none.

    Blank rather than `0.00`, for the reason the token rate below is blank rather than zero: an
    Attempt that never moved the environment has no *steps-to-last-Checkpoint*, and printing a
    floor value would put it in the same column as one that found something immediately and then
    went quiet. Those are opposite findings.
    """
    if not spent or not marks:
        return ""
    return f"{max(marks) / spent:.2f}"


def _rate(checkpoints: int, tokens: int) -> str:
    """Checkpoints per thousand tokens, or blank where no turn of this Attempt was ever metered."""
    return f"{checkpoints / (tokens / 1000):.3f}" if tokens else ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="streams, or directories of them (default: runs/)")
    arguments = parser.parse_args(argv)

    runs = stream.load(arguments.paths)
    print(stream.heading(runs))

    live = Thresholds()
    rows, totals = [], {"checkpoints": 0, "tokens": 0, "steps": 0, "unmetered": 0}
    for run in runs:
        for attempt in (one for one in run.attempts if one.opened):
            marks = replay(attempt, live, said=run.said(attempt)).checkpoints
            spent, tokens = len(attempt.model_steps), attempt.tokens
            totals["checkpoints"] += len(marks)
            totals["tokens"] += tokens
            totals["steps"] += spent
            totals["unmetered"] += 0 if tokens else 1
            rows.append(
                [
                    attempt.attempt_id,
                    attempt.category,
                    spent,
                    len(marks),
                    max(marks) if marks else 0,
                    _ratio(marks, spent),
                    tokens or "",
                    _rate(len(marks), tokens),
                    attempt.cause or stream.NEVER_CLOSED,
                ]
            )

    if not rows:
        print("\nno Attempt in these streams — nothing to measure")
        return 0

    print(f"\nsteps to the last Checkpoint, over the Steps the model spent ({len(rows)} attempts):\n")
    print(
        stream.table(
            ["attempt", "category", "steps", "checkpoints", "last at", "ratio", "tokens", "per 1k", "ended"],
            rows,
        )
    )

    print(
        f"\n{totals['checkpoints']} Checkpoint(s) over {totals['steps']} model Step(s) "
        f"and {totals['tokens']} token(s): {_rate(totals['checkpoints'], totals['tokens']) or 'no metered turn'}"
        " per thousand"
    )
    if totals["unmetered"]:
        print(f"{totals['unmetered']} attempt(s) carry no tokens at all — every turn of theirs was killed mid-flight")
    if not totals["checkpoints"]:
        print("no Checkpoint anywhere in these Runs: the extension path never opened, and the ratio says nothing yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
