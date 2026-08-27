"""Eval question 4 — **is Tier predictive?** The Tier a Challenge was given, against how it ended.

    python3 scripts/eval_tier.py [stream ...]

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) asks for Tier against
the cause an Attempt actually ended with, and the matrix below is that. Tier sets the Attempt budget
(`L(c) = L* × tier_weight(c)`), so a Tier that does not separate outcomes is a budget being handed
out at random — and the fix is not a better model, it is dropping the input.

The second table is the same question asked of the **provenance**, which is why
[ADR-0006](../docs/adr/0006-a-version-is-a-capability-set-and-its-gate.md) makes Triage extract
rather than predict: a Tier read off a stated difficulty, one inferred from `solves` and one a model
judged are three different qualities of evidence, and an analysis that cannot tell them apart cannot
say whether the model's judgement was ever worth paying for. The `triage` record carries the
provenance per Challenge and this is the one query that reads it.

Read down a Tier's row: a Tier whose Attempts all end `cut:step-cliff` predicted nothing, and one
whose low Tiers end `flag` while its high ones end on a counter predicted exactly what it is for.
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
from solver.record import CAUSES  # noqa: E402


def _provenance(runs: Sequence[stream.Run]) -> dict[str, str]:
    """How each Challenge got its Tier, keyed by challenge id as a string.

    Later Triage lines win: Triage runs again as the Board moves, and the Tier an Attempt was opened
    under is the one it was picked with.
    """
    how: dict[str, str] = {}
    for run in runs:
        for record in run.of(stream.TRIAGE):
            for judged in record.get("tiers") or []:
                if isinstance(judged, dict):
                    how[str(judged.get("challenge_id"))] = str(judged.get("provenance", "")) or "(unstated)"
    return how


def _matrix(counts: dict[tuple[str, str], int], keys: Sequence[str], causes: Sequence[str], label: str) -> str:
    rows = []
    for key in keys:
        spent = [counts.get((key, cause), 0) for cause in causes]
        rows.append([key, sum(spent), *(count or "" for count in spent)])
    return stream.table([label, "attempts", *causes], rows)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="streams, or directories of them (default: runs/)")
    arguments = parser.parse_args(argv)

    runs = stream.load(arguments.paths)
    print(stream.heading(runs))

    attempts = [attempt for run in runs for attempt in run.attempts if attempt.opened]
    if not attempts:
        print("\nno Attempt in these streams — nothing to test Tier against")
        return 0

    how = _provenance(runs)
    by_tier: dict[tuple[str, str], int] = {}
    by_provenance: dict[tuple[str, str], int] = {}
    for attempt in attempts:
        cause = attempt.cause or stream.NEVER_CLOSED
        tier = "(untiered)" if attempt.tier is None else f"tier {attempt.tier}"
        origin = how.get(str(attempt.opened.get("challenge_id")), "(untriaged)")
        by_tier[tier, cause] = by_tier.get((tier, cause), 0) + 1
        by_provenance[origin, cause] = by_provenance.get((origin, cause), 0) + 1

    # Only the causes that actually fired get a column — nine of them would make a table nobody
    # reads across. What is lost by filtering is named instead, on the line below the matrix: a
    # reader who does not already know the closed vocabulary cannot tell a cause that never fired
    # from one nobody records, and that is the same silence `scripts/credentials_held.py` breaks by
    # printing its absent names.
    declared = [*CAUSES, stream.NEVER_CLOSED]
    causes = [cause for cause in declared if any(key[1] == cause for key in by_tier)]
    never = [cause for cause in declared if cause not in causes]

    print(f"\nTier against the cause it ended with ({len(attempts)} attempts):\n")
    print(_matrix(by_tier, sorted({key[0] for key in by_tier}), causes, "tier"))
    if never:
        print(f"\n  never fired here, and so has no column: {', '.join(never)}")

    print("\nthe same, against how that Tier was arrived at:\n")
    print(_matrix(by_provenance, sorted({key[0] for key in by_provenance}), causes, "provenance"))

    tiers = {key[0] for key in by_tier}
    if len(tiers) < 2:
        print(f"\nevery Attempt here sits at {next(iter(tiers))} — this Run cannot say whether Tier separates anything")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
