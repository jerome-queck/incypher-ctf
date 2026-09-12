"""Typed canonical evidence for one bounded model Triage answer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from solver.event_store_contracts import InvalidEventError

TRIAGE_JUDGEMENT_RECORDED = "triage-judgement.recorded"


@dataclass(frozen=True)
class TriageJudgementRecorded:
    event_id: str
    prompt_digest: str
    ts: str

    @property
    def event_type(self) -> str:
        return TRIAGE_JUDGEMENT_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "prompt_digest": self.prompt_digest,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        expected = {"event_id", "prompt_digest", "ts", "blob_digest", "blob_bytes"}
        if set(payload) != expected:
            raise InvalidEventError("Triage judgement shape is unsupported", sequence=sequence)
        if not all(isinstance(payload[name], str) and payload[name] for name in expected - {"blob_bytes"}):
            raise InvalidEventError("Triage judgement text is invalid", sequence=sequence)
        for name in ("prompt_digest", "blob_digest"):
            value = payload[name]
            if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
                raise InvalidEventError("Triage judgement digest is invalid", sequence=sequence)
        if not isinstance(payload["blob_bytes"], int) or isinstance(payload["blob_bytes"], bool):
            raise InvalidEventError("Triage judgement size is invalid", sequence=sequence)


__all__ = ["TRIAGE_JUDGEMENT_RECORDED", "TriageJudgementRecorded"]
