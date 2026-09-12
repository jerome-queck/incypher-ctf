"""Small public types for generation-scoped Board authority."""

from __future__ import annotations

import enum
import base64
import datetime as dt
from dataclasses import dataclass
from typing import Any, Mapping

from solver.board import Held, Mana, Reply, Standing, Verdict
from solver.capability import CapabilityBinding
from solver.event_store_contracts import InvalidEventError

BOARD_BROKER_RECORDED = "board-broker.recorded"
BOARD_BROKER_SOCKET_ENV = "INCYPHER_BOARD_BROKER_SOCKET"
BOARD_BROKER_HOLDINGS_ENV = "INCYPHER_BOARD_BROKER_HOLDINGS"
BOARD_PROFILE_HANDLE_ENV = "INCYPHER_BOARD_PROFILE_HANDLE"
SCHEMA_VERSION = 1


class BoardOperation(str, enum.Enum):
    READ_CONTRACT = "read-contract"
    CHALLENGES = "challenges"
    CHALLENGE = "challenge"
    SCOREBOARD = "scoreboard"
    DOWNLOAD = "download"
    SUBMIT = "submit"
    INSTANCE_DEPLOY = "instance-deploy"
    INSTANCE_READ = "instance-read"
    INSTANCE_RENEW = "instance-renew"
    INSTANCE_TERMINATE = "instance-terminate"
    INSTANCE_MANA = "instance-mana"
    INSTANCES_HELD = "instances-held"


class BoardOutcome(str, enum.Enum):
    ANSWERED = "answered"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    MALFORMED = "malformed"
    AUTH_FAILURE = "auth-failure"
    TOO_LARGE = "too-large"
    REVOKED = "revoked"
    CAPABILITY_REFUSED = "capability-refused"
    RESERVATION_REFUSED = "reservation-refused"
    EFFECT_INDETERMINATE = "effect-indeterminate"


class BoardRecord(str, enum.Enum):
    RESERVED = "reserved"
    CLASSIFIED = "classified"


@dataclass(frozen=True)
class BoardProvenance:
    endpoint: str = ""
    http_status: int = 0
    response_digest: str = ""
    original_bytes: int = 0
    truncated: bool = False
    lost_bytes: int = 0


@dataclass(frozen=True)
class ReadContractValue:
    reaches_ctfd: bool


@dataclass(frozen=True)
class ChallengesValue:
    entries: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class ChallengeValue:
    fields: Mapping[str, object]


@dataclass(frozen=True)
class ScoreboardValue:
    standings: tuple[Standing, ...]


@dataclass(frozen=True)
class DownloadValue:
    content: bytes
    hops: tuple[str, ...]


BoardValue = (
    ReadContractValue
    | ChallengesValue
    | ChallengeValue
    | ScoreboardValue
    | DownloadValue
    | Verdict
    | Reply
    | Mana
    | tuple[Held, ...]
)


@dataclass(frozen=True)
class BoardBrokerResult:
    operation: BoardOperation
    outcome: BoardOutcome
    value: BoardValue | None = None
    provenance: BoardProvenance = BoardProvenance()
    request_id: str = ""


def binding_document(binding: CapabilityBinding) -> dict[str, str]:
    return binding.document()


def binding_from(document: Mapping[str, object]) -> CapabilityBinding:
    fields = ("run_id", "boot_id", "generation_id", "lane_id", "attempt_id", "step_id")
    if set(document) != set(fields) or any(not isinstance(document[name], str) for name in fields):
        raise ValueError("Board capability binding is invalid")
    return CapabilityBinding(*(str(document[name]) for name in fields))


def encode_result(result: BoardBrokerResult) -> dict[str, object]:
    return {
        "operation": result.operation.value,
        "outcome": result.outcome.value,
        "value": _encode_value(result.operation, result.value),
        "provenance": {
            "endpoint": result.provenance.endpoint,
            "http_status": result.provenance.http_status,
            "response_digest": result.provenance.response_digest,
            "original_bytes": result.provenance.original_bytes,
            "truncated": result.provenance.truncated,
            "lost_bytes": result.provenance.lost_bytes,
        },
        "request_id": result.request_id,
    }


