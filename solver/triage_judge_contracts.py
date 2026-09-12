"""Typed, effectless values for one bounded Triage Judge arrival batch."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol

from solver.event_store_contracts import InvalidEventError

TRIAGE_JUDGE_RECORDED = "triage-judge.recorded"
MAX_EVIDENCE_ITEMS = 32
MAX_SUMMARY_BYTES = 1024


class TriageJudgeClassification(str, enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    MALFORMED = "malformed"
    UNMEASURED = "unmeasured"
    LATE = "late"
    DUPLICATE = "already-accepted"


@dataclass(frozen=True)
class TriageEvidence:
    kind: str
    reference: str
    digest: str
    summary: str


@dataclass(frozen=True)
class TriageProposal:
    kind: str
    value: str
    confidence: float
    provenance: str


@dataclass(frozen=True)
class TriageJudgeMeasure:
    route: str
    model: str
    duration_ms: int
    tokens_in: int
    tokens_out: int
    usage_known: bool
    completed_at: str


@dataclass(frozen=True)
class MeasuredTriageJudgement:
    proposal: TriageProposal
    measure: TriageJudgeMeasure


@dataclass(frozen=True)
class TriageJudgeRequest:
    batch_id: str
    evidence_digest: str
    evidence: tuple[TriageEvidence, ...]
    deterministic: TriageProposal
    deadline: str


class TriageJudgeModel(Protocol):
    def __call__(self, evidence: tuple[TriageEvidence, ...]) -> object: ...


@dataclass(frozen=True)
class TriageJudgeOutcome:
    classification: TriageJudgeClassification
    verdict_id: str
    proposal: TriageProposal
    accepted_source: str
    acceptance_reason: str


@dataclass(frozen=True)
class TriageJudgeRecorded:
    event_id: str
    batch_id: str
    request_digest: str
    evidence_digest: str
    classification: TriageJudgeClassification
    verdict_id: str
    accepted_source: str
    acceptance_reason: str
    proposal_kind: str
    proposal_value: str
    proposal_confidence: float
    proposal_provenance: str
    measured_route: str
    measured_model: str
    duration_ms: int
    tokens_in: int
    tokens_out: int
    usage_known: bool
    completed_at: str
    deterministic_same: bool

    exact_payload_identity = True

    @property
    def event_type(self):
        return TRIAGE_JUDGE_RECORDED

    @property
    def identity(self):
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int):
        document = asdict(self)
        document["classification"] = self.classification.value
        document.update(blob_digest=blob_digest, blob_bytes=blob_bytes)
        return document

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        required = set(cls.__dataclass_fields__) | {"blob_digest", "blob_bytes"}
        if set(payload) != required:
            raise InvalidEventError("Triage Judge arrival-batch shape is unsupported", sequence=sequence)
        if payload["classification"] not in {item.value for item in TriageJudgeClassification}:
            raise InvalidEventError("Triage Judge classification is unsupported", sequence=sequence)
        for field in ("request_digest", "evidence_digest", "blob_digest"):
            value = payload[field]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise InvalidEventError("Triage Judge digest is invalid", sequence=sequence)


__all__ = [
    "MeasuredTriageJudgement",
    "TRIAGE_JUDGE_RECORDED",
    "TriageEvidence",
    "TriageJudgeClassification",
    "TriageJudgeMeasure",
    "TriageJudgeModel",
    "TriageJudgeOutcome",
    "TriageJudgeRecorded",
    "TriageJudgeRequest",
    "TriageProposal",
]
