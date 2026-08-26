"""Eval question 7 — **are Instances leaking?** Deploys against terminates, per Attempt.

    python3 scripts/eval_instances.py [stream ...]

[ADR-0007](../docs/adr/0007-truth-about-an-instance-lives-on-the-board.md) puts the truth about an
Instance on the Board and never in a dictionary of ours, and chall-manager **never evicts**: an
Instance still held when the process exits is capacity nobody reclaims for the rest of the event,
and mana is the concurrency cap. So the leak sweep runs at every Attempt boundary and again at Run
close, and this is the query that says whether it worked.

Three things it reads, in rising order of how much they prove:

- **Deploys against terminates**, per Attempt and per Challenge. Per Challenge as well as per
  Attempt because a Lease legitimately spans consecutive Attempts on one Challenge
  ([ADR-0014](../docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)),
  so a deploy in Attempt 1 released in Attempt 3 is correct and reads as a leak per Attempt.
- **`instance_until` on the Attempt's open line**, which is the Board's own deadline and therefore
  the one field that says a Lease was really taken rather than a deploy merely attempted.
- **The sweep's exit code**, which is the verdict. `solver/instance.py` records a sweep as exit 0
  when the ledger came away empty and exit 1 when something was left behind, so the last sweep of a
  Run is where "did this Run leak" is actually settled.

**A deploy Step is written even where nothing was deployed** — a Challenge that is not the instanced
type is attempted as static, and that decision is a fact about the Attempt recorded like any other.
So the deploy column counts deploy *decisions*, and the `until` column is what counts Leases. Naming
which is which needs the Observation body, which a promoted stream does not carry; the exit code and
`instance_until` are in the lines and are what this reads.
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
from solver.profile import ABSENT  # noqa: E402

# The Instance path's own tool names (`solver/instance.py`). A Step of any of these is the
# orchestrator talking to the Board about a Lease, and none of them is the model.
DEPLOY = "deploy"
RENEW = "renew"
TERMINATE = "terminate"
SWEEP = "sweep"


def _ledger(run: stream.Run) -> str:
    """What the Board profile said about chall-manager when the Run opened.

    A Board with no plugin has no ledger to read, and `solver/run.py` skips the sweep entirely
    there — so "no sweep" means two opposite things and only the profile tells them apart.
    """
    opened = next(iter(run.of(stream.RUN_OPEN)), {})
    profile = opened.get("board_profile")
    return str(profile.get("chall_manager", "")) if isinstance(profile, dict) else ""


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", help="streams, or directories of them (default: runs/)")
    arguments = parser.parse_args(argv)

    runs = stream.load(arguments.paths)
    print(stream.heading(runs))

    attempts = [(run, one) for run in runs for one in run.attempts if one.opened]
    if not attempts:
        print("\nno Attempt in these streams — nothing was ever deployed")
        return 0

    rows, per_challenge, left_behind = [], {}, 0
    for run, attempt in attempts:
        deploys, terminates = attempt.spent(DEPLOY), attempt.spent(TERMINATE)
        sweeps = attempt.spent(SWEEP)
        unswept = sum(1 for sweep in sweeps if sweep.exit_code != 0)
        left_behind += unswept
        key = (run.run_id, str(attempt.opened.get("challenge_id")))
        held = per_challenge.setdefault(key, [0, 0, 0])
        held[0] += len(deploys)
        held[1] += len(terminates)
        held[2] += 1 if attempt.instance_until else 0
        rows.append(
            [
                run.run_id,
                attempt.attempt_id,
                attempt.opened.get("challenge_type", ""),
                len(deploys),
                len(attempt.spent(RENEW)),
                len(terminates),
                "yes" if attempt.instance_until else "",
                len(sweeps),
                "LEFT HELD" if unswept else "",
            ]
        )

    print(f"\nper Attempt ({len(rows)}):\n")
    print(
        stream.table(
            ["run", "attempt", "type", "deploys", "renews", "terminates", "lease?", "sweeps", "sweep verdict"], rows
        )
    )

    print("\nper Challenge — where a Lease spanning consecutive Attempts is correct rather than a leak:\n")
    print(
        stream.table(
            ["run", "challenge", "deploys", "terminates", "attempts with a lease"],
            [[run_id, challenge, *held] for (run_id, challenge), held in sorted(per_challenge.items())],
        )
    )

    for run in runs:
        sweeps = [step for attempt in run.attempts for step in attempt.spent(SWEEP)]
        if sweeps:
            last = max(sweeps, key=lambda step: step.seq)
            verdict = "left something held" if last.exit_code != 0 else "came away with the ledger empty"
            print(f"\n{run.run_id}: {len(sweeps)} sweep(s); the last one {verdict}")
        elif _ledger(run) == ABSENT:
            print(
                f"\n{run.run_id}: the Board runs no chall-manager, so there is no ledger to sweep and no leak to have"
            )
        else:
            print(f"\n{run.run_id}: no sweep at all, on a Board that has a ledger — nothing here proves it was read")

    if left_behind:
        print(f"\n{left_behind} sweep(s) across these Runs left an Instance held — that is the leak, and it is real")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
