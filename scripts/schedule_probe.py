"""Rank a live board and walk the pick sequence, without working a single Challenge.

    python3 scripts/schedule_probe.py [--picks 12] [--lasting 19800] [--cut 600]

Reads CTFD_URL and CTFD_API_TOKEN from .env exactly as `scripts/ctfd_probe.py` does, syncs the
board once through Intake, and then drives the scheduler across as many Attempt boundaries as asked
for — printing Order's whole rank vector at the first boundary and one line per pick after it. Each
pick is released as a `cut:budget` that spent its whole budget, so the walk is what a Run of pure
failures would have reached.

Two things no offline test can say, and both are why this exists:

- **What Order does to a real board.** The weights are uncalibrated guesses and the shape of a
  board — how the solve counts and point values are actually distributed — is what decides whether
  they separate anything at all. A rank vector where the top twenty are indistinguishable is a
  finding, and it is invisible against constructed fixtures.
- **That a restarted Solver does not extend its own window.** The stamp is re-read at the end from
  the same `/state` directory, which is the whole of the claim.

Nothing here deploys an Instance, submits anything, or asks a model: the judge is left unasked, so
a Challenge with nothing stated and no solves is reported at Triage's floor rather than being given
a Tier this script did not pay for. It downloads next to nothing either — Order reads solves,
value, position and the deploy terms and never opens a file, so the fetch cap is set low by default
and a rank vector cannot cost the 6 GB archive a board once linked from a description.

Standard library only — it has to run inside the Solver image with nothing installed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# As in `scripts/intake_probe.py`: the pre-flight probe owns the env-file reading and it is not
# reimplemented here. The repository root is what the insert above is for.
import ctfd_probe  # noqa: E402
from solver.board import Board  # noqa: E402
from solver.intake import Intake  # noqa: E402
from solver.record import CUT_BUDGET, Recorder  # noqa: E402
from solver.redaction import Redactor  # noqa: E402
from solver.schedule import Ended, Pick, Scheduler, Window, render  # noqa: E402

RUN_ID = "schedule-probe"
COMPETITION_SECONDS = 5.5 * 3600
# Enough for a board that serves a small file inline, and far short of anything worth having.
SMALL_ENOUGH_FOR_A_RANKING = 64 * 1024


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--picks", type=int, default=12, help="how many Attempt boundaries to walk (default 12)")
    parser.add_argument(
        "--lasting",
        type=float,
        default=COMPETITION_SECONDS,
        help="the window to open, in seconds — ignored where one is already stamped on disk",
    )
    parser.add_argument(
        "--fetch-bytes",
        type=int,
        default=SMALL_ENOUGH_FOR_A_RANKING,
        help="the cap on any single fetch, in bytes — Order opens no file, so this stays low",
    )
    parser.add_argument(
        "--cut",
        type=float,
        default=600.0,
        help="how long each simulated Attempt spends before it is cut (default 600)",
    )
    arguments = parser.parse_args()

    ctfd_probe.load_env(REPO_ROOT / ".env")
    url, token = os.environ.get("CTFD_URL", ""), os.environ.get("CTFD_API_TOKEN", "")
    if not url:
        print("CTFD_URL must be set — run `bash scripts/setup-board.sh`", file=sys.stderr)
        return 2

    board = Board(url, token, fetch_bytes=arguments.fetch_bytes)
    recorder = Recorder(REPO_ROOT / "state", RUN_ID, redactor=Redactor.for_declared_secrets(os.environ))
    print(f"syncing {board.url}" + ("" if board.authenticated else " (anonymously — no token set)"))
    snapshot = Intake(board, recorder).sync()
    if not snapshot.believable:
        print(f"the sync failed — {snapshot.outcome}: {snapshot.detail}", file=sys.stderr)
        return 1

    clock = _Clock(dt.datetime.now(dt.timezone.utc))
    window = Window.opened(recorder.run_dir, lasting=arguments.lasting, now=clock())
    scheduler = Scheduler(window, recorder, now=clock)
    print(
        f"\nwindow {window.opened_at.isoformat()} → {window.ends_at.isoformat()}"
        + (" (inherited from disk — this is a restart)" if window.restarted else "")
    )

    print(f"\nOrder over {len(snapshot.unsolved)} unsolved challenges:\n")
    print(render(scheduler.order(snapshot)))

    print(f"\nthe pick sequence, {arguments.cut:.0f}s a cut:\n")
    for _ in range(max(1, arguments.picks)):
        pick = scheduler.acquire(snapshot)
        if pick is None:
            print("  the clock can no longer buy an Attempt — this is where the tail begins")
            break
        print(f"  {_picked(pick)}")
        scheduler.release(Ended(pick.challenge.challenge_id, cause=CUT_BUDGET, seconds=arguments.cut))
        clock.on(pick.budget_s)

    reopened = Window.opened(recorder.run_dir, lasting=arguments.lasting, now=clock())
    print(f"\nreopened at {clock().isoformat()} → {reopened.ends_at.isoformat()}")
    if reopened.ends_at != window.ends_at:
        print("the window MOVED — a restart extended the Run's own deadline", file=sys.stderr)
        return 1
    print("the window did not move: a restart is handed the time that is left, not a fresh one")
    print(f"\nrecord written to {recorder.stream_path}")
    return 0


class _Clock:
    """The Run's clock, advanced by hand so a whole window is walked in a second.

    The scheduler takes its wall-clock at the edge precisely so this is possible: `T_remaining`,
    `K`, the tail and `L_min` are all read through it, so a probe drives the same arithmetic a Run
    would without waiting 5.5 hours to see the working set narrow.
    """

    def __init__(self, at: dt.datetime) -> None:
        self.at = at

    def __call__(self) -> dt.datetime:
        return self.at

    def on(self, seconds: float) -> None:
        self.at += dt.timedelta(seconds=seconds)


def _picked(pick: Pick) -> str:
    return (
        f"attempt {pick.attempt_sequence} on {pick.challenge.challenge_id} {pick.challenge.name!r} "
        f"· tier {pick.tier} · {pick.budget_s}s until {pick.deadline.isoformat()} "
        f"· working set of {len(pick.working_set)}" + (" · EXPLORING" if pick.exploring else "")
    )


if __name__ == "__main__":
    sys.exit(main())
