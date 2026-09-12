"""One sequencer feeds up to two isolated Lanes from freshly recomputed Order."""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable, Collection, Sequence
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import RLock

from solver.event_store_contracts import GenerationDisposition
from solver.lane_topology_contracts import (
    LaneBinding,
    LaneCycleResult,
    LaneJournalState,
    LaneOutcome,
    LaneProfile,
    LaneTimeline,
    LaneVerdict,
    OwnerTermination,
    WorkCandidate,
)
from solver.lane_topology_receipt import write_receipt
from solver.lane_topology_journal import LaneJournal
from solver.work_generation import GenerationFence


OrderPort = Callable[[Collection[str]], Sequence[WorkCandidate]]
ExecutePort = Callable[[LaneBinding], LaneOutcome]


@dataclass
class _CycleState:
    profile: LaneProfile
    started: float = field(default_factory=time.monotonic)
    pending: dict = field(default_factory=dict)
    began: dict[str, float] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)
    admitted: list[LaneBinding] = field(default_factory=list)
    outcomes: dict[str, LaneOutcome] = field(default_factory=dict)
    free_lanes: list[str] = field(init=False)
    peak_resources: int = 0
    peak_envelope: dict[str, int] = field(
        default_factory=lambda: {"cpu_quota_us": 0, "memory_bytes": 0, "pids": 0, "filesystem_bytes": 0}
    )

    def __post_init__(self) -> None:
        self.free_lanes = [f"lane-{ordinal}" for ordinal in range(1, self.profile.lanes + 1)]

    @property
    def remaining(self) -> float:
        return max(0.0, self.profile.global_wall_seconds - (time.monotonic() - self.started))

    @property
    def active_resources(self) -> int:
        return sum(binding.resource_units for binding in self.pending.values())

    def admit(self, binding: LaneBinding, future) -> None:
        self.free_lanes.remove(binding.lane_id)
        self.claimed.add(binding.work_id)
        self.admitted.append(binding)
        self.pending[future] = binding
        self.began[binding.attempt_id] = time.monotonic()
        self.peak_resources = max(self.peak_resources, self.active_resources)
        envelopes = tuple(item.envelope for item in self.pending.values())
        for name in self.peak_envelope:
            total = sum(getattr(envelope, name) for envelope in envelopes)
            self.peak_envelope[name] = max(self.peak_envelope[name], total)

    def close(self, future, outcome: LaneOutcome) -> None:
        binding = self.pending.pop(future)
        self.outcomes[binding.attempt_id] = outcome
        self.free_lanes.append(binding.lane_id)
        self.free_lanes.sort()


