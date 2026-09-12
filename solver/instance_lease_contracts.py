"""Public typed contracts for durable Instance-Lease authority."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


SCHEMA_VERSION = 1
RECEIPT_TYPE = "instance-lease"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.board-target-lease"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


class LeasePhase(str, Enum):
    RESERVED = "reserved"
    ATTEMPT_BOUND = "attempt-bound"
    RECOVERABLE = "recoverable"
    CLOSED = "closed"


class LeaseCloseCause(str, Enum):
    NONE = ""
    TERMINATED = "terminated"
    EXPIRED = "expired"
    NEVER_DEPLOYED = "never-deployed"


class LeaseVerdict(str, Enum):
    NONE = ""
    CREATE_AMBIGUOUS = "create-response-ambiguous"
    RENEW_AMBIGUOUS = "renew-response-ambiguous"
    RELEASE_AMBIGUOUS = "release-response-ambiguous"
    EXPIRY_AMBIGUOUS = "expiry-response-ambiguous"
    CREATE_UNSETTLED = "create-response-unsettled"
    RENEW_UNSETTLED = "renew-response-unsettled"
    RELEASE_UNSETTLED = "release-response-unsettled"
    EXPIRY_UNSETTLED = "expiry-response-unsettled"
    FOREIGN_ROW = "foreign-row"
    UNCORROBORATED_ROW = "uncorroborated-row"


@dataclass(frozen=True)
class LeaseIdentity:
    run_id: str
    lease_seq: int


@dataclass(frozen=True)
class RowCorroboration:
    verdict: LeaseVerdict = LeaseVerdict.NONE
    row_id: str = ""

    def __post_init__(self) -> None:
        if bool(self.row_id) != (self.verdict is LeaseVerdict.NONE):
            raise ValueError("row corroboration must be either one owned row or one refusal verdict")


@dataclass(frozen=True)
class LeaseGrant:
    identity: LeaseIdentity
    challenge_id: int | str
    board_id: str
    owner_id: str
    epoch: int
    generation_id: str
    attempt_id: str
    phase: LeasePhase
    operation_key: str
    generation_authority_sequence: int = 0
    generation_authority_event_id: str = ""
    renewal_sequence: int = 0
    row_id: str = ""
    target: str = ""
    until: dt.datetime | None = None
    verdict: LeaseVerdict = LeaseVerdict.NONE
    close_cause: LeaseCloseCause = LeaseCloseCause.NONE


def grant_document(grant: LeaseGrant) -> dict[str, object]:
    return {
        "run_id": grant.identity.run_id,
        "lease_seq": grant.identity.lease_seq,
        "challenge_id": grant.challenge_id,
        "board_id": grant.board_id,
        "owner_id": grant.owner_id,
        "epoch": grant.epoch,
        "generation_id": grant.generation_id,
        "attempt_id": grant.attempt_id,
        "phase": grant.phase.value,
        "operation_key": grant.operation_key,
        "generation_authority_sequence": grant.generation_authority_sequence,
        "generation_authority_event_id": grant.generation_authority_event_id,
        "renewal_sequence": grant.renewal_sequence,
        "row_id": grant.row_id,
        "target": grant.target,
        "until": grant.until.isoformat() if grant.until else "",
        "verdict": grant.verdict.value,
        "close_cause": grant.close_cause.value,
    }


def grant_from_document(row: Mapping[str, Any]) -> LeaseGrant:
    return LeaseGrant(
        LeaseIdentity(str(row["run_id"]), int(row["lease_seq"])),
        row["challenge_id"],
        str(row["board_id"]),
        str(row["owner_id"]),
        int(row["epoch"]),
        str(row["generation_id"]),
        str(row["attempt_id"]),
        LeasePhase(str(row["phase"])),
        str(row["operation_key"]),
        int(row.get("generation_authority_sequence", 0)),
        str(row.get("generation_authority_event_id", "")),
        int(row.get("renewal_sequence", 0)),
        str(row.get("row_id", "")),
        str(row.get("target", "")),
        dt.datetime.fromisoformat(str(row["until"])) if row.get("until") else None,
        LeaseVerdict(str(row.get("verdict", ""))),
        LeaseCloseCause(str(row.get("close_cause", ""))),
    )


__all__ = [
    "LeaseCloseCause",
    "LeaseGrant",
    "LeaseIdentity",
    "LeasePhase",
    "LeaseVerdict",
    "MANIFEST_RECEIPT_REF",
    "MANIFEST_ROW_ID",
    "RECEIPT_FILENAME",
    "RECEIPT_TYPE",
    "RowCorroboration",
    "SCHEMA_VERSION",
    "grant_document",
    "grant_from_document",
]