def decode_result(document: Mapping[str, object]) -> BoardBrokerResult:
    operation = BoardOperation(str(document["operation"]))
    outcome = BoardOutcome(str(document["outcome"]))
    provenance = document.get("provenance")
    if not isinstance(provenance, Mapping):
        raise ValueError("Board broker provenance is absent")
    return BoardBrokerResult(
        operation,
        outcome,
        _decode_value(operation, document.get("value")),
        BoardProvenance(
            endpoint=str(provenance.get("endpoint", "")),
            http_status=int(provenance.get("http_status", 0)),
            response_digest=str(provenance.get("response_digest", "")),
            original_bytes=int(provenance.get("original_bytes", 0)),
            truncated=bool(provenance.get("truncated", False)),
            lost_bytes=int(provenance.get("lost_bytes", 0)),
        ),
        str(document.get("request_id", "")),
    )


def _encode_value(operation: BoardOperation, value: BoardValue | None) -> object:
    if value is None:
        return None
    if isinstance(value, ReadContractValue):
        return {"reaches_ctfd": value.reaches_ctfd}
    if isinstance(value, ChallengesValue):
        return {"entries": list(value.entries)}
    if isinstance(value, ChallengeValue):
        return {"fields": dict(value.fields)}
    if isinstance(value, ScoreboardValue):
        return {"standings": [vars(row) for row in value.standings]}
    if isinstance(value, DownloadValue):
        return {"content": base64.b64encode(value.content).decode(), "hops": list(value.hops)}
    if isinstance(value, Verdict):
        return vars(value)
    if isinstance(value, Reply):
        return {**vars(value), "until": value.until.isoformat() if value.until else ""}
    if isinstance(value, Mana):
        return vars(value)
    if operation is BoardOperation.INSTANCES_HELD:
        return {"held": [vars(row) for row in value]}
    raise TypeError("Board broker value does not match its operation")


def _decode_value(operation: BoardOperation, value: object) -> BoardValue | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("Board broker value is not an object")
    if operation is BoardOperation.READ_CONTRACT:
        return ReadContractValue(bool(value["reaches_ctfd"]))
    if operation is BoardOperation.CHALLENGES:
        return ChallengesValue(tuple(value["entries"]))
    if operation is BoardOperation.CHALLENGE:
        return ChallengeValue(dict(value["fields"]))
    if operation is BoardOperation.SCOREBOARD:
        return ScoreboardValue(tuple(Standing(**row) for row in value["standings"]))
    if operation is BoardOperation.DOWNLOAD:
        return DownloadValue(base64.b64decode(str(value["content"])), tuple(value["hops"]))
    if operation is BoardOperation.SUBMIT:
        return Verdict(**value)
    if operation in {
        BoardOperation.INSTANCE_DEPLOY,
        BoardOperation.INSTANCE_READ,
        BoardOperation.INSTANCE_RENEW,
        BoardOperation.INSTANCE_TERMINATE,
    }:
        until = dt.datetime.fromisoformat(str(value["until"])) if value.get("until") else None
        return Reply(str(value["outcome"]), str(value["connection_info"]), until, str(value["detail"]))
    if operation is BoardOperation.INSTANCE_MANA:
        return Mana(**value)
    if operation is BoardOperation.INSTANCES_HELD:
        return tuple(Held(**row) for row in value["held"])
    raise ValueError("Board broker operation has no typed value")


