"""Compatibility projection and independently verifiable store receipts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from solver.event_store_contracts import (
    CANONICAL_SCHEMA_VERSION,
    RECEIPT_FILENAME,
    RECEIPT_TYPE,
    CommittedEvent,
    EventReservation,
    EventStoreReceipt,
    InvalidReceiptError,
    ProjectionCheck,
    ProjectionMismatchError,
    ReservationStatus,
)
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes


def projection_fields(event: CommittedEvent, reference: str) -> dict[str, Any]:
    payload = event.payload
    return {
        "attempt_id": payload.get("attempt_id"),
        "step_index": payload.get("step_index"),
        "command_raw": payload.get("command_raw"),
        "command_normalised": payload.get("command_normalised"),
        "tool": payload.get("tool"),
        "source": payload.get("source"),
        "exit_code": payload.get("exit_code"),
        "duration_ms": payload.get("duration_ms"),
        "observation_digest": event.blob_digest,
        "observation_bytes": event.blob_bytes,
        "observation_ref": reference,
        "checkpoint": payload.get("checkpoint"),
        "model": payload.get("model"),
        "tokens_in": payload.get("tokens_in"),
        "tokens_out": payload.get("tokens_out"),
        "cache_read": payload.get("cache_read"),
        "cache_write": payload.get("cache_write"),
        "usage_known": payload.get("usage_known"),
    }


class CanonicalStoreView(Protocol):
    """The public core seam required to verify a projection or receipt."""

    run_id: str

    def events(self) -> list[CommittedEvent]: ...

    def blob(self, digest: str) -> bytes: ...

    def reservation(self, sequence: int) -> EventReservation | None: ...


class ProjectionReceiptService:
    """Derive compatibility rows and receipts from an explicit canonical view."""

    def __init__(self, core: CanonicalStoreView, run_dir: Path, canonical_dir: Path) -> None:
        self._core = core
        self._run_dir = Path(run_dir)
        self._canonical_dir = Path(canonical_dir)

    def projection(
        self,
        event: CommittedEvent,
        *,
        projection_path: Path | None = None,
    ) -> ProjectionCheck:
        path = Path(projection_path) if projection_path is not None else self._run_dir / "stream.jsonl"
        if not path.exists():
            return ProjectionCheck(row=None, reference=None, digest=None)
        try:
            lines = path.read_text().splitlines()
        except (OSError, UnicodeDecodeError) as error:
            raise ProjectionMismatchError("v1 projection cannot be read", sequence=event.sequence) from error
        rows: list[dict[str, Any]] = []
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
        identity = (event.payload.get("attempt_id"), event.payload.get("step_index"))
        target_begin = _begin_sequence(event)
        candidates: list[dict[str, Any]] = []
        for row in rows:
            if row.get("record") != "step-end":
                continue
            if (row.get("attempt_id"), row.get("step_index")) != identity:
                continue
            if target_begin is not None:
                if isinstance(row.get("seq"), int) and row["seq"] > target_begin:
                    candidates.append(row)
            elif (
                row.get("observation_digest") == event.blob_digest and row.get("observation_bytes") == event.blob_bytes
            ):
                candidates.append(row)
        if target_begin is not None and candidates:
            candidates = [min(candidates, key=lambda row: row["seq"])]

        matches: list[tuple[dict[str, Any], str]] = []
        for row in candidates:
            reference = row.get("observation_ref")
            if not isinstance(reference, str) or not _relative_reference(reference):
                raise ProjectionMismatchError("v1 observation reference is invalid", sequence=event.sequence)
            expected = projection_fields(event, reference)
            if any(row.get(key) != value for key, value in expected.items() if key in row):
                raise ProjectionMismatchError("v1 row disagrees with canonical event", sequence=event.sequence)
            body_path = self._run_dir / reference
            try:
                body = body_path.read_bytes()
            except OSError as error:
                raise ProjectionMismatchError("v1 projection body is missing", sequence=event.sequence) from error
            if body != event.body:
                raise ProjectionMismatchError(
                    "v1 projection body disagrees with canonical blob", sequence=event.sequence
                )
            matches.append((row, reference))
        if len(matches) > 1:
            raise ProjectionMismatchError("canonical event has duplicate v1 projections", sequence=event.sequence)
        if not matches:
            return ProjectionCheck(row=None, reference=None, digest=None)
        row, reference = matches[0]
        return ProjectionCheck(row=row, reference=reference, digest=digest_bytes(canonical_bytes(row)))

    def write_receipt(
        self,
        *,
        event_sequence: int | None = None,
        projection_path: Path | None = None,
        destination: Path | None = None,
    ) -> Path:
        receipt = self._receipt(event_sequence=event_sequence, projection_path=projection_path)
        path = destination or self._canonical_dir / RECEIPT_FILENAME
        atomic_write(path, canonical_bytes(receipt.as_dict()) + b"\n")
        return path

    def verify_receipt(self, path: Path, *, projection_path: Path | None = None) -> EventStoreReceipt:
        try:
            supplied = json.loads(Path(path).read_text())
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidReceiptError("receipt cannot be read as JSON") from error
        if not isinstance(supplied, dict):
            raise InvalidReceiptError("receipt is not an object")
        sequence = supplied.get("event_sequence")
        if not isinstance(sequence, int):
            raise InvalidReceiptError("receipt event sequence is not an integer")
        expected = self._receipt(event_sequence=sequence, projection_path=projection_path)
        if supplied != expected.as_dict():
            raise InvalidReceiptError("receipt does not match verified canonical state", sequence=sequence)
        return expected

    def _receipt(
        self,
        *,
        event_sequence: int | None,
        projection_path: Path | None,
    ) -> EventStoreReceipt:
        events = self._core.events()
        if not events:
            raise InvalidReceiptError("cannot prove an empty canonical store")
        if event_sequence is None:
            event = events[-1]
        else:
            try:
                event = next(item for item in events if item.sequence == event_sequence)
            except StopIteration as error:
                raise InvalidReceiptError(
                    f"canonical event sequence {event_sequence} is unavailable",
                    sequence=event_sequence,
                ) from error
        projection = self.projection(event, projection_path=projection_path)
        reservation = self._core.reservation(event.sequence)
        reservation_status = reservation.status if reservation is not None else None
        crash_point = (
            "after_append"
            if reservation_status in {ReservationStatus.RESERVED, ReservationStatus.RECOVERED}
            else "none"
        )
        if projection.row is None and crash_point == "none":
            crash_point = "between_canonical_and_projection"
        return EventStoreReceipt(
            schema_version=CANONICAL_SCHEMA_VERSION,
            receipt_type=RECEIPT_TYPE,
            run_id=self._core.run_id,
            receipt_id=digest_bytes(f"{self._core.run_id}:{RECEIPT_TYPE}:{event.event_digest}".encode()),
            event_sequence=event.sequence,
            event_chain_head=events[-1].event_digest,
            sealed_blob_digest=event.blob_digest,
            sealed_blob_bytes=event.blob_bytes,
            projection_ref=projection.reference,
            projection_digest=projection.digest,
            crash_point=crash_point,
            compatibility_projection=projection.row is not None,
            reservation_status=reservation_status,
        )


def _begin_sequence(event: CommittedEvent) -> int | None:
    event_id = event.payload.get("event_id")
    if not isinstance(event_id, str):
        return None
    try:
        return int(event_id.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def _relative_reference(reference: str) -> bool:
    path = Path(reference)
    return not path.is_absolute() and ".." not in path.parts
