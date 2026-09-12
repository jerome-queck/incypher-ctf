"""Canonical facts supplied to Order by collaborators it does not own."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from solver.event_store_contracts import InvalidEventError

ORDER_INPUT_RECORDED = "order-input.recorded"


class OrderInputRecord(str, enum.Enum):
    DURABLE_TIERS = "durable-tiers"
    ADMISSION = "admission"
    BOUNDARY_CLOCK = "boundary-clock"
    BOUNDARY_FACTS = "boundary-facts"


@dataclass(frozen=True)
class OrderInputRecorded:
    event_id: str
    record: OrderInputRecord
    ts: str

    @property
    def event_type(self) -> str:
        return ORDER_INPUT_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "record": self.record.value,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        expected = {"event_id", "record", "ts", "blob_digest", "blob_bytes"}
        if set(payload) != expected:
            raise InvalidEventError("Order input fact shape is unsupported", sequence=sequence)
        if not all(isinstance(payload[name], str) and payload[name] for name in expected - {"blob_bytes"}):
            raise InvalidEventError("Order input fact text is invalid", sequence=sequence)
        if payload["record"] not in {item.value for item in OrderInputRecord}:
            raise InvalidEventError("Order input fact record is unsupported", sequence=sequence)
        digest = payload["blob_digest"]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise InvalidEventError("Order input fact digest is invalid", sequence=sequence)
        if (
            not isinstance(payload["blob_bytes"], int)
            or isinstance(payload["blob_bytes"], bool)
            or payload["blob_bytes"] < 0
        ):
            raise InvalidEventError("Order input fact size is invalid", sequence=sequence)


__all__ = ["ORDER_INPUT_RECORDED", "OrderInputRecord", "OrderInputRecorded"]
