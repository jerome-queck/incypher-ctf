"""Eval question 5 — **is Order banking Flags early?** Cumulative Flags against elapsed Run time.

    python3 scripts/eval_banking.py [--event <name>] [stream ...]

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

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import stream  # noqa: E402


@dataclass(frozen=True)
class Banked:
    """One Flag, and where in its Run it landed.

    `share` is kept as the number it is rather than as the string the table prints. Formatting it
    into a cell and parsing it back out to count the early ones would make the display the source of
    truth for the finding, which is how a rounding choice silently becomes a result.
    """

    run_id: str
    banked: int
    seconds_in: float
    attempt_id: str
    challenge: str
    category: str
    share: float | None

    def row(self) -> list[object]:
        return [
            self.run_id,
            self.banked,
            f"{self.seconds_in / 60:.1f}",
            self.attempt_id,
            self.challenge,
            self.category,
            "" if self.share is None else f"{self.share:.2f}",
        ]

    @property
    def early(self) -> bool:
        return self.share is not None and self.share <= 0.5


def _banked(run: stream.Run) -> list[Banked]:
    """One Run's Flags, in the order they landed."""
    opened = run.opened
    closed = stream.at(str((run.closed or {}).get("ts", "")))
    if opened is None:
        return []
    last = closed or max((step.ts for attempt in run.attempts for step in attempt.steps if step.ts), default=opened)
    lasted = (last - opened).total_seconds()

    landed = sorted(
        (
            (moment, attempt)
            for attempt in run.attempts
            if attempt.flag and (moment := stream.at(str((attempt.closed or {}).get("ts", "")))) is not None
        ),
        key=lambda pair: pair[0],
    )
    return [
        Banked(
            run_id=run.run_id,
            banked=at,
            seconds_in=(into := (moment - opened).total_seconds()),
            attempt_id=attempt.attempt_id,
            challenge=str(attempt.opened.get("challenge_name", "")),
            category=attempt.category,
            share=(into / lasted) if lasted else None,
        )
        for at, (moment, attempt) in enumerate(landed, start=1)
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = stream.asking(__doc__)
    arguments = parser.parse_args(argv)

    runs = stream.load(arguments.paths, event=arguments.event)
    print(stream.heading(runs, event=arguments.event))

    banked = [flag for run in runs for flag in _banked(run)]
    first_half = sum(1 for flag in banked if flag.early)

    if not banked:
        worked = sum(len([one for one in run.attempts if one.opened]) for run in runs)
        print(f"\nno Flag in these streams, over {worked} attempt(s) — there is no curve to read yet")
        return 0

    print(f"\n{len(banked)} Flag(s), in the order they were banked:\n")
    headers = ["run", "banked", "minutes in", "attempt", "challenge", "category", "share of run"]
    print(stream.table(headers, [flag.row() for flag in banked]))
    print(f"\n{first_half} of {len(banked)} landed in the first half of the Run they were won in")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
