"""Reserve-first, restart-idempotent publication of complete Order decisions."""

from __future__ import annotations

import base64
import contextlib
import fcntl
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solver.event_store import CommittedEvent, EventStore, ReservationStatus
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.order_contracts import ORDER_PUBLICATION_RECORDED, OrderPublicationRecord, OrderPublicationRecorded
from solver.order_policy import OrderDecision
from solver.redaction import Redactor

ROWS_PER_CHUNK = 256
MAX_CHUNK_BYTES = 1024 * 1024
MAX_BOUNDARY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PublishedOrder:
    publication_id: str
    decision_digest: str
    chunk_count: int
    events: tuple[CommittedEvent, ...]


@dataclass(frozen=True)
class ReplayedOrderPublication:
    publication_id: str
    decision_digest: str
    rows: tuple[dict[str, Any], ...]
    events: tuple[CommittedEvent, ...]
    boundary: dict[str, Any]


class OrderJournal:
    """The only writer of Order boundary and grant publications."""

    def __init__(
        self,
        state: Path,
        run_id: str,
        redactor: Redactor,
        *,
        timestamp: Callable[[], str],
        publication_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.store = EventStore(state, run_id=run_id, redactor=redactor)
        self._redactor = redactor
        self._timestamp = timestamp
        self._hook = publication_hook or (lambda _point: None)
        self._pending_path = self.store.canonical_dir / "order-publication.pending.json"
        self._lock_path = self.store.canonical_dir / "order-publication.lock"

    def resume_pending(self) -> PublishedOrder | None:
        """Finish an already-reserved publication from its bounded sanitized staging record."""

        with self._locked():
            return self._resume_pending()

    def _resume_pending(self) -> PublishedOrder | None:

        if not self._pending_path.exists():
            return None
        try:
            document = json.loads(self._pending_path.read_bytes())
            entries = tuple(_pending_entry(item) for item in document["entries"])
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("pending Order publication is unreadable") from error
        reservation_order = (entries[-1], *entries[:-1])
        base_sequence = self.store.events()[-1].sequence if self.store.events() else 0
        desired = {event.identity: base_sequence + index for index, (event, _body) in enumerate(entries, start=1)}
        reservations = {
            event.identity: self.store.reserve(
                event,
                blob_digest=digest_bytes(body),
                blob_bytes=len(body),
                sequence=desired[event.identity],
            )
            for event, body in reservation_order
        }
        committed = []
        for event, body in entries:
            committed.append(self.store.commit(reservations[event.identity], event, body=body))
        self._pending_path.unlink()
        boundary = entries[-1][0]
        return PublishedOrder(boundary.publication_id, boundary.decision_digest, len(entries) - 1, tuple(committed))

    def publish(self, decision: OrderDecision) -> PublishedOrder:
        with self._locked():
            return self._publish(decision)

    def _publish(self, decision: OrderDecision) -> PublishedOrder:
        decision_document = json.loads(self._redactor.redact(canonical_bytes(decision.document())))
        decision_digest = digest_bytes(canonical_bytes(decision_document))
        resumed = self._resume_pending()
        if resumed is not None and resumed.decision_digest == decision_digest:
            return resumed
        expected_fence = decision_document["input_document"]["run"]["prior_order_fence"]
        try:
            current = replay_order_publication(Path(self.store.run_dir).parents[1], self.store.run_id)
            actual_fence = {
                "publication_id": current.publication_id,
                "decision_digest": current.decision_digest,
                "boundary_event_digest": current.events[-1].event_digest,
            }
            if current.decision_digest == decision_digest:
                return PublishedOrder(
                    current.publication_id, current.decision_digest, len(current.events) - 1, current.events
                )
        except LookupError:
            actual_fence = {"publication_id": "", "decision_digest": "", "boundary_event_digest": ""}
        if expected_fence != actual_fence:
            self._record_fence_conflict(decision_document, decision_digest, expected_fence, actual_fence)
            raise ValueError("Order publication conflicts with the current complete boundary fence")
        entries = self._entries(decision, decision_document, decision_digest)
        expected = {
            digest_bytes(canonical_bytes(event.payload(blob_digest=digest_bytes(body), blob_bytes=len(body))))
            for event, body in entries
        }
        latest_reservations = {item.reservation_id: item for item in self.store.reservations()}
        pending = {
            item.event_fingerprint
            for item in latest_reservations.values()
            if item.event_type == ORDER_PUBLICATION_RECORDED and item.status is ReservationStatus.RESERVED
        }
        if pending - expected:
            raise ValueError("pending Order publication must finish before a newer boundary")
        self._hook("before_pending_write")
        _write_pending(self._pending_path, entries)
        self._hook("after_pending_write")
        reservations = {}
        reservation_order = (entries[-1], *entries[:-1])
        existing = self.store.events()
        base_sequence = existing[-1].sequence if existing else 0
        desired = {event.identity: base_sequence + index for index, (event, _body) in enumerate(entries, start=1)}
        for index, (event, body) in enumerate(reservation_order):
            self._hook(f"before_reserve:{index}")
            reservations[event.identity] = self.store.reserve(
                event,
                blob_digest=digest_bytes(body),
                blob_bytes=len(body),
                sequence=desired[event.identity],
            )
            self._hook(f"after_reserve:{index}")
        self._hook("after_reservations")
        self._hook("before_first_commit")
        committed = []
        for index, (event, body) in enumerate(entries):
            committed.append(self.store.commit(reservations[event.identity], event, body=body))
            self._hook(
                "after_boundary_commit"
                if event.record is OrderPublicationRecord.BOUNDARY
                else f"after_chunk_commit:{index + 1}"
            )
        self._pending_path.unlink()
        return PublishedOrder(entries[-1][0].publication_id, decision_digest, len(entries) - 1, tuple(committed))

    def _record_fence_conflict(self, decision, decision_digest, expected, observed) -> None:
        publication_id = f"order-publication:{decision['boundary_id']}:{decision_digest}"
        body = canonical_bytes(
            {"schema_version": 1, "reason": "prior-order-fence-changed", "expected": expected, "observed": observed}
        )
        event = OrderPublicationRecorded(
            publication_id=publication_id,
            boundary_id=str(decision["boundary_id"]),
            record=OrderPublicationRecord.FENCE_CONFLICT,
            decision_digest=decision_digest,
            snapshot_digest=str(decision["snapshot_digest"]),
            policy_digest=str(decision["policy_digest"]),
            intake_event_digest=str(decision["intake_fence"]["event_digest"]),
            chunk_count=1,
            chunk_index=0,
            ts=self._timestamp(),
        )
        self.store.append(event, body=body)

    @contextlib.contextmanager
    def _locked(self):
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock_path.open("a+b") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def _entries(
        self, decision: OrderDecision, decision_document: dict[str, Any], decision_digest: str
    ) -> tuple[tuple[OrderPublicationRecorded, bytes], ...]:
        publication_id = f"order-publication:{decision.boundary_id}:{decision_digest}"
        rows = tuple(decision_document["rows"])
        chunks = tuple(rows[index : index + ROWS_PER_CHUNK] for index in range(0, len(rows), ROWS_PER_CHUNK)) or ((),)
        chunk_bodies = tuple(
            canonical_bytes(
                {
                    "schema_version": 1,
                    "publication_id": publication_id,
                    "decision_digest": decision_digest,
                    "chunk_index": index,
                    "rows": list(chunk),
                }
            )
            for index, chunk in enumerate(chunks, start=1)
        )
        if any(len(body) > MAX_CHUNK_BYTES for body in chunk_bodies):
            raise ValueError("Order decision chunk exceeds one MiB")
        grant = decision_document["grant"]
        common = {
            "publication_id": publication_id,
            "boundary_id": decision.boundary_id,
            "decision_digest": decision_digest,
            "snapshot_digest": decision.snapshot_digest,
            "policy_digest": decision.policy_digest,
            "intake_event_digest": decision.intake_fence.event_digest,
            "chunk_count": len(chunks),
            "ts": self._timestamp(),
        }
        boundary_body = canonical_bytes(
            {
                "schema_version": 1,
                "publication_id": publication_id,
                "decision": {name: value for name, value in decision_document.items() if name != "rows"},
                "row_chunk_digests": [digest_bytes(body) for body in chunk_bodies],
            }
        )
        if len(boundary_body) > MAX_BOUNDARY_BYTES:
            raise ValueError("Order terminal boundary exceeds one MiB")
        boundary = OrderPublicationRecorded(
            **common,
            record=OrderPublicationRecord.BOUNDARY,
            chunk_index=0,
            grant_attempt_id=str(grant["attempt_id"]) if grant else "",
            grant_generation_id=str(grant["generation_id"]) if grant else "",
        )
        chunk_events = tuple(
            OrderPublicationRecorded(**common, record=OrderPublicationRecord.CHUNK, chunk_index=index)
            for index in range(1, len(chunks) + 1)
        )
        # Chunks carry no scheduling authority.  The boundary/grant is the terminal commit.
        return (*tuple(zip(chunk_events, chunk_bodies)), (boundary, boundary_body))


def replay_order_publication(state: Path, run_id: str, publication_id: str | None = None) -> ReplayedOrderPublication:
    events = [
        event for event in EventStore(state, run_id=run_id).events() if event.event_type == ORDER_PUBLICATION_RECORDED
    ]
    boundaries = [event for event in events if event.payload["record"] == OrderPublicationRecord.BOUNDARY.value]
    complete = []
    for boundary in boundaries:
        candidate_id = str(boundary.payload["publication_id"])
        chunks = [
            event
            for event in events
            if event.payload["record"] == "chunk" and event.payload["publication_id"] == candidate_id
        ]
        chunks.sort(key=lambda event: int(event.payload["chunk_index"]))
        count = int(boundary.payload["chunk_count"])
        if [event.payload["chunk_index"] for event in chunks] != list(range(1, count + 1)):
            continue
        body = json.loads(boundary.body)
        if body.get("row_chunk_digests") != [event.blob_digest for event in chunks]:
            raise ValueError("Order publication chunk closure differs from sealed bodies")
        rows = []
        for event in chunks:
            document = json.loads(event.body)
            if (
                document.get("publication_id") != candidate_id
                or document.get("decision_digest") != boundary.payload["decision_digest"]
            ):
                raise ValueError("Order publication chunk belongs to another decision")
            rows.extend(document.get("rows", []))
        complete.append((boundary, chunks, body, tuple(rows)))
    if not complete:
        raise LookupError("no complete Order publication exists")
    if publication_id is not None:
        complete = [item for item in complete if item[0].payload["publication_id"] == publication_id]
        if len(complete) != 1:
            raise LookupError("named complete Order publication does not exist")
    boundary, chunks, body, rows = complete[-1]
    return ReplayedOrderPublication(
        str(boundary.payload["publication_id"]),
        str(boundary.payload["decision_digest"]),
        rows,
        (*chunks, boundary),
        body,
    )


def _write_pending(path: Path, entries: tuple[tuple[OrderPublicationRecorded, bytes], ...]) -> None:
    document = {
        "schema_version": 1,
        "entries": [
            {
                "publication_id": event.publication_id,
                "boundary_id": event.boundary_id,
                "record": event.record.value,
                "decision_digest": event.decision_digest,
                "snapshot_digest": event.snapshot_digest,
                "policy_digest": event.policy_digest,
                "intake_event_digest": event.intake_event_digest,
                "chunk_count": event.chunk_count,
                "chunk_index": event.chunk_index,
                "grant_attempt_id": event.grant_attempt_id,
                "grant_generation_id": event.grant_generation_id,
                "ts": event.ts,
                "body": base64.b64encode(body).decode("ascii"),
            }
            for event, body in entries
        ],
    }
    atomic_write(path, canonical_bytes(document) + b"\n")


def _pending_entry(item: dict[str, object]) -> tuple[OrderPublicationRecorded, bytes]:
    event = OrderPublicationRecorded(
        publication_id=str(item["publication_id"]),
        boundary_id=str(item["boundary_id"]),
        record=OrderPublicationRecord(str(item["record"])),
        decision_digest=str(item["decision_digest"]),
        snapshot_digest=str(item["snapshot_digest"]),
        policy_digest=str(item["policy_digest"]),
        intake_event_digest=str(item["intake_event_digest"]),
        chunk_count=int(item["chunk_count"]),
        chunk_index=int(item["chunk_index"]),
        grant_attempt_id=str(item["grant_attempt_id"]),
        grant_generation_id=str(item["grant_generation_id"]),
        ts=str(item["ts"]),
    )
    body = base64.b64decode(str(item["body"]), validate=True)
    return event, body


__all__ = ["OrderJournal", "PublishedOrder", "ReplayedOrderPublication", "replay_order_publication"]
