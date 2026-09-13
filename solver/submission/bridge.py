"""Immediate Lead-Candidate admission and generation-bound Board dispatch."""

from __future__ import annotations

from solver.candidate_admission import CandidateAdmission
from solver.candidate_admission_contracts import (
    CandidateDerivation,
    CandidateDisposition,
    CandidateProposal,
    CandidateProvenance,
    DerivationKind,
)
from solver.capability import CapabilityBinding
from solver.event_store_storage import digest_bytes
from solver.flag import Candidate
from solver.lead_contracts import CandidateProposal as LeadCandidateProposal
from solver.lead_contracts import LeadOutcome
from solver.submission.authority import SerialSubmission, SubmissionResult
from solver.lead_v1_adapter import V1LeadTurn


class CandidateSubmissionBridge:
    """Consume one durable Lead proposal without another scheduling turn."""

    def __init__(self, admission: CandidateAdmission, submission: SerialSubmission) -> None:
        self._admission = admission
        self._submission = submission

    def __call__(self, incoming: V1LeadTurn, outcome: LeadOutcome) -> SubmissionResult | None:
        proposal = outcome.proposal
        if not isinstance(proposal, LeadCandidateProposal) or not outcome.proposal_id:
            return None
        derivation = self._derivation(proposal, outcome.proposal_id)
        provenance = self._admission.lead_provenance(tuple(proposal.evidence_refs), outcome.proposal_id, derivation)
        ready = self._admission.admit_ready(
            CandidateProposal(
                outcome.proposal_id,
                int(incoming.work_id),
                incoming.generation_id,
                proposal.value.encode(),
                provenance,
            )
        )
        if ready is None:
            return None
        binding = CapabilityBinding(
            incoming.run_id,
            incoming.boot_id,
            incoming.generation_id,
            incoming.lane_id,
            incoming.attempt_id,
            f"submission:{outcome.proposal_id}",
        )
        return self._submission.dispatch(ready, binding=binding)

    @staticmethod
    def _derivation(proposal: LeadCandidateProposal, proposal_id: str) -> CandidateDerivation | None:
        value = proposal.derivation
        if not isinstance(value, dict):
            return None
        return CandidateDerivation(
            DerivationKind(str(value["kind"])),
            tuple(str(item) for item in value["source_digests"]),
            str(value["relationship"]),
            proposal_id,
        )


class ObservedCandidateSubmissionBridge:
    """Cut the v1 Observation sweep over to admitted Candidate authority."""

    def __init__(
        self,
        admission: CandidateAdmission,
        submission: SerialSubmission,
        *,
        run_id: str,
        boot_id: str,
    ) -> None:
        self._admission = admission
        self._submission = submission
        self._run_id = run_id
        self._boot_id = boot_id

    def __call__(
        self, candidate: Candidate, *, attempt_id: str, challenge_id: int, generation_id: str
    ) -> SubmissionResult | None:
        if not candidate.ref:
            return None
        source_digest = self._source(candidate, attempt_id)
        proposal_id = f"{attempt_id}:candidate:{digest_bytes(candidate.text.encode())}"
        ready = self._admission.admit_ready(
            CandidateProposal(
                proposal_id,
                challenge_id,
                generation_id,
                candidate.text.encode(),
                CandidateProvenance(CandidateDisposition.OBSERVED, (source_digest,), ()),
            )
        )
        if ready is None:
            return None
        binding = CapabilityBinding(
            self._run_id,
            self._boot_id,
            generation_id,
            "lane-1",
            attempt_id,
            f"submission:{ready.candidate.identity}",
        )
        return self._submission.dispatch(ready, binding=binding)

    def _source(self, candidate: Candidate, attempt_id: str) -> str:
        expected = self._admission.store.run_dir / candidate.ref
        body_digest = digest_bytes(expected.read_bytes())
        return self._admission.observation_digest(attempt_id=attempt_id, body_digest=body_digest)
