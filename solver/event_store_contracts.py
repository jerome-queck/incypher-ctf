"""Stable contracts and damage classifications for the canonical event store."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

CANONICAL_SCHEMA_VERSION = 1
EVENTS_DIRECTORY = "canonical"
EVENTS_FILENAME = "events.jsonl"
RESERVATIONS_FILENAME = "reservations.jsonl"
SEALED_DIRECTORY = "sealed/sha256"
RECEIPT_FILENAME = "canonical-event-store.receipt.json"
OBSERVATION_RECORDED = "observation.recorded"
RECEIPT_TYPE = "canonical-event-store"


class DamageKind(str, enum.Enum):
    TORN_APPEND = "torn-append"
    INVALID_EVENT = "invalid-event"
    UNKNOWN_SCHEMA = "unknown-schema"
    DUPLICATE_SEQUENCE = "duplicate-sequence"
    SEQUENCE_GAP = "sequence-gap"
    PREVIOUS_DIGEST_MISMATCH = "previous-digest-mismatch"
    EVENT_DIGEST_MISMATCH = "event-digest-mismatch"
    MISSING_BLOB = "missing-blob"
    BLOB_DIGEST_MISMATCH = "blob-digest-mismatch"
    PROJECTION_MISMATCH = "projection-mismatch"
    INVALID_RECEIPT = "invalid-receipt"


class ReservationStatus(str, enum.Enum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    RECOVERED = "recovered"


class EventStoreDamage(RuntimeError):
    """A typed, fail-closed canonical-store damage report."""

    kind = DamageKind.INVALID_EVENT

    def __init__(
        self,
        kind_or_message: DamageKind | str,
        message: str | None = None,
        *,
        sequence: int | None = None,
    ) -> None:
        if isinstance(kind_or_message, DamageKind):
            kind = kind_or_message
            detail = message or ""
        else:
            kind = self.kind
            detail = kind_or_message
        self.kind = kind
        self.classification = kind.value
        self.sequence = sequence
        super().__init__(f"{kind.value}: {detail}")


class TornAppendError(EventStoreDamage):
    kind = DamageKind.TORN_APPEND


class InvalidEventError(EventStoreDamage):
    kind = DamageKind.INVALID_EVENT


class UnknownSchemaError(EventStoreDamage):
    kind = DamageKind.UNKNOWN_SCHEMA


class DuplicateSequenceError(EventStoreDamage):
    kind = DamageKind.DUPLICATE_SEQUENCE


class SequenceGapError(EventStoreDamage):
    kind = DamageKind.SEQUENCE_GAP


class PreviousDigestMismatchError(EventStoreDamage):
    kind = DamageKind.PREVIOUS_DIGEST_MISMATCH


class EventDigestMismatchError(EventStoreDamage):
    kind = DamageKind.EVENT_DIGEST_MISMATCH


class MissingBlobError(EventStoreDamage):
    kind = DamageKind.MISSING_BLOB


class BlobDigestMismatchError(EventStoreDamage):
    kind = DamageKind.BLOB_DIGEST_MISMATCH


class ProjectionMismatchError(EventStoreDamage):
    kind = DamageKind.PROJECTION_MISMATCH


class InvalidReceiptError(EventStoreDamage):
    kind = DamageKind.INVALID_RECEIPT


@dataclass(frozen=True)
class ObservationRecorded:
    attempt_id: str
    step_index: int
    command_raw: str
    command_normalised: str
    tool: str
    source: str = "solver"
    exit_code: int | None = None
    duration_ms: int = 0
    checkpoint: str | None = None
    model: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    usage_known: bool = True
    ts: str | None = None
    mono: float | None = None
    event_id: str | None = None

    @property
    def event_type(self) -> str:
        return OBSERVATION_RECORDED

    @property
    def identity(self) -> tuple[str, int]:
        return self.attempt_id, self.step_index

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "step_index": self.step_index,
            "command_raw": self.command_raw,
            "command_normalised": self.command_normalised,
            "tool": self.tool,
            "source": self.source,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "checkpoint": self.checkpoint,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cache_read": self.cache_read,
            "cache_write": self.cache_write,
            "usage_known": self.usage_known,
            "ts": self.ts,
            "mono": self.mono,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
            "event_id": self.event_id,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        """Refuse a stored Observation that the typed writer could not have produced."""

        field_types: dict[str, tuple[type[Any], ...]] = {
            "attempt_id": (str,),
            "step_index": (int,),
            "command_raw": (str,),
            "command_normalised": (str,),
            "tool": (str,),
            "source": (str,),
            "exit_code": (int, type(None)),
            "duration_ms": (int,),
            "checkpoint": (str, type(None)),
            "model": (str,),
            "tokens_in": (int,),
            "tokens_out": (int,),
            "cache_read": (int,),
            "cache_write": (int,),
            "usage_known": (bool,),
            "ts": (str, type(None)),
            "mono": (int, float, type(None)),
            "blob_digest": (str,),
            "blob_bytes": (int,),
            "event_id": (str, type(None)),
        }
        missing = [field for field in field_types if field not in payload]
        if missing:
            raise InvalidEventError(
                f"Observation payload is missing required fields: {', '.join(missing)}",
                sequence=sequence,
            )
        integer_fields = {
            "exit_code",
            "step_index",
            "duration_ms",
            "tokens_in",
            "tokens_out",
            "cache_read",
            "cache_write",
            "blob_bytes",
        }
        for field, expected in field_types.items():
            value = payload[field]
            if not isinstance(value, expected) or (field in integer_fields | {"mono"} and isinstance(value, bool)):
                names = " or ".join(item.__name__ for item in expected if item is not type(None))
                raise InvalidEventError(
                    f"Observation payload field {field!r} must be {names}",
                    sequence=sequence,
                )
        digest = payload["blob_digest"]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise InvalidEventError("Observation blob digest is not lowercase SHA-256", sequence=sequence)
        if (
            payload["step_index"] < 1
            or payload["duration_ms"] < 0
            or any(
                payload[field] < 0 for field in ("tokens_in", "tokens_out", "cache_read", "cache_write", "blob_bytes")
            )
        ):
            raise InvalidEventError("Observation payload contains a negative count", sequence=sequence)


@dataclass(frozen=True)
class CommittedEvent:
    sequence: int
    previous_digest: str
    event_digest: str
    event_type: str
    payload: Mapping[str, Any]
    envelope: Mapping[str, Any]
    blob_digest: str
    blob_bytes: int
    body: bytes = b""


@dataclass(frozen=True)
class EventReservation:
    sequence: int
    previous_digest: str
    run_id: str
    event_type: str
    event_fingerprint: str
    blob_digest: str
    blob_bytes: int
    reservation_id: str
    status: ReservationStatus | str = ReservationStatus.RESERVED

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", ReservationStatus(self.status))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CANONICAL_SCHEMA_VERSION,
            "reservation_id": self.reservation_id,
            "sequence": self.sequence,
            "previous_digest": self.previous_digest,
            "run_id": self.run_id,
            "event_type": self.event_type,
            "event_fingerprint": self.event_fingerprint,
            "blob_digest": self.blob_digest,
            "blob_bytes": self.blob_bytes,
            "status": self.status.value,
        }


@dataclass(frozen=True)
class EventStoreReceipt:
    schema_version: int
    receipt_type: str
    run_id: str
    receipt_id: str
    event_sequence: int
    event_chain_head: str
    sealed_blob_digest: str
    sealed_blob_bytes: int
    projection_ref: str | None
    projection_digest: str | None
    crash_point: str
    compatibility_projection: bool
    reservation_status: ReservationStatus | None

    def __post_init__(self) -> None:
        if self.reservation_status is not None:
            object.__setattr__(self, "reservation_status", ReservationStatus(self.reservation_status))

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "receipt_type": self.receipt_type,
            "run_id": self.run_id,
            "receipt_id": self.receipt_id,
            "event_sequence": self.event_sequence,
            "event_chain_head": self.event_chain_head,
            "sealed_blob_digest": self.sealed_blob_digest,
            "sealed_blob_bytes": self.sealed_blob_bytes,
            "projection_ref": self.projection_ref,
            "projection_digest": self.projection_digest,
            "crash_point": self.crash_point,
            "compatibility_projection": self.compatibility_projection,
            "reservation_status": self.reservation_status.value if self.reservation_status is not None else None,
        }


@dataclass(frozen=True)
class ProjectionCheck:
    row: Mapping[str, Any] | None
    reference: str | None
    digest: str | None
