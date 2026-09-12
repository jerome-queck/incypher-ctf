"""Canonical event contract for one complete Order publication."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from solver.event_store_contracts import InvalidEventError

ORDER_PUBLICATION_RECORDED = "order-publication.recorded"


class OrderPublicationRecord(str, enum.Enum):
    BOUNDARY = "boundary"
    CHUNK = "chunk"
    FENCE_CONFLICT = "fence-conflict"


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


@dataclass(frozen=True)
class OrderPublicationRecorded:
    publication_id: str
    boundary_id: str
    record: OrderPublicationRecord
    decision_digest: str
    snapshot_digest: str
    policy_digest: str
    intake_event_digest: str
    chunk_count: int
    chunk_index: int
    grant_attempt_id: str = ""
    grant_generation_id: str = ""
    ts: str = ""

    @property
    def event_type(self) -> str:
        return ORDER_PUBLICATION_RECORDED

    @property
    def identity(self) -> str:
        suffix = (
            "boundary"
            if self.record is OrderPublicationRecord.BOUNDARY
            else "fence-conflict"
            if self.record is OrderPublicationRecord.FENCE_CONFLICT
            else f"chunk:{self.chunk_index:06d}"
        )
        return f"{self.publication_id}:{suffix}"

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        suffix = (
            "boundary"
            if payload.get("record") == "boundary"
            else "fence-conflict"
            if payload.get("record") == "fence-conflict"
            else f"chunk:{int(payload.get('chunk_index', -1)):06d}"
        )
        return f"{payload.get('publication_id', '')}:{suffix}"

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "publication_id": self.publication_id,
            "boundary_id": self.boundary_id,
            "record": self.record.value,
            "decision_digest": self.decision_digest,
            "snapshot_digest": self.snapshot_digest,
            "policy_digest": self.policy_digest,
            "intake_event_digest": self.intake_event_digest,
            "chunk_count": self.chunk_count,
            "chunk_index": self.chunk_index,
            "grant_attempt_id": self.grant_attempt_id,
            "grant_generation_id": self.grant_generation_id,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        expected = {
            "publication_id",
            "boundary_id",
            "record",
            "decision_digest",
            "snapshot_digest",
            "policy_digest",
            "intake_event_digest",
            "chunk_count",
            "chunk_index",
            "grant_attempt_id",
            "grant_generation_id",
            "ts",
            "blob_digest",
            "blob_bytes",
        }
        if set(payload) != expected:
            raise InvalidEventError("Order publication shape is unsupported", sequence=sequence)
        text = expected - {"chunk_count", "chunk_index", "blob_bytes"}
        if any(not isinstance(payload[name], str) for name in text):
            raise InvalidEventError("Order publication text is invalid", sequence=sequence)
        if not payload["publication_id"] or not payload["boundary_id"] or not payload["ts"]:
            raise InvalidEventError("Order publication identity is absent", sequence=sequence)
        for name in ("decision_digest", "snapshot_digest", "policy_digest", "intake_event_digest", "blob_digest"):
            if not _digest(payload[name]):
                raise InvalidEventError(f"Order {name} is invalid", sequence=sequence)
        for name in ("chunk_count", "chunk_index", "blob_bytes"):
            value = payload[name]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise InvalidEventError("Order publication number is invalid", sequence=sequence)
        if payload["chunk_count"] < 1:
            raise InvalidEventError("Order publication has no chunks", sequence=sequence)
        record = payload["record"]
        if record == OrderPublicationRecord.BOUNDARY.value:
            if payload["chunk_index"] != 0:
                raise InvalidEventError("Order boundary has a chunk index", sequence=sequence)
            if bool(payload["grant_attempt_id"]) != bool(payload["grant_generation_id"]):
                raise InvalidEventError("Order boundary grant identity is incomplete", sequence=sequence)
        elif record == OrderPublicationRecord.CHUNK.value:
            if not 1 <= payload["chunk_index"] <= payload["chunk_count"]:
                raise InvalidEventError("Order chunk index is invalid", sequence=sequence)
            if payload["grant_attempt_id"] or payload["grant_generation_id"]:
                raise InvalidEventError("Order chunk carries grant authority", sequence=sequence)
        elif record == OrderPublicationRecord.FENCE_CONFLICT.value:
            if payload["chunk_index"] != 0 or payload["grant_attempt_id"] or payload["grant_generation_id"]:
                raise InvalidEventError("Order fence conflict carries scheduling authority", sequence=sequence)
        else:
            raise InvalidEventError("Order publication record is unsupported", sequence=sequence)


__all__ = ["ORDER_PUBLICATION_RECORDED", "OrderPublicationRecord", "OrderPublicationRecorded"]
