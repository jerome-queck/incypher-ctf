"""Authority boundary from Lead Candidate proposal to sealed submission readiness."""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator, Sequence

from solver.candidate_admission_contracts import (
    CANDIDATE_ADMISSION_RECORDED,
    AdmissionDecision,
    AdmissionOutcome,
    CandidateAdmissionRecorded,
    CandidateDerivation,
    CandidateDisposition,
    CandidateProposal,
    CandidateProvenance,
    DerivationKind,
    ReadyCandidate,
    ReadyAdmission,
)
from solver.candidate_admission_projection import project_candidate
from solver.candidate_admission_receipt import write_receipt
from solver.candidate_template import describes_template
from solver.candidate_vault import CandidateVaultCipher
from solver.event_store import EventStore, GenerationAuthority
from solver.event_store_contracts import OBSERVATION_RECORDED, WORK_GENERATION_RECORDED
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_contracts import LEAD_ENGAGEMENT_RECORDED, LeadClassification, LeadRecord
from solver.redaction import Redactor
from solver.tool_control_contracts import TOOL_CONTROL_RECORDED, ToolRecord
from solver.work_generation import GenerationFence
from solver.wrapper import compiled, found_in

ADMISSION_RULE = "candidate-admission-v1"
MAX_PROPOSAL_BYTES = 4096


