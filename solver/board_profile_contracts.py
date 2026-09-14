"""Canonical transitions for the Boot-owned Board-profile authority gate."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Mapping

from solver.board_profile_documents import PROFILE_ENDPOINTS, is_profile_document_name
from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError

BOARD_PROFILE_PHASE_RECORDED = "board-profile-phase.recorded"
BOARD_PROFILE_OBSERVATION_RECORDED = "board-profile-observation.recorded"
PROFILE_DOCUMENT_NAMES = frozenset(PROFILE_ENDPOINTS)


class ProfilePhaseRecord(str, enum.Enum):
    PROBE_STARTED = "probe-started"
    PROFILE_DECIDED = "profile-decided"
    OPERATIONS_OPENED = "operations-opened"


@dataclass(frozen=True)
class BoardProfilePhaseRecorded:
    event_id: str
    probe_id: str
    record: ProfilePhaseRecord
    rules_digest: str
    decision: str = ""
    receipt_digest: str = ""
    peer_identity_digest: str = ""
    ts: str = ""

    @property
    def event_type(self) -> str:
        return BOARD_PROFILE_PHASE_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "probe_id": self.probe_id,
            "record": self.record.value,
            "rules_digest": self.rules_digest,
            "decision": self.decision,
            "receipt_digest": self.receipt_digest,
            "peer_identity_digest": self.peer_identity_digest,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        fields = {
            "event_id",
            "probe_id",
            "record",
            "rules_digest",
            "decision",
            "receipt_digest",
            "peer_identity_digest",
            "ts",
            "blob_digest",
        }
        if set(payload) != fields | {"blob_bytes"} or any(not isinstance(payload[field], str) for field in fields):
            raise InvalidEventError("Board-profile phase payload shape is invalid", sequence=sequence)
        if not isinstance(payload["blob_bytes"], int) or isinstance(payload["blob_bytes"], bool):
            raise InvalidEventError("Board-profile phase body size is invalid", sequence=sequence)
        if not payload["event_id"] or not payload["probe_id"]:
            raise InvalidEventError("Board-profile phase identity is incomplete", sequence=sequence)
        if payload["record"] not in {item.value for item in ProfilePhaseRecord}:
            raise InvalidEventError("Board-profile phase record is unsupported", sequence=sequence)
        for name in ("rules_digest", "blob_digest"):
            digest = payload[name]
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise InvalidEventError(f"Board-profile {name} is not lowercase SHA-256", sequence=sequence)
        if payload["blob_bytes"] != 0 or payload["blob_digest"] != EMPTY_BLOB_DIGEST:
            raise InvalidEventError("Board-profile phase transitions cannot carry a body", sequence=sequence)
        record = payload["record"]
        if record == ProfilePhaseRecord.PROBE_STARTED.value:
            if payload["decision"] or payload["receipt_digest"] or not payload["peer_identity_digest"]:
                raise InvalidEventError("Board-profile probe start fields disagree", sequence=sequence)
        else:
            if payload["decision"] not in {"authoritative", "refused"}:
                raise InvalidEventError("Board-profile decision is unsupported", sequence=sequence)
            digest = payload["receipt_digest"]
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise InvalidEventError("Board-profile receipt digest is invalid", sequence=sequence)
            if payload["peer_identity_digest"]:
                raise InvalidEventError("Board-profile decision carries probe peer data", sequence=sequence)
        if record == ProfilePhaseRecord.OPERATIONS_OPENED.value and payload["decision"] != "authoritative":
            raise InvalidEventError("Board operations opened without authority", sequence=sequence)


@dataclass(frozen=True)
class BoardProfileObservationRecorded:
    event_id: str
    probe_id: str
    cycle: int
    document_name: str
    request_id: str
    endpoint: str
    http_status: int
    content_type: str
    original_bytes: int
    complete: bool
    ts: str = ""

    @property
    def event_type(self) -> str:
        return BOARD_PROFILE_OBSERVATION_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "probe_id": self.probe_id,
            "cycle": self.cycle,
            "document_name": self.document_name,
            "request_id": self.request_id,
            "endpoint": self.endpoint,
            "http_status": self.http_status,
            "content_type": self.content_type,
            "original_bytes": self.original_bytes,
            "complete": self.complete,
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        strings = {
            "event_id",
            "probe_id",
            "document_name",
            "request_id",
            "endpoint",
            "content_type",
            "ts",
            "blob_digest",
        }
        integers = {"cycle", "http_status", "original_bytes", "blob_bytes"}
        if set(payload) != strings | integers | {"complete"}:
            raise InvalidEventError("Board-profile observation shape is invalid", sequence=sequence)
        if any(not isinstance(payload[name], str) for name in strings):
            raise InvalidEventError("Board-profile observation text is invalid", sequence=sequence)
        if any(not isinstance(payload[name], int) or isinstance(payload[name], bool) for name in integers):
            raise InvalidEventError("Board-profile observation number is invalid", sequence=sequence)
        if not isinstance(payload["complete"], bool):
            raise InvalidEventError("Board-profile observation completeness is invalid", sequence=sequence)
        if (
            not payload["event_id"]
            or not payload["probe_id"]
            or not is_profile_document_name(payload["document_name"])
            or payload["cycle"] not in (1, 2)
            or payload["http_status"] < 0
            or payload["original_bytes"] < payload["blob_bytes"]
            or payload["complete"] != (payload["original_bytes"] == payload["blob_bytes"])
        ):
            raise InvalidEventError("Board-profile observation fields disagree", sequence=sequence)
        digest = payload["blob_digest"]
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise InvalidEventError("Board-profile observation body digest is invalid", sequence=sequence)


__all__ = [
    "BOARD_PROFILE_OBSERVATION_RECORDED",
    "BOARD_PROFILE_PHASE_RECORDED",
    "BoardProfileObservationRecorded",
    "BoardProfilePhaseRecorded",
    "PROFILE_DOCUMENT_NAMES",
    "is_profile_document_name",
    "ProfilePhaseRecord",
]
