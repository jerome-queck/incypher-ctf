"""Production composition for serial, ambiguity-fenced Board submission."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

from solver.board_broker import BoardBrokerClient
from solver.submission.ambiguity import AmbiguousSubmissionFence
from solver.submission.ambiguity_adapter import AmbiguityAwareSerialSubmission
from solver.submission.authority import (
    ACCOUNT_POST_INTERVAL_SECONDS,
    SUBMISSION_REQUEST_DEADLINE_SECONDS,
    SUBMISSION_UNCERTAINTY_MARGIN_SECONDS,
    SerialSubmission,
)
from solver.submission.reconciliation import SubmissionReconciler, broker_evidence_probe


@dataclass(frozen=True)
class SubmissionRuntime:
    submission: AmbiguityAwareSerialSubmission
    fence: AmbiguousSubmissionFence
    reconciler: SubmissionReconciler

    def quiesce(self):
        self.reconciler.close()

    def close(self):
        self.quiesce()
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
    sleep=time.sleep,
    post_interval_seconds=ACCOUNT_POST_INTERVAL_SECONDS,
    request_deadline_seconds=SUBMISSION_REQUEST_DEADLINE_SECONDS,
    uncertainty_margin_seconds=SUBMISSION_UNCERTAINTY_MARGIN_SECONDS,
    open_client=BoardBrokerClient.open,
    reconcile_interval=0.25,
    recovery=None,
):
    fence = None
    registry = None

    def binding_for(pending):
        if fence is None:
            raise RuntimeError("submission fence is not composed")
        return fence.reconciliation_binding(pending)

    def recover_closed(pending):
        if recovery is None or epoch_authority is None or not board_identity:
            return
        from solver.recovery.contracts import FaultKind
        from solver.recovery.submission import submission_recovery

        before = epoch_authority.current(board_identity)
        recovery.handle(
            kind=FaultKind.SUBMISSION_AMBIGUITY,
            fault_id=f"submission:{pending.effect_id}",
            scope="run-shared:submission",
            generation_id="submission-reconciliation",
            evidence=pending.provenance or pending.disposition.value,
            failed_action_value=f"epoch-{before}",
            original_deadline=dt.datetime.fromtimestamp(wall_time() + 180.0, dt.timezone.utc),
            recovery=submission_recovery(
                pending_effect=pending.effect_id,
                closed_epoch=before,
                board_identity=board_identity,
                epochs=epoch_authority,
                fence=fence,
                authority=recorder.write_authority,
            ),
        )

    fence = AmbiguousSubmissionFence(
        state,
        recorder.write_authority,
        run_id=run_id,
        boot_id=boot_id,
        monotonic=monotonic,
        wall_time=wall_time,
        probe=broker_evidence_probe(open_client, board_broker_path, binding_for),
        on_close=recover_closed,
    )
    if recovery is not None and epoch_authority is not None and board_identity:
        from solver.recovery.runtime import RecoveryRegistry
        from solver.recovery.submission import SUBMISSION_ADAPTER, submission_recovery

        registry = RecoveryRegistry()
        registry.register(
            SUBMISSION_ADAPTER,
            lambda config: submission_recovery(
                pending_effect=config["pending_effect"],
                closed_epoch=int(config["closed_epoch"]),
                board_identity=config["board_identity"],
                epochs=epoch_authority,
                fence=fence,
                authority=recorder.write_authority,
            ),
        )
        recovery.replay(registry)
    serial = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        timestamp,
        board_broker_path,
        open_client=lambda path, candidate_binding: open_client(path, candidate_binding, scope="board.submit"),
        identity_for=identity_for,
        sleep=sleep,
        post_interval_seconds=post_interval_seconds,
        request_deadline_seconds=request_deadline_seconds,
        uncertainty_margin_seconds=uncertainty_margin_seconds,
    )
    reconciler = SubmissionReconciler(fence, interval_seconds=reconcile_interval).start()
    submission = AmbiguityAwareSerialSubmission(
        serial,
        fence,
        identity_for,
        epoch_authority=epoch_authority,
        board_identity=board_identity,
        quiesce=reconciler.close,
        probation=lambda: recovery.replay(registry) if recovery is not None and registry is not None else (),
    )
    return SubmissionRuntime(submission, fence, reconciler)