class CandidateAdmission:
    def __init__(
        self,
        state: Path,
        run_id: str,
        redactor: Redactor,
        timestamp: Callable[[], str],
        wrappers: Sequence[str],
        vault_key: bytes,
        fence: GenerationFence | None = None,
    ) -> None:
        self.state = Path(state)
        self.run_id = run_id
        self.store = EventStore(state, run_id=run_id, redactor=redactor)
        self._timestamp = timestamp
        self._matchers, self._broken = compiled(wrappers)
        self._fence = fence or GenerationFence(state, run_id, redactor, timestamp)
        self._cipher = CandidateVaultCipher(vault_key)
        self._lock_path = self.store.canonical_dir / "candidate-admission.lock"
        self._lock_path.touch(exist_ok=True, mode=0o600)

    def admit(self, proposal: CandidateProposal) -> AdmissionOutcome:
        with self._admission_lock():
            return self._admit_locked(proposal)

    def _admit_locked(self, proposal: CandidateProposal) -> AdmissionOutcome:
        problem = self._validate(proposal)
        if problem:
            return AdmissionOutcome(problem, detail=problem.value)
        candidates = tuple(dict.fromkeys(found_in(proposal.candidate, self._matchers)))
        if len(candidates) != 1 or candidates[0] != proposal.candidate:
            return AdmissionOutcome(
                AdmissionDecision.MALFORMED,
                detail="proposal must be exactly one Candidate string",
            )
        candidate = candidates[0]
        if self._wrapper_only(candidate):
            return AdmissionOutcome(
                AdmissionDecision.WRAPPER_ONLY,
                detail="proposal describes a Candidate shape, not a value",
            )
        identity_document = {
            "challenge_id": proposal.challenge_id,
            "candidate_digest": self._cipher.digest(candidate),
            "generation_id": proposal.generation_id,
            **proposal.provenance.document(),
            "admission_rule": ADMISSION_RULE,
        }
        candidate_id = digest_bytes(canonical_bytes(identity_document))
        provenance_digest = digest_bytes(canonical_bytes(proposal.provenance.document()))
        existing = next(
            (
                event
                for event in self.store.events()
                if event.event_type == CANDIDATE_ADMISSION_RECORDED
                and event.payload["event_id"] == proposal.proposal_id
            ),
            None,
        )
        if existing is not None:
            if existing.payload["candidate_id"] != candidate_id:
                return AdmissionOutcome(AdmissionDecision.UNSUPPORTED, detail="proposal identity conflicts")
            decision = AdmissionDecision(existing.payload["decision"])
            candidate = project_candidate(existing, self._cipher) if decision is AdmissionDecision.ADMITTED else None
            write_receipt(self.state, self.run_id, self._cipher.key)
            return AdmissionOutcome(decision, candidate)
        duplicate = any(
            ready.challenge_id == proposal.challenge_id
            and ready.generation_id == proposal.generation_id
            and ready.candidate_digest == self._cipher.digest(candidate)
            for ready in self.ready()
        )
        decision = AdmissionDecision.DUPLICATE if duplicate else AdmissionDecision.ADMITTED
        vault_document = {
            "candidate_hex": candidate.hex(),
            **identity_document,
        }
        vault = b"" if duplicate else self._cipher.seal(vault_document)

        def commit(_grant):
            return self.store.append(
                CandidateAdmissionRecorded(
                    event_id=proposal.proposal_id,
                    candidate_id=candidate_id,
                    challenge_id=proposal.challenge_id,
                    generation_id=proposal.generation_id,
                    candidate_digest=self._cipher.digest(candidate),
                    provenance_digest=provenance_digest,
                    admission_rule=ADMISSION_RULE,
                    decision=decision,
                    duplicate_class="equivalent" if duplicate else "",
                    ts=self._timestamp(),
                ),
                body=vault,
            )

        authority, event = self._fence.authorize_and_commit(
            proposal.generation_id,
            GenerationAuthority.CANDIDATE,
            commit,
            evidence=canonical_bytes({"proposal_id": proposal.proposal_id, "candidate_id": candidate_id}),
        )
        if not authority.accepted or event is None:
            return AdmissionOutcome(AdmissionDecision.STALE_GENERATION, detail=authority.classification.value)
        candidate = None if duplicate else project_candidate(event, self._cipher)
        write_receipt(self.state, self.run_id, self._cipher.key)
        return AdmissionOutcome(decision, candidate)

    def ready(self) -> tuple[ReadyCandidate, ...]:
        return tuple(
            project_candidate(event, self._cipher)
            for event in self.store.events()
            if event.event_type == CANDIDATE_ADMISSION_RECORDED and event.payload["decision"] == "admitted"
        )

    def admit_ready(self, proposal: CandidateProposal) -> ReadyAdmission | None:
        """Admit and return its canonical queue position without exposing EventStore queries."""
        outcome = self.admit(proposal)
        if outcome.decision not in {AdmissionDecision.ADMITTED, AdmissionDecision.DUPLICATE}:
            return None
        events = tuple(
            event
            for event in self.store.events()
            if event.event_type == CANDIDATE_ADMISSION_RECORDED
            and event.payload["decision"] == AdmissionDecision.ADMITTED.value
            and event.payload["generation_id"] == proposal.generation_id
        )
        target = next(
            (
                event
                for event in events
                if event.payload["challenge_id"] == proposal.challenge_id
                and event.payload["candidate_digest"] == self._cipher.digest(proposal.candidate)
            ),
            None,
        )
        if target is None:
            return None
        candidate = project_candidate(target, self._cipher)
        predecessors = tuple(str(event.payload["candidate_id"]) for event in events if event.sequence < target.sequence)
        return ReadyAdmission(candidate, str(target.payload["ts"]), target.sequence, predecessors)

    def observation_digest(self, *, attempt_id: str, body_digest: str) -> str:
        """Resolve one projected Observation body at the admission authority boundary."""
        return next(
            event.event_digest
            for event in reversed(self.store.events())
            if event.event_type == OBSERVATION_RECORDED
            and event.payload.get("attempt_id") == attempt_id
            and event.payload.get("blob_digest") == body_digest
        )

    def lead_provenance(
        self,
        references: tuple[str, ...],
        proposal_id: str,
        derivation: CandidateDerivation | None,
    ) -> CandidateProvenance:
        """Resolve typed Lead evidence without leaking EventStore traversal into adapters."""
        by_digest = {event.event_digest: event for event in self.store.events()}
        observations = tuple(
            ref for ref in references if ref in by_digest and by_digest[ref].event_type == OBSERVATION_RECORDED
        )
        tools = tuple(
            ref for ref in references if ref in by_digest and by_digest[ref].event_type == TOOL_CONTROL_RECORDED
        )
        model = next(
            event
            for event in reversed(tuple(by_digest.values()))
            if event.event_type == LEAD_ENGAGEMENT_RECORDED and event.payload.get("proposal_id") == proposal_id
        )
        return CandidateProvenance(
            CandidateDisposition.DERIVED if derivation is not None else CandidateDisposition.OBSERVED,
            observations,
            tools,
            model.event_digest if derivation is not None else "",
            derivation,
        )

    def _validate(self, proposal: CandidateProposal) -> AdmissionDecision | None:
        if not isinstance(proposal, CandidateProposal):
            return AdmissionDecision.UNSUPPORTED
        if type(proposal.schema_version) is not int or proposal.schema_version != 1:
            return AdmissionDecision.UNSUPPORTED
        if (
            not isinstance(proposal.proposal_id, str)
            or not proposal.proposal_id
            or not isinstance(proposal.challenge_id, int)
            or isinstance(proposal.challenge_id, bool)
            or proposal.challenge_id <= 0
            or not isinstance(proposal.generation_id, str)
            or not proposal.generation_id
            or not isinstance(proposal.candidate, bytes)
        ):
            return AdmissionDecision.MALFORMED
        if not proposal.candidate or len(proposal.candidate) > MAX_PROPOSAL_BYTES or self._broken or not self._matchers:
            return (
                AdmissionDecision.OVERSIZE
                if len(proposal.candidate) > MAX_PROPOSAL_BYTES
                else AdmissionDecision.MALFORMED
            )
        provenance = proposal.provenance
        if not isinstance(provenance, CandidateProvenance):
            return AdmissionDecision.UNSUPPORTED
        if not isinstance(provenance.disposition, CandidateDisposition):
            return AdmissionDecision.UNSUPPORTED
        if not isinstance(provenance.model_digest, str):
            return AdmissionDecision.PROVENANCE_INCOMPLETE
        if not isinstance(provenance.observation_digests, tuple) or not isinstance(
            provenance.tool_receipt_digests, tuple
        ):
            return AdmissionDecision.PROVENANCE_INCOMPLETE
        digests = (*provenance.observation_digests, *provenance.tool_receipt_digests)
        if provenance.model_digest:
            digests = (*digests, provenance.model_digest)
        if provenance.disposition is CandidateDisposition.OBSERVED and not provenance.observation_digests:
            return AdmissionDecision.PROVENANCE_INCOMPLETE
        if provenance.disposition is CandidateDisposition.DERIVED and not (
            provenance.observation_digests or provenance.tool_receipt_digests
        ):
            return AdmissionDecision.PROVENANCE_INCOMPLETE
        if provenance.disposition is CandidateDisposition.DERIVED and not provenance.model_digest:
            return AdmissionDecision.PROVENANCE_INCOMPLETE
        if any(
            not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value)
            for value in digests
        ):
            return AdmissionDecision.PROVENANCE_INCOMPLETE
        if provenance.disposition is CandidateDisposition.OBSERVED:
            if provenance.derivation is not None:
                return AdmissionDecision.PROVENANCE_INCOMPLETE
        elif not self._derivation_is_complete(provenance):
            return AdmissionDecision.PROVENANCE_INCOMPLETE
        if not self._provenance_is_reachable(proposal):
            return AdmissionDecision.PROVENANCE_INCOMPLETE
        return None

    def _provenance_is_reachable(self, proposal: CandidateProposal) -> bool:
        events = self.store.events()
        generation = next(
            (
                event
                for event in events
                if event.event_type == WORK_GENERATION_RECORDED
                and event.payload["generation_id"] == proposal.generation_id
                and event.payload["record"] == "acquire"
            ),
            None,
        )
        if generation is None or generation.payload["work_id"] != f"integer:{proposal.challenge_id}":
            return False
        attempt_id = generation.payload["attempt_id"]
        by_digest = {event.event_digest: event for event in events}
        observations = [by_digest.get(value) for value in proposal.provenance.observation_digests]
        tools = [by_digest.get(value) for value in proposal.provenance.tool_receipt_digests]
        model = by_digest.get(proposal.provenance.model_digest) if proposal.provenance.model_digest else None
        try:
            candidate_text = proposal.candidate.decode("utf-8")
        except UnicodeDecodeError:
            return False
        expected_evidence = [
            *proposal.provenance.observation_digests,
            *proposal.provenance.tool_receipt_digests,
        ]
        expected_proposal: dict[str, object] = {
            "kind": "candidate",
            "value": candidate_text,
            "evidence_refs": expected_evidence,
        }
        if proposal.provenance.derivation is not None:
            expected_proposal["derivation"] = proposal.provenance.derivation.document()
        observations_valid = all(
            event is not None and event.event_type == OBSERVATION_RECORDED and event.payload["attempt_id"] == attempt_id
            for event in observations
        )
        tools_valid = all(
            event is not None
            and event.event_type == TOOL_CONTROL_RECORDED
            and event.payload["record"] == ToolRecord.COMPLETED.value
            and event.payload["generation_id"] == proposal.generation_id
            and event.payload["attempt_id"] == attempt_id
            for event in tools
        )
        if model is None:
            model_valid = not proposal.provenance.model_digest
        else:
            try:
                model_proposal = json.loads(model.body)["turn"]["proposal"]
            except (KeyError, TypeError, json.JSONDecodeError):
                return False
            model_valid = (
                model.event_type == LEAD_ENGAGEMENT_RECORDED
                and model.payload["record"] == LeadRecord.TURN.value
                and model.payload["classification"] == LeadClassification.ACCEPTED.value
                and model.payload["binding"]["generation_id"] == proposal.generation_id
                and model.payload["binding"]["attempt_id"] == attempt_id
                and model.payload["proposal_kind"] == "candidate"
                and model.payload["proposal_id"] == proposal.proposal_id
                and model_proposal == expected_proposal
                and digest_bytes(canonical_bytes(model_proposal)) == model.payload["proposal_digest"]
                and (
                    proposal.provenance.derivation is None
                    or proposal.provenance.derivation.claim_ref == model.payload["proposal_id"]
                )
            )
        observed = any(proposal.candidate in event.body for event in observations if event is not None)
        disposition_valid = observed if proposal.provenance.disposition is CandidateDisposition.OBSERVED else True
        return observations_valid and tools_valid and model_valid and disposition_valid

    @staticmethod
    def _derivation_is_complete(provenance: CandidateProvenance) -> bool:
        derivation = provenance.derivation
        evidence = {*provenance.observation_digests, *provenance.tool_receipt_digests}
        return (
            isinstance(derivation, CandidateDerivation)
            and isinstance(derivation.kind, DerivationKind)
            and isinstance(derivation.source_digests, tuple)
            and bool(derivation.source_digests)
            and all(isinstance(item, str) and item in evidence for item in derivation.source_digests)
            and isinstance(derivation.relationship, str)
            and 0 < len(derivation.relationship.encode()) <= 4096
            and isinstance(derivation.claim_ref, str)
            and bool(derivation.claim_ref)
        )

    @contextmanager
    def _admission_lock(self) -> Iterator[None]:
        with self._lock_path.open("r+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _wrapper_only(candidate: bytes) -> bool:
        try:
            text = candidate.decode("utf-8")
        except UnicodeDecodeError:
            return True
        return describes_template(text)


__all__ = [
    "AdmissionDecision",
    "AdmissionOutcome",
    "CandidateAdmission",
    "CandidateDerivation",
    "CandidateDisposition",
    "CandidateProposal",
    "CandidateProvenance",
    "DerivationKind",
    "ReadyCandidate",
]
