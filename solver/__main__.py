"""The Run-controller Boot, its refusals, and the exit code the Supervisor reads.

`python3 -m solver` remains the v1 execution path behind v2's PID-1 Supervisor. The child keeps the
existing Board and Attempt composition while the Supervisor owns Boot identity, signals and process
lifecycle.

Everything below the loop is handed its collaborators, so this is the only file that reads the
environment, opens files, or decides what talks to what. It is also the only place a `Refusal`
becomes an exit code: the whole point of refusing is that it happens at 10:15 with a human standing
there, so it exits with a sentence naming the fact that was missing rather than raising a traceback
at nobody.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import signal
import sys
from collections.abc import Mapping
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from solver import boot, profile
from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import RuntimeBinding
from solver.attempt_executor_pool import ATTEMPT_UID, POOL_ENV, attach_attempt_pool
from solver.attempt_executor_runtime import AttemptRuntime
from solver.board import Board
from solver.capability import CapabilityAuthority, PeerIdentity
from solver.board_broker import BoardCompatibilityClient, BoardProfileClient
from solver.coherent_intake import BrokerIntakeSource, CoherentIntake, contract_from_profile_receipt
from solver.board_broker_contracts import BOARD_BROKER_SOCKET_ENV, BOARD_PROFILE_HANDLE_ENV
from solver.boot import Refusal
from solver.codex import Invocation, asking
from solver.event_store import EventStoreDamage
from solver.flag import Flags, Pace
from solver.instance import Instances
from solver.intake import Intake
from solver.intake_qualification import NoCoherentSnapshot
from solver.isolation import IMAGE_ID as STRICT_IMAGE_ENV
from solver.isolation_receipt import RECEIPT_FILENAME as ISOLATION_RECEIPT_FILENAME
from solver.record import Recorder
from solver.redaction import Redactor
from solver.replay import verify_and_materialize_run_state
from solver.run import WORK_ROOT, Ending, Run, Steps
from solver.schedule import Dials, Scheduler, Window
from solver.order_runtime import CanonicalScheduler
from solver.tool_control import AttemptToolRuntime, ToolComponent, ToolController

# Where **Run state** goes: ADR-0008's one writable path, host-mounted, holding what a Run produces
# and nothing it reads. Not `state` bare — that reads as the Solver's in-memory state, which is a
# different thing and survives nothing (`CONTEXT.md`, *Run state*).
RUN_STATE = Path("/state")

# The two directories this file names under the mount it was pointed at, and they are named the
# same way on purpose: a Solver handed a different `/state` is handed a different one whole, and a
# path that stayed absolute while its sibling followed the mount is the seam only this file can see.
#
# The judge is spawned in its own rather than in an Attempt's or in the Run's record: the CLI runs
# *in* its working directory, and either of those would stand a judgement among a Challenge's files
# or this Run's own stream. What that buys, and what a read-only sandbox still allows, is
# `solver/codex.py`, at `asking`. The other is `run.WORK_ROOT` re-rooted rather than restated, so
# the model's directories cannot drift from the module that lays them out (ADR-0025 namespaces the
# event beneath it).
JUDGE_WORKDIR = "triage"
ATTEMPT_WORKDIRS = WORK_ROOT.name

# What a Run exits with, because a supervisor at v2 and a human at 16:05 read the same number.
CLEAN = 0
BROKEN = 1
REFUSED = 2

# The run-close cause of a Run that opened its window and then refused. It is written rather than
# left off, because a stream that stops after `run-open` is indistinguishable from a container that
# was killed, and those want opposite investigations.
REFUSED_AT_BOOT = "refused-at-boot"

# Tests may inject the retired direct transport explicitly. Production never assigns this seam.
TEST_DIRECT_BOARD_FACTORY: Callable[[str, str], Board] | None = None


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
    try:
        verify_and_materialize_run_state(run_state, held.run_id, Redactor.for_declared_secrets(environ))
    except EventStoreDamage as damage:
        raise Refusal(f"{boot.MARK} canonical state verification refused this Run — {damage.classification}") from None
    except (OSError, ValueError) as unusable:
        raise Refusal(f"{boot.MARK} {run_state} is not usable as this Run's state — {unusable}") from None
    return _run_admitted(
        environ,
        run_state=run_state,
        held=held,
        rules=rules,
        rules_source=str(Path(boards) / f"{rules.event}{profile.SUFFIX}"),
        profile_handle=environ.get(BOARD_PROFILE_HANDLE_ENV, ""),
        board_broker_path=Path(environ[BOARD_BROKER_SOCKET_ENV]) if environ.get(BOARD_BROKER_SOCKET_ENV) else None,
        boot_id=environ.get("SUPERVISOR_BOOT_ID", ""),
    )


def _run_admitted(
    environ: Mapping[str, str],
    *,
    run_state: Path,
    held: boot.Setup,
    rules: profile.Rules,
    rules_source: str = "",
    profile_handle: str = "",
    board_broker_path: Path | None = None,
    boot_id: str = "",
) -> Ending:
    """Keep capability IPC live beside every admitted v1 Board and inference call."""

    held.must_hold(rules.requires)
    if board_broker_path is None:
        if TEST_DIRECT_BOARD_FACTORY is None:
            raise Refusal(f"{boot.MARK} qualified Board broker is required for production Intake")
        board = TEST_DIRECT_BOARD_FACTORY(held.url, held.token)
        discovered = profile.discovered(board, TEST_DIRECT_BOARD_FACTORY(held.url, ""), rules)
    else:
        profiler = BoardProfileClient(board_broker_path, profile_handle)
        decision = profiler.qualify(rules, rules_source or f"{rules.event}{profile.SUFFIX}")
        if not decision.authoritative or decision.profile is None:
            raise Refusal(f"{boot.MARK} Board profile is incompatible — {decision.reason}")
        profiler.open_operations()
        discovered = decision.profile
        board = BoardCompatibilityClient(board_broker_path)

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

    if board_broker_path is None:
        intake = Intake(board, recorder)
    else:
        intake_contract = contract_from_profile_receipt(run_state, held.run_id)
        intake = CoherentIntake(
            BrokerIntakeSource(
                board_broker_path,
                state=run_state,
                run_id=held.run_id,
                boot_id=boot_id,
                board_url=held.url,
                profile_handle=profile_handle,
                contract=intake_contract,
                generations=recorder.generations,
            ),
            recorder,
            intake_contract,
            Redactor.for_declared_secrets(environ),
        )
    opening = intake.sync()
    if not isinstance(opening, NoCoherentSnapshot) and hasattr(opening, "believable") and not opening.believable:
        # The same fault mid-Run keeps the last snapshot and carries on — a Board that cannot be
        # read is not a Board that emptied. At boot there is no last snapshot to keep, and a Run that
        # started here would spend its window ranking nothing while reporting success.
        recorder.run_close(cause=f"{REFUSED_AT_BOOT} — {opening.outcome}")
        raise Refusal(f"{boot.MARK} the first Intake did not believe the Board — {opening.detail}")

    steps = Steps()
    instances = Instances(board, recorder, step_numbers=steps.spend)
    # Triage's last resort, and the one collaborator only this file can hand it: what the Board
    # states and what its solves say are read off the Board itself, and the model is asked about
    # whatever neither of them could rank. Left at its default nothing is asked at all, and a Board
    # that publishes no difficulty is triaged entirely at the floor — every Challenge budgeted
    # alike, and every Tier recorded `unjudged` (ADR-0006). Brunner published one on nearly every
    # Challenge, which is why four gate Runs never showed this.
    #
    # It is handed the rung this Run leads with and no chain behind it: failing over would spend a
    # second invocation on a Tier, and a judge that answers nothing leaves the floor either way. And
    # it is handed the Board's `web_search` for the reason the Attempt below is: the tool is the
    # Board's to withdraw, and a Run whose judge kept it would be playing one invocation outside the
    # rules the rest of it obeys (ADR-0014).
    judge = asking(held.chain[0], recorder=recorder, workdir=run_state / JUDGE_WORKDIR, web_search=rules.web_search)
    attempt_executor = None
    tool_runtime = None
    if POOL_ENV in environ:
        binding = RuntimeBinding(
            image_id=environ.get(STRICT_IMAGE_ENV, ""),
            image_manifest_digest=environ.get("INCYPHER_IMAGE_MANIFEST", ""),
            image_config_digest=environ.get("INCYPHER_IMAGE_CONFIG", ""),
            platform=environ.get("INCYPHER_IMAGE_PLATFORM", ""),
        )
        attempt_executor = AttemptExecutor(
            state=run_state,
            run_id=held.run_id,
            isolation_receipt=(run_state / "runs" / held.run_id / "canonical" / ISOLATION_RECEIPT_FILENAME),
            binding=binding,
            generation_fence=recorder.generations,
            runtime=AttemptRuntime(attach_attempt_pool(environ)),
            timestamp=lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        tool_peer = PeerIdentity(
            pid=os.getpid(),
            uid=ATTEMPT_UID,
            gid=ATTEMPT_UID,
            started=boot_id,
            cgroup="attempt-executor-pool",
        )
        tool_authority = CapabilityAuthority(
            state=run_state,
            run_id=held.run_id,
            boot_id=boot_id,
            redactor=Redactor.for_declared_secrets(environ),
            peer_identity=lambda _connection: tool_peer,
            timestamp=lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        file_digest = "sha256:" + hashlib.sha256(Path("/usr/bin/file").read_bytes()).hexdigest()
        tool_controller = ToolController(
            state=run_state,
            run_id=held.run_id,
            authority=tool_authority,
            image_digest=binding.image_manifest_digest,
            components=(
                ToolComponent(
                    capability_id="recon.mime",
                    component_id="file",
                    version=file_digest,
                    profiles=("resident",),
                    max_arguments=4,
                    max_output_bytes=8 * 1024,
                ),
            ),
            timestamp=lambda: dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        tool_runtime = AttemptToolRuntime(
            controller=tool_controller,
            executor=attempt_executor,
            run_id=held.run_id,
            boot_id=boot_id,
            peer=tool_peer,
        )
    run = Run(
        profile=discovered,
        recorder=recorder,
        intake=intake,
        scheduler=(
            CanonicalScheduler(window, recorder, intake.order_authority, dials=dials, judge=judge)
            if isinstance(intake, CoherentIntake)
            else Scheduler(window, recorder, dials=dials, judge=judge)
        ),
        flags=Flags(
            board,
            recorder,
            flag_wrappers=rules.flag_wrappers,
            instances=instances,
            pace=Pace(per_minute=discovered.submissions_per_minute),
            step_numbers=steps.spend,
        ),
        instances=instances,
        steps=steps,
        chain=held.chain,
        invocation=Invocation(reasoning_effort=dials.reasoning_effort, web_search=rules.web_search),
        work_root=run_state / ATTEMPT_WORKDIRS,
        attempt_executor=attempt_executor,
        tool_runtime=tool_runtime,
        board_broker_path=board_broker_path,
        board_broker_boot_id=boot_id,
    )
    _on_signal(run)
    try:
        return run.work()
    finally:
        if attempt_executor is not None:
            attempt_executor.close()


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
