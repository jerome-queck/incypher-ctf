"""Constrained identities and states for ambiguous submission recovery."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from solver.board_broker_contracts import BoardBrokerResult, BoardOutcome, SubmissionLedgerValue
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.write_reservation import EffectIdentity


class AmbiguityEvent(str, Enum):
    WIRE_STARTED = "wire-started"
    POSSIBLY_SENT = "possibly-sent"
    BOOT_REPLAYED = "boot-replayed"
    EVIDENCE_PROBE = "evidence-probe"
    FENCE_CLOSED = "fence-closed"


class EvidenceKind(str, Enum):
    SCORE_CHANGE = "score-change"
    UNSETTLED = "unsettled"
    EXACT_CANDIDATE_VERDICT = "exact-candidate-verdict"


class SubmissionVerdict(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    REFUSED = "refused"
    PAUSED = "paused"
    RATE_LIMITED = "rate-limited"


class SubmissionDisposition(str, Enum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REFUSED_AND_SPENT = "refused-and-spent"
    UNKNOWN_AND_SPENT = "unknown-and-spent"


class LedgerRowType(str, Enum):
    SUBMISSION = "submission"


class FenceClosed(RuntimeError):
    """The Candidate's at-most-once authority has already been consumed."""


@dataclass(frozen=True)
class CompleteSubmissionIdentity:
    board_identity: str
    challenge_id: int
    challenge_revision: str
    instance_provenance: str
    candidate_digest: str
    submission_epoch: int

    def __post_init__(self):
        if (
            any(
                not item
                for item in (
                    self.board_identity,
                    self.challenge_revision,
                    self.instance_provenance,
                    self.candidate_digest,
                )
            )
            or self.challenge_id < 1
            or self.submission_epoch < 1
        ):
            raise ValueError("complete submission identity is incomplete")

    @property
    def payload_identity(self):
        return digest_bytes(canonical_bytes(self.__dict__))

    @property
    def reservation_id(self):
        return f"serial-submit:{self.payload_identity}"

    @property
    def effect_id(self):
        return EffectIdentity("board.submit-candidate", str(self.challenge_id), self.payload_identity).fingerprint

    def document(self):
        return {**self.__dict__, "reservation_id": self.reservation_id, "effect_id": self.effect_id}


@dataclass(frozen=True)
class Evidence:
    kind: EvidenceKind
    source: str

    @classmethod
    def score_change(cls, source: str):
        return cls(EvidenceKind.SCORE_CHANGE, source)

    @classmethod
    def unsettled(cls, source: str):
        return cls(EvidenceKind.UNSETTLED, source)


@dataclass(frozen=True)
class AuthenticatedSubmissionEvidence:
    kind: EvidenceKind
    source: str
    verdict: SubmissionVerdict
    candidate_id: str
    request_id: str
    classified_event_id: str
    binding_digest: str
    peer_identity_digest: str
    effect_id: str
    submission_epoch: int
    supplied_value_digest: str
    board_row_id: str
    row_type: LedgerRowType
    submitted_at: str
    complete_identity: Mapping[str, object]

    @classmethod
    def from_broker(cls, result: BoardBrokerResult):
        provenance = result.provenance
        row = result.value.row if isinstance(result.value, SubmissionLedgerValue) else result.value
        required = {
            "board_row_id",
            "row_type",
            "submitted_at",
            "request_id",
            "verdict",
            "candidate_id",
            "supplied_value_digest",
            "effect_id",
            "submission_epoch",
            "complete_identity",
        }
        if not isinstance(row, Mapping) or set(row) != required or not isinstance(row["complete_identity"], Mapping):
            raise ValueError("submission ledger row shape is invalid")
        try:
            verdict = SubmissionVerdict(str(row["verdict"]))
            row_type = LedgerRowType(str(row["row_type"]))
        except ValueError as error:
            raise ValueError("unsupported submission-ledger value") from error
        if (
            result.outcome is not BoardOutcome.ANSWERED
            or not result.request_id
            or row["request_id"] != result.request_id
            or not provenance.classified_event_id
            or not provenance.binding_digest
            or not provenance.peer_identity_digest
        ):
            raise ValueError("submission ledger evidence is not broker-authenticated")
        return cls(
            EvidenceKind.EXACT_CANDIDATE_VERDICT,
            provenance.endpoint,
            verdict,
            str(row["candidate_id"]),
            result.request_id,
            provenance.classified_event_id,
            provenance.binding_digest,
            provenance.peer_identity_digest,
            str(row["effect_id"]),
            int(row["submission_epoch"]),
            str(row["supplied_value_digest"]),
            str(row["board_row_id"]),
            row_type,
            str(row["submitted_at"]),
            dict(row["complete_identity"]),
        )


@dataclass(frozen=True)
class PendingSubmission:
    candidate_id: str
    effect_id: str
    challenge_id: int
    wire_started_at: float
    deadline: float
    disposition: SubmissionDisposition = SubmissionDisposition.PENDING
    provenance: str = ""
    complete_identity: Mapping[str, object] | None = None
