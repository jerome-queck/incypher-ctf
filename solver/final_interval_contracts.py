"""Typed canonical facts for the final part of a Run."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError

FINAL_INTERVAL_RECORDED = "final-interval.recorded"
SCHEMA_VERSION = 2
RECEIPT_KIND = "final-interval"
RECEIPT = "final-interval.receipt.json"


class FinalIntervalRecord(str, enum.Enum):
    TRANSITION = "transition"
    ENTITLEMENT_RESERVED = "entitlement-reserved"
    ENTITLEMENT_SPENT = "entitlement-spent"
    ENTITLEMENT_CLOSED = "entitlement-closed"
    DRAIN_REQUESTED = "drain-requested"
    DRAIN_RESULT = "drain-result"
    SUBMISSION_RECONCILED = "submission-reconciled"
    CLEANUP_REQUESTED = "cleanup-requested"
    CLEANUP_RESULT = "cleanup-result"
    TERMINAL_INVENTORY = "terminal-inventory"


class AdmissionMode(str, enum.Enum):
    ORDINARY = "ordinary"
    FINAL_CHANCE = "final-chance"
    SUBMISSION_RESERVE = "submission-reserve"
    CLOSED = "closed"


class FinalChanceState(str, enum.Enum):
    RESERVED = "reserved"
    SPENT = "spent"
    CLOSED = "closed"


class SubmissionDisposition(str, enum.Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REFUSED_AND_SPENT = "refused-and-spent"
    POSSIBLY_SENT = "possibly-sent"
    UNKNOWN_AND_SPENT = "unknown-and-spent"


class SubmissionDeferred(RuntimeError):
    """The Candidate was fenced before wire and remains eligible for later drain."""


class CleanupDisposition(str, enum.Enum):
    RELEASED = "released"
    UNSETTLED = "unsettled"


class RunDisposition(str, enum.Enum):
    CLOSED = "closed"
    CLOSED_WITH_UNSETTLED_CLEANUP = "closed-with-unsettled-cleanup"


@dataclass(frozen=True)
class FinalIntervalRecorded:
    event_id: str
    record: FinalIntervalRecord
    observed_at: str
    lane_id: str = ""
    challenge_id: str = ""
    candidate_id: str = ""
    reservation_id: str = ""
    state: str = ""
    outcome: str = ""
    inventory: Mapping[str, object] | None = None

    @property
    def event_type(self) -> str:
        return FINAL_INTERVAL_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "record": self.record.value,
            "observed_at": self.observed_at,
            "lane_id": self.lane_id,
            "challenge_id": self.challenge_id,
            "candidate_id": self.candidate_id,
            "reservation_id": self.reservation_id,
            "state": self.state,
            "outcome": self.outcome,
            "inventory": dict(self.inventory or {}),
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        fields = {
            "event_id": str,
            "record": str,
            "observed_at": str,
            "lane_id": str,
            "challenge_id": str,
            "candidate_id": str,
            "reservation_id": str,
            "state": str,
            "outcome": str,
            "inventory": dict,
            "blob_digest": str,
            "blob_bytes": int,
        }
        if set(fields) - set(payload):
            raise InvalidEventError("Final-interval payload is incomplete", sequence=sequence)
        if any(not isinstance(payload[key], kind) for key, kind in fields.items()):
            raise InvalidEventError("Final-interval payload field has the wrong type", sequence=sequence)
        if not payload["event_id"] or payload["record"] not in {item.value for item in FinalIntervalRecord}:
            raise InvalidEventError("Final-interval identity or record is unsupported", sequence=sequence)
        if payload["blob_digest"] != EMPTY_BLOB_DIGEST or payload["blob_bytes"] != 0:
            raise InvalidEventError("Final-interval facts cannot carry a body", sequence=sequence)


__all__ = [
    "AdmissionMode",
    "CleanupDisposition",
    "FINAL_INTERVAL_RECORDED",
    "FinalChanceState",
    "FinalIntervalRecord",
    "FinalIntervalRecorded",
    "RECEIPT",
    "RECEIPT_KIND",
    "RunDisposition",
    "SCHEMA_VERSION",
    "SubmissionDisposition",
    "SubmissionDeferred",
]
