"""Typed facts at the confirmed Attempt-progress boundary."""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from solver.event_store_contracts import InvalidEventError

ATTEMPT_PROGRESS_RECORDED = "attempt-progress.recorded"


class ProgressStatus(str, enum.Enum):
    ACCEPTED = "accepted"
    REPEATED = "repeated"
    UNCONFIRMED = "unconfirmed"
    NO_INFERENCE = "no-inference"
    STALE_EPOCH = "stale-epoch"
    OWNER_MISMATCH = "owner-mismatch"
    LATE = "late"


class EvidenceClassification(str, enum.Enum):
    CONFIRMED = "confirmed-semantic-progress"
    ACTIVITY = "unconfirmed-activity"
    NO_INFERENCE = "no-inference"


class JudgeSource(str, enum.Enum):
    DETERMINISTIC = "deterministic-stall-judge"
    NATIVE = "native-judge"
    CPA = "cpa-judge"
    NONE = "no-inference"


@dataclass(frozen=True)
class EvidenceArtifact:
    evidence_id: str
    sealed_digest: str
    judge_source: JudgeSource
    classification: EvidenceClassification
    moved: str
    replay: str


@dataclass(frozen=True)
class ProgressRequest:
    generation_id: str
    attempt_id: str
    checkpoint_id: str
    epoch: int
    observed_at: str
    checkpoint_at: str
    evidence: EvidenceArtifact


@dataclass(frozen=True)
class ProgressOutcome:
    status: ProgressStatus
    epoch_before: int
    epoch_after: int
    carry_digest: str


@dataclass(frozen=True)
class CarriedCheckpoint:
    evidence_id: str
    sealed_digest: str
    moved: str
    replay: str


@dataclass(frozen=True)
class CarryProjection:
    generation_id: str
    checkpoints: tuple[CarriedCheckpoint, ...]
    digest: str

    @property
    def evidence_ids(self):
        return tuple(item.evidence_id for item in self.checkpoints)


@dataclass(frozen=True)
class AttemptProgressRecorded:
    event_id: str
    generation_id: str
    attempt_id: str
    checkpoint_id: str
    evidence_id: str
    evidence_digest: str
    judge_source: str
    moved: str
    replay: str
    status: ProgressStatus
    epoch_before: int
    epoch_after: int
    extension_bound: int
    carry_digest: str
    authority_event_id: str
    authority_sequence: int
    ts: str

    exact_payload_identity = True

    @property
    def event_type(self):
        return ATTEMPT_PROGRESS_RECORDED

    @property
    def identity(self):
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int):
        result = asdict(self)
        result["status"] = self.status.value
        result.update(blob_digest=blob_digest, blob_bytes=blob_bytes)
        return result

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        if set(payload) != set(cls.__dataclass_fields__) | {"blob_digest", "blob_bytes"}:
            raise InvalidEventError("Attempt progress shape is unsupported", sequence=sequence)
        recorded = {item.value for item in ProgressStatus} - {ProgressStatus.LATE.value, ProgressStatus.REPEATED.value}
        if payload["status"] not in recorded:
            raise InvalidEventError("Attempt progress status is unsupported", sequence=sequence)
        before, after = payload["epoch_before"], payload["epoch_after"]
        if after < before or after > before + 1:
            raise InvalidEventError("Attempt progress epoch transition is invalid", sequence=sequence)


__all__ = [
    "ATTEMPT_PROGRESS_RECORDED",
    "AttemptProgressRecorded",
    "CarryProjection",
    "CarriedCheckpoint",
    "EvidenceArtifact",
    "EvidenceClassification",
    "JudgeSource",
    "ProgressOutcome",
    "ProgressRequest",
    "ProgressStatus",
]
