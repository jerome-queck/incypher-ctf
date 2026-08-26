"""PID 1 — the composition root, the boot refusals, and the exit code a Run ends on.

`python3 -m solver` is the image's entry point and there is no supervisor in front of it (ADR-0008):
v2 wraps this, and anything between the process and the container at v1 makes a clean exit
indistinguishable from a restart loop, which is the one behaviour v1's gate is trying to observe.

Everything below the loop is handed its collaborators, so this is the only file that reads the
environment, opens files, or decides what talks to what. It is also the only place a `Refusal`
becomes an exit code: the whole point of refusing is that it happens at 10:15 with a human standing
there, so it exits with a sentence naming the fact that was missing rather than raising a traceback
at nobody.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import os
import signal
import sys
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path

from solver import boot, profile
from solver.board import Board
from solver.boot import Refusal
from solver.codex import Invocation
from solver.flag import Flags, Pace
from solver.instance import Instances
from solver.intake import Intake
from solver.record import Recorder
from solver.redaction import Redactor
from solver.run import Ending, Run, Steps
from solver.schedule import Dials, Scheduler, Window

# Where **Run state** goes: ADR-0008's one writable path, host-mounted, holding what a Run produces
# and nothing it reads. Not `state` bare — that reads as the Solver's in-memory state, which is a
# different thing and survives nothing (`CONTEXT.md`, *Run state*).
RUN_STATE = Path("/state")

# What a Run exits with, because a supervisor at v2 and a human at 16:05 read the same number.
CLEAN = 0
BROKEN = 1
REFUSED = 2

# The run-close cause of a Run that opened its window and then refused. It is written rather than
# left off, because a stream that stops after `run-open` is indistinguishable from a container that
# was killed, and those want opposite investigations.
REFUSED_AT_BOOT = "refused-at-boot"


def main(environ: Mapping[str, str], *, run_state: Path = RUN_STATE, boards: Path = profile.BOARDS) -> int:
    """Boot, run, and answer with the exit code. The only function in this repository that prints."""
    try:
        ending = _run(environ, run_state=run_state, boards=boards)
    except Refusal as refused:
        print(refused, file=sys.stderr, flush=True)
        return REFUSED
    print(
        f"{ending.cause}: {ending.attempts} attempt(s), {len(ending.flags)} flag(s)"
        + (f", still held: {', '.join(ending.left_held)}" if ending.left_held else "")
        + (f", not swept: {ending.unswept}" if ending.unswept else "")
        + (f" — {ending.detail}" if ending.detail else ""),
        flush=True,
    )
    return CLEAN if ending.clean else BROKEN


def _run(environ: Mapping[str, str], *, run_state: Path, boards: Path) -> Ending:
    """Every refusal, then the Run — in the order that puts each check before the thing it guards.

    The order is the design. Credentials come first because they cost no network call; the tracked
    profile comes next because it decides whether we are even pointed at a Board we hold rules for;
    the read contract comes before anything is believed off the wire; and the first Intake comes last
    because it is the most expensive and the only one that needs a Recorder to write to.
    """
    held = boot.setup(environ)
    rules = profile.rules_for(held.url, boards)
    board = Board(held.url, held.token)
    # The same Board addressed by nobody. Whether an unauthenticated read is answered is a profile
    # field, and asking it needs a second address rather than a flag — the token is applied by the
    # seam and not by its caller.
    held.must_hold(rules.requires)
    discovered = profile.discovered(board, Board(held.url, ""), rules)

    now = dt.datetime.now(dt.timezone.utc)
    try:
        recorder = Recorder(run_state, held.run_id, Redactor.for_declared_secrets(environ))
        window = Window.opened(
            recorder.run_dir,
            lasting=boot.lasting(rules.closes_at, rules.window_seconds, held.run_seconds, now),
            now=now,
        )
    except (OSError, ValueError) as unusable:
        # The mount is the one thing outside the image a Run depends on, and Colima mounts `$HOME`
        # and nothing else — a `-v` from outside it hands the container an empty directory in
        # silence. A window already there that cannot be read is refused for the same reason it is
        # never replaced: writing a fresh one over it is the silent extension the stamp prevents.
        raise Refusal(f"{boot.MARK} {run_state} is not usable as this Run's state — {unusable}") from None
    dials = Dials()
    recorder.run_open(
        board_profile={
            **discovered.recorded(),
            "run": {
                **held.recorded(),
                "restarted": window.restarted,
                "opened_at": window.opened_at.isoformat(),
                "ends_at": window.ends_at.isoformat(),
            },
            "dials": asdict(dials),
        }
    )

    intake = Intake(board, recorder)
    opening = intake.sync()
    if not opening.believable:
        # The same fault mid-Run keeps the last snapshot and carries on — a Board that cannot be
        # read is not a Board that emptied. At boot there is no last snapshot to keep, and a Run that
        # started here would spend its window ranking nothing while reporting success.
        recorder.run_close(cause=f"{REFUSED_AT_BOOT} — {opening.outcome}")
        raise Refusal(f"{boot.MARK} the first Intake did not believe the Board — {opening.detail}")

    steps = Steps()
    instances = Instances(board, recorder, step_numbers=steps.spend)
    run = Run(
        profile=discovered,
        recorder=recorder,
        intake=intake,
        scheduler=Scheduler(window, recorder, dials=dials),
        flags=Flags(
            board,
            recorder,
            flag_pattern=rules.flag_wrapper,
            instances=instances,
            pace=Pace(per_minute=discovered.submissions_per_minute),
            step_numbers=steps.spend,
        ),
        instances=instances,
        steps=steps,
        chain=held.chain,
        invocation=Invocation(reasoning_effort=dials.reasoning_effort, web_search=rules.web_search),
    )
    _on_signal(run)
    return run.work()


def _on_signal(run: Run) -> None:
    """Reach the reserved tail on `docker stop` rather than the ten-second grace and a SIGKILL.

    PID 1 is not handed the default action for a signal it has no handler for, so without this the
    Solver ignores SIGTERM entirely — and the Instances it holds are never reclaimed, because
    chall-manager does not evict.
    """
    for caught in (signal.SIGTERM, signal.SIGINT):
        signal.signal(caught, lambda _number, _frame: run.stop())


if __name__ == "__main__":
    sys.exit(main(os.environ))
