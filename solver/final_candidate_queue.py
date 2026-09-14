"""Replay-ready Candidate queue consumed by the production final interval."""

from __future__ import annotations

import datetime as dt

from solver.capability import CapabilityBinding
from solver.candidate_admission import CandidateAdmission
from solver.submission.ambiguity_adapter import AmbiguityAwareSerialSubmission
from solver.final_interval_contracts import SubmissionDeferred
from solver.write_reservation import EffectIndeterminate


class FinalCandidateQueue:
    def __init__(
        self,
        admission: CandidateAdmission,
        submission: AmbiguityAwareSerialSubmission,
        *,
        run_id: str,
        boot_id: str,
        generation_id: str = "",
    ) -> None:
        self._admission, self._submission = admission, submission
        self._run_id, self._boot_id = run_id, boot_id
        self._generation_id = generation_id

    def candidate_ids(self) -> tuple[str, ...]:
        return tuple(ready.candidate.identity for ready in self._admission.ready_admissions())

    def pending_candidate_ids(self) -> tuple[str, ...]:
        return self._submission.pending_candidate_ids()

    def submission_dispositions(self) -> dict[str, str]:
        return self._submission.submission_dispositions()

    def prepare_all(self) -> None:
        for admission in self._admission.ready_admissions():
            self._submission.prepare(admission)

    def quiesce(self) -> None:
        self._submission.quiesce()

    def submit(self, candidate_id: str, *, deadline: dt.datetime | None = None) -> str:
        ready = next(row for row in self._admission.ready_admissions() if row.candidate.identity == candidate_id)
        try:
            binding = CapabilityBinding(
                self._run_id,
                self._boot_id,
                self._generation_id or ready.candidate.generation_id,
                "final-interval",
                "final-interval",
                f"submission:{candidate_id}",
            )
            result = self._submission.dispatch(
                ready,
                binding=binding,
                **({"deadline": deadline} if deadline is not None else {}),
            )
        except (EffectIndeterminate, RuntimeError, OSError) as error:
            if self._submission.was_possibly_sent(candidate_id):
                return "possibly-sent"
            self._submission.defer(candidate_id)
            raise SubmissionDeferred("Candidate was fenced before wire") from error
        outcome = result.verdict.outcome if result is not None else ""
        if outcome == "correct":
            return "accepted"
        if outcome in {"incorrect", "already_solved"}:
            return "rejected"
        return "possibly-sent"


__all__ = ["FinalCandidateQueue", "SubmissionDeferred"]
