"""Eval question 5 — **is Order banking Flags early?** Cumulative Flags against elapsed Run time.

    python3 scripts/eval_banking.py [stream ...]

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) asks it as a curve,
and the shape of that curve is the whole verdict on Order. The Run is 5.5 hours and it can be cut
short — by a crash, by a venue, by a window that turns out to close earlier than the Board said — so
Flags banked in the first half are worth more than Flags banked in the second. An Order that ranks
well produces a curve that rises steeply and flattens; one that ranks badly produces a straight
line, which is what picking at random looks like.

**Elapsed time is measured from `run-open`**, not from the first Attempt: the Board sync and Triage
that precede the first pick are part of the Run's clock, and a version that made them faster would
show up here and nowhere else.

The last column is the reading: what share of the Run had gone when each Flag landed. A Run whose
Flags all sit above 0.5 banked nothing early, whatever the total says.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import stream  # noqa: E402


def _at(moment: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(moment)
    except (TypeError, ValueError):
        return None


def _banked(run: stream.Run) -> tuple[list[list[object]], float]:
    """One Run's Flags in the order they landed, and how long the Run ran for."""
    opened = run.opened
    closed = _at(str((run.closed or {}).get("ts", "")))
    if opened is None:
        return [], 0.0
    last = closed or max((step.ts for attempt in run.attempts for step in attempt.steps if step.ts), default=opened)
    lasted = (last - opened).total_seconds()

    landed = sorted(
        (
            (moment, attempt)
            for attempt in run.attempts
            if attempt.flag and (moment := _at(str((attempt.closed or {}).get("ts", "")))) is not None
        ),
        key=lambda pair: pair[0],
    )
    rows: list[list[object]] = []
    for banked, (moment, attempt) in enumerate(landed, start=1):
        into = (moment - opened).total_seconds()
        rows.append(
            [
                run.run_id,
                banked,
                f"{into / 60:.1f}",
                attempt.attempt_id,
                attempt.opened.get("challenge_name", ""),
                attempt.category,
                f"{into / lasted:.2f}" if lasted else "",
            ]
        )
    return rows, lasted


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="streams, or directories of them (default: runs/)")
    arguments = parser.parse_args(argv)

    runs = stream.load(arguments.paths)
    print(stream.heading(runs))

    rows: list[list[object]] = []
    first_half = 0
    for run in runs:
        banked, _ = _banked(run)
        rows.extend(banked)
        first_half += sum(1 for row in banked if row[-1] and float(str(row[-1])) <= 0.5)

    if not rows:
        worked = sum(len([one for one in run.attempts if one.opened]) for run in runs)
        print(f"\nno Flag in these streams, over {worked} attempt(s) — there is no curve to read yet")
        return 0

    print(f"\n{len(rows)} Flag(s), in the order they were banked:\n")
    print(stream.table(["run", "banked", "minutes in", "attempt", "challenge", "category", "share of run"], rows))
    print(f"\n{first_half} of {len(rows)} landed in the first half of the Run they were won in")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
