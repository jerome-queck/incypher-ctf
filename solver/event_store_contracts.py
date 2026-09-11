"""Stable contracts and damage classifications for the canonical event store."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from solver.capability_event_contracts import CapabilityFact

CANONICAL_SCHEMA_VERSION = 1
EVENTS_DIRECTORY = "canonical"
EVENTS_FILENAME = "events.jsonl"
RESERVATIONS_FILENAME = "reservations.jsonl"
SEALED_DIRECTORY = "sealed/sha256"
RECEIPT_FILENAME = "canonical-event-store.receipt.json"
OBSERVATION_RECORDED = "observation.recorded"
LIFECYCLE_RECORDED = "lifecycle.recorded"
WORK_GENERATION_RECORDED = "work-generation.recorded"
CAPABILITY_CUSTODY_RECORDED = "capability-custody.recorded"
ATTEMPT_ENVELOPE_RECORDED = "attempt-envelope.recorded"
RECEIPT_TYPE = "canonical-event-store"

EMPTY_BLOB_DIGEST = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


class LifecycleRecord(str, enum.Enum):
    RUN_OPEN = "run-open"
    SERVICE_STARTED = "service-started"
    BOOT_OPEN = "boot-open"
    SIGNAL_FORWARDED = "signal-forwarded"
    CHILD_REAPED = "child-reaped"
    BOOT_CLOSE = "boot-close"
    RUN_CLOSE = "run-close"


class TerminalDisposition(str, enum.Enum):
    NORMAL = "normal"
    REFUSED = "refused"
    INTERRUPTED = "interrupted"
    CRASHED = "crashed"


class ForwardedSignal(str, enum.Enum):
    TERM = "SIGTERM"
    INT = "SIGINT"


class GenerationDisposition(str, enum.Enum):
    COMPLETE = "complete"
    INTERRUPT = "interrupt"
    SUPERSEDE = "supersede"
    ABANDON = "abandon"


class GenerationAuthority(str, enum.Enum):
    CARRY = "carry"
    CANDIDATE = "candidate"
    TOOL = "tool"
    AUTHORITY = "authority"


class GenerationRecord(str, enum.Enum):
    ACQUIRE = "acquire"
    CLOSE = "close"
    AUTHORITY = "authority"
    LATE_EVENT = "late-event"


class GenerationClassification(str, enum.Enum):
    CURRENT = "current-generation"
    CLOSED = "closed-generation"
    SUPERSEDED = "superseded-generation"


class CapabilityRecord(str, enum.Enum):
    ENV_CUTOVER = "env-cutover"
    TRANSFER_ACCEPTED = "transfer-accepted"
    SOURCE_CLEARED = "source-cleared"
    PROBE_RECORDED = "probe-recorded"
    ISSUED = "issued"
    AUTHORIZED = "authorized"
    DENIED = "denied"
    REVOKED = "revoked"


class ServiceName(str, enum.Enum):
    VERIFIED_REPLAY = "verified-replay"
    STORAGE_ADMISSION = "storage-admission"
    STRICT_ISOLATION = "strict-isolation"
    CREDENTIAL_CUSTODY = "credential-custody"
    RUN_CONTROLLER = "run-controller"


@dataclass(frozen=True)
class RunOpened:
    pass


@dataclass(frozen=True)
class ServiceStarted:
    boot_id: str
    service: ServiceName


@dataclass(frozen=True)
class BootOpened:
    boot_id: str


@dataclass(frozen=True)
class SignalForwarded:
    boot_id: str
    signal: ForwardedSignal


@dataclass(frozen=True)
class ChildReaped:
    boot_id: str
    reaped_children: int


@dataclass(frozen=True)
class BootClosed:
    boot_id: str
    disposition: TerminalDisposition
    detail: str = ""


@dataclass(frozen=True)
class RunClosed:
    disposition: TerminalDisposition
    detail: str = ""


LifecycleFact = RunOpened | ServiceStarted | BootOpened | SignalForwarded | ChildReaped | BootClosed | RunClosed


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


class RunSealedError(RuntimeError):
    """A new canonical fact was attempted after the terminal Run fact."""


class RunNotTerminalError(RuntimeError):
    """A terminal snapshot was requested before the Run was complete."""


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

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> tuple[str, int]:
        return str(payload.get("attempt_id", "")), int(payload.get("step_index", -1))

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
class LifecycleRecorded:
    """One durable Supervisor lifecycle fact in the canonical Run chain."""

    event_id: str
    fact: LifecycleFact
    ts: str = ""

    @property
    def event_type(self) -> str:
        return LIFECYCLE_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        payload = {
            "event_id": self.event_id,
            "record": "",
            "boot_id": "",
            "service": "",
            "disposition": "",
            "signal": "",
            "reaped_children": 0,
            "detail": "",
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }
        fact = self.fact
        if isinstance(fact, RunOpened):
            payload["record"] = LifecycleRecord.RUN_OPEN.value
        elif isinstance(fact, ServiceStarted):
            payload.update(
                record=LifecycleRecord.SERVICE_STARTED.value, boot_id=fact.boot_id, service=fact.service.value
            )
        elif isinstance(fact, BootOpened):
            payload.update(record=LifecycleRecord.BOOT_OPEN.value, boot_id=fact.boot_id)
        elif isinstance(fact, SignalForwarded):
            payload.update(
                record=LifecycleRecord.SIGNAL_FORWARDED.value, boot_id=fact.boot_id, signal=fact.signal.value
            )
        elif isinstance(fact, ChildReaped):
            payload.update(
                record=LifecycleRecord.CHILD_REAPED.value,
                boot_id=fact.boot_id,
                reaped_children=fact.reaped_children,
            )
        elif isinstance(fact, BootClosed):
            payload.update(
                record=LifecycleRecord.BOOT_CLOSE.value,
                boot_id=fact.boot_id,
                disposition=fact.disposition.value,
                detail=fact.detail,
            )
        elif isinstance(fact, RunClosed):
            payload.update(
                record=LifecycleRecord.RUN_CLOSE.value,
                disposition=fact.disposition.value,
                detail=fact.detail,
            )
        return payload

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        fields = {
            "event_id": str,
            "record": str,
            "boot_id": str,
            "service": str,
            "disposition": str,
            "signal": str,
            "reaped_children": int,
            "detail": str,
            "ts": str,
            "blob_digest": str,
            "blob_bytes": int,
        }
        missing = [field for field in fields if field not in payload]
        if missing:
            raise InvalidEventError(
                f"Lifecycle payload is missing required fields: {', '.join(missing)}",
                sequence=sequence,
            )
        for field, expected in fields.items():
            value = payload[field]
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                raise InvalidEventError(f"Lifecycle payload field {field!r} has the wrong type", sequence=sequence)
        if not payload["event_id"] or payload["record"] not in {item.value for item in LifecycleRecord}:
            raise InvalidEventError("Lifecycle identity or record is unsupported", sequence=sequence)
        if payload["reaped_children"] < 0 or payload["blob_bytes"] < 0:
            raise InvalidEventError("Lifecycle payload contains a negative count", sequence=sequence)
        digest = payload["blob_digest"]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise InvalidEventError("Lifecycle blob digest is not lowercase SHA-256", sequence=sequence)
        if payload["blob_bytes"] != 0 or digest != EMPTY_BLOB_DIGEST:
            raise InvalidEventError("Lifecycle events cannot carry a sealed body", sequence=sequence)
        record = payload["record"]
        if record in {"boot-close", "run-close"}:
            if payload["disposition"] not in {item.value for item in TerminalDisposition}:
                raise InvalidEventError("Lifecycle terminal disposition is unsupported", sequence=sequence)
        elif payload["disposition"]:
            raise InvalidEventError("Lifecycle nonterminal record carries a disposition", sequence=sequence)
        if record in {"service-started", "boot-open", "signal-forwarded", "child-reaped", "boot-close"}:
            if not payload["boot_id"]:
                raise InvalidEventError("Lifecycle Boot record has no Boot identity", sequence=sequence)
        elif payload["boot_id"]:
            raise InvalidEventError("Lifecycle Run record carries a Boot identity", sequence=sequence)
        if (record == "service-started") != bool(payload["service"]):
            raise InvalidEventError("Lifecycle service identity disagrees with its record", sequence=sequence)
        if record == "signal-forwarded":
            if payload["signal"] not in {item.value for item in ForwardedSignal}:
                raise InvalidEventError("Lifecycle forwarded signal is unsupported", sequence=sequence)
        elif payload["signal"]:
            raise InvalidEventError("Lifecycle nonsignal record carries a signal", sequence=sequence)
        if record != "child-reaped" and payload["reaped_children"]:
            raise InvalidEventError("Lifecycle non-reap record carries a child count", sequence=sequence)


@dataclass(frozen=True)
class WorkGenerationRecorded:
    """One identity-bound Work-generation transition or rejected late event."""

    event_id: str
    generation_id: str
    work_id: str
    attempt_id: str
    record: GenerationRecord
    disposition: GenerationDisposition | None = None
    authority: GenerationAuthority | None = None
    classification: GenerationClassification | None = None
    ts: str = ""

    @property
    def event_type(self) -> str:
        return WORK_GENERATION_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "generation_id": self.generation_id,
            "work_id": self.work_id,
            "attempt_id": self.attempt_id,
            "record": self.record.value,
            "disposition": self.disposition.value if self.disposition is not None else "",
            "authority": self.authority.value if self.authority is not None else "",
            "classification": self.classification.value if self.classification is not None else "",
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        fields = {
            "event_id": str,
            "generation_id": str,
            "work_id": str,
            "attempt_id": str,
            "record": str,
            "disposition": str,
            "authority": str,
            "classification": str,
            "ts": str,
            "blob_digest": str,
            "blob_bytes": int,
        }
        missing = [field for field in fields if field not in payload]
        if missing:
            raise InvalidEventError(
                f"Work-generation payload is missing required fields: {', '.join(missing)}",
                sequence=sequence,
            )
        for field, expected in fields.items():
            value = payload[field]
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                raise InvalidEventError(
                    f"Work-generation payload field {field!r} has the wrong type",
                    sequence=sequence,
                )
        if not all(payload[field] for field in ("event_id", "generation_id", "work_id", "attempt_id")):
            raise InvalidEventError("Work-generation identity is incomplete", sequence=sequence)
        if payload["record"] not in {item.value for item in GenerationRecord}:
            raise InvalidEventError("Work-generation record is unsupported", sequence=sequence)
        if payload["blob_bytes"] < 0:
            raise InvalidEventError("Work-generation body size is negative", sequence=sequence)
        digest = payload["blob_digest"]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise InvalidEventError("Work-generation blob digest is not lowercase SHA-256", sequence=sequence)
        record = payload["record"]
        if record == GenerationRecord.ACQUIRE.value:
            if any(payload[field] for field in ("disposition", "authority", "classification")):
                raise InvalidEventError("Work-generation acquisition carries closing data", sequence=sequence)
            cls._require_empty_body(payload, sequence)
        elif record == GenerationRecord.CLOSE.value:
            if payload["disposition"] not in {item.value for item in GenerationDisposition}:
                raise InvalidEventError("Work-generation close disposition is unsupported", sequence=sequence)
            if payload["authority"] or payload["classification"]:
                raise InvalidEventError("Work-generation close carries late-event data", sequence=sequence)
            cls._require_empty_body(payload, sequence)
        elif record == GenerationRecord.AUTHORITY.value:
            if payload["disposition"]:
                raise InvalidEventError(
                    "Work-generation authority reservation carries a disposition", sequence=sequence
                )
            if payload["authority"] not in {item.value for item in GenerationAuthority}:
                raise InvalidEventError("Work-generation authority kind is unsupported", sequence=sequence)
            if payload["classification"] != GenerationClassification.CURRENT.value:
                raise InvalidEventError("Work-generation authority reservation is not current", sequence=sequence)
            cls._require_empty_body(payload, sequence)
        else:
            if payload["disposition"]:
                raise InvalidEventError("Work-generation late event carries a close disposition", sequence=sequence)
            if payload["authority"] not in {item.value for item in GenerationAuthority}:
                raise InvalidEventError("Work-generation authority kind is unsupported", sequence=sequence)
            if payload["classification"] not in {
                GenerationClassification.CLOSED.value,
                GenerationClassification.SUPERSEDED.value,
            }:
                raise InvalidEventError("Work-generation late-event classification is unsupported", sequence=sequence)

    @staticmethod
    def _require_empty_body(payload: Mapping[str, Any], sequence: int) -> None:
        if payload["blob_bytes"] != 0 or payload["blob_digest"] != EMPTY_BLOB_DIGEST:
            raise InvalidEventError("Work-generation transition cannot carry a sealed body", sequence=sequence)


@dataclass(frozen=True)
class CapabilityCustodyRecorded:
    """Envelope for one record-specific sanitized capability fact."""

    event_id: str
    fact: CapabilityFact
    ts: str = ""

    @property
    def event_type(self) -> str:
        return CAPABILITY_CUSTODY_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        payload = {
            "event_id": self.event_id,
            "record": self.fact.record.value,
            "handle_digest": "",
            "run_id": "",
            "boot_id": "",
            "generation_id": "",
            "lane_id": "",
            "attempt_id": "",
            "step_id": "",
            "scope": "",
            "broker": "",
            "secret_names": [],
            "probe_kind": "",
            "probe_result": "",
            "evidence_digest": "",
            "peer_uid": -1,
            "peer_identity_digest": "",
            "decision": "",
            "reason": "",
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }
        payload.update(self.fact.fields())
        payload["secret_names"] = list(payload["secret_names"])
        return payload

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        strings = (
            "event_id",
            "record",
            "handle_digest",
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "scope",
            "broker",
            "probe_kind",
            "probe_result",
            "evidence_digest",
            "peer_identity_digest",
            "decision",
            "reason",
            "ts",
            "blob_digest",
        )
        missing = [field for field in (*strings, "secret_names", "peer_uid", "blob_bytes") if field not in payload]
        if missing:
            raise InvalidEventError(
                f"Capability-custody payload is missing required fields: {', '.join(missing)}",
                sequence=sequence,
            )
        if any(not isinstance(payload[field], str) for field in strings):
            raise InvalidEventError("Capability-custody payload has a non-string field", sequence=sequence)
        if not isinstance(payload["secret_names"], list) or any(
            not isinstance(name, str) or not name for name in payload["secret_names"]
        ):
            raise InvalidEventError("Capability-custody secret names are invalid", sequence=sequence)
        for field in ("peer_uid", "blob_bytes"):
            if not isinstance(payload[field], int) or isinstance(payload[field], bool):
                raise InvalidEventError(
                    f"Capability-custody payload field {field!r} is not an integer", sequence=sequence
                )
        if not payload["event_id"] or payload["record"] not in {item.value for item in CapabilityRecord}:
            raise InvalidEventError("Capability-custody identity or record is unsupported", sequence=sequence)
        for field in ("blob_digest",):
            digest = payload[field]
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise InvalidEventError(f"Capability-custody {field} is not lowercase SHA-256", sequence=sequence)
        if payload["blob_bytes"] != 0 or payload["blob_digest"] != EMPTY_BLOB_DIGEST:
            raise InvalidEventError("Capability-custody events cannot carry a sealed body", sequence=sequence)
        record = payload["record"]
        handle_records = {
            CapabilityRecord.ISSUED.value,
            CapabilityRecord.AUTHORIZED.value,
            CapabilityRecord.DENIED.value,
            CapabilityRecord.REVOKED.value,
        }
        if record in handle_records and payload["handle_digest"]:
            digest = payload["handle_digest"]
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise InvalidEventError("Capability handle digest is not lowercase SHA-256", sequence=sequence)
        elif payload["handle_digest"]:
            raise InvalidEventError("Capability non-handle record carries a handle digest", sequence=sequence)
        if record in {
            CapabilityRecord.ENV_CUTOVER.value,
            CapabilityRecord.TRANSFER_ACCEPTED.value,
            CapabilityRecord.SOURCE_CLEARED.value,
            CapabilityRecord.PROBE_RECORDED.value,
        }:
            digest = payload["evidence_digest"]
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise InvalidEventError("Capability evidence digest is not lowercase SHA-256", sequence=sequence)
        elif payload["evidence_digest"]:
            raise InvalidEventError("Capability handle record carries an evidence digest", sequence=sequence)
        from solver.capability_event_contracts import validate_capability_fact

        validate_capability_fact(payload, sequence=sequence)


CanonicalEvent = ObservationRecorded | LifecycleRecorded | WorkGenerationRecorded | CapabilityCustodyRecorded


def event_contract(event_type: str):
    from solver.attempt_executor_contracts import AttemptEnvelopeRecorded
    from solver.attempt_process_contracts import ATTEMPT_PROCESS_RECORDED, AttemptProcessRecorded
    from solver.storage_governor_contracts import STORAGE_GOVERNOR_RECORDED, StorageGovernorRecorded

    contracts = {
        OBSERVATION_RECORDED: ObservationRecorded,
        LIFECYCLE_RECORDED: LifecycleRecorded,
        WORK_GENERATION_RECORDED: WorkGenerationRecorded,
        CAPABILITY_CUSTODY_RECORDED: CapabilityCustodyRecorded,
        ATTEMPT_ENVELOPE_RECORDED: AttemptEnvelopeRecorded,
        ATTEMPT_PROCESS_RECORDED: AttemptProcessRecorded,
        STORAGE_GOVERNOR_RECORDED: StorageGovernorRecorded,
    }
    return contracts.get(event_type)


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
class TerminalEventStoreSnapshot:
    """One lock-consistent complete terminal canonical source."""

    run_id: str
    events: tuple[CommittedEvent, ...]
    chain_head: str


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
