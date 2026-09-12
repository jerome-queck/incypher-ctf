"""Public contracts for the private CPA inference route."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError
from solver.lead_contracts import LeadProposal, MeasuredLeadTurn

SERVICE_VERSION = "cpa-harness/v1"
CPA_HARNESS_RECORDED = "cpa-harness.recorded"


@dataclass(frozen=True)
class CPAConfig:
    max_turns: int
    max_tools: int
    allowed_tools: tuple[str, ...]
    model: str = "cpa"
    credential_mode: str = "supervisor-custodied-oauth"

    def __post_init__(self) -> None:
        if self.max_turns < 1 or self.max_tools < 0:
            raise ValueError("CPA bounds must be non-negative and permit one turn")
        if not self.credential_mode or len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("CPA configuration is invalid")

    def document(self) -> dict[str, object]:
        return {
            "allowed_tools": list(self.allowed_tools),
            "credential_mode": self.credential_mode,
            "max_tools": self.max_tools,
            "max_turns": self.max_turns,
            "model": self.model,
        }


@dataclass(frozen=True)
class CPAToolCall:
    name: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class CPAModelReply:
    tool_calls: tuple[CPAToolCall, ...] = ()
    proposal: LeadProposal | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    service_version: str = SERVICE_VERSION


@dataclass(frozen=True)
class CPAModelRequest:
    prompt: str
    tool_results: tuple[tuple[str, str], ...]


class CPAModel(Protocol):
    def respond(self, request: CPAModelRequest, *, credential: str, parallel_tool_calls: bool) -> CPAModelReply: ...


class CPAStatus(str, enum.Enum):
    PROPOSED = "proposed"
    REFUSED = "refused"
    MALFORMED = "malformed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    CRASHED = "crashed"


@dataclass(frozen=True)
class CPAOutcome:
    status: CPAStatus
    turn_count: int
    tool_count: int
    turn: MeasuredLeadTurn | None = None
    detail: str = ""
    audit: tuple[str, ...] = field(default_factory=tuple)


class CPAServiceRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class CPAHarnessRecorded:
    event_id: str
    request_id: str
    record: str
    status: str
    config_digest: str
    peer_digest: str
    turn_count: int = 0
    tool_count: int = 0
    measured: bool = False
    model: str = ""
    duration_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    proposal_kind: str = ""
    proposal_digest: str = ""
    detail: str = ""
    ts: str = ""

    @property
    def event_type(self) -> str:
        return CPA_HARNESS_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {**self.__dict__, "blob_digest": blob_digest, "blob_bytes": blob_bytes}

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        required = set(cls.__dataclass_fields__) | {"blob_digest", "blob_bytes"}
        if set(payload) != required or not all(
            isinstance(payload.get(name), str)
            for name in (
                "event_id",
                "request_id",
                "record",
                "status",
                "config_digest",
                "peer_digest",
                "model",
                "proposal_kind",
                "proposal_digest",
                "detail",
                "ts",
            )
        ):
            raise InvalidEventError("CPA Harness event is incomplete", sequence=sequence)
        if (
            not payload["event_id"]
            or not payload["request_id"]
            or payload["record"] not in {"request", "denial", "teardown", "probe"}
        ):
            raise InvalidEventError("CPA Harness event identity is invalid", sequence=sequence)
        if payload["status"] not in {item.value for item in CPAStatus}:
            raise InvalidEventError("CPA Harness status is invalid", sequence=sequence)
        for name in ("config_digest", "peer_digest"):
            value = payload[name]
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise InvalidEventError("CPA Harness digest is invalid", sequence=sequence)
        if any(
            not isinstance(payload.get(name), int) or isinstance(payload[name], bool) or payload[name] < 0
            for name in ("turn_count", "tool_count", "duration_ms", "tokens_in", "tokens_out", "blob_bytes")
        ) or not isinstance(payload.get("measured"), bool):
            raise InvalidEventError("CPA Harness count is invalid", sequence=sequence)
        if payload["measured"]:
            if (
                not payload["model"]
                or not payload["proposal_kind"]
                or len(payload["proposal_digest"]) != 64
                or any(character not in "0123456789abcdef" for character in payload["proposal_digest"])
            ):
                raise InvalidEventError("CPA Harness measurement is incomplete", sequence=sequence)
        elif any(payload[name] for name in ("model", "proposal_kind", "proposal_digest")) or any(
            payload[name] for name in ("duration_ms", "tokens_in", "tokens_out")
        ):
            raise InvalidEventError("unmeasured CPA Harness event carries measurement", sequence=sequence)
        if payload["blob_digest"] != EMPTY_BLOB_DIGEST or payload["blob_bytes"] != 0:
            raise InvalidEventError("CPA Harness event cannot carry a body", sequence=sequence)


__all__ = [
    "CPAConfig",
    "CPAModel",
    "CPAModelReply",
    "CPAModelRequest",
    "CPAOutcome",
    "CPAServiceRefused",
    "CPAStatus",
    "CPAHarnessRecorded",
    "CPA_HARNESS_RECORDED",
    "CPAToolCall",
    "SERVICE_VERSION",
]
