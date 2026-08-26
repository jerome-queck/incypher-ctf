"""Eval question 3 — **where did the budget go?** Tokens and wall-clock, three ways.

    python3 scripts/eval_budget.py [stream ...]

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) asks for it per
Attempt, per Category and per cause, and all three are here because they answer different questions
about the same 5.5 hours: which Attempt ate the Run, which kind of Challenge is expensive, and which
way of ending one is expensive. `L*`, the per-Attempt cap, is chosen off the second and third of
those and has no other source.

**Tokens, never cost.** The record carries no price table and neither does this
([ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md)) — a price table
lives outside the record and changes underneath it, so a cost computed here would be a number that
silently stopped being true. `cache read` is reported beside the tokens drawn rather than inside
them: it is context re-read, and folding it in would say the model got cheaper every time it read
the same prompt again.

**Wall-clock runs from an Attempt's first Step, not from its `attempt-open`.** The deploy is Step 1
of an Attempt whose open line is written after it (`solver/run.py`), so measuring from the open
would drop exactly the one operation most able to hang.

**A turn killed at the deadline reports no tokens**, because the vendor meters a turn on completion.
So an Attempt can show real minutes against zero tokens, and that pairing is a finding rather than a
gap: it is the shape of a Run spending its budget on turns that never got to finish.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import stream  # noqa: E402


@dataclass
class Spend:
    """What one group of Attempts cost, in the two currencies a Run actually has."""

    attempts: int = 0
    seconds: float = 0.0
    tokens: int = 0
    cache_read: int = 0
    model_steps: int = 0
    flags: int = 0

    def took(self, attempt: stream.Attempt) -> None:
        self.attempts += 1
        self.seconds += attempt.seconds
        self.tokens += attempt.tokens
        self.cache_read += attempt.cache_read
        self.model_steps += len(attempt.model_steps)
        self.flags += 1 if attempt.flag else 0

    def row(self, name: str) -> list[object]:
        return [
            name,
            self.attempts,
            f"{self.seconds / 60:.1f}",
            f"{self.seconds / 60 / self.attempts:.1f}" if self.attempts else "",
            self.tokens,
            self.cache_read,
            self.model_steps,
            self.flags or "",
        ]


HEADERS = ["", "attempts", "minutes", "mean min", "tokens", "cache read", "model steps", "flags"]


@dataclass
class Grouped:
    """Attempts gathered under one key, kept in the order the keys were first seen."""

    spends: dict[str, Spend] = field(default_factory=dict)

    def took(self, key: str, attempt: stream.Attempt) -> None:
        self.spends.setdefault(key, Spend()).took(attempt)

    def rows(self) -> list[list[object]]:
        ordered = sorted(self.spends.items(), key=lambda pair: pair[1].seconds, reverse=True)
        return [spend.row(key) for key, spend in ordered]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="streams, or directories of them (default: runs/)")
    arguments = parser.parse_args(argv)

    runs = stream.load(arguments.paths)
    print(stream.heading(runs))

    attempts = [attempt for run in runs for attempt in run.attempts if attempt.opened]
    if not attempts:
        print("\nno Attempt in these streams — nothing to account for")
        return 0

    per_attempt, per_category, per_cause, whole = Grouped(), Grouped(), Grouped(), Spend()
    for attempt in attempts:
        per_attempt.took(f"{attempt.attempt_id} {attempt.opened.get('challenge_name', '')}".strip(), attempt)
        per_category.took(attempt.category, attempt)
        per_cause.took(attempt.cause or "(never closed)", attempt)
        whole.took(attempt)

    for what, grouped in (("attempt", per_attempt), ("category", per_category), ("cause", per_cause)):
        print(f"\nper {what}:\n")
        print(stream.table([what, *HEADERS[1:]], grouped.rows()))

    print("\n" + stream.table(HEADERS, [whole.row("everything")]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
