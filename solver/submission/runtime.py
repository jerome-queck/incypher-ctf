"""Production composition for serial, ambiguity-fenced Board submission."""

from __future__ import annotations

import time
from dataclasses import dataclass

from solver.board_broker import BoardBrokerClient
from solver.submission.ambiguity import AmbiguousSubmissionFence
from solver.submission.ambiguity_adapter import AmbiguityAwareSerialSubmission
from solver.submission.authority import SerialSubmission
from solver.submission.reconciliation import SubmissionReconciler, broker_evidence_probe


@dataclass(frozen=True)
class SubmissionRuntime:
    submission: AmbiguityAwareSerialSubmission
    fence: AmbiguousSubmissionFence
    reconciler: SubmissionReconciler

    def close(self):
        self.reconciler.close()
        self.fence.write_receipt()


def compose_submission_runtime(
    *,
    state,
    recorder,
    run_id,
    boot_id,
    board_broker_path,
    timestamp,
    identity_for,
    epoch_authority=None,
    board_identity=None,
    monotonic=time.monotonic,
    wall_time=time.time,
    open_client=BoardBrokerClient.open,
    reconcile_interval=0.25,
):
    fence = None

    def binding_for(pending):
        if fence is None:
            raise RuntimeError("submission fence is not composed")
        return fence.reconciliation_binding(pending)

    fence = AmbiguousSubmissionFence(
        state,
        recorder.write_authority,
        run_id=run_id,
        boot_id=boot_id,
        monotonic=monotonic,
        wall_time=wall_time,
        probe=broker_evidence_probe(open_client, board_broker_path, binding_for),
    )
    serial = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        timestamp,
        board_broker_path,
        open_client=lambda path, candidate_binding: open_client(path, candidate_binding, scope="board.submit"),
        identity_for=identity_for,
    )
    submission = AmbiguityAwareSerialSubmission(
        serial,
        fence,
        identity_for,
        epoch_authority=epoch_authority,
        board_identity=board_identity,
    )
    reconciler = SubmissionReconciler(fence, interval_seconds=reconcile_interval).start()
    return SubmissionRuntime(submission, fence, reconciler)
