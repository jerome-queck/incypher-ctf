"""Submission successor-epoch Recovery and semantic probation."""

from __future__ import annotations

from solver.recovery.contracts import ProbationOutcome
from solver.recovery.runtime import AuthoritativeChange, DomainRecovery
from solver.write_reservation_contracts import ReservationState

SUBMISSION_ADAPTER = "submission-successor-v1"


def submission_recovery(*, pending_effect, closed_epoch, board_identity, epochs, fence, authority):
    advanced: list[int] = []

    def project():
        if fence.closed_effect_at_epoch(closed_epoch) != pending_effect:
            return None
        return AuthoritativeChange(
            f"epoch-{closed_epoch}",
            f"epoch-{closed_epoch + 1}",
            "closed-ambiguity-submission-epoch",
        )

    def apply():
        advanced[:] = [epochs.advance_after(board_identity, pending_effect, closed_epoch)]
        return advanced[0] == closed_epoch + 1

    def probation():
        successor = [
            reservation
            for reservation in authority.reservations()
            if reservation.identity.operation == "board.submit-candidate"
            and (reservation.observation or {}).get("submission_epoch") == closed_epoch + 1
        ]
        if any(item.state is ReservationState.COMMITTED for item in successor):
            return ProbationOutcome.PASSED
        if any(item.state in {ReservationState.POSSIBLY_SENT, ReservationState.TERMINAL} for item in successor):
            return ProbationOutcome.FAILED
        return ProbationOutcome.UNSETTLED

    return DomainRecovery(
        "submission-epoch",
        project,
        apply,
        probation,
        capture=lambda: pending_effect.encode(),
        adapter_id=SUBMISSION_ADAPTER,
        adapter_config={
            "pending_effect": pending_effect,
            "closed_epoch": str(closed_epoch),
            "board_identity": board_identity,
        },
    )
