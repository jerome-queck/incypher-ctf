"""Stable canonical contract for Candidate admission decisions."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError

CANDIDATE_ADMISSION_RECORDED = "candidate-admission.recorded"


class AdmissionDecision(str, enum.Enum):
    ADMITTED = "admitted"
    DUPLICATE = "duplicate"
    MALFORMED = "malformed"
    OVERSIZE = "oversize"
    PROVENANCE_INCOMPLETE = "provenance-incomplete"
    STALE_GENERATION = "stale-generation"
    UNSUPPORTED = "unsupported"
    WRAPPER_ONLY = "wrapper-only"


class CandidateDisposition(str, enum.Enum):
    OBSERVED = "observed"
    DERIVED = "derived"


class DerivationKind(str, enum.Enum):
    VISUAL_REGION = "visual-region"
    SEMANTIC_RECOGNITION = "semantic-recognition"
    FRAGMENTS = "fragments"
    OSINT_INFERENCE = "osint-inference"


@dataclass(frozen=True)
class CandidateDerivation:
    kind: DerivationKind
    source_digests: tuple[str, ...]
    relationship: str
    claim_ref: str

    def document(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_digests": list(self.source_digests),
            "relationship": self.relationship,
            "claim_ref": self.claim_ref,
        }


@dataclass(frozen=True)
class CandidateProvenance:
    disposition: CandidateDisposition
    observation_digests: tuple[str, ...]
    tool_receipt_digests: tuple[str, ...]
    model_digest: str = ""
    derivation: CandidateDerivation | None = None

    def document(self) -> dict[str, object]:
        return {
            "disposition": self.disposition.value,
            "observation_digests": list(self.observation_digests),
            "tool_receipt_digests": list(self.tool_receipt_digests),
            "model_digest": self.model_digest,
            "derivation": self.derivation.document() if self.derivation is not None else None,
        }


@dataclass(frozen=True)
class CandidateProposal:
    proposal_id: str
    challenge_id: int
    generation_id: str
    candidate: bytes
    provenance: CandidateProvenance
    schema_version: int = 1


@dataclass(frozen=True)
class ReadyCandidate:
    identity: str
    challenge_id: int
    generation_id: str
    candidate: bytes
    candidate_digest: str
    provenance: CandidateProvenance
    admission_rule: str


@dataclass(frozen=True)
class AdmissionOutcome:
    decision: AdmissionDecision
    candidate: ReadyCandidate | None = None
    detail: str = ""


@dataclass(frozen=True)
class CandidateAdmissionRecorded:
    event_id: str
    candidate_id: str
    challenge_id: int
    generation_id: str
    candidate_digest: str
    provenance_digest: str
    admission_rule: str
    decision: AdmissionDecision
    duplicate_class: str = ""
    ts: str = ""

    @property
    def event_type(self) -> str:
        return CANDIDATE_ADMISSION_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "candidate_id": self.candidate_id,
            "challenge_id": self.challenge_id,
            "generation_id": self.generation_id,
            "candidate_digest": self.candidate_digest,
            "provenance_digest": self.provenance_digest,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
            "vault_digest": blob_digest,
            "vault_bytes": blob_bytes,
            "admission_rule": self.admission_rule,
            "decision": self.decision.value,
            "duplicate_class": self.duplicate_class,
            "ts": self.ts,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        strings = (
            "event_id",
            "candidate_id",
            "generation_id",
            "candidate_digest",
            "provenance_digest",
            "vault_digest",
            "admission_rule",
            "decision",
            "duplicate_class",
            "ts",
        )
        if any(name not in payload for name in (*strings, "challenge_id", "vault_bytes", "blob_digest", "blob_bytes")):
            raise InvalidEventError("Candidate admission payload is incomplete", sequence=sequence)
        if any(not isinstance(payload[name], str) for name in strings):
            raise InvalidEventError("Candidate admission string field has the wrong type", sequence=sequence)
        if not isinstance(payload["challenge_id"], int) or isinstance(payload["challenge_id"], bool):
            raise InvalidEventError("Candidate Challenge identity has the wrong type", sequence=sequence)
        if not isinstance(payload["vault_bytes"], int) or isinstance(payload["vault_bytes"], bool):
            raise InvalidEventError("Candidate vault size has the wrong type", sequence=sequence)
        if payload["blob_digest"] != payload["vault_digest"] or payload["blob_bytes"] != payload["vault_bytes"]:
            raise InvalidEventError("Candidate vault aliases disagree with sealed body", sequence=sequence)
        if payload["challenge_id"] <= 0 or payload["vault_bytes"] < 0:
            raise InvalidEventError("Candidate admission contains an invalid count", sequence=sequence)
        for name in ("candidate_id", "candidate_digest", "provenance_digest", "vault_digest"):
            value = payload[name]
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise InvalidEventError(f"Candidate {name} is not lowercase SHA-256", sequence=sequence)
        try:
            decision = AdmissionDecision(payload["decision"])
        except ValueError as error:
            raise InvalidEventError("Candidate admission decision is unsupported", sequence=sequence) from error
        if not payload["event_id"] or not payload["generation_id"] or not payload["admission_rule"]:
            raise InvalidEventError("Candidate admission identity is empty", sequence=sequence)
        if decision not in {AdmissionDecision.ADMITTED, AdmissionDecision.DUPLICATE}:
            raise InvalidEventError("Stored Candidate decision is not authoritative", sequence=sequence)
        if decision is AdmissionDecision.ADMITTED:
            if payload["duplicate_class"] or payload["vault_bytes"] <= 0:
                raise InvalidEventError("Admitted Candidate has invalid vault metadata", sequence=sequence)
        elif payload["duplicate_class"] != "equivalent" or payload["vault_bytes"] != 0:
            raise InvalidEventError("Duplicate Candidate has invalid equivalence metadata", sequence=sequence)
        if decision is AdmissionDecision.DUPLICATE and payload["vault_digest"] != EMPTY_BLOB_DIGEST:
            raise InvalidEventError("Duplicate Candidate cannot carry vault bytes", sequence=sequence)
