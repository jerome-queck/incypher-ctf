"""Replay-ready Candidate queue consumed by the production final interval."""

from __future__ import annotations

from solver.capability import CapabilityBinding
from solver.final_interval_contracts import SubmissionDeferred
from solver.write_reservation import EffectIndeterminate


class FinalCandidateQueue:
    def __init__(self, admission, submission, *, run_id: str, boot_id: str, generation_id: str = "") -> None:
        self._admission, self._submission = admission, submission
        self._run_id, self._boot_id = run_id, boot_id
        self._generation_id = generation_id

    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(ready.candidate.identity for ready in self._admission.ready_admissions())

    def pending_candidate_ids(self) -> tuple[str, ...]:
        return self._submission.pending_candidate_ids()

    def prepare_all(self) -> None:
        prepare = getattr(self._submission, "prepare", None)
        if prepare is not None:
            for admission in self._admission.ready_admissions():
                prepare(admission)

    def submit(self, candidate_id: str) -> str:
        ready = next(row for row in self._admission.ready_admissions() if row.candidate.identity == candidate_id)
        try:
            result = self._submission.dispatch(
                ready,
                binding=CapabilityBinding(
                    self._run_id,
                    self._boot_id,
                    self._generation_id or ready.candidate.generation_id,
                    "final-interval",
                    "final-interval",
                    f"submission:{candidate_id}",
                ),
            )
        except (EffectIndeterminate, RuntimeError, OSError) as error:
            was_possibly_sent = getattr(self._submission, "was_possibly_sent", lambda _candidate_id: True)
            if was_possibly_sent(candidate_id):
                return "possibly-sent"
            defer = getattr(self._submission, "defer", None)
            if defer is not None:
                defer(candidate_id)
            raise SubmissionDeferred("Candidate was fenced before wire") from error
        verdict = getattr(result, "verdict", result)
        outcome = getattr(verdict, "outcome", "")
        if outcome == "correct":
            return "accepted"
        if outcome in {"incorrect", "already_solved"}:
            return "rejected"
        return "possibly-sent"


__all__ = ["FinalCandidateQueue", "SubmissionDeferred"]
