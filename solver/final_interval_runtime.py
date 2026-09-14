"""Final-interval orchestration over the Run's explicit shutdown capabilities."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Protocol

from solver.final_candidate_queue import FinalCandidateQueue
from solver.final_interval import FinalIntervalController, RunInventory

if TYPE_CHECKING:
    from solver.run import Ending


class FinalRunPort(Protocol):
    def wait_until(self, deadline: dt.datetime) -> bool: ...

    def interrupted_boot(self) -> Ending: ...

    def final_inventory(self) -> RunInventory: ...

    def reclaim_final_resources(self) -> tuple[tuple[str, ...], str, dict[str, str]]: ...

    def drain_legacy_candidates(self) -> None: ...

    def finish_final_interval(
        self, terminal: Mapping[str, object], left_held: tuple[str, ...], unswept: str
    ) -> Ending: ...


class FinalIntervalRuntime:
    def __init__(
        self,
        controller: FinalIntervalController,
        queue: FinalCandidateQueue | None,
        run: FinalRunPort,
        *,
        now: Callable[[], dt.datetime],
    ) -> None:
        self._final, self._queue, self._run, self._now = controller, queue, run, now

    def close(self) -> Ending:
        final = self._final
        stopped = self._run.wait_until(final.cutoff)
        now = self._now()
        if stopped and now < final.ends_at:
            return self._run.interrupted_boot()
        if final.cutoff <= now < final.ends_at:
            self._drain()
        if self._run.wait_until(final.ends_at) and self._now() < final.ends_at:
            return self._run.interrupted_boot()
        if self._queue is not None:
            self._queue.quiesce()
        self._reconcile_submissions()
        left_held: tuple[str, ...] = ()
        unswept = ""

        def cleanup():
            nonlocal left_held, unswept
            left_held, unswept, outcomes = self._run.reclaim_final_resources()
            return (
                outcomes
                | {f"instance:{name}": "unsettled" for name in left_held}
                | {"run-cleanup": "unsettled" if unswept else "released"}
            )

        terminal = final.close(self._run.final_inventory, cleanup)
        return self._run.finish_final_interval(terminal, left_held, unswept)

    def _reconcile_submissions(self) -> None:
        if self._queue is None:
            return
        recorded = self._final.receipt()["submissions"]
        for candidate_id, outcome in self._queue.submission_dispositions().items():
            row = recorded.get(candidate_id)
            if row is not None and row.get("outcome") == "possibly-sent" and outcome != "pending":
                self._final.reconcile_submission(candidate_id, outcome)

    def _drain(self) -> None:
        if self._queue is None:
            self._run.drain_legacy_candidates()
            return
        self._final.drain(
            self._queue.candidate_ids(),
            lambda candidate_id: self._queue.submit(candidate_id, deadline=self._final.ends_at),
        )
