"""Typed values and durable facts at the Solve Lead boundary."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, TypeAlias

from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError

LEAD_ENGAGEMENT_RECORDED = "lead-engagement.recorded"
MAX_CONTEXT_BYTES = 16 * 1024
MAX_APPROACH_BYTES = 200
MAX_TURN_BYTES = 16 * 1024
MAX_RESUME_DELTA_BYTES = 8 * 1024
ROLE = "solve-lead"
CONTROLLER_OWNER = "run-controller"


class LeadRecord(str, enum.Enum):
    ADMITTED = "admitted"
    TURN = "turn"
    QUARANTINED = "quarantined"


class LeadClassification(str, enum.Enum):
    ACCEPTED = "accepted"
    DUPLICATE = "already-accepted"
    CONFLICT = "conflict-quarantined"
    MALFORMED = "malformed"
    OVERSIZED = "oversized"
    LATE = "late"
    UNMEASURED = "unmeasured"


class ProposalResultStatus(str, enum.Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUSED = "refused"


@dataclass(frozen=True)
class LeadBinding:
    """Controller-authored facts immutable for one Engagement."""

    run_id: str
    boot_id: str
    generation_id: str
    lane_id: str
    attempt_id: str
    work_id: str
    engagement_id: str
    owner_id: str
    parent_engagement_id: str
    context_digest: str
    evidence_digest: str
    prompt_bundle_digest: str
    playbook_digest: str
    tool_schema_digest: str
    capability_digest: str
    harness: str
    requested_route: str
    requested_model: str
    selected_model: str
    effective_model: str
    requested_effort: str
    effective_effort: str
    catalog_digest: str
    admitted_budget: int
    deadline: str
    started_at: str
    role: str = ROLE
    max_context_bytes: int = MAX_CONTEXT_BYTES
    max_turn_bytes: int = MAX_TURN_BYTES
    max_resume_delta_bytes: int = MAX_RESUME_DELTA_BYTES

    def document(self) -> dict[str, object]:
        return asdict(self)


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
    BoardProposal: ("board", "", True),
    TargetProposal: ("target", "", True),
    ResearchProposal: ("research", "", True),
    ToolProposal: ("tool", "", True),
    CandidateProposal: ("candidate", "candidate", False),
    ProgressProposal: ("progress", "", False),
    StopProposal: ("stop", "stop", False),
}
PROPOSAL_KINDS = frozenset(kind for kind, _, _ in PROPOSAL_CONTRACTS.values())


@dataclass(frozen=True)
class LeadProposalResult:
    proposal_id: str
    status: ProposalResultStatus
    evidence_refs: tuple[str, ...] = ()
    detail: str = ""


@dataclass(frozen=True)
class LeadInitialContext:
    context: str


@dataclass(frozen=True)
class LeadResume:
    delta: str
    result: LeadProposalResult | None = None


LeadInput: TypeAlias = LeadInitialContext | LeadResume


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
    binding: LeadBinding
    turn_index: int
    input: LeadInput

    @property
    def engagement_id(self) -> str:
        return self.binding.engagement_id

    @property
    def generation_id(self) -> str:
        return self.binding.generation_id

    @property
    def context(self) -> str:
        return self.input.context if isinstance(self.input, LeadInitialContext) else self.input.delta


class LeadModel(Protocol):
    def __call__(self, request: LeadRequest) -> object: ...


@dataclass(frozen=True)
class LeadTransition:
    sequence: int
    event_id: str
    turn_index: int
    classification: LeadClassification
    proposal_id: str
    proposal_kind: str
    proposal_digest: str
    context_bytes: int
    output_bytes: int
    measure: TurnMeasure
    result: LeadProposalResult | None
    transition_digest: str


@dataclass(frozen=True)
class LeadState:
    run_id: str
    binding: LeadBinding
    turn_count: int
    disposition: str | None
    outstanding_proposal_id: str
    outstanding_proposal_kind: str
    transition_digest: str
    transitions: tuple[LeadTransition, ...]

    @property
    def engagement_id(self) -> str:
        return self.binding.engagement_id

    @property
    def generation_id(self) -> str:
        return self.binding.generation_id

    @property
    def role(self) -> str:
        return self.binding.role


@dataclass(frozen=True)
class LeadOutcome:
    classification: LeadClassification
    state: LeadState
    proposal: LeadProposal | None = None
    proposal_id: str = ""
    detail: str = ""


@dataclass(frozen=True)
class LeadEngagementRecorded:
    event_id: str
    record: LeadRecord
    binding: LeadBinding
    authority_run_id: str
    authority_event_id: str
    authority_sequence: int
    turn_index: int
    classification: LeadClassification
    request_digest: str
    transition_digest: str = ""
    proposal_id: str = ""
    proposal_kind: str = ""
    proposal_digest: str = ""
    result_digest: str = ""
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
            "record": self.record.value,
            "binding": self.binding.document(),
            "authority_run_id": self.authority_run_id,
            "authority_event_id": self.authority_event_id,
            "authority_sequence": self.authority_sequence,
            "turn_index": self.turn_index,
            "classification": self.classification.value,
            "request_digest": self.request_digest,
            "transition_digest": self.transition_digest,
            "proposal_id": self.proposal_id,
            "proposal_kind": self.proposal_kind,
            "proposal_digest": self.proposal_digest,
            "result_digest": self.result_digest,
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
            "record",
            "authority_run_id",
            "authority_event_id",
            "classification",
            "request_digest",
            "transition_digest",
            "proposal_id",
            "proposal_kind",
            "proposal_digest",
            "result_digest",
            "model",
            "disposition",
            "detail",
            "ts",
            "blob_digest",
        )
        integers = (
            "authority_sequence",
            "turn_index",
            "context_bytes",
            "output_bytes",
            "duration_ms",
            "tokens_in",
            "tokens_out",
            "blob_bytes",
        )
        missing = [field for field in (*strings, *integers, "usage_known", "binding") if field not in payload]
        if missing:
            raise InvalidEventError(f"Lead payload is missing required fields: {', '.join(missing)}", sequence=sequence)
        if any(not isinstance(payload[field], str) for field in strings):
            raise InvalidEventError("Lead payload has a non-string field", sequence=sequence)
        if any(not isinstance(payload[field], int) or isinstance(payload[field], bool) for field in integers):
            raise InvalidEventError("Lead payload has a non-integer count", sequence=sequence)
        if not isinstance(payload["usage_known"], bool) or not isinstance(payload["binding"], Mapping):
            raise InvalidEventError("Lead payload has an invalid structured field", sequence=sequence)
        try:
            binding = LeadBinding(**payload["binding"])
        except (TypeError, ValueError) as error:
            raise InvalidEventError("Lead binding is invalid", sequence=sequence) from error
        validate_binding(binding, sequence)
        if payload["record"] not in {item.value for item in LeadRecord}:
            raise InvalidEventError("Lead record is unsupported", sequence=sequence)
        if payload["classification"] not in {item.value for item in LeadClassification}:
            raise InvalidEventError("Lead classification is unsupported", sequence=sequence)
        if (
            not payload["event_id"]
            or not payload["authority_run_id"]
            or not payload["authority_event_id"]
            or payload["turn_index"] < 1
            or payload["authority_sequence"] < 1
        ):
            raise InvalidEventError("Lead identity is incomplete", sequence=sequence)
        if any(payload[field] < 0 for field in integers[2:]):
            raise InvalidEventError("Lead payload contains an invalid count", sequence=sequence)
        for field in ("request_digest", "blob_digest"):
            require_digest(payload[field], field, sequence)
        for field in ("transition_digest", "proposal_digest", "result_digest"):
            if payload[field]:
                require_digest(payload[field], field, sequence)
        if payload["proposal_kind"] and payload["proposal_kind"] not in PROPOSAL_KINDS:
            raise InvalidEventError("Lead proposal kind is unsupported", sequence=sequence)
        if payload["disposition"] not in {"", "candidate", "stop"}:
            raise InvalidEventError("Lead disposition is unsupported", sequence=sequence)
        record = LeadRecord(payload["record"])
        if record is LeadRecord.ADMITTED:
            if payload["classification"] != LeadClassification.ACCEPTED.value:
                raise InvalidEventError("Lead admission is not accepted", sequence=sequence)
            if payload["proposal_id"] or payload["proposal_digest"] or payload["transition_digest"]:
                raise InvalidEventError("Lead admission carries an output", sequence=sequence)
            if not 0 < payload["blob_bytes"] <= binding.max_turn_bytes:
                raise InvalidEventError("Lead admission body exceeds its bound", sequence=sequence)
        elif record is LeadRecord.TURN:
            accepted = payload["classification"] == LeadClassification.ACCEPTED.value
            if accepted != bool(payload["proposal_id"] and payload["proposal_kind"] and payload["proposal_digest"]):
                raise InvalidEventError("Lead accepted proposal fields disagree", sequence=sequence)
            if not payload["transition_digest"]:
                raise InvalidEventError("Lead turn has no transition digest", sequence=sequence)
            if accepted and (not payload["model"] or not 0 < payload["blob_bytes"] <= binding.max_turn_bytes):
                raise InvalidEventError("Lead accepted turn exceeds its bound", sequence=sequence)
            if not accepted and payload["blob_bytes"]:
                raise InvalidEventError("Lead rejected turn carries a body", sequence=sequence)
        elif not 0 < payload["blob_bytes"] <= binding.max_turn_bytes:
            raise InvalidEventError("Lead quarantine exceeds its bound", sequence=sequence)
        if payload["blob_bytes"] == 0 and payload["blob_digest"] != EMPTY_BLOB_DIGEST:
            raise InvalidEventError("Lead empty body digest disagrees", sequence=sequence)


def require_digest(value: str, field: str, sequence: int = 0) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise InvalidEventError(f"Lead {field} is not lowercase SHA-256", sequence=sequence or None)


def validate_binding(binding: LeadBinding, sequence: int = 0) -> None:
    required = (
        binding.run_id,
        binding.boot_id,
        binding.generation_id,
        binding.lane_id,
        binding.attempt_id,
        binding.work_id,
        binding.engagement_id,
        binding.owner_id,
        binding.harness,
        binding.requested_route,
        binding.requested_model,
        binding.selected_model,
        binding.effective_model,
        binding.requested_effort,
        binding.effective_effort,
        binding.deadline,
        binding.started_at,
    )
    if not all(isinstance(value, str) and value for value in required) or binding.role != ROLE:
        raise InvalidEventError("Lead binding identity is incomplete", sequence=sequence or None)
    for field in (
        "context_digest",
        "evidence_digest",
        "prompt_bundle_digest",
        "playbook_digest",
        "tool_schema_digest",
        "capability_digest",
        "catalog_digest",
    ):
        require_digest(getattr(binding, field), field, sequence)
    for value in (
        binding.admitted_budget,
        binding.max_context_bytes,
        binding.max_turn_bytes,
        binding.max_resume_delta_bytes,
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise InvalidEventError("Lead binding has an invalid bound", sequence=sequence or None)


__all__ = [
    "BoardProposal",
    "CandidateProposal",
    "CONTROLLER_OWNER",
    "LeadBinding",
    "LeadClassification",
    "LeadEngagementRecorded",
    "LeadInitialContext",
    "LeadModel",
    "LeadOutcome",
    "LeadProposal",
    "LeadProposalResult",
    "LeadRecord",
    "LeadRequest",
    "LeadResume",
    "LeadState",
    "LeadTransition",
    "MAX_APPROACH_BYTES",
    "MAX_CONTEXT_BYTES",
    "MAX_RESUME_DELTA_BYTES",
    "MAX_TURN_BYTES",
    "MeasuredLeadTurn",
    "ProgressProposal",
    "PROPOSAL_CONTRACTS",
    "PROPOSAL_KINDS",
    "ProposalResultStatus",
    "ResearchProposal",
    "StopProposal",
    "TargetProposal",
    "ToolProposal",
    "TurnMeasure",
    "validate_binding",
]
