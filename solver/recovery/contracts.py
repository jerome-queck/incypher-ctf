"""Typed canonical contracts for deterministic Incident containment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

INCIDENT_RECORDED = "incident.recorded"


class FaultKind(str, Enum):
    WORKER_CRASH = "worker-crash"
    UNCLASSIFIED = "unclassified"
    AMBIGUOUS = "ambiguous"


class IncidentDisposition(str, Enum):
    CONTAINING = "containing"
    REPLACEMENT_ADMITTED = "replacement-admitted"
    REPLACEMENT_REFUSED = "replacement-refused"


class IncidentStep(str, Enum):
    GENERATION_FENCE = "generation-fence"
    EVIDENCE_CAPTURE = "evidence-capture"
    FULL_TEARDOWN = "full-teardown"
    BOUNDED_REPLACEMENT = "bounded-replacement"


FAULT_REASONS = {
    FaultKind.WORKER_CRASH: "classified-worker-process-crash",
    FaultKind.AMBIGUOUS: "ambiguous-fault-refused",
    FaultKind.UNCLASSIFIED: "unclassified-fault-refused",
}


@dataclass(frozen=True)
class IncidentRecorded:
    event_id: str
    incident_id: str
    fault_identity: str
    fault_id: str
    generation_id: str
    scope: str
    fault_kind: str
    reason: str
    authority_state: str
    disposition: str
    steps: tuple[str, ...]
    completed_steps: tuple[str, ...]
    duplicate_reports: int
    replay_count: int
    terminal: bool
    evidence_digest: str = ""
    evidence_projection: str = ""
    replacement_admitted: bool = False

    @property
    def event_type(self) -> str:
        return INCIDENT_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            **self.__dict__,
            "steps": list(self.steps),
            "completed_steps": list(self.completed_steps),
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        strings = (
            "event_id",
            "incident_id",
            "fault_identity",
            "fault_id",
            "generation_id",
            "scope",
            "fault_kind",
            "reason",
            "authority_state",
            "disposition",
            "evidence_digest",
            "evidence_projection",
            "blob_digest",
        )
        if any(not isinstance(payload.get(name), str) for name in strings):
            raise ValueError(f"Incident event {sequence} has an invalid string field")
        if any(not isinstance(payload.get(name), list) for name in ("steps", "completed_steps")) or any(
            not isinstance(item, str) for name in ("steps", "completed_steps") for item in payload.get(name, ())
        ):
            raise ValueError(f"Incident event {sequence} has invalid steps")
        counts = (payload.get(name) for name in ("duplicate_reports", "replay_count", "blob_bytes"))
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
            raise ValueError(f"Incident event {sequence} has an invalid count")
        if any(not isinstance(payload.get(name), bool) for name in ("terminal", "replacement_admitted")):
            raise ValueError(f"Incident event {sequence} has an invalid decision")
        from solver.event_store_contracts import EMPTY_BLOB_DIGEST
        from solver.write_reservation_contracts import FINAL_STATES, ReservationState

        if payload["fault_kind"] not in {item.value for item in FaultKind}:
            raise ValueError(f"Incident event {sequence} has an invalid fault kind")
        if payload["disposition"] not in {item.value for item in IncidentDisposition}:
            raise ValueError(f"Incident event {sequence} has an invalid disposition")
        valid_steps = {item.value for item in IncidentStep}
        if not set(payload["completed_steps"]).issubset(payload["steps"]) or not set(payload["steps"]).issubset(
            valid_steps
        ):
            raise ValueError(f"Incident event {sequence} has invalid step values")
        authority_states = {""} | {item.value for item in ReservationState}
        if payload["authority_state"] not in authority_states:
            raise ValueError(f"Incident event {sequence} has an invalid authority state")
        if payload["terminal"] and payload["authority_state"] not in {item.value for item in FINAL_STATES}:
            raise ValueError(f"Incident event {sequence} has nonterminal authority")
        for name in ("fault_identity", "evidence_digest", "blob_digest"):
            value = payload[name]
            if value and (len(value) != 64 or any(char not in "0123456789abcdef" for char in value)):
                raise ValueError(f"Incident event {sequence} has an invalid digest")
        if payload["blob_digest"] != EMPTY_BLOB_DIGEST or payload["blob_bytes"] != 0:
            raise ValueError(f"Incident event {sequence} cannot carry a sealed body")
