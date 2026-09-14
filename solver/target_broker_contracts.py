"""Public contracts for one Attempt's declared Target capability."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any, Mapping, TypedDict

from solver.event_store_contracts import InvalidEventError

TARGET_EXCHANGE_RECORDED = "target-broker.recorded"
SCHEMA_VERSION = 1
TARGET_EXCHANGE_COMMAND = "exchange"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


class TargetProtocol(str, enum.Enum):
    HTTP = "http"
    HTTPS = "https"
    TCP = "tcp"


class TargetOutcome(str, enum.Enum):
    ANSWERED = "answered"
    DENIED = "denied"
    TOO_LARGE = "too-large"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    AMBIGUOUS_CLOSE = "ambiguous-close"
    REVOKED = "revoked"
    CAPABILITY_REFUSED = "capability-refused"
    BUDGET_EXHAUSTED = "budget-exhausted"


class TargetRecord(str, enum.Enum):
    RESERVED = "reserved"
    CLASSIFIED = "classified"


class TcpTargetExchangeRequest(TypedDict):
    body: str


class HttpTargetExchangeRequest(TypedDict):
    method: str
    path: str
    body: str


TargetExchangeRequest = TcpTargetExchangeRequest | HttpTargetExchangeRequest


@dataclass(frozen=True)
class TargetEndpoint:
    protocol: TargetProtocol
    host: str
    port: int

    def __post_init__(self) -> None:
        if not self.host or not 1 <= self.port <= 65535:
            raise ValueError("Target endpoint must name one host and port")


@dataclass(frozen=True)
class TargetLimits:
    max_connections: int
    max_request_bytes: int
    max_response_bytes: int
    timeout_seconds: float

    def __post_init__(self) -> None:
        if min(self.max_connections, self.max_request_bytes, self.max_response_bytes) <= 0:
            raise ValueError("Target limits must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("Target timeout must be positive")


@dataclass(frozen=True)
class TargetCandidateBinding:
    image_id: str
    manifest_digest: str
    config_digest: str
    platform: str
    profile_digest: str

    def __post_init__(self) -> None:
        if any(_DIGEST.fullmatch(value) is None for value in (self.image_id, self.manifest_digest, self.config_digest)):
            raise ValueError("Target candidate image binding is invalid")
        if self.platform not in {"linux/amd64", "linux/arm64"} or _HEX_DIGEST.fullmatch(self.profile_digest) is None:
            raise ValueError("Target candidate profile binding is invalid")


@dataclass(frozen=True)
class TargetProvenance:
    challenge_id: str = ""
    generation_id: str = ""
    endpoint: str = ""
    protocol: TargetProtocol | None = None
    request_bytes: int = 0
    response_bytes: int = 0
    transcript_digest: str = ""
    elapsed_ms: int = 0


@dataclass(frozen=True)
class TargetResult:
    outcome: TargetOutcome
    body: bytes = b""
    status: int = 0
    provenance: TargetProvenance = TargetProvenance()
    request_id: str = ""


@dataclass(frozen=True)
class TargetBrokerRecorded:
    event_id: str
    request_id: str
    record: TargetRecord
    binding_digest: str
    run_id: str
    boot_id: str
    generation_id: str
    lane_id: str
    attempt_id: str
    step_id: str
    challenge_id: str
    image_id: str
    image_manifest_digest: str
    image_config_digest: str
    platform: str
    profile_digest: str
    endpoint: str
    resolved_address: str
    protocol: TargetProtocol
    max_connections: int
    max_request_bytes: int
    max_response_bytes: int
    timeout_ms: int
    request_digest: str
    observation_sequence: int = 0
    observation_digest: str = ""
    outcome: TargetOutcome | None = None
    request_bytes: int = 0
    response_bytes: int = 0
    status: int = 0
    elapsed_ms: int = 0
    transcript_digest: str = ""
    probe_kind: str = ""
    attempted_endpoint_digest: str = ""
    ts: str = ""

    @property
    def event_type(self) -> str:
        return TARGET_EXCHANGE_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            **{
                name: getattr(self, name)
                for name in (
                    "event_id",
                    "request_id",
                    "binding_digest",
                    "run_id",
                    "boot_id",
                    "generation_id",
                    "lane_id",
                    "attempt_id",
                    "step_id",
                    "challenge_id",
                    "image_id",
                    "image_manifest_digest",
                    "image_config_digest",
                    "platform",
                    "profile_digest",
                    "endpoint",
                    "resolved_address",
                    "max_connections",
                    "max_request_bytes",
                    "max_response_bytes",
                    "timeout_ms",
                    "request_digest",
                    "observation_sequence",
                    "observation_digest",
                    "request_bytes",
                    "response_bytes",
                    "status",
                    "elapsed_ms",
                    "transcript_digest",
                    "probe_kind",
                    "attempted_endpoint_digest",
                    "ts",
                )
            },
            "record": self.record.value,
            "protocol": self.protocol.value,
            "outcome": self.outcome.value if self.outcome else "",
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        required = {
            "event_id",
            "request_id",
            "record",
            "binding_digest",
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "challenge_id",
            "image_id",
            "image_manifest_digest",
            "image_config_digest",
            "platform",
            "profile_digest",
            "endpoint",
            "resolved_address",
            "protocol",
            "max_connections",
            "max_request_bytes",
            "max_response_bytes",
            "timeout_ms",
            "request_digest",
            "observation_sequence",
            "observation_digest",
            "outcome",
            "request_bytes",
            "response_bytes",
            "status",
            "elapsed_ms",
            "transcript_digest",
            "probe_kind",
            "attempted_endpoint_digest",
            "ts",
            "blob_digest",
            "blob_bytes",
            "observation_sequence",
        }
        if set(payload) != required:
            raise InvalidEventError("Target-broker event fields are invalid", sequence=sequence)
        try:
            record = TargetRecord(str(payload["record"]))
            TargetProtocol(str(payload["protocol"]))
            if payload["outcome"]:
                TargetOutcome(str(payload["outcome"]))
        except ValueError as error:
            raise InvalidEventError("Target-broker event enum is invalid", sequence=sequence) from error
        identities = (
            "event_id",
            "request_id",
            "binding_digest",
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "challenge_id",
            "image_id",
            "image_manifest_digest",
            "image_config_digest",
            "platform",
            "profile_digest",
            "endpoint",
            "resolved_address",
            "request_digest",
            "ts",
        )
        if any(not isinstance(payload[name], str) or not payload[name] for name in identities):
            raise InvalidEventError("Target-broker identity is incomplete", sequence=sequence)
        if (
            any(
                _DIGEST.fullmatch(str(payload[name])) is None
                for name in ("image_id", "image_manifest_digest", "image_config_digest")
            )
            or payload["platform"] not in {"linux/amd64", "linux/arm64"}
            or _HEX_DIGEST.fullmatch(str(payload["profile_digest"])) is None
        ):
            raise InvalidEventError("Target-broker candidate binding is invalid", sequence=sequence)
        numbers = (
            "max_connections",
            "max_request_bytes",
            "max_response_bytes",
            "timeout_ms",
            "request_bytes",
            "response_bytes",
            "status",
            "elapsed_ms",
            "blob_bytes",
        )
        if any(
            not isinstance(payload[name], int) or isinstance(payload[name], bool) or payload[name] < 0
            for name in numbers
        ):
            raise InvalidEventError("Target-broker accounting is invalid", sequence=sequence)
        if any(
            payload[name] <= 0 for name in ("max_connections", "max_request_bytes", "max_response_bytes", "timeout_ms")
        ):
            raise InvalidEventError("Target-broker bounds are invalid", sequence=sequence)
        if record is TargetRecord.RESERVED and (payload["outcome"] or payload["transcript_digest"]):
            raise InvalidEventError("Target-broker reservation carries a classification", sequence=sequence)
        if record is TargetRecord.CLASSIFIED and not payload["outcome"]:
            raise InvalidEventError("Target-broker classification has no outcome", sequence=sequence)


__all__ = [
    "HttpTargetExchangeRequest",
    "TcpTargetExchangeRequest",
    "TargetEndpoint",
    "TargetCandidateBinding",
    "TargetLimits",
    "TargetOutcome",
    "TargetProtocol",
    "TargetProvenance",
    "TargetRecord",
    "TargetResult",
    "TargetExchangeRequest",
    "TARGET_EXCHANGE_COMMAND",
    "TARGET_EXCHANGE_RECORDED",
]
