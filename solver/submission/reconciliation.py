"""Boot-owned reconciliation lifecycle for open ambiguous Board submissions."""

from __future__ import annotations

import threading
from collections.abc import Callable

from solver.board_broker_contracts import BoardOutcome
from solver.submission.ambiguity_types import AuthenticatedSubmissionEvidence, Evidence


class SubmissionReconciler:
    """Resume every open fence and drive its bounded evidence schedule."""

    def __init__(self, fence, *, interval_seconds: float = 0.25):
        self._fence = fence
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        if self._thread is not None:
            raise RuntimeError("submission reconciliation is already started")
        self._thread = threading.Thread(target=self._run, name="submission-reconciliation", daemon=True)
        self._thread.start()
        return self

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._interval * 2))
            if self._thread.is_alive():
                raise RuntimeError("submission reconciliation did not stop")

    def cycle(self):
        for pending in self._fence.pending():
            self._fence.reconcile(pending)

    def _run(self):
        while not self._stop.is_set():
            self.cycle()
            self._stop.wait(self._interval)


def broker_evidence_probe(open_client: Callable, socket_path, binding):
    """Build one authenticated submission-ledger probe with broker-side secrets."""

    def probe(pending):
        client = open_client(socket_path, binding, scope="board.submit")
        try:
            result = client.submission_ledger(pending.effect_id)
            if result.outcome is not BoardOutcome.ANSWERED:
                return Evidence.unsettled(f"board-broker:{result.outcome.value}")
            try:
                return AuthenticatedSubmissionEvidence.from_broker(result)
            except ValueError:
                return Evidence.unsettled("board-broker:unsettled-ledger")
        finally:
            client.close()

    return probe
