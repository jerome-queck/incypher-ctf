"""Canonical sequencing, reservations, sealed bodies, and chain verification."""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Mapping

from solver.event_store_contracts import (
    CANONICAL_SCHEMA_VERSION,
    CanonicalEvent,
    EVENTS_DIRECTORY,
    EVENTS_FILENAME,
    RESERVATIONS_FILENAME,
    SEALED_DIRECTORY,
    BlobDigestMismatchError,
    CommittedEvent,
    DuplicateSequenceError,
    EventReservation,
    EventStoreDamage,
    EventDigestMismatchError,
    InvalidEventError,
    MissingBlobError,
    PreviousDigestMismatchError,
    SequenceGapError,
    TornAppendError,
    UnknownSchemaError,
    ReservationStatus,
    event_contract,
)
from solver.event_store_storage import append_durable, atomic_write, canonical_bytes, digest_bytes
from solver.redaction import Redactor


def _event_fingerprint(payload: Mapping[str, Any]) -> str:
    return digest_bytes(canonical_bytes(payload))


def _redact(value: Any, redactor: Redactor) -> Any:
    if isinstance(value, str):
        return redactor.redact(value).decode("utf-8", "replace")
    if isinstance(value, dict):
        return {key: _redact(item, redactor) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, redactor) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, redactor) for item in value]
    return value