@dataclass(frozen=True)
class BoardBrokerRecorded:
    event_id: str
    request_id: str
    record: BoardRecord
    operation: BoardOperation
    binding_digest: str
    run_id: str
    boot_id: str
    generation_id: str
    lane_id: str
    attempt_id: str
    step_id: str
    scope: str
    peer_identity_digest: str
    request_digest: str
    outcome: BoardOutcome | None = None
    endpoint: str = ""
    http_status: int = 0
    response_digest: str = ""
    response_original_bytes: int = 0
    response_truncated: bool = False
    response_lost_bytes: int = 0
    ts: str = ""

    @property
    def event_type(self) -> str:
        return BOARD_BROKER_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "record": self.record.value,
            "operation": self.operation.value,
            "binding_digest": self.binding_digest,
            "run_id": self.run_id,
            "boot_id": self.boot_id,
            "generation_id": self.generation_id,
            "lane_id": self.lane_id,
            "attempt_id": self.attempt_id,
            "step_id": self.step_id,
            "scope": self.scope,
            "peer_identity_digest": self.peer_identity_digest,
            "request_digest": self.request_digest,
            "outcome": self.outcome.value if self.outcome else "",
            "endpoint": self.endpoint,
            "http_status": self.http_status,
            "response_digest": self.response_digest,
            "response_original_bytes": self.response_original_bytes,
            "response_truncated": self.response_truncated,
            "response_lost_bytes": self.response_lost_bytes,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        strings = (
            "event_id",
            "request_id",
            "record",
            "operation",
            "binding_digest",
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "scope",
            "peer_identity_digest",
            "request_digest",
            "outcome",
            "endpoint",
            "response_digest",
            "ts",
            "blob_digest",
        )
        integers = ("http_status", "response_original_bytes", "response_lost_bytes", "blob_bytes")
        required = (*strings, *integers, "response_truncated")
        if missing := [name for name in required if name not in payload]:
            raise InvalidEventError(f"Board-broker payload is missing: {', '.join(missing)}", sequence=sequence)
        if any(not isinstance(payload[name], str) for name in strings):
            raise InvalidEventError("Board-broker payload has a non-string field", sequence=sequence)
        if any(not isinstance(payload[name], int) or isinstance(payload[name], bool) for name in integers):
            raise InvalidEventError("Board-broker payload has a non-integer count", sequence=sequence)
        if not isinstance(payload["response_truncated"], bool):
            raise InvalidEventError("Board-broker truncation marker is not boolean", sequence=sequence)
        if not all(
            payload[name]
            for name in (
                "event_id",
                "request_id",
                "operation",
                "binding_digest",
                "run_id",
                "boot_id",
                "generation_id",
                "lane_id",
                "attempt_id",
                "step_id",
                "scope",
                "peer_identity_digest",
                "request_digest",
            )
        ):
            raise InvalidEventError("Board-broker authority identity is incomplete", sequence=sequence)
        if payload["record"] not in {item.value for item in BoardRecord}:
            raise InvalidEventError("Board-broker record is unsupported", sequence=sequence)
        if payload["operation"] not in {item.value for item in BoardOperation}:
            raise InvalidEventError("Board-broker operation is unsupported", sequence=sequence)
        for name in ("binding_digest", "peer_identity_digest", "request_digest", "blob_digest"):
            if not _digest(payload[name]):
                raise InvalidEventError(f"Board-broker {name} is not lowercase SHA-256", sequence=sequence)
        if min(payload[name] for name in integers) < 0:
            raise InvalidEventError("Board-broker payload contains a negative count", sequence=sequence)
        if payload["record"] == BoardRecord.RESERVED.value:
            if (
                payload["outcome"]
                or payload["endpoint"]
                or payload["http_status"]
                or payload["response_digest"]
                or payload["response_original_bytes"]
                or payload["response_truncated"]
                or payload["response_lost_bytes"]
                or payload["blob_bytes"]
            ):
                raise InvalidEventError("Board-broker reservation carries a response", sequence=sequence)
        else:
            if payload["outcome"] not in {item.value for item in BoardOutcome}:
                raise InvalidEventError("Board-broker result has no typed outcome", sequence=sequence)
            if payload["response_digest"] and not _digest(payload["response_digest"]):
                raise InvalidEventError("Board-broker response digest is invalid", sequence=sequence)
            expected_loss = max(0, payload["response_original_bytes"] - payload["blob_bytes"])
            if payload["response_lost_bytes"] != expected_loss:
                raise InvalidEventError("Board-broker response loss accounting disagrees", sequence=sequence)
            if payload["response_truncated"] != bool(payload["response_lost_bytes"]):
                raise InvalidEventError("Board-broker truncation marker disagrees", sequence=sequence)


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not set(value) - set("0123456789abcdef")


__all__ = [
    "BOARD_BROKER_RECORDED",
    "BOARD_BROKER_HOLDINGS_ENV",
    "BOARD_BROKER_SOCKET_ENV",
    "BOARD_PROFILE_HANDLE_ENV",
    "BoardBrokerRecorded",
    "BoardBrokerResult",
    "BoardProvenance",
    "ChallengeValue",
    "ChallengesValue",
    "DownloadValue",
    "ReadContractValue",
    "ScoreboardValue",
    "BoardOperation",
    "BoardOutcome",
    "BoardRecord",
    "SCHEMA_VERSION",
    "binding_document",
    "binding_from",
    "decode_result",
    "encode_result",
]
