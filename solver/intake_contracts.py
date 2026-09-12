"""Canonical event contracts for coherent Intake publication."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from solver.event_store_contracts import InvalidEventError
from solver.intake_qualification import PriorFence

INTAKE_DECISION_RECORDED = "intake-decision.recorded"
INTAKE_OBSERVATION_RECORDED = "intake-observation.recorded"


class IntakeRecord(str, enum.Enum):
    STARTED = "started"
    SETTLED = "settled"
    UNSETTLED = "unsettled"
    FENCE_CONFLICT = "fence-conflict"


@dataclass(frozen=True)
class IntakeDecisionRecorded:
    event_id: str
    attempt_id: str
    record: IntakeRecord
    profile_digest: str
    contract_digest: str
    authority_digest: str
    expected_fence: PriorFence
    reason: str = ""
    observation_ids: tuple[str, ...] = ()
    snapshot_digest: str = ""
    proposed_snapshot_digest: str = ""
    observed_fence: PriorFence | None = None
    ts: str = ""

    @property
    def event_type(self) -> str:
        return INTAKE_DECISION_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "attempt_id": self.attempt_id,
            "record": self.record.value,
            "profile_digest": self.profile_digest,
            "contract_digest": self.contract_digest,
            "authority_digest": self.authority_digest,
            "expected_fence": self.expected_fence.document(),
            "observed_fence": self.observed_fence.document() if self.observed_fence else {},
            "reason": self.reason,
            "observation_ids": list(self.observation_ids),
            "snapshot_digest": self.snapshot_digest,
            "proposed_snapshot_digest": self.proposed_snapshot_digest,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        expected = {
            "event_id",
            "attempt_id",
            "record",
            "profile_digest",
            "contract_digest",
            "authority_digest",
            "expected_fence",
            "observed_fence",
            "reason",
            "observation_ids",
            "snapshot_digest",
            "proposed_snapshot_digest",
            "ts",
            "blob_digest",
            "blob_bytes",
        }
        if set(payload) != expected:
            raise InvalidEventError("Intake decision shape is unsupported", sequence=sequence)
        strings = expected - {"expected_fence", "observed_fence", "observation_ids", "blob_bytes"}
        if any(not isinstance(payload[name], str) for name in strings):
            raise InvalidEventError("Intake decision text is invalid", sequence=sequence)
        if (
            not isinstance(payload["blob_bytes"], int)
            or isinstance(payload["blob_bytes"], bool)
            or payload["blob_bytes"] < 0
        ):
            raise InvalidEventError("Intake decision body size is invalid", sequence=sequence)
        if not payload["event_id"] or not payload["attempt_id"]:
            raise InvalidEventError("Intake decision identity is absent", sequence=sequence)
        if payload["record"] not in {item.value for item in IntakeRecord}:
            raise InvalidEventError("Intake decision record is unsupported", sequence=sequence)
        for name in ("profile_digest", "contract_digest", "authority_digest", "blob_digest"):
            if not _digest(payload[name]):
                raise InvalidEventError(f"Intake {name} is invalid", sequence=sequence)
        _fence(payload["expected_fence"], sequence=sequence, allow_genesis=True)
        observations = payload["observation_ids"]
        if not isinstance(observations, list) or any(not isinstance(item, str) or not item for item in observations):
            raise InvalidEventError("Intake observation closure is invalid", sequence=sequence)
        if len(observations) != len(set(observations)):
            raise InvalidEventError("Intake observation closure is duplicated", sequence=sequence)
        record = payload["record"]
        snapshot = payload["snapshot_digest"]
        proposed = payload["proposed_snapshot_digest"]
        observed = payload["observed_fence"]
        if record == IntakeRecord.STARTED.value:
            if payload["reason"] or observations or snapshot or proposed or observed:
                raise InvalidEventError("Intake start carries terminal state", sequence=sequence)
        elif record == IntakeRecord.SETTLED.value:
            if not _digest(snapshot) or proposed or observed or not payload["reason"]:
                raise InvalidEventError("settled Intake decision is incomplete", sequence=sequence)
        elif record == IntakeRecord.UNSETTLED.value:
            if snapshot or proposed or observed or not payload["reason"]:
                raise InvalidEventError("unsettled Intake decision carries authority", sequence=sequence)
        else:
            _fence(observed, sequence=sequence, allow_genesis=True)
            if snapshot or not _digest(proposed) or not payload["reason"]:
                raise InvalidEventError("Intake fence conflict is incomplete", sequence=sequence)


@dataclass(frozen=True)
class IntakeObservationRecorded:
    event_id: str
    attempt_id: str
    classified_event_id: str
    request_id: str
    kind: str
    pass_no: int
    raw_digest: str
    profile_digest: str
    subject_digest: str
    capability_digest: str
    peer_digest: str
    endpoint: str
    status: int
    content_type: str
    original_bytes: int
    complete: bool
    page: int
    challenge_id: object
    outcome: str
    location: str
    raw_blob_digest: str
    sanitized_blob_digest: str
    request_digest: str
    hop: int
    auth_forwarded: bool
    resource_identity: str
    ts: str

    @property
    def event_type(self) -> str:
        return INTAKE_OBSERVATION_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "attempt_id": self.attempt_id,
            "classified_event_id": self.classified_event_id,
            "request_id": self.request_id,
            "kind": self.kind,
            "pass": self.pass_no,
            "raw_digest": self.raw_digest,
            "profile_digest": self.profile_digest,
            "subject_digest": self.subject_digest,
            "capability_digest": self.capability_digest,
            "peer_digest": self.peer_digest,
            "endpoint": self.endpoint,
            "status": self.status,
            "content_type": self.content_type,
            "original_bytes": self.original_bytes,
            "complete": self.complete,
            "page": self.page,
            "challenge_id": self.challenge_id,
            "outcome": self.outcome,
            "location": self.location,
            "raw_blob_digest": self.raw_blob_digest,
            "sanitized_blob_digest": self.sanitized_blob_digest,
            "request_digest": self.request_digest,
            "hop": self.hop,
            "auth_forwarded": self.auth_forwarded,
            "resource_identity": self.resource_identity,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        expected = {
            "event_id",
            "attempt_id",
            "classified_event_id",
            "request_id",
            "kind",
            "pass",
            "raw_digest",
            "profile_digest",
            "subject_digest",
            "capability_digest",
            "peer_digest",
            "endpoint",
            "status",
            "content_type",
            "original_bytes",
            "complete",
            "page",
            "challenge_id",
            "outcome",
            "location",
            "raw_blob_digest",
            "sanitized_blob_digest",
            "request_digest",
            "hop",
            "auth_forwarded",
            "resource_identity",
            "ts",
            "blob_digest",
            "blob_bytes",
        }
        if set(payload) != expected:
            raise InvalidEventError("Intake observation shape is unsupported", sequence=sequence)
        strings = expected - {
            "pass",
            "status",
            "original_bytes",
            "complete",
            "page",
            "challenge_id",
            "auth_forwarded",
            "hop",
            "blob_bytes",
        }
        if any(not isinstance(payload[name], str) for name in strings):
            raise InvalidEventError("Intake observation text is invalid", sequence=sequence)
        integers = ("status", "original_bytes", "page", "hop", "blob_bytes")
        if (
            payload["pass"] not in (1, 2)
            or any(
                not isinstance(payload[name], int) or isinstance(payload[name], bool) or payload[name] < 0
                for name in integers
            )
            or not isinstance(payload["complete"], bool)
            or not isinstance(payload["auth_forwarded"], bool)
        ):
            raise InvalidEventError("Intake observation number is invalid", sequence=sequence)
        if any(
            not payload[name]
            for name in (
                "event_id",
                "attempt_id",
                "classified_event_id",
                "request_id",
                "kind",
                "endpoint",
                "outcome",
            )
        ):
            raise InvalidEventError("Intake observation identity is absent", sequence=sequence)
        for name in (
            "raw_digest",
            "profile_digest",
            "subject_digest",
            "capability_digest",
            "peer_digest",
            "blob_digest",
        ):
            if payload[name] and not _digest(payload[name]):
                raise InvalidEventError(f"Intake observation {name} is invalid", sequence=sequence)
        if not _digest(payload["raw_digest"]) or not _digest(payload["profile_digest"]):
            raise InvalidEventError("Intake observation core digest is invalid", sequence=sequence)


def _fence(value: object, *, sequence: int, allow_genesis: bool) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "profile_digest",
        "event_id",
        "event_digest",
        "snapshot_digest",
    }:
        raise InvalidEventError("Intake fence shape is invalid", sequence=sequence)
    if not _digest(value["profile_digest"]) or not isinstance(value["event_id"], str) or not value["event_id"]:
        raise InvalidEventError("Intake fence identity is invalid", sequence=sequence)
    if value["event_id"] == "genesis" and allow_genesis:
        if value["event_digest"] or value["snapshot_digest"]:
            raise InvalidEventError("Intake genesis fence carries authority", sequence=sequence)
        return
    if not _digest(value["event_digest"]) or not _digest(value["snapshot_digest"]):
        raise InvalidEventError("Intake fence digest is invalid", sequence=sequence)


def _digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and not set(value) - set("0123456789abcdef")


__all__ = [
    "INTAKE_DECISION_RECORDED",
    "INTAKE_OBSERVATION_RECORDED",
    "IntakeDecisionRecorded",
    "IntakeObservationRecorded",
    "IntakeRecord",
]
