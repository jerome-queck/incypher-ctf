"""Durable authenticated facts bracketing one Board submission effect."""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass

from solver.event_store_storage import digest_bytes
from solver.submission.ambiguity_types import CompleteSubmissionIdentity

SUBMISSION_LEDGER_RECORDED = "submission-ledger.recorded"


class SubmissionLedgerPhase(str, enum.Enum):
    INTENDED = "intended"
    CLASSIFIED = "classified"


@dataclass(frozen=True)
class SubmissionLedgerRecorded:
    event_id: str
    phase: SubmissionLedgerPhase
    request_id: str
    candidate_id: str
    supplied_value: str
    identity: CompleteSubmissionIdentity
    submitted_at: str
    verdict: str = ""

    @property
    def event_type(self):
        return SUBMISSION_LEDGER_RECORDED

    def payload(self, *, blob_digest: str, blob_bytes: int):
        return {
            "event_id": self.event_id,
            "phase": self.phase.value,
            "request_id": self.request_id,
            "candidate_id": self.candidate_id,
            "supplied_value_digest": digest_bytes(self.supplied_value.encode()),
            "complete_identity": self.identity.document(),
            "submitted_at": self.submitted_at,
            "verdict": self.verdict,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, object]):
        return str(payload.get("event_id", ""))

    @classmethod
    def validate_payload(cls, payload: Mapping[str, object], *, sequence: int):
        required = {
            "event_id",
            "phase",
            "request_id",
            "candidate_id",
            "supplied_value_digest",
            "complete_identity",
            "submitted_at",
            "verdict",
            "blob_digest",
            "blob_bytes",
        }
        if set(payload) != required or not payload["event_id"] or not payload["request_id"]:
            raise ValueError("submission ledger row is incomplete")
        SubmissionLedgerPhase(str(payload["phase"]))
        identity = payload["complete_identity"]
        if not isinstance(identity, Mapping) or not identity.get("effect_id"):
            raise ValueError("submission ledger identity is incomplete")


def project_submission_row(events, effect_id: str):
    rows = [
        event.payload
        for event in events
        if event.event_type == SUBMISSION_LEDGER_RECORDED
        and event.payload["complete_identity"]["effect_id"] == effect_id
    ]
    if not rows:
        return None
    intended = [row for row in rows if row["phase"] == SubmissionLedgerPhase.INTENDED]
    classified = [row for row in rows if row["phase"] == SubmissionLedgerPhase.CLASSIFIED]
    if len(intended) != 1 or len(classified) > 1:
        raise ValueError("submission ledger replay is ambiguous")
    if not classified:
        return {"status": "unsettled", **intended[0]}
    if any(
        classified[0][key] != intended[0][key]
        for key in ("request_id", "candidate_id", "supplied_value_digest", "complete_identity")
    ):
        raise ValueError("submission ledger result does not match intent")
    return {
        "status": "classified",
        **classified[0],
        "row_type": "submission",
        "board_row_id": classified[0]["event_id"],
        "submission_epoch": classified[0]["complete_identity"]["submission_epoch"],
        "effect_id": effect_id,
    }
