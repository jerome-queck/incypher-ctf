"""Bounded worker-process replacement through the Incident engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from solver.recovery.incident import Fault, IncidentEngine
from solver.supervisor_process import BootProcess, ProcessOwner


@dataclass(frozen=True)
class WorkerResult:
    boots: int
    exit_code: int | None


class WorkerSupervisor:
    """Own worker trees and spend at most one Incident-authorized replacement."""

    def __init__(
        self,
        engine: IncidentEngine,
        launch: Callable[[str], BootProcess],
        reap: Callable[[], int],
        generation_id: Callable[[], str],
        *,
        grace_seconds: float = 330.0,
    ) -> None:
        self._engine = engine
        self._launch = launch
        self._reap = reap
        self._generation_id = generation_id
        self._grace_seconds = grace_seconds

    def run(self) -> WorkerResult:
        self._engine.replay()
        for index in (1, 2):
            owner = ProcessOwner(
                launch=self._launch,
                reap=self._reap,
                record_signal=lambda _index, _signal: None,
                grace_seconds=self._grace_seconds,
            )
            outcome = owner.run(f"worker-{index:06d}", started=lambda: None)
            if outcome.exit_code == 0:
                return WorkerResult(index, outcome.exit_code)
            if index == 2:
                return WorkerResult(index, outcome.exit_code)
            result = self._engine.report(
                Fault(
                    "worker-process-exit",
                    "worker",
                    self._generation_id(),
                    f"exit_code={outcome.exit_code}; detail={outcome.detail}",
                )
            )
            if result.disposition != "replacement-admitted":
                return WorkerResult(index, outcome.exit_code)
        raise AssertionError("bounded worker loop exhausted")
