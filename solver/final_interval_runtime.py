"""Production adapter owning Lane admission and the complete final-interval close."""

from __future__ import annotations

import hashlib

from solver.attempt_executor_contracts import EnvelopeSpec, NetworkPolicy
from solver.final_interval import RunInventory
from solver.lane_topology_contracts import LaneBinding, LaneOutcome, WorkCandidate
from solver.schedule import Ended

TAIL = "run-tail"
WINDOW_CLOSED = "window-closed"
CRASHED = "crashed"


class FinalIntervalRuntime:
    """One deep production boundary for Lane work, submission drain, cleanup and terminality."""

    def __init__(self, run) -> None:
        self._run = run

    def lane_order(self, snapshot, excluded: frozenset[str]) -> tuple[WorkCandidate, ...]:
        run = self._run
        pick = run._scheduler.acquire(snapshot, leased=tuple(run._leases), solved=tuple(run._solved))
        if pick is None or str(pick.challenge.challenge_id) in excluded:
            return ()
        work_id = str(pick.challenge.challenge_id)
        run._lane_picks[work_id] = pick
        budget = max(0.001, (pick.deadline - run._now()).total_seconds())
        profile = run._lane_controller.profile
        envelope = EnvelopeSpec(
            cpu_seconds=budget,
            cpu_quota_us=max(1, profile.global_cpu_quota_us // profile.lanes),
            memory_bytes=max(1, profile.global_memory_bytes // profile.lanes),
            pids=max(1, profile.global_pids // profile.lanes),
            filesystem_bytes=max(1, profile.global_filesystem_bytes // profile.lanes),
            network=NetworkPolicy.DENY,
            wall_seconds=budget,
            cleanup_seconds=profile.global_cleanup_seconds,
        )
        return (
            WorkCandidate(
                work_id,
                min(pick.order_ranks.values()),
                budget,
                1,
                None,
                envelope,
                tier=pick.tier,
            ),
        )

    def lane_execute(self, _sighting, binding: LaneBinding) -> LaneOutcome:
        run = self._run
        pick = run._lane_picks.pop(binding.work_id)
        try:
            held = run._attempt(pick, lane_binding=binding)
            run._sweep(attempt_id=held.attempt_id, keeping=None)
        finally:
            cause = held.cause if "held" in locals() else CRASHED
            spent = run._spent(held) if "held" in locals() else 0.0
            checkpoints = held.checkpoints if "held" in locals() else 0
            run._scheduler.release(Ended(pick.challenge.challenge_id, cause, spent, checkpoints))
        if held.cause == CRASHED:
            # The LaneController contains a final-chance worker failure; it must not cancel the
            # submission reserve and cleanup authority owned by the Run.
            return LaneOutcome.failed(binding, held.cause)
        return LaneOutcome.complete(binding, seconds=run._spent(held))

    def close(self):
        run = self._run
        run._steps.restart()
        final = run._final_interval
        if not run._stopping:
            while run._now() < final.cutoff:
                run._sleep(min(run._idle_seconds, (final.cutoff - run._now()).total_seconds()))
        if run._now() >= final.cutoff:
            self._drain()
        if not run._stopping:
            while run._scheduler.window.left(run._now()) > 0:
                run._sleep(min(run._idle_seconds, run._scheduler.window.left(run._now())))
        cleanup_result: dict[str, object] = {}

        def cleanup():
            left_held, unswept, outcomes = self._reclaim()
            cleanup_result.update(left_held=left_held, unswept=unswept)
            return (
                outcomes
                | {name: "unsettled" for name in left_held}
                | ({"instance-ledger": "unsettled"} if unswept else {})
            )

        def outstanding_submissions():
            if run._final_candidate_queue is None:
                return tuple(
                    hashlib.sha256(candidate.text.encode()).hexdigest()
                    for row in run._pending.values()
                    for candidate in row.candidates
                )
            recorded = run._final_interval.receipt()["submissions"]
            pending = set(run._final_candidate_queue.pending_candidate_ids())
            unsubmitted = tuple(
                candidate for candidate in run._final_candidate_queue.candidate_ids() if candidate not in recorded
            )
            return tuple(dict.fromkeys((*unsubmitted, *sorted(pending))))

        def inventory():
            return RunInventory(
                attempts=tuple(
                    state.attempt_id for state in run._recorder.generations.projection().generations if state.active
                ),
                instances=tuple(str(key) for key in run._leases),
                submissions=outstanding_submissions(),
            )

        terminal = final.close(
            inventory,
            cleanup,
            allow_early_terminal=bool(run._stopping),
        )
        cause = run._stopping or WINDOW_CLOSED
        run._recorder.run_close(cause=f"{cause} — {run._crashed}" if run._crashed else cause)
        return run._ending_type(
            cause=cause,
            attempts=run._attempts,
            flags=tuple(run._won),
            left_held=tuple(
                dict.fromkeys((*cleanup_result.get("left_held", ()), *terminal["remaining"].get("instances", ())))
            ),
            unswept=str(cleanup_result.get("unswept", "")),
            detail=run._crashed or str(terminal["disposition"]),
        )

    def _drain(self) -> None:
        run = self._run
        if run._final_candidate_queue is not None:
            candidate_ids = run._final_candidate_queue.candidate_ids()
            run._final_interval.drain(candidate_ids, run._final_candidate_queue.submit)
            run._pending.clear()
            return
        for pending in list(run._pending.values()):
            candidates = {
                hashlib.sha256(candidate.text.encode()).hexdigest(): candidate for candidate in pending.candidates
            }

            def submit(candidate_id: str) -> str:
                outcome = run._flags.submit(
                    (candidates[candidate_id],),
                    attempt_id=TAIL,
                    challenge_id=pending.challenge_id,
                    slots=run._slots_now(pending.challenge_id),
                    workdir=pending.workdir,
                    lease=run._leases.get(pending.challenge_id),
                    last_call=True,
                    generation_id=pending.generation_id,
                )
                if outcome.flag:
                    run._won.append(outcome.flag)
                if outcome.solved:
                    return "accepted"
                return "rejected" if outcome.graded else "refused-and-spent"

            run._final_interval.drain(tuple(candidates), submit)
        run._pending.clear()

    def _reclaim(self) -> tuple[tuple[str, ...], str, dict[str, str]]:
        run = self._run
        outcomes = {}
        for challenge_id in list(run._leases):
            identity = f"instance:{challenge_id}"
            try:
                answer = run._instances.terminate(challenge_id, attempt_id=TAIL)
            except Exception:
                outcomes[identity] = "unsettled"
            else:
                outcomes[identity] = "released" if answer.released else "unsettled"
                if answer.released:
                    run._leases.pop(challenge_id, None)
        try:
            left_held, unswept = run._sweep(attempt_id=TAIL, keeping=None)
        except Exception as error:
            left_held, unswept = tuple(str(key) for key in run._leases), type(error).__name__
        for state in run._recorder.generations.projection().generations:
            if not state.active:
                continue
            identity = f"attempt:{state.attempt_id}"
            try:
                run._recorder.interrupt_generation(state.generation_id)
            except Exception:
                outcomes[identity] = "unsettled"
            else:
                outcomes[identity] = "released"
        return left_held, unswept, outcomes
