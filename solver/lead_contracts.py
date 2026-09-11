"""Typed values crossing the Solve Lead's narrow model boundary."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, TypeAlias

from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError


LEAD_ENGAGEMENT_RECORDED = "lead-engagement.recorded"
MAX_CONTEXT_BYTES = 16 * 1024
MAX_APPROACH_BYTES = 200
MAX_TURN_BYTES = 16 * 1024
ROLE = "solve-lead"


class LeadClassification(str, enum.Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "already-accepted"
    CONFLICT = "conflict-quarantined"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    LATE = "late"
    UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class BoardProposal:
    operation: str
    reference: str


@dataclass(frozen=True)
class TargetProposal:
    operation: str
    target_ref: str
    payload: str = ""


@dataclass(frozen=True)
class ResearchProposal:
    query: str


@dataclass(frozen=True)
class ToolProposal:
    tool: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class CandidateProposal:
    value: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class ProgressProposal:
    detail: str


@dataclass(frozen=True)
class StopProposal:
    reason: str


LeadProposal: TypeAlias = (
    BoardProposal
    | TargetProposal
    | ResearchProposal
    | ToolProposal
    | CandidateProposal
    | ProgressProposal
    | StopProposal
)
PROPOSAL_CONTRACTS = {
    BoardProposal: ("board", ""),
    TargetProposal: ("target", ""),
    ResearchProposal: ("research", ""),
    ToolProposal: ("tool", ""),
    CandidateProposal: ("candidate", "candidate"),
    ProgressProposal: ("progress", ""),
    StopProposal: ("stop", "stop"),
}
PROPOSAL_KINDS = frozenset(kind for kind, _ in PROPOSAL_CONTRACTS.values())


@dataclass(frozen=True)
class TurnMeasure:
    model: str
    duration_ms: int
    tokens_in: int
    tokens_out: int
    usage_known: bool


@dataclass(frozen=True)
class MeasuredLeadTurn:
    approach: str
    proposal: LeadProposal
    measure: TurnMeasure | None


@dataclass(frozen=True)
class LeadRequest:
    engagement_id: str
    generation_id: str
    turn_index: int
    context: str


class LeadModel(Protocol):
    def __call__(self, request: LeadRequest) -> object: ...


@dataclass(frozen=True)
class LeadTransition:
    sequence: int
    event_id: str
    turn_index: int
    classification: LeadClassification
    proposal_kind: str
    proposal_digest: str
    context_bytes: int
    output_bytes: int
    measure: TurnMeasure
    transition_digest: str


@dataclass(frozen=True)
class LeadState:
    run_id: str
    engagement_id: str
    generation_id: str
    role: str
    turn_count: int
    disposition: str | None
    transition_digest: str
    transitions: tuple[LeadTransition, ...]


@dataclass(frozen=True)
class LeadOutcome:
    classification: LeadClassification
    state: LeadState
    proposal: LeadProposal | None = None
    detail: str = ""


@dataclass(frozen=True)
class LeadEngagementRecorded:
    event_id: str
    engagement_id: str
    generation_id: str
    turn_index: int
    classification: LeadClassification
    request_digest: str
    transition_digest: str
    proposal_kind: str = ""
    proposal_digest: str = ""
    context_bytes: int = 0
    output_bytes: int = 0
    model: str = ""
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usage_known: bool = False
    disposition: str = ""
    detail: str = ""
    ts: str = ""

    exact_payload_identity = True

    @property
    def event_type(self) -> str:
        return LEAD_ENGAGEMENT_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "engagement_id": self.engagement_id,
            "generation_id": self.generation_id,
            "turn_index": self.turn_index,
            "role": ROLE,
            "classification": self.classification.value,
            "request_digest": self.request_digest,
            "transition_digest": self.transition_digest,
            "proposal_kind": self.proposal_kind,
            "proposal_digest": self.proposal_digest,
            "context_bytes": self.context_bytes,
            "output_bytes": self.output_bytes,
            "model": self.model,
            "duration_ms": self.duration_ms,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "usage_known": self.usage_known,
            "disposition": self.disposition,
            "detail": self.detail,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        strings = (
            "event_id",
            "engagement_id",
            "generation_id",
            "role",
            "classification",
            "request_digest",
            "transition_digest",
            "proposal_kind",
            "proposal_digest",
            "model",
            "disposition",
            "detail",
            "ts",
            "blob_digest",
        )
        integers = (
            "turn_index",
            "context_bytes",
            "output_bytes",
            "duration_ms",
            "tokens_in",
            "tokens_out",
            "blob_bytes",
        )
        missing = [field for field in (*strings, *integers, "usage_known") if field not in payload]
        if missing:
            raise InvalidEventError(f"Lead payload is missing required fields: {', '.join(missing)}", sequence=sequence)
        if any(not isinstance(payload[field], str) for field in strings):
            raise InvalidEventError("Lead payload has a non-string field", sequence=sequence)
        if any(not isinstance(payload[field], int) or isinstance(payload[field], bool) for field in integers):
            raise InvalidEventError("Lead payload has a non-integer count", sequence=sequence)
        if not isinstance(payload["usage_known"], bool):
            raise InvalidEventError("Lead payload usage_known is not boolean", sequence=sequence)
        if not all(payload[field] for field in ("event_id", "engagement_id", "generation_id", "request_digest")):
            raise InvalidEventError("Lead identity is incomplete", sequence=sequence)
        if payload["role"] != ROLE or payload["classification"] not in {item.value for item in LeadClassification}:
            raise InvalidEventError("Lead role or classification is unsupported", sequence=sequence)
        if payload["classification"] in {LeadClassification.DUPLICATE.value, LeadClassification.LATE.value}:
            raise InvalidEventError("Lead outcome-only classification was stored", sequence=sequence)
        if payload["turn_index"] < 1 or any(payload[field] < 0 for field in integers[1:]):
            raise InvalidEventError("Lead payload contains an invalid count", sequence=sequence)
        for field in ("request_digest", "transition_digest", "blob_digest"):
            digest = payload[field]
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise InvalidEventError(f"Lead {field} is not lowercase SHA-256", sequence=sequence)
        if payload["proposal_digest"]:
            digest = payload["proposal_digest"]
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise InvalidEventError("Lead proposal digest is not lowercase SHA-256", sequence=sequence)
        accepted = payload["classification"] == LeadClassification.ACCEPTED.value
        if accepted != bool(payload["proposal_kind"] and payload["proposal_digest"]):
            raise InvalidEventError("Lead accepted proposal fields disagree", sequence=sequence)
        if payload["proposal_kind"] and payload["proposal_kind"] not in PROPOSAL_KINDS:
            raise InvalidEventError("Lead proposal kind is unsupported", sequence=sequence)
        if payload["disposition"] not in {"", "candidate", "stop"}:
            raise InvalidEventError("Lead disposition is unsupported", sequence=sequence)
        expected_disposition = payload["proposal_kind"] if payload["proposal_kind"] in {"candidate", "stop"} else ""
        if payload["disposition"] != expected_disposition:
            raise InvalidEventError("Lead proposal and disposition disagree", sequence=sequence)
        if accepted and (
            not payload["model"]
            or payload["context_bytes"] > MAX_CONTEXT_BYTES
            or not 0 < payload["output_bytes"] <= MAX_TURN_BYTES
            or not 0 < payload["blob_bytes"] <= MAX_TURN_BYTES
        ):
            raise InvalidEventError("Lead accepted turn exceeds its measured bounds", sequence=sequence)
        if (
            not accepted
            and payload["blob_bytes"] != 0
            and payload["classification"] != LeadClassification.CONFLICT.value
        ):
            raise InvalidEventError("Lead rejected outcome carries an unbounded body", sequence=sequence)
        if payload["classification"] == LeadClassification.CONFLICT.value and not (
            0 < payload["blob_bytes"] <= MAX_TURN_BYTES
        ):
            raise InvalidEventError("Lead conflict quarantine exceeds its bound", sequence=sequence)
        if payload["blob_bytes"] == 0 and payload["blob_digest"] != EMPTY_BLOB_DIGEST:
            raise InvalidEventError("Lead empty body digest disagrees", sequence=sequence)


__all__ = [
    "BoardProposal",
    "CandidateProposal",
    "LeadClassification",
    "LeadEngagementRecorded",
    "LeadModel",
    "LeadOutcome",
    "LeadProposal",
    "LeadRequest",
    "LeadState",
    "LeadTransition",
    "MAX_APPROACH_BYTES",
    "MAX_CONTEXT_BYTES",
    "MAX_TURN_BYTES",
    "MeasuredLeadTurn",
    "ProgressProposal",
    "PROPOSAL_CONTRACTS",
    "PROPOSAL_KINDS",
    "ResearchProposal",
    "StopProposal",
    "TargetProposal",
    "ToolProposal",
    "TurnMeasure",
]
