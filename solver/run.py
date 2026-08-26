"""One unattended outing at one Board — every Attempt it takes, and the tail it ends on.

Two rules shape everything here and both are `CONTEXT.md`'s (*Run*). **A Run ends when the window
closes or when it crashes, never because the Board looks finished** — Challenges drop mid-event, so
"nothing left to work" is not a conclusion the Solver is allowed to reach, and `Scheduler.acquire`
answering `None` is read through `out_of_time()` rather than assumed. And **a Run survives a
restart**, which is why nothing here mints an identity or a deadline: both were settled at boot and
one of them is on disk.

The loop is `acquire` → work → `release`, and one hold is one **Attempt** — open at the recon that
starts it, closed at the Cut or the Flag that ends it (`CONTEXT.md`, *Attempt*). Inside it the
vendor's agent may take several **turns**: ending a turn with budget left is not an ending
(`solver/codex.py` says so at its `STOPPED` constant), so an empty cause re-invokes over the same
working directory with a fresh `Watch` — fresh because a turn re-orients itself and re-running `ls`
is not a stall — and never `resume`, which would carry the model's prose across the reset ADR-0005
exists to make. Each turn costs one line on the `Boundary`, which is carry.py's bound exactly.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from solver import codex, prompt, recon
from solver.board import BoardFailure
from solver.carry import Boundary, label
from solver.codex import CLAIM, COMMAND, STOPPED, Credential, Invocation
from solver.flag import Candidate, Flags, Outcome, Slots
from solver.instance import Instances, Lease
from solver.intake import Intake, Sighting
from solver.profile import Profile
from solver.prompt import APPROACH
from solver.record import CUT_BUDGET, FLAG, Recorder
from solver.schedule import Ended, Pick, Scheduler
from solver.stall import Breaker, Deadline, Watch

MARK = "[run]"

# Why the Run ended, written on the run-close line. Two endings, because there are only two: the
# clock, and us. The third name is the clock brought forward by hand, kept apart so a post-mortem
# does not read an operator stopping the container as a window that closed on time.
WINDOW_CLOSED = "window-closed"
CRASHED = "crashed"
SIGNALLED = "stopped-on-signal"

# Where the model writes, deliberately outside the Run's record: the sandbox makes the working
# directory the one place the vendor's agent may write, and a Run whose stream sat in there would
# be handing the model the file its own stall is judged from.
WORKDIRS = Path("/state/work")

# The tail's Steps belong to the Run rather than to any Attempt, and are addressed as such.
TAIL = "run-tail"

# How long the loop waits when Order has nothing eligible **right now**. Short, because what is
# being waited for is the next Intake and a Challenge released at 13:00 is worth noticing; cheap,
# because `Intake.due` is a memory read and `sync` holds its own clock.
#
# Not a `schedule.Dials` value, though every other uncalibrated v1 number is one: `Dials` is what
# the *scheduler* turns, and a replayed Run re-ranks a stored stream at other weights. This turns
# nothing that is ranked — it is how often a loop with nothing to do asks again, bounded by
# `Intake.cycle_seconds` above it and by `out_of_time()` below.
IDLE_SECONDS = 15.0


class Steps:
    """One Attempt's Step numbers, in the one counter its four writers share.

    A deploy, the recon cascade, the model's own commands and a Flag submission are all Steps of one
    Attempt, and each module numbers from wherever it is told to — two counters would put two Steps
    at the same address (`solver/record.py`). The counter belongs to whoever owns the Attempt, which
    is this module, and it is handed to `Instances` and `Flags` at construction.
    """

    def __init__(self) -> None:
        self.spent = 0

    def restart(self) -> None:
        self.spent = 0

    def spend(self) -> int:
        self.spent += 1
        return self.spent

    def next_index(self) -> int:
        return self.spent + 1

    def reached(self, index: int) -> None:
        """Catch up to a Step somebody else numbered — the recon cascade and the adapter are each
        handed a first index and count on from it."""
        self.spent = max(self.spent, index)


@dataclass
class Pending:
    """What the submission gate held back on one Challenge, kept for the reserved tail.

    Held candidates are reported rather than dropped precisely so this can exist: a candidate nobody
    submitted and nobody recorded is afterwards indistinguishable from one that was never found, and
    at the end of a Run there is no later Attempt for the reserve to be reserved *for*.
    """

    challenge_id: int | str
    slots: Slots
    workdir: Path
    candidates: tuple[Candidate, ...] = ()


@dataclass
class Ending:
    """How the Run finished, as the entry point needs to hear it — and as its exit code."""

    cause: str
    attempts: int = 0
    flags: tuple[str, ...] = ()
    left_held: tuple[str, ...] = ()
    # Why the leak sweep could not run, where it could not. Its own field rather than an empty
    # `left_held`, because a sweep that never reached the Board and one that found nothing held are
    # otherwise byte-identical — and the second is a clean Run while the first is capacity nobody
    # can account for.
    unswept: str = ""
    # The sentence behind a crash. Empty on every other ending, because a Run that ended on its
    # clock has nothing to explain.
    detail: str = ""

    @property
    def clean(self) -> bool:
        return self.cause != CRASHED and not self.left_held and not self.unswept


@dataclass
class _Held:
    """One hold: the Challenge, the clock it is worked under, and everything that outlives a turn
    within it — the Lease, the working directory, and the five things that cross a boundary."""

    pick: Pick
    workdir: Path
    boundary: Boundary
    deadline: Deadline
    attempt_id: str
    began: dt.datetime
    recon_block: str = ""
    lease: Lease | None = None
    turns: int = 0
    checkpoints: int = 0
    steps: int = 0
    cause: str = ""
    flag: str = ""
    approach: str = ""
    artefacts: tuple[Path, ...] = ()


class Run:
    """One unattended outing at one Board, from the first Attempt to the last thing the tail does.

    Every collaborator is handed in. Nothing here reads the environment, opens a window or decides
    an identity: all three were settled before the loop, which is what makes a refusal loud at boot
    rather than silent at 13:00 with nobody there.
    """

    def __init__(
        self,
        *,
        profile: Profile,
        recorder: Recorder,
        intake: Intake,
        scheduler: Scheduler,
        flags: Flags,
        instances: Instances,
        steps: Steps,
        chain: tuple[Credential, ...],
        invocation: Invocation = Invocation(),
        workdirs: Path = WORKDIRS,
        launch: codex.Launch | None = None,
        idle_seconds: float = IDLE_SECONDS,
        now: Callable[[], dt.datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.profile = profile
        self._recorder = recorder
        self._intake = intake
        self._scheduler = scheduler
        self._flags = flags
        self._instances = instances
        self._steps = steps
        self._chain = chain
        self._invocation = invocation
        self._breaker = Breaker()
        self._workdirs = Path(workdirs)
        self._launch = launch
        self._idle_seconds = idle_seconds
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._sleep = sleep
        self._boundaries: dict[int | str, Boundary] = {}
        self._pending: dict[int | str, Pending] = {}
        # Every candidate already put to the submission gate, per Challenge. Turns of one Attempt
        # share an `attempt_id`, so a second turn's sweep re-finds the first turn's Observations —
        # and a Board-wide incorrect-submission limiter makes sending the same wrong Flag twice a
        # slot spent on every other Challenge's behalf.
        self._offered: dict[int | str, set[str]] = {}
        self._leases: dict[int | str, Lease] = {}
        self._won: list[str] = []
        self._attempts = 0
        self._in_flight: Deadline | None = None
        self._stopping = ""
        self._crashed = ""

    def stop(self, why: str = SIGNALLED) -> None:
        """End the Run at the next boundary, and bring the turn in flight forward to now.

        Installed as the signal handler by the entry point, so `docker stop` reaches the tail rather
        than the ten-second grace and a SIGKILL. It shortens rather than kills: the tail still has to
        submit what is pending and reclaim every Instance, and a process that died on the signal
        would leave both behind.
        """
        self._stopping = why
        if self._in_flight is not None:
            self._in_flight.shorten(self._now())

    def work(self) -> Ending:
        """Take Attempts until the clock can no longer buy one, then run the reserved tail.

        A crash is one of the two ways a Run ends, and it still owes the tail: an Instance held when
        the process exits is capacity nobody reclaims, and a candidate the gate held back is a Flag
        we found and never sent. So the loop is caught here rather than at the entry point, where
        the tail would already have been skipped.
        """
        try:
            self._loop()
        except Exception as broken:
            self._stopping = CRASHED
            self._crashed = f"{type(broken).__name__}: {broken}"
        return self._tail()

    def _loop(self) -> None:
        while not self._stopping:
            if self._intake.due():
                self._intake.sync()
            pick = self._scheduler.acquire(self._intake.snapshot, leased=tuple(self._leases))
            if pick is None:
                if self._scheduler.out_of_time():
                    break
                # Nothing is eligible *right now*: what remains is being worked, or is `shared` and
                # deployable by nobody but an admin. It is never the Board having emptied, so this
                # waits for the next Intake rather than ending the Run.
                self._sleep(self._idle_seconds)
                continue
            held = self._attempt(pick)
            self._scheduler.release(Ended(pick.challenge.challenge_id, held.cause, self._spent(held), held.checkpoints))
            # Nothing is kept: the Attempt released its own Lease as it closed, so anything the
            # ledger still lists at this boundary is a leak by definition.
            self._sweep(attempt_id=held.attempt_id, keeping=None)
            if backoff := self._breaker.backoff():
                self._sleep(backoff)

    def _attempt(self, pick: Pick) -> _Held:
        """One Attempt: open it with recon, take turns until something ends it, close it.

        The Attempt is the hold rather than the turn, which is `CONTEXT.md`'s definition read
        literally — it runs from the recon that opens it to the Cut or the Flag that ends it, and an
        early stop is neither. `record.CAUSES` agrees: it has no name for *the model stopped*,
        because that is not something that ends anything.
        """
        challenge = pick.challenge
        workdir, artefacts = self._staged(challenge)
        held = _Held(
            pick=pick,
            workdir=workdir,
            boundary=self._boundaries.setdefault(challenge.challenge_id, Boundary()),
            deadline=Deadline(budget=pick.deadline),
            attempt_id=f"{challenge.challenge_id}-{pick.attempt_sequence}",
            began=self._now(),
            artefacts=artefacts,
        )
        self._steps.restart()
        # Before the open, and it has to be: `attempt_open` records the Instance this Attempt was
        # given, and there is no Instance to record until the deploy has answered. So the deploy is
        # Step 1 of an Attempt whose `attempt-open` line comes after it — that line describes the
        # Attempt rather than starting it, and a reader of the stream reconciles on `attempt_id`.
        self._deploy(held)
        self._recorder.attempt_open(
            attempt_id=held.attempt_id,
            challenge_id=challenge.challenge_id,
            challenge_name=challenge.name,
            category=challenge.category,
            challenge_type=challenge.challenge_type,
            solves_at_open=challenge.solves,
            tier=pick.tier,
            budget_s=pick.budget_s,
            attempt_sequence=pick.attempt_sequence,
            instance_until=held.lease.until.isoformat() if held.lease and held.lease.until else None,
            order_ranks=pick.order_ranks,
            exploring=pick.exploring,
        )
        self._in_flight = held.deadline
        try:
            held.recon_block = self._recon(held, challenge)
            while not self._turn(held, challenge):
                self._renew(held)
        except Exception as broken:
            # The Attempt is closed whatever happened to it. A stream holding an `attempt-open` with
            # no terminator is a Run the eval cannot read at all, and #16's schema is meant stable
            # from v1 — so the crash is recorded here, on the Attempt, and again at Run close.
            held.cause = CRASHED
            self._stopping, self._crashed = CRASHED, f"{type(broken).__name__}: {broken}"
        self._in_flight = None
        self._attempts += 1
        self._recorder.attempt_close(
            attempt_id=held.attempt_id,
            cause=held.cause,
            approach_label=held.approach,
            solves_at_close=self._solves_now(challenge),
            extensions_granted=held.deadline.granted,
            flag=held.flag or None,
        )
        self._end_lease(held)
        return held

    def _turn(self, held: _Held, challenge: Sighting) -> bool:
        """One invocation of the vendor's agent, and whether it ended the Attempt.

        The `Watch` is fresh per turn and the `Boundary` is not: the counters are about one
        trajectory and a turn that re-orients itself is not the same trajectory, while the workdir
        and the five carried things are the Challenge's and outlive both.
        """
        watch = Watch(deadline=held.deadline, steps=self._steps.spent)
        said: list[str] = []
        # What the *model* observed, counted apart from `watch.steps`. The Watch is seeded with the
        # Attempt's Steps so far because recon **is** the opening of an Attempt and its probes are
        # its first Steps — which means `watch.steps` is never zero, and the circuit breaker's whole
        # question is *did this spend no Steps at all*. Recon answers that question for the recon.
        observed = 0
        text = prompt.compose(
            challenge=challenge,
            rules=self.profile.rules,
            boundary=held.boundary,
            recon=held.recon_block,
            workdir=held.workdir,
            budget_s=held.pick.budget_s,
            lease=held.lease,
        )
        for taken in codex.run_attempt(
            text,
            held.workdir,
            held.deadline,
            recorder=self._recorder,
            attempt_id=held.attempt_id,
            chain=self._chain,
            invocation=self._invocation,
            first_step=self._steps.next_index(),
            launch=self._launch,
            now=self._now,
        ):
            self._steps.reached(taken.step_index)
            if taken.kind == COMMAND:
                observed += 1
                watch.observed(taken.command, exit_code=taken.exit_code, digest=taken.digest)
            elif taken.kind == CLAIM:
                said.append(taken.shown)
                watch.said(taken.shown, now=self._now())
            if watch.cause(self._now()):
                # The one lever the orchestrator has over a child already running: bring the kill
                # forward, and let the adapter end every command in flight and reap. Abandoning the
                # iterator instead would leave a process behind holding the working directory open.
                held.deadline.shorten(self._now())
        held.turns += 1
        held.checkpoints += len(watch.checkpoints)
        held.approach = label(named) if (named := _approach(said)) else held.approach
        # The breaker is told about every turn, not only the ones no counter ended — a turn that
        # spent the whole budget and observed nothing is the dead-adapter shape it exists for. Its
        # verdict wins over a Cut, because a Cut would say the Challenge stopped this Attempt and a
        # Solver broken at its own end is not something the Challenge did.
        broken = self._breaker.closed(str(challenge.challenge_id), steps=observed)
        cause = broken or watch.cause(self._now())
        if self._submit(held, challenge, said).solved:
            cause = FLAG
        held.boundary.closed(watch, approach=held.approach, cause=cause or STOPPED)
        if cause or self._stopping:
            held.cause = cause or self._budget_cause(held)
            return True
        return False

    def _budget_cause(self, held: _Held) -> str:
        """The cause an Attempt cut short by the operator is closed with.

        `stop()` shortened the deadline, so this is the budget running out — brought forward by
        hand, which the run-close line says and the Attempt's own does not need to.
        """
        return held.deadline.spent(self._now()) or CUT_BUDGET

    def _submit(self, held: _Held, challenge: Sighting, said: list[str]) -> Outcome:
        """Sweep this turn's Observations for the Board's Flag wrapper, and spend what the gate
        allows. What it holds back is kept for the reserved tail rather than dropped."""
        offered = self._offered.setdefault(challenge.challenge_id, set())
        candidates = tuple(
            one for one in self._flags.candidates(attempt_id=held.attempt_id, said=said) if one.text not in offered
        )
        if not candidates:
            return Outcome()
        offered.update(one.text for one in candidates)
        outcome = self._flags.submit(
            candidates,
            attempt_id=held.attempt_id,
            challenge_id=challenge.challenge_id,
            slots=challenge.slots,
            workdir=held.workdir,
            lease=held.lease,
        )
        if outcome.held:
            waiting = self._pending.get(challenge.challenge_id)
            carried = (waiting.candidates if waiting else ()) + outcome.held
            self._pending[challenge.challenge_id] = Pending(
                challenge.challenge_id, challenge.slots, held.workdir, carried
            )
        if outcome.solved:
            self._pending.pop(challenge.challenge_id, None)
            self._leases.pop(challenge.challenge_id, None)
            held.lease = None
            if outcome.flag:
                held.flag = outcome.flag
                self._won.append(outcome.flag)
        return outcome

    def _staged(self, challenge: Sighting) -> tuple[Path, tuple[Path, ...]]:
        """The Challenge's working directory and the Board's own files inside it.

        A copy rather than the Intake original: the model unpacks archives and edits what it finds,
        and Intake's copy is change detection's evidence that we still hold what the Board listed.
        The directory is the memory that crosses an Attempt boundary, so it is keyed by Challenge and
        never cleared between Attempts.

        The artefacts are answered separately rather than read back off the directory, because by
        the second Attempt the directory also holds whatever the model made — and a recon cascade
        over the model's own output is recon over a Claim.
        """
        workdir = self._workdirs / str(challenge.challenge_id)
        workdir.mkdir(parents=True, exist_ok=True)
        staged = []
        for attachment in challenge.attachments:
            if attachment.held and attachment.path is not None:
                landing = workdir / attachment.name
                if not landing.exists():
                    shutil.copy2(attachment.path, landing)
                staged.append(landing)
        return workdir, tuple(staged)

    def _recon(self, held: _Held, challenge: Sighting) -> str:
        found = recon.recon(
            challenge.description,
            held.artefacts,
            flag_pattern=self.profile.rules.flag_wrapper,
            recorder=self._recorder,
            attempt_id=held.attempt_id,
            first_step=self._steps.next_index(),
        )
        self._steps.reached(self._steps.spent + len(found.probes))
        return found.block()

    def _deploy(self, held: _Held) -> None:
        """Take a Lease where this Challenge needs one, and narrow the Attempt's clock to it.

        The seam's own default branch answers for a Challenge that is not `dynamic_iac`, so there is
        no type test here: the rule about which types are instanced lives in one place.
        """
        terms = held.pick.challenge.terms
        answer = self._instances.deploy(terms, attempt_id=held.attempt_id)
        if answer.lease is None:
            return
        held.lease = answer.lease
        self._leases[terms.challenge_id] = answer.lease
        held.deadline.instance = answer.lease.attempt_deadline(held.pick.deadline)

    def _renew(self, held: _Held) -> None:
        """Renew late and only at a turn boundary — a renew sets `until = now + timeout`, so every
        second renewed early is a second thrown away."""
        if held.lease is None or not held.lease.due_for_renewal(self._now()):
            return
        answer = self._instances.renew(held.lease, attempt_id=held.attempt_id)
        if answer.lease is None:
            return
        held.lease = answer.lease
        self._leases[held.lease.challenge_id] = answer.lease
        held.deadline.instance = answer.lease.attempt_deadline(held.pick.deadline)

    def _end_lease(self, held: _Held) -> None:
        """Release the Instance the Attempt was worked on. A Flag already released it, so this is
        the Cut path — and holding one past the Attempt costs mana nobody reclaims."""
        if held.lease is None:
            return
        self._instances.terminate(held.lease.challenge_id, attempt_id=held.attempt_id)
        self._leases.pop(held.lease.challenge_id, None)
        held.lease = None

    def _spent(self, held: _Held) -> float:
        """Wall-clock under this hold, which is what the ledger's anti-livelock term is made of. A
        clock reading and never a judgement, so an Attempt that crashed at once still costs what it
        took to crash."""
        return max(0.0, (self._now() - held.began).total_seconds())

    def _sweep(self, *, attempt_id: str, keeping: str | None) -> tuple[tuple[str, ...], str]:
        """The leak sweep, at every Attempt boundary and again at Run close — and why it could not
        run, where it could not.

        Asked of the Board rather than of a private copy, because a private copy goes stale in
        exactly the situation it would exist for. Skipped entirely where the Board runs no
        chall-manager: the ledger is a plugin page, and asking a Board without one is a fault rather
        than an empty answer — that skip is the one case that is genuinely nothing to report.

        A sweep that could not reach the Board answers with the fault rather than with an empty
        hand. Swallowing it would make a Run that never looked indistinguishable from one that
        looked and found nothing, and only one of those is clean.
        """
        if not self.profile.instances_reachable:
            return (), ""
        known = {one.name: one.challenge_id for one in self._intake.snapshot.challenges}
        try:
            swept = self._instances.sweep(attempt_id=attempt_id, keeping=keeping, known=known)
        except (BoardFailure, OSError) as unreadable:
            return (), f"the Instance ledger could not be read, so nothing was reclaimed — {unreadable}"
        return swept.still_held + swept.unresolved, ""

    def _solves_now(self, challenge: Sighting) -> int:
        current = next(
            (one for one in self._intake.snapshot.challenges if one.challenge_id == challenge.challenge_id), None
        )
        return current.solves if current else challenge.solves

    def _tail(self) -> Ending:
        """The reserved tail, in the order its four jobs have to happen in.

        The scheduler has already held this time back — `acquire` never returns a budget that eats
        into `Dials.tail_seconds` — so what is left here is doing the jobs, not finding the room.
        Submitting comes first because it is the only one that can still score, and reclaiming comes
        before the record because an Instance still held when the process exits is capacity nobody
        gets back: chall-manager never evicts.
        """
        self._steps.restart()
        self._last_call()
        left_held, unswept = self._reclaim()
        cause = self._stopping or WINDOW_CLOSED
        self._recorder.run_close(cause=f"{cause} — {self._crashed}" if self._crashed else cause)
        return Ending(
            cause=cause,
            attempts=self._attempts,
            flags=tuple(self._won),
            left_held=left_held,
            unswept=unswept,
            detail=self._crashed,
        )

    def _last_call(self) -> None:
        """Spend what the gate reserved. There is no later Attempt for the reserve to be reserved
        *for*, so a candidate carried out of a Run unsubmitted is a Flag we found and never sent."""
        for pending in list(self._pending.values()):
            outcome = self._flags.submit(
                pending.candidates,
                attempt_id=TAIL,
                challenge_id=pending.challenge_id,
                slots=pending.slots,
                workdir=pending.workdir,
                lease=self._leases.get(pending.challenge_id),
                last_call=True,
            )
            if outcome.flag:
                self._won.append(outcome.flag)
        self._pending.clear()

    def _reclaim(self) -> tuple[tuple[str, ...], str]:
        """Destroy every Instance, and answer with whatever the Board would not let go of.

        Reported rather than swallowed: a terminate the plugin refused and a ledger row nothing on
        the Board answers to are both capacity that stays spent, and a Run that could not say so
        would look like one that left nothing behind.
        """
        for challenge_id in list(self._leases):
            self._instances.terminate(challenge_id, attempt_id=TAIL)
            self._leases.pop(challenge_id, None)
        return self._sweep(attempt_id=TAIL, keeping=None)


def _approach(said: list[str]) -> str:
    """The one line the model writes that crosses a boundary, taken from its own marker.

    Read from the last marked line rather than the first: a turn that names an approach, works, and
    names a better one at the end has told us the second, and the marker is what makes that a choice
    the model made rather than a sentence a parser picked out of a paragraph.
    """
    marked = [line for prose in said for line in prose.splitlines() if line.strip().startswith(APPROACH)]
    return marked[-1].strip()[len(APPROACH) :].strip() if marked else ""
