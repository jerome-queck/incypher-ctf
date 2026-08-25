"""Run Intake and Triage against a live board, twice, and say what the second cycle cost.

    python3 scripts/intake_probe.py [--cycles 2] [--fetch-bytes N]

Reads CTFD_URL and CTFD_API_TOKEN from .env (or the environment), exactly as
`scripts/ctfd_probe.py` does. What it is for is the one claim about Intake that no offline test can
make: that a **second** sync over an unmoved board fetches nothing, because change detection is
comparing the board's own strings rather than our digests of bytes we had to download to compute.

It prints every Tier with its provenance, which is the other thing worth seeing against a real
board — how much of a board states its own difficulty decides how much of Triage is extraction and
how much is a model's guess, and that ratio is a property of the board rather than of this code.

Nothing here deploys an Instance or submits anything: Intake reads and downloads, and Triage reads
the manifest. The judge is left unasked, so a Challenge with nothing stated and no solves is
reported `unjudged` rather than quietly given a Tier by a model this script does not pay for.

Standard library only — it has to run inside the Solver image with nothing installed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import ctfd_probe  # noqa: E402
from solver.board import MAX_FETCH_BYTES, Board  # noqa: E402
from solver.intake import HELD, Intake, Snapshot  # noqa: E402
from solver.record import Recorder  # noqa: E402
from solver.redaction import Redactor  # noqa: E402
from solver.triage import render, triage  # noqa: E402

RUN_ID = "intake-probe"


class Counting(Board):
    """The seam, with a counter around the one call that spends bandwidth.

    Counted here rather than inside `Intake` because "how many files did this cycle download" is a
    question about a probe run and not a fact about a Run — and the claim being made is precisely
    that the second cycle makes no such call, which is a claim about the seam being left alone.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fetches = 0

    def download(self, file_path: str):
        self.fetches += 1
        return super().download(file_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=2, help="how many times to sync (default 2)")
    parser.add_argument(
        "--fetch-bytes",
        type=int,
        default=MAX_FETCH_BYTES,
        help="the cap on any single fetch, in bytes — lower it to keep a probe run cheap",
    )
    arguments = parser.parse_args()

    ctfd_probe.load_env(REPO_ROOT / ".env")
    url, token = os.environ.get("CTFD_URL", ""), os.environ.get("CTFD_API_TOKEN", "")
    if not url:
        print("CTFD_URL must be set — run `bash scripts/setup-board.sh`", file=sys.stderr)
        return 2

    board = Counting(url, token, fetch_bytes=arguments.fetch_bytes)
    recorder = Recorder(REPO_ROOT / "state", RUN_ID, redactor=Redactor.for_declared_secrets(os.environ))
    intake = Intake(board, recorder)
    print(f"syncing {board.url}" + ("" if board.authenticated else " (anonymously — no token set)"))

    for _ in range(max(1, arguments.cycles)):
        before = board.fetches
        snapshot = intake.sync()
        print(f"  {_summary(snapshot, board.fetches - before)}")
    if not snapshot.believable:
        print("\nthe last sync failed — the snapshot above is the previous one, kept.", flush=True)
        return 1

    print()
    print(render(triage(snapshot.unsolved, recorder=recorder)))
    print(f"\nrecord written to {recorder.stream_path}")
    return 0


def _summary(snapshot: Snapshot, fetches: int) -> str:
    """One line per cycle, and the number that matters is `fetched`: on an unmoved board every cycle
    after the first must report zero, or change detection is not doing its job."""
    if not snapshot.believable:
        return f"cycle {snapshot.cycle}: FAILED — {snapshot.failed}"
    files = [one for sighting in snapshot.challenges for one in sighting.attachments]
    held = [one for one in files if one.held]
    refused = [one for one in files if one.outcome != HELD]
    return (
        f"cycle {snapshot.cycle}: {len(snapshot.challenges)} challenges, {len(snapshot.changed)} changed, "
        f"{len(files)} files listed, {fetches} downloaded this cycle, {len(held)} held "
        f"({sum(one.nbytes for one in held)} bytes), {len(refused)} refused"
        + (f", mana {snapshot.mana.used}/{snapshot.mana.total}" if snapshot.mana else "")
    )


if __name__ == "__main__":
    sys.exit(main())
