"""The narrow public facade for canonical Observation storage."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from solver.event_store_contracts import (
    CANONICAL_SCHEMA_VERSION,
    EVENTS_DIRECTORY,
    EVENTS_FILENAME,
    OBSERVATION_RECORDED,
    RECEIPT_FILENAME,
    RECEIPT_TYPE,
    RESERVATIONS_FILENAME,
    SEALED_DIRECTORY,
    BlobDigestMismatchError,
    CommittedEvent,
    DamageKind,
    DuplicateSequenceError,
    EventReservation,
    EventStoreDamage,
    EventStoreReceipt,
    EventDigestMismatchError,
    InvalidEventError,
    InvalidReceiptError,
    MissingBlobError,
    ObservationRecorded,
    PreviousDigestMismatchError,
    ProjectionCheck,
    ProjectionMismatchError,
    ReservationStatus,
    SequenceGapError,
    TornAppendError,
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

    def append(self, event: ObservationRecorded, *, body: bytes) -> CommittedEvent:
        return self._core.append(event, body=body)

    def reserve(
        self,
        event: ObservationRecorded,
        *,
        blob_digest: str,
        blob_bytes: int,
    ) -> EventReservation:
        return self._core.reserve(event, blob_digest=blob_digest, blob_bytes=blob_bytes)

    def commit(
        self,
        reservation: EventReservation,
        event: ObservationRecorded,
        *,
        body: bytes,
    ) -> CommittedEvent:
        return self._core.commit(reservation, event, body=body)

    def events(self) -> list[CommittedEvent]:
        return self._core.events()

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
    "CANONICAL_SCHEMA_VERSION",
    "CommittedEvent",
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
    "MissingBlobError",
    "OBSERVATION_RECORDED",
    "ObservationRecorded",
    "PreviousDigestMismatchError",
    "ProjectionCheck",
    "ProjectionMismatchError",
    "projection_fields",
    "ReservationStatus",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "RESERVATIONS_FILENAME",
    "SEALED_DIRECTORY",
    "SequenceGapError",
    "TornAppendError",
    "UnknownSchemaError",
]