class EventStoreCore:
    """The locked canonical writer and verifier used by the public EventStore facade."""

    def __init__(
        self,
        root: Path,
        run_id: str | None = None,
        *,
        redactor: Redactor | None = None,
        append_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.run_dir = Path(root) / "runs" / run_id if run_id is not None else Path(root)
        self.run_id = run_id or self.run_dir.name
        self.canonical_dir = self.run_dir / EVENTS_DIRECTORY
        self.events_path = self.canonical_dir / EVENTS_FILENAME
        self.reservations_path = self.canonical_dir / RESERVATIONS_FILENAME
        self.lock_path = self.canonical_dir / "events.lock"
        self.sealed_dir = self.run_dir / SEALED_DIRECTORY
        self._redactor = redactor or Redactor({})
        self._append_hook = append_hook
        self.canonical_dir.mkdir(parents=True, exist_ok=True)
        self.sealed_dir.mkdir(parents=True, exist_ok=True)
        self.lock_path.touch(exist_ok=True, mode=0o600)

    def append(self, event: CanonicalEvent, *, body: bytes) -> CommittedEvent:
        body_bytes = self._redactor.redact(body)
        payload = _redact(
            event.payload(blob_digest=digest_bytes(body_bytes), blob_bytes=len(body_bytes)),
            self._redactor,
        )
        with self._locked():
            existing = self._read_verified()
            prior = self._find_identity(existing, event, payload)
            if prior is not None:
                self._recover_reservation_locked(prior)
                return prior
            reservation = self._find_reservation_for_payload(event, payload)
            if reservation is None:
                reservation = self._reserve_locked(event, payload, existing)
            return self._commit_locked(reservation, event, payload, body_bytes, existing)

    def reserve(
        self,
        event: CanonicalEvent,
        *,
        blob_digest: str,
        blob_bytes: int,
    ) -> EventReservation:
        if len(blob_digest) != 64 or any(character not in "0123456789abcdef" for character in blob_digest):
            raise ValueError("blob_digest must be a lowercase SHA-256 hex digest")
        if blob_bytes < 0:
            raise ValueError("blob_bytes must be non-negative")
        payload = _redact(event.payload(blob_digest=blob_digest, blob_bytes=blob_bytes), self._redactor)
        with self._locked():
            existing = self._read_verified()
            prior = self._find_identity(existing, event, payload)
            if prior is not None:
                self._recover_reservation_locked(prior)
                return self._reservation_from_event(prior)
            prior_reservation = self._find_reservation_for_payload(event, payload)
            if prior_reservation is not None:
                return prior_reservation
            return self._reserve_locked(event, payload, existing)

    def commit(
        self,
        reservation: EventReservation,
        event: CanonicalEvent,
        *,
        body: bytes,
    ) -> CommittedEvent:
        body_bytes = self._redactor.redact(body)
        payload = _redact(
            event.payload(blob_digest=digest_bytes(body_bytes), blob_bytes=len(body_bytes)),
            self._redactor,
        )
        with self._locked():
            existing = self._read_verified()
            prior = self._find_identity(existing, event, payload)
            if prior is not None:
                self._recover_reservation_locked(prior)
                return prior
            return self._commit_locked(reservation, event, payload, body_bytes, existing)

    def events(self) -> list[CommittedEvent]:
        return self._read_verified()

    def reservations(self) -> list[EventReservation]:
        return self._read_reservations()

    def blob(self, digest: str) -> bytes:
        path = self.sealed_dir / digest
        try:
            body = path.read_bytes()
        except OSError as error:
            raise MissingBlobError(f"sealed blob {digest!r} is unavailable") from error
        if digest_bytes(body) != digest:
            raise BlobDigestMismatchError(f"sealed blob {digest!r} has different content")
        return body

    def reservation(self, sequence: int) -> EventReservation | None:
        """Return the latest durable status for one canonical sequence."""
        return self._reservation_for_sequence(sequence)

    def _commit_locked(
        self,
        reservation: EventReservation,
        event: CanonicalEvent,
        payload: Mapping[str, Any],
        body: bytes,
        existing: list[CommittedEvent],
    ) -> CommittedEvent:
        expected_sequence = existing[-1].sequence + 1 if existing else 1
        previous_digest = existing[-1].event_digest if existing else ""
        if reservation.status != ReservationStatus.RESERVED:
            raise InvalidEventError("reservation is already closed", sequence=reservation.sequence)
        if reservation.sequence != expected_sequence:
            raise SequenceGapError(
                f"reservation {reservation.sequence} cannot commit before sequence {expected_sequence}",
                sequence=reservation.sequence,
            )
        if reservation.run_id != self.run_id or reservation.event_type != event.event_type:
            raise InvalidEventError("reservation belongs to another event store", sequence=reservation.sequence)
        if reservation.event_fingerprint != _event_fingerprint(payload):
            raise InvalidEventError("reservation payload differs from proposed event", sequence=reservation.sequence)
        if reservation.blob_digest != payload["blob_digest"] or reservation.blob_bytes != payload["blob_bytes"]:
            raise BlobDigestMismatchError("reservation blob differs from proposed body", sequence=reservation.sequence)
        base = {
            "schema_version": CANONICAL_SCHEMA_VERSION,
            "seq": reservation.sequence,
            "prev_digest": previous_digest,
            "event_type": event.event_type,
            "run_id": self.run_id,
            "payload": dict(payload),
        }
        event_digest = digest_bytes(canonical_bytes(base))
        envelope = {**base, "event_digest": event_digest}
        self._seal_blob(reservation.blob_digest, body)
        self._call_hook("before_append")
        append_durable(self.events_path, canonical_bytes(envelope) + b"\n")
        self._call_hook("after_append")
        self._record_reservation(reservation, ReservationStatus.COMMITTED)
        return CommittedEvent(
            sequence=reservation.sequence,
            previous_digest=previous_digest,
            event_digest=event_digest,
            event_type=event.event_type,
            payload=payload,
            envelope=envelope,
            blob_digest=reservation.blob_digest,
            blob_bytes=reservation.blob_bytes,
            body=body,
        )

    def _call_hook(self, phase: str) -> None:
        if self._append_hook is not None:
            self._append_hook(phase)

    def _reserve_locked(
        self,
        event: CanonicalEvent,
        payload: Mapping[str, Any],
        existing: list[CommittedEvent],
    ) -> EventReservation:
        contract = event_contract(event.event_type)
        if contract is None:
            raise InvalidEventError(f"unsupported event type {event.event_type!r}")
        contract.validate_payload(payload, sequence=len(existing) + 1)
        reservations = self._read_reservations()
        highest = max(
            [item.sequence for item in existing] + [item.sequence for item in reservations],
            default=0,
        )
        reservation = EventReservation(
            sequence=highest + 1,
            previous_digest=existing[-1].event_digest if existing else "",
            run_id=self.run_id,
            event_type=event.event_type,
            event_fingerprint=_event_fingerprint(payload),
            blob_digest=str(payload["blob_digest"]),
            blob_bytes=int(payload["blob_bytes"]),
            reservation_id=f"{self.run_id}:{highest + 1}:{event.event_type}",
        )
        self._record_reservation(reservation)
        self._call_hook("after_reserve")
        return reservation

    def _find_reservation_for_payload(
        self,
        event: CanonicalEvent,
        payload: Mapping[str, Any],
    ) -> EventReservation | None:
        candidate: EventReservation | None = None
        fingerprint = _event_fingerprint(payload)
        for reservation in self._read_reservations():
            if (
                reservation.status == ReservationStatus.RESERVED
                and reservation.event_type == event.event_type
                and reservation.run_id == self.run_id
                and reservation.event_fingerprint == fingerprint
                and reservation.blob_digest == payload.get("blob_digest")
                and reservation.blob_bytes == payload.get("blob_bytes")
            ):
                if candidate is not None:
                    raise DuplicateSequenceError("multiple reservations match one Observation")
                candidate = reservation
        return candidate

    def _record_reservation(self, reservation: EventReservation, status: ReservationStatus | None = None) -> None:
        append_durable(
            self.reservations_path,
            canonical_bytes(replace(reservation, status=status or reservation.status).as_dict()) + b"\n",
        )

    def _recover_reservation_locked(self, event: CommittedEvent) -> None:
        for reservation in reversed(self._read_reservations()):
            if reservation.sequence == event.sequence and reservation.status == ReservationStatus.RESERVED:
                self._record_reservation(reservation, ReservationStatus.RECOVERED)
                return

    def _read_reservations(self) -> list[EventReservation]:
        if not self.reservations_path.exists():
            return []
        try:
            raw = self.reservations_path.read_bytes()
        except OSError as error:
            raise EventStoreDamage("reservation stream cannot be read") from error
        if raw and not raw.endswith(b"\n"):
            raise TornAppendError("reservation stream has an incomplete final line")
        reservations: list[EventReservation] = []
        latest_sequence = 0
        latest_by_id: dict[str, EventReservation] = {}
        for line_number, line in enumerate(raw.splitlines(), start=1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise InvalidEventError("reservation stream contains invalid JSON", sequence=line_number) from error
            if not isinstance(row, dict):
                raise InvalidEventError("reservation is not an object", sequence=line_number)
            if row.get("schema_version") != CANONICAL_SCHEMA_VERSION:
                raise UnknownSchemaError("reservation schema version is unsupported", sequence=row.get("sequence"))
            required = (
                "reservation_id",
                "sequence",
                "previous_digest",
                "run_id",
                "event_type",
                "event_fingerprint",
                "blob_digest",
                "blob_bytes",
                "status",
            )
            if any(key not in row for key in required):
                raise InvalidEventError("reservation is missing required fields", sequence=row.get("sequence"))
            if not isinstance(row["sequence"], int) or not isinstance(row["blob_bytes"], int):
                raise InvalidEventError("reservation sequence or body size is invalid", sequence=row.get("sequence"))
            try:
                status = ReservationStatus(row["status"])
            except (TypeError, ValueError) as error:
                raise InvalidEventError("reservation status is unsupported", sequence=row.get("sequence")) from error
            reservation = EventReservation(
                sequence=row["sequence"],
                previous_digest=str(row["previous_digest"]),
                run_id=str(row["run_id"]),
                event_type=str(row["event_type"]),
                event_fingerprint=str(row["event_fingerprint"]),
                blob_digest=str(row["blob_digest"]),
                blob_bytes=row["blob_bytes"],
                reservation_id=str(row["reservation_id"]),
                status=status,
            )
            if reservation.sequence < 1:
                raise InvalidEventError("reservation sequence is not positive", sequence=reservation.sequence)
            previous = latest_by_id.get(reservation.reservation_id)
            if previous is None:
                if reservation.sequence < latest_sequence:
                    raise DuplicateSequenceError("reservation sequence was reused", sequence=reservation.sequence)
                if reservation.sequence > latest_sequence + 1:
                    raise SequenceGapError("reservation sequence has a gap", sequence=reservation.sequence)
            elif (
                previous.sequence != reservation.sequence
                or previous.run_id != reservation.run_id
                or previous.event_type != reservation.event_type
                or previous.event_fingerprint != reservation.event_fingerprint
                or previous.blob_digest != reservation.blob_digest
                or previous.blob_bytes != reservation.blob_bytes
                or previous.status != ReservationStatus.RESERVED
                or reservation.status not in {ReservationStatus.COMMITTED, ReservationStatus.RECOVERED}
            ):
                raise DuplicateSequenceError("reservation id was reused", sequence=reservation.sequence)
            if reservation.run_id != self.run_id:
                raise InvalidEventError("reservation belongs to another Run", sequence=reservation.sequence)
            reservations.append(reservation)
            latest_by_id[reservation.reservation_id] = reservation
            latest_sequence = max(latest_sequence, reservation.sequence)
        return reservations

    def _reservation_for_sequence(self, sequence: int) -> EventReservation | None:
        matches = [item for item in self._read_reservations() if item.sequence == sequence]
        return matches[-1] if matches else None

    def _reservation_from_event(self, event: CommittedEvent) -> EventReservation:
        reservation = self._reservation_for_sequence(event.sequence)
        if reservation is not None:
            return reservation
        return EventReservation(
            sequence=event.sequence,
            previous_digest=event.previous_digest,
            run_id=self.run_id,
            event_type=event.event_type,
            event_fingerprint=_event_fingerprint(event.payload),
            blob_digest=event.blob_digest,
            blob_bytes=event.blob_bytes,
            reservation_id=f"{self.run_id}:{event.sequence}:{event.event_type}",
            status=ReservationStatus.COMMITTED,
        )

    def _find_identity(
        self,
        existing: list[CommittedEvent],
        event: CanonicalEvent,
        payload: Mapping[str, Any],
    ) -> CommittedEvent | None:
        for prior in existing:
            contract = event_contract(prior.event_type)
            identity = contract.identity_from_payload(prior.payload) if contract is not None else None
            if prior.event_type != event.event_type or identity != event.identity:
                continue
            if getattr(event, "event_id", None) is None:
                if dict(prior.payload) == dict(payload):
                    return prior
                continue
            if prior.payload.get("event_id") != event.event_id:
                continue
            exact_payload_required = getattr(contract, "exact_payload_identity", False)
            if prior.blob_digest == payload.get("blob_digest") and (
                not exact_payload_required or dict(prior.payload) == dict(payload)
            ):
                return prior
            raise DuplicateSequenceError(
                f"Canonical identity {event.identity!r} was already committed with different content",
                sequence=prior.sequence,
            )
        return None

    def _seal_blob(self, digest: str, body: bytes) -> None:
        path = self.sealed_dir / digest
        if path.exists():
            try:
                existing = path.read_bytes()
            except OSError as error:
                raise MissingBlobError(f"sealed blob {digest!r} cannot be read") from error
            if existing != body or digest_bytes(existing) != digest:
                raise BlobDigestMismatchError(f"sealed blob {digest!r} has different content")
            return
        atomic_write(path, body)

    @contextmanager
    def _locked(self):
        with self.lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            yield

    def _read_verified(self) -> list[CommittedEvent]:
        self._read_reservations()
        if not self.events_path.exists():
            return []
        try:
            raw = self.events_path.read_bytes()
        except OSError as error:
            raise EventStoreDamage("canonical stream cannot be read") from error
        if raw and not raw.endswith(b"\n"):
            raise TornAppendError("canonical stream has an incomplete final line")
        events: list[CommittedEvent] = []
        expected_sequence = 1
        previous_digest = ""
        lines = raw.splitlines()
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                raise TornAppendError("canonical stream contains an empty line", sequence=expected_sequence)
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError as error:
                error_type = TornAppendError if line_number == len(lines) else InvalidEventError
                raise error_type("canonical stream contains invalid JSON", sequence=expected_sequence) from error
            if not isinstance(envelope, dict):
                raise InvalidEventError("canonical envelope is not an object", sequence=expected_sequence)
            sequence = envelope.get("seq")
            if not isinstance(sequence, int):
                raise InvalidEventError("canonical envelope has no integer seq", sequence=expected_sequence)
            schema = envelope.get("schema_version")
            if schema != CANONICAL_SCHEMA_VERSION:
                raise UnknownSchemaError(f"schema version {schema!r} is unsupported", sequence=sequence)
            if sequence < expected_sequence:
                raise DuplicateSequenceError("canonical sequence was reused", sequence=sequence)
            if sequence > expected_sequence:
                raise SequenceGapError("canonical sequence has a gap", sequence=sequence)
            if envelope.get("prev_digest") != previous_digest:
                raise PreviousDigestMismatchError("predecessor digest does not match chain head", sequence=sequence)
            event_digest = envelope.get("event_digest")
            if not isinstance(event_digest, str):
                raise InvalidEventError("canonical envelope has no event digest", sequence=sequence)
            unsigned = {key: value for key, value in envelope.items() if key != "event_digest"}
            if digest_bytes(canonical_bytes(unsigned)) != event_digest:
                raise EventDigestMismatchError("event content does not match event digest", sequence=sequence)
            event_type = envelope.get("event_type")
            run_id = envelope.get("run_id")
            payload = envelope.get("payload")
            if not isinstance(event_type, str) or not isinstance(run_id, str) or not isinstance(payload, dict):
                raise InvalidEventError("canonical envelope is missing typed fields", sequence=sequence)
            if run_id != self.run_id:
                raise InvalidEventError("canonical envelope belongs to another Run", sequence=sequence)
            contract = event_contract(event_type)
            if contract is None:
                raise InvalidEventError(f"unsupported event type {event_type!r}", sequence=sequence)
            contract.validate_payload(payload, sequence=sequence)
            blob_digest = payload.get("blob_digest")
            blob_bytes = payload.get("blob_bytes")
            if not isinstance(blob_digest, str) or not isinstance(blob_bytes, int):
                raise InvalidEventError("canonical payload has no blob identity", sequence=sequence)
            events.append(
                CommittedEvent(
                    sequence=sequence,
                    previous_digest=envelope["prev_digest"],
                    event_digest=event_digest,
                    event_type=event_type,
                    payload=payload,
                    envelope=envelope,
                    blob_digest=blob_digest,
                    blob_bytes=blob_bytes,
                    body=b"",
                )
            )
            expected_sequence += 1
            previous_digest = event_digest
        return self._attach_verified_bodies(events)

    def _attach_verified_bodies(self, events: list[CommittedEvent]) -> list[CommittedEvent]:
        released = self._released_event_sequences(events)
        verified = []
        for event in events:
            try:
                body = self.blob(event.blob_digest)
            except MissingBlobError as error:
                if event.sequence not in released:
                    error.sequence = event.sequence
                    raise
                body = b""
            if body and len(body) != event.blob_bytes:
                raise BlobDigestMismatchError("sealed blob length differs from payload", sequence=event.sequence)
            if not body and event.blob_bytes and event.sequence not in released:
                raise BlobDigestMismatchError("sealed blob length differs from payload", sequence=event.sequence)
            verified.append(replace(event, body=body))
        return verified

    @staticmethod
    def _released_event_sequences(events: list[CommittedEvent]) -> set[int]:
        by_sequence = {event.sequence: event for event in events}
        released = set()
        for event in events:
            if event.event_type != "storage-governor.recorded" or event.payload.get("record") != "retirement-tombstone":
                continue
            digest = event.payload["target_digest"]
            length = event.payload["target_length"]
            path = event.payload["path"]
            if path != f"sealed/sha256/{digest}":
                raise InvalidEventError("retirement tombstone path does not match its digest", sequence=event.sequence)
            for sequence in event.payload["event_sequences"]:
                target = by_sequence.get(sequence)
                if (
                    target is None
                    or sequence >= event.sequence
                    or target.blob_digest != digest
                    or target.blob_bytes != length
                ):
                    raise InvalidEventError(
                        "retirement tombstone releases a different canonical reference", sequence=event.sequence
                    )
                released.add(sequence)
        return released
