"""Canonical contracts for one Attempt process-tree lifecycle."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from solver.attempt_executor_contracts import ProcessLifecycle
from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError


ATTEMPT_PROCESS_RECORDED = "attempt-process.recorded"


class ProcessRecord(str, enum.Enum):
    FENCED = "fenced"
    OBSERVED = "observed"


@dataclass(frozen=True)
class AttemptProcessRecorded:
    event_id: str
    record: ProcessRecord
    owner_epoch: str
    envelope_id: str
    generation_id: str
    attempt_id: str
    step_id: str
    cgroup_path: str
    fence_sequence: int = 0
    fence_ts: str = ""
    lifecycle: ProcessLifecycle | None = None
    ts: str = ""

    @property
    def event_type(self) -> str:
        return ATTEMPT_PROCESS_RECORDED

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
            "owner_epoch": self.owner_epoch,
            "envelope_id": self.envelope_id,
            "generation_id": self.generation_id,
            "attempt_id": self.attempt_id,
            "step_id": self.step_id,
            "cgroup_path": self.cgroup_path,
            "fence_sequence": self.fence_sequence,
            "fence_ts": self.fence_ts,
            "lifecycle": self.lifecycle.document() if self.lifecycle is not None else {},
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        strings = (
            "event_id",
            "record",
            "owner_epoch",
            "envelope_id",
            "generation_id",
            "attempt_id",
            "step_id",
            "cgroup_path",
            "fence_ts",
            "ts",
            "blob_digest",
        )
        missing = [field for field in (*strings, "fence_sequence", "lifecycle", "blob_bytes") if field not in payload]
        if missing or any(not isinstance(payload.get(field), str) for field in strings):
            raise InvalidEventError("Attempt-process payload is incomplete", sequence=sequence)
        if not all(payload[field] for field in strings[:7]):
            raise InvalidEventError("Attempt-process identity is incomplete", sequence=sequence)
        if payload["record"] not in {item.value for item in ProcessRecord}:
            raise InvalidEventError("Attempt-process record is unsupported", sequence=sequence)
        for field in ("fence_sequence", "blob_bytes"):
            if not isinstance(payload[field], int) or isinstance(payload[field], bool) or payload[field] < 0:
                raise InvalidEventError("Attempt-process count is invalid", sequence=sequence)
        if not isinstance(payload["lifecycle"], dict):
            raise InvalidEventError("Attempt-process lifecycle is not an object", sequence=sequence)
        if payload["blob_digest"] != EMPTY_BLOB_DIGEST or payload["blob_bytes"] != 0:
            raise InvalidEventError("Attempt-process facts cannot carry a body", sequence=sequence)
        if payload["record"] == ProcessRecord.FENCED.value:
            if payload["fence_sequence"] < 1 or not payload["fence_ts"] or payload["lifecycle"]:
                raise InvalidEventError("Attempt-process fence is incomplete", sequence=sequence)
        else:
            if payload["fence_sequence"] or payload["fence_ts"]:
                raise InvalidEventError("Attempt-process observation carries fence authority", sequence=sequence)
            _validate_lifecycle(payload["lifecycle"], sequence)


def _validate_lifecycle(document: Mapping[str, Any], sequence: int) -> None:
    try:
        ProcessLifecycle(
            descendants=tuple(document["descendants"]),
            after_term=tuple(document["after_term"]),
            after_kill=tuple(document["after_kill"]),
            term_sent=document["term_sent"],
            kill_sent=document["kill_sent"],
            term_grace_seconds=document["term_grace_seconds"],
            cleanup_seconds=document["cleanup_seconds"],
            stream_limit_bytes=document["stream_limit_bytes"],
            stream_captured_bytes=document["stream_captured_bytes"],
            stream_total_bytes=document["stream_total_bytes"],
            stream_truncated=document["stream_truncated"],
            control_eof=document["control_eof"],
            inventory_complete=document["inventory_complete"],
            teardown_acknowledged=document["teardown_acknowledged"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidEventError("Attempt-process lifecycle is invalid", sequence=sequence) from error


__all__ = ["ATTEMPT_PROCESS_RECORDED", "AttemptProcessRecorded", "ProcessRecord"]
