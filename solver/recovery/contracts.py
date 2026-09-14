"""Typed canonical contracts for deterministic Incident containment."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

INCIDENT_RECORDED = "incident.recorded"


class FaultKind(str, Enum):
    WORKER_CRASH = "worker-crash"
    ROUTE_LOCAL_INFERENCE = "route-local-inference"
    TARGET_RESEARCH = "target-research"
    INSTANCE = "instance"
    SUBMISSION_AMBIGUITY = "submission-ambiguity"
    STORAGE = "storage"
    FINAL_INTERVAL = "final-interval"
    UNCLASSIFIED = "unclassified"
    AMBIGUOUS = "ambiguous"


class IncidentDisposition(str, Enum):
    CONTAINING = "containing"
    REPLACEMENT_ADMITTED = "replacement-admitted"
    REPLACEMENT_REFUSED = "replacement-refused"
    PROBATION = "probation"
    RESOLVED = "resolved"
    CONTAINED = "contained"
    TERMINAL = "terminal"


class IncidentStep(str, Enum):
    GENERATION_FENCE = "generation-fence"
    EVIDENCE_CAPTURE = "evidence-capture"
    FULL_TEARDOWN = "full-teardown"
    BOUNDED_REPLACEMENT = "bounded-replacement"
    FIXED_PROBE = "fixed-probe"
    CHANGED_REMEDY = "changed-remedy"
    SEMANTIC_PROBATION = "semantic-probation"


FAULT_REASONS = {
    FaultKind.WORKER_CRASH: "classified-worker-process-crash",
    FaultKind.ROUTE_LOCAL_INFERENCE: "classified-route-local-inference-fault",
    FaultKind.TARGET_RESEARCH: "classified-target-research-fault",
    FaultKind.INSTANCE: "classified-instance-fault",
    FaultKind.SUBMISSION_AMBIGUITY: "classified-submission-ambiguity",
    FaultKind.STORAGE: "classified-storage-fault",
    FaultKind.FINAL_INTERVAL: "classified-final-interval-fault",
    FaultKind.AMBIGUOUS: "ambiguous-fault-refused",
    FaultKind.UNCLASSIFIED: "unclassified-fault-refused",
}


class ProbationOutcome(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    UNSETTLED = "unsettled"


@dataclass(frozen=True)
class ChangedAction:
    dimension: str
    before: str
    after: str
    source: str

    @property
    def changed(self) -> bool:
        return bool(self.dimension and self.source and self.before != self.after)

    def document(self, *, accepted: bool) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "before": self.before,
            "after": self.after,
            "source": self.source,
            "accepted": accepted,
        }


@dataclass(frozen=True)
class ProbeObservation:
    outcome: str
    changed_action: ChangedAction | None = None
    reason: str = ""

    @classmethod
    def settled(cls, changed_action: ChangedAction) -> ProbeObservation:
        return cls("settled", changed_action)

    @classmethod
    def unsettled(cls, reason: str) -> ProbeObservation:
        return cls("unsettled", reason=reason)


@dataclass(frozen=True)
class RecoveryContext:
    original_deadline: str
    allowance: int
    failed_action_value: str
    adapter_id: str = "legacy-caller-v1"
    adapter_config: str = "{}"

    def __post_init__(self) -> None:
        if (
            not self.original_deadline
            or self.allowance < 1
            or not self.failed_action_value
            or not self.adapter_id
            or not self.adapter_config
        ):
            raise ValueError("Recovery context must preserve its deadline, allowance, and failed action")


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
    catalogue_version: str = ""
    probe_id: str = ""
    probe_outcome: str = ""
    remedy_id: str = ""
    remedy_version: str = ""
    changed_dimension: str = ""
    changed_before: str = ""
    changed_after: str = ""
    changed_source: str = ""
    changed_accepted: bool = False
    original_deadline: str = ""
    allowance: int = 0
    consumed_allowance: int = 0
    probation_outcome: str = ""
    final_outcome: str = ""
    failed_action_value: str = ""
    adapter_id: str = ""
    adapter_config: str = "{}"

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
        extension_strings = (
            "catalogue_version",
            "probe_id",
            "probe_outcome",
            "remedy_id",
            "remedy_version",
            "changed_dimension",
            "changed_before",
            "changed_after",
            "changed_source",
            "original_deadline",
            "probation_outcome",
            "final_outcome",
            "failed_action_value",
            "adapter_id",
            "adapter_config",
        )
        if any(name in payload and not isinstance(payload[name], str) for name in extension_strings):
            raise ValueError(f"Incident event {sequence} has an invalid Recovery extension")
        if any(not isinstance(payload.get(name), list) for name in ("steps", "completed_steps")) or any(
            not isinstance(item, str) for name in ("steps", "completed_steps") for item in payload.get(name, ())
        ):
            raise ValueError(f"Incident event {sequence} has invalid steps")
        counts = (payload.get(name) for name in ("duplicate_reports", "replay_count", "blob_bytes"))
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in counts):
            raise ValueError(f"Incident event {sequence} has an invalid count")
        extension_counts = (payload.get(name, 0) for name in ("allowance", "consumed_allowance"))
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in extension_counts):
            raise ValueError(f"Incident event {sequence} has an invalid Recovery count")
        if any(not isinstance(payload.get(name), bool) for name in ("terminal", "replacement_admitted")):
            raise ValueError(f"Incident event {sequence} has an invalid decision")
        if "changed_accepted" in payload and not isinstance(payload["changed_accepted"], bool):
            raise ValueError(f"Incident event {sequence} has an invalid Recovery decision")
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