class LaneController:
    """Run-scoped Lane capacity; Work identities remain generation-scoped."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        profile: LaneProfile,
        generations: GenerationFence,
        timestamp: Callable[[], str],
        terminate: Callable[[LaneBinding], OwnerTermination],
        hook: Callable[[str], None] | None = None,
    ) -> None:
        if not run_id:
            raise ValueError("Lane topology requires a Run identity")
        self.state = Path(state)
        self.run_id = run_id
        self.profile = profile
        self.generations = generations
        self._timestamp = timestamp
        self._terminate = terminate
        self._hook = hook or (lambda point: None)
        self._lock = RLock()
        self._attempt_sequence = len(generations.projection().generations)
        self._journal = LaneJournal(self.state, self.run_id)
        self._fence_interrupted_lanes()

    def run_cycle(self, order: OrderPort, execute: ExecutePort) -> LaneCycleResult:
        """Keep each free Lane fed directly from fresh Order until no Work is eligible."""

        pool = ThreadPoolExecutor(max_workers=self.profile.lanes, thread_name_prefix="solver-lane")
        cycle = _CycleState(self.profile)
        try:
            self._fill(cycle, pool, order, execute)
            while cycle.pending:
                self._drain(cycle)
                self._fill(cycle, pool, order, execute)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)
        cycle_seconds = time.monotonic() - cycle.started
        if cycle_seconds > self.profile.global_wall_seconds + self.profile.global_cleanup_seconds:
            raise RuntimeError("Lane cycle exceeded its declared work and cleanup clock")
        outcomes = tuple(cycle.outcomes[binding.attempt_id] for binding in cycle.admitted)
        timelines = tuple(self._timeline(outcome) for outcome in outcomes)
        receipt_path = write_receipt(
            self.state,
            run_id=self.run_id,
            profile=self.profile,
            timelines=timelines,
            generation_digest=self.generations.projection().digest,
            control_digest=self._journal.digest,
            peak_resource_units=cycle.peak_resources,
            peak_envelope=cycle.peak_envelope,
            cycle_seconds=cycle_seconds,
            timestamp=self._timestamp(),
        )
        return LaneCycleResult(
            timelines=timelines,
            total_resource_units=cycle.peak_resources,
            parked=(),
            receipt_path=str(receipt_path),
        )

    def _fill(self, cycle: _CycleState, pool, order: OrderPort, execute: ExecutePort) -> None:
        while cycle.free_lanes and cycle.remaining > 0:
            binding = self._admit_one(
                order,
                cycle.claimed,
                cycle.free_lanes[0],
                cycle.active_resources,
                tuple(cycle.pending.values()),
                cycle.remaining,
            )
            if binding is None:
                return
            cycle.admit(binding, pool.submit(execute, binding))

    def _drain(self, cycle: _CycleState) -> None:
        remaining = min(
            cycle.began[binding.attempt_id] + binding.budget_seconds - time.monotonic()
            for binding in cycle.pending.values()
        )
        completed, _ = wait(tuple(cycle.pending), timeout=max(0.0, remaining), return_when=FIRST_COMPLETED)
        for future in completed:
            binding = cycle.pending[future]
            try:
                outcome = future.result()
                if outcome.binding != binding:
                    raise ValueError("Lane executor returned another owner's binding")
                elapsed = max(0.0, time.monotonic() - cycle.began[binding.attempt_id])
                outcome = replace(outcome, seconds=min(binding.budget_seconds, elapsed))
            except BaseException as error:
                outcome = LaneOutcome.failed(binding, type(error).__name__)
            self._close(binding, outcome)
            cycle.close(future, outcome)
        self._expire(cycle)

    def _expire(self, cycle: _CycleState) -> None:
        expired = [
            future
            for future, binding in cycle.pending.items()
            if cycle.began[binding.attempt_id] + binding.budget_seconds <= time.monotonic()
        ]
        for future in expired:
            binding = cycle.pending[future]
            termination = self._terminate(binding)
            if not termination.ended or termination.remaining_processes:
                raise RuntimeError("Lane owner survived bounded termination")
            outcome = LaneOutcome.stalled(binding, termination)
            self._close(binding, outcome)
            cycle.close(future, outcome)

    def _admit_one(
        self,
        order: OrderPort,
        claimed: set[str],
        lane_id: str,
        active_resources: int,
        active_bindings: tuple[LaneBinding, ...],
        remaining_global_seconds: float,
    ) -> LaneBinding | None:
        ranked = tuple(order(frozenset(claimed)))
        candidate = next((item for item in ranked if item.work_id not in claimed), None)
        if candidate is None or not self._fits(candidate, active_bindings, active_resources):
            return None
        admitted_at = self._timestamp()
        budget_seconds = min(candidate.budget_seconds, remaining_global_seconds)
        hard_deadline = (dt.datetime.fromisoformat(admitted_at) + dt.timedelta(seconds=budget_seconds)).isoformat()
        with self._lock:
            self._attempt_sequence += 1
            attempt_id = f"lane-attempt-{self._attempt_sequence:06d}"
        envelope_id = f"{attempt_id}:resource-envelope"
        self._journal.reserve(lane_id, candidate, attempt_id, envelope_id, admitted_at, hard_deadline, budget_seconds)
        self._hook("after_reservation")
        generation = self.generations.acquire(candidate.work_id, attempt_id)
        self._hook("after_generation")
        binding = LaneBinding(
            lane_id=lane_id,
            work_id=candidate.work_id,
            attempt_id=attempt_id,
            generation=generation,
            envelope_id=envelope_id,
            envelope=candidate.envelope,
            lease=candidate.lease,
            order_rank=candidate.order_rank,
            budget_seconds=budget_seconds,
            resource_units=candidate.resource_units,
            tier=candidate.tier,
            order_version=candidate.order_version,
            admitted_at=admitted_at,
            hard_deadline=hard_deadline,
        )
        self._journal.bind(binding)
        self._hook("after_admission")
        return binding

    def _fits(
        self,
        candidate: WorkCandidate,
        active: tuple[LaneBinding, ...],
        active_resource_units: int,
    ) -> bool:
        envelopes = tuple(binding.envelope for binding in active) + (candidate.envelope,)
        return (
            active_resource_units + candidate.resource_units <= self.profile.global_resource_units
            and sum(item.cpu_quota_us for item in envelopes) <= self.profile.global_cpu_quota_us
            and sum(item.memory_bytes for item in envelopes) <= self.profile.global_memory_bytes
            and sum(item.pids for item in envelopes) <= self.profile.global_pids
            and sum(item.filesystem_bytes for item in envelopes) <= self.profile.global_filesystem_bytes
        )

    def _close(self, binding: LaneBinding, outcome: LaneOutcome) -> None:
        disposition = (
            GenerationDisposition.COMPLETE
            if outcome.outcome is LaneVerdict.COMPLETE
            else GenerationDisposition.INTERRUPT
        )
        self._journal.begin_settle(outcome)
        self._hook("after_settlement_reservation")
        self.generations.close(binding.generation.generation_id, disposition)
        self._hook("after_generation_close")
        self._journal.finish_settle(binding.generation.generation_id)

    def _fence_interrupted_lanes(self) -> None:
        """A replacement never resumes an earlier Lane Attempt identity."""

        attempts = set(self._journal.unsettled_attempt_ids)
        for state in self.generations.projection().generations:
            if state.active and state.attempt_id in attempts:
                self.generations.close(state.generation_id, GenerationDisposition.INTERRUPT)
        for attempt_id in attempts:
            self._journal.interrupt_attempt(attempt_id)
        states = {state.generation_id: state for state in self.generations.projection().generations}
        for row in self._journal.closing_rows:
            state = states[row["generation_id"]]
            journal_state = LaneJournalState(row["state"])
            if state.active:
                disposition = (
                    GenerationDisposition.COMPLETE
                    if journal_state.verdict is LaneVerdict.COMPLETE
                    else GenerationDisposition.INTERRUPT
                )
                self.generations.close(state.generation_id, disposition)
            self._journal.finish_settle(state.generation_id)

    @staticmethod
    def _timeline(outcome: LaneOutcome) -> LaneTimeline:
        binding = outcome.binding
        return LaneTimeline(
            lane_id=binding.lane_id,
            work_id=binding.work_id,
            attempt_id=binding.attempt_id,
            generation_id=binding.generation.generation_id,
            envelope_id=binding.envelope_id,
            lease_id=f"{binding.lease.run_id}:{binding.lease.lease_seq}",
            envelope=binding.envelope.document(),
            order_rank=binding.order_rank,
            budget_seconds=binding.budget_seconds,
            resource_units=binding.resource_units,
            tier=binding.tier,
            order_version=binding.order_version,
            admitted_at=binding.admitted_at,
            hard_deadline=binding.hard_deadline,
            outcome=outcome.outcome.value,
            seconds=max(0.0, outcome.seconds),
            reason=outcome.reason,
        )


__all__ = ["ExecutePort", "LaneController", "OrderPort"]
