"""The narrow public facade for canonical Run events and their sealed bodies."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from solver.event_store_contracts import (
    CANONICAL_SCHEMA_VERSION,
    CanonicalEvent,
    EVENTS_DIRECTORY,
    EVENTS_FILENAME,
    OBSERVATION_RECORDED,
    RECEIPT_FILENAME,
    RECEIPT_TYPE,
    RESERVATIONS_FILENAME,
    SEALED_DIRECTORY,
    BlobDigestMismatchError,
    CommittedEvent,
    CompareAndAppendResult,
    DamageKind,
    DuplicateSequenceError,
    EventReservation,
    EventStoreDamage,
    EventStoreReceipt,
    EventDigestMismatchError,
    InvalidEventError,
    InvalidReceiptError,
    LIFECYCLE_RECORDED,
    LifecycleRecorded,
    WORK_GENERATION_RECORDED,
    CAPABILITY_CUSTODY_RECORDED,
    CapabilityCustodyRecorded,
    CapabilityRecord,
    GenerationAuthority,
    GenerationClassification,
    GenerationDisposition,
    GenerationRecord,
    WorkGenerationRecorded,
    MissingBlobError,
    ObservationRecorded,
    PreviousDigestMismatchError,
    ProjectionCheck,
    ProjectionMismatchError,
    ReservationStatus,
    RunNotTerminalError,
    RunSealedError,
    SequenceGapError,
    TornAppendError,
    TerminalEventStoreSnapshot,
    UnknownSchemaError,
)
from solver.event_store_core import EventStoreCore
from solver.event_store_projection import ProjectionReceiptService, projection_fields
from solver.redaction import Redactor


class EventStore:
    """Small boundary for canonical facts, their projection, and receipt proof."""

    def __init__(
        self,
        root: Path,
        run_id: str | None = None,
        *,
        redactor: Redactor | None = None,
        append_hook: Callable[[str], None] | None = None,
    ) -> None:
        self._core = EventStoreCore(root, run_id, redactor=redactor, append_hook=append_hook)
        self._proof = ProjectionReceiptService(self._core, self._core.run_dir, self._core.canonical_dir)

    @property
    def run_dir(self) -> Path:
        return self._core.run_dir

    @property
    def run_id(self) -> str:
        return self._core.run_id

    @property
    def canonical_dir(self) -> Path:
        return self._core.canonical_dir

    @property
    def events_path(self) -> Path:
        return self._core.events_path

    @property
    def reservations_path(self) -> Path:
        return self._core.reservations_path

    @property
    def sealed_dir(self) -> Path:
        return self._core.sealed_dir

    @contextmanager
    def writer_batch(self) -> Iterator[EventStore]:
        """Hold the canonical writer across a bounded reserve/commit batch."""
        with self._core.writer_batch():
            yield self

    def append(self, event: CanonicalEvent, *, body: bytes) -> CommittedEvent:
        return self._core.append(event, body=body)

    def compare_and_append(
        self,
        event: CanonicalEvent,
        *,
        body: bytes,
        latest_event_type: str,
        latest_payload: dict[str, object],
        expected_projection: dict[str, object],
    ) -> CompareAndAppendResult:
        return self._core.compare_and_append(
            event,
            body=body,
            latest_event_type=latest_event_type,
            latest_payload=latest_payload,
            expected_projection=expected_projection,
        )

    def reserve(
        self,
        event: CanonicalEvent,
        *,
        blob_digest: str,
        blob_bytes: int,
        sequence: int | None = None,
    ) -> EventReservation:
        return self._core.reserve(event, blob_digest=blob_digest, blob_bytes=blob_bytes, sequence=sequence)

    def commit(
        self,
        reservation: EventReservation,
        event: CanonicalEvent,
        *,
        body: bytes,
    ) -> CommittedEvent:
        return self._core.commit(reservation, event, body=body)

    def events(self) -> list[CommittedEvent]:
        return self._core.events()

    def terminal_snapshot(self) -> TerminalEventStoreSnapshot:
        return self._core.terminal_snapshot()

    def reservations(self) -> list[EventReservation]:
        return self._core.reservations()

    def blob(self, digest: str) -> bytes:
        return self._core.blob(digest)

    def projection(
        self,
        event: CommittedEvent,
        *,
        projection_path: Path | None = None,
    ) -> ProjectionCheck:
        return self._proof.projection(event, projection_path=projection_path)

    def write_receipt(
        self,
        *,
        event_sequence: int | None = None,
        projection_path: Path | None = None,
        destination: Path | None = None,
    ) -> Path:
        return self._proof.write_receipt(
            event_sequence=event_sequence,
            projection_path=projection_path,
            destination=destination,
        )

    def verify_receipt(self, path: Path, *, projection_path: Path | None = None) -> EventStoreReceipt:
        return self._proof.verify_receipt(path, projection_path=projection_path)


__all__ = [
    "BlobDigestMismatchError",
    "CAPABILITY_CUSTODY_RECORDED",
    "CANONICAL_SCHEMA_VERSION",
    "CapabilityCustodyRecorded",
    "CapabilityRecord",
    "CommittedEvent",
    "CompareAndAppendResult",
    "DamageKind",
    "DuplicateSequenceError",
    "EventReservation",
    "EventStore",
    "EventStoreDamage",
    "EventStoreReceipt",
    "EventDigestMismatchError",
    "EVENTS_DIRECTORY",
    "EVENTS_FILENAME",
    "InvalidEventError",
    "InvalidReceiptError",
    "LIFECYCLE_RECORDED",
    "LifecycleRecorded",
    "WORK_GENERATION_RECORDED",
    "GenerationAuthority",
    "GenerationClassification",
    "GenerationDisposition",
    "GenerationRecord",
    "WorkGenerationRecorded",
    "MissingBlobError",
    "OBSERVATION_RECORDED",
    "ObservationRecorded",
    "PreviousDigestMismatchError",
    "ProjectionCheck",
    "ProjectionMismatchError",
    "projection_fields",
    "ReservationStatus",
    "RunNotTerminalError",
    "RunSealedError",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "RESERVATIONS_FILENAME",
    "SEALED_DIRECTORY",
    "SequenceGapError",
    "TornAppendError",
    "TerminalEventStoreSnapshot",
    "UnknownSchemaError",
]
