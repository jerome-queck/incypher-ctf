"""Record-specific facts carried by capability-custody canonical events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from solver.event_store_contracts import CapabilityRecord, InvalidEventError


class CapabilityFact(Protocol):
    record: CapabilityRecord

    def fields(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class EnvironmentCutover:
    run_id: str
    boot_id: str
    evidence_digest: str
    record: CapabilityRecord = CapabilityRecord.ENV_CUTOVER

    def fields(self) -> Mapping[str, object]:
        return {
            "run_id": self.run_id,
            "boot_id": self.boot_id,
            "scope": "single-env-atomic-replace",
            "evidence_digest": self.evidence_digest,
            "decision": "passed",
        }


@dataclass(frozen=True)
class BrokerCustody:
    record: CapabilityRecord
    run_id: str
    boot_id: str
    broker: str
    secret_names: tuple[str, ...]
    evidence_digest: str
    peer_uid: int
    peer_identity_digest: str

    def __post_init__(self) -> None:
        if self.record not in {CapabilityRecord.TRANSFER_ACCEPTED, CapabilityRecord.SOURCE_CLEARED}:
            raise ValueError("broker custody requires a transfer or source-clear record")

    def fields(self) -> Mapping[str, object]:
        decision = "accepted" if self.record == CapabilityRecord.TRANSFER_ACCEPTED else "cleared"
        return {
            "run_id": self.run_id,
            "boot_id": self.boot_id,
            "broker": self.broker,
            "secret_names": self.secret_names,
            "evidence_digest": self.evidence_digest,
            "peer_uid": self.peer_uid,
            "peer_identity_digest": self.peer_identity_digest,
            "decision": decision,
        }


@dataclass(frozen=True)
class ExecutorProbe:
    run_id: str
    boot_id: str
    probe_kind: str
    probe_result: str
    evidence_digest: str
    record: CapabilityRecord = CapabilityRecord.PROBE_RECORDED

    def fields(self) -> Mapping[str, object]:
        return {
            "run_id": self.run_id,
            "boot_id": self.boot_id,
            "probe_kind": self.probe_kind,
            "probe_result": self.probe_result,
            "evidence_digest": self.evidence_digest,
            "decision": "recorded",
        }


@dataclass(frozen=True)
class SocketProbe:
    run_id: str
    boot_id: str
    evidence_digest: str
    probe_result: str = "refused"
    record: CapabilityRecord = CapabilityRecord.PROBE_RECORDED

    def fields(self) -> Mapping[str, object]:
        return {
            "run_id": self.run_id,
            "boot_id": self.boot_id,
            "probe_kind": "socket",
            "probe_result": self.probe_result,
            "evidence_digest": self.evidence_digest,
            "decision": "recorded",
        }


@dataclass(frozen=True)
class HandleDecision:
    record: CapabilityRecord
    handle_digest: str
    run_id: str
    boot_id: str
    generation_id: str
    lane_id: str
    attempt_id: str
    step_id: str
    scope: str
    peer_uid: int
    peer_identity_digest: str
    reason: str = ""

    def __post_init__(self) -> None:
        if self.record not in {
            CapabilityRecord.ISSUED,
            CapabilityRecord.AUTHORIZED,
            CapabilityRecord.DENIED,
            CapabilityRecord.REVOKED,
        }:
            raise ValueError("handle decision requires a handle record")

    def fields(self) -> Mapping[str, object]:
        decisions = {
            CapabilityRecord.ISSUED: "issued",
            CapabilityRecord.AUTHORIZED: "granted",
            CapabilityRecord.DENIED: "refused",
            CapabilityRecord.REVOKED: "revoked",
        }
        return {
            "handle_digest": self.handle_digest,
            "run_id": self.run_id,
            "boot_id": self.boot_id,
            "generation_id": self.generation_id,
            "lane_id": self.lane_id,
            "attempt_id": self.attempt_id,
            "step_id": self.step_id,
            "scope": self.scope,
            "peer_uid": self.peer_uid,
            "peer_identity_digest": self.peer_identity_digest,
            "decision": decisions[self.record],
            "reason": self.reason,
        }


@dataclass(frozen=True)
class PeerAuthenticationFailure:
    reason: str
    record: CapabilityRecord = CapabilityRecord.DENIED

    def fields(self) -> Mapping[str, object]:
        return {"decision": "refused", "reason": self.reason}


def validate_capability_fact(payload: Mapping[str, Any], *, sequence: int) -> None:
    """Dispatch one flat replay payload to its record-specific validator."""

    validators = {
        CapabilityRecord.ENV_CUTOVER.value: _validate_env_cutover,
        CapabilityRecord.TRANSFER_ACCEPTED.value: _validate_broker_custody,
        CapabilityRecord.SOURCE_CLEARED.value: _validate_broker_custody,
        CapabilityRecord.PROBE_RECORDED.value: _validate_probe,
        CapabilityRecord.ISSUED.value: _validate_handle,
        CapabilityRecord.AUTHORIZED.value: _validate_handle,
        CapabilityRecord.DENIED.value: _validate_denial,
        CapabilityRecord.REVOKED.value: _validate_handle,
    }
    validator = validators.get(payload["record"])
    if validator is None:
        raise InvalidEventError("Capability-custody record is unsupported", sequence=sequence)
    validator(payload, sequence)


def _validate_env_cutover(payload: Mapping[str, Any], sequence: int) -> None:
    if (
        not payload["run_id"]
        or not payload["boot_id"]
        or payload["scope"] != "single-env-atomic-replace"
        or payload["decision"] != "passed"
        or payload["reason"]
    ):
        raise InvalidEventError("Capability env cutover evidence is incomplete", sequence=sequence)


def _validate_broker_custody(payload: Mapping[str, Any], sequence: int) -> None:
    if (
        not payload["run_id"]
        or not payload["boot_id"]
        or not payload["broker"]
        or not payload["secret_names"]
        or payload["peer_uid"] < 0
        or not payload["peer_identity_digest"]
    ):
        raise InvalidEventError("Capability transfer has incomplete broker custody", sequence=sequence)
    expected = "accepted" if payload["record"] == CapabilityRecord.TRANSFER_ACCEPTED.value else "cleared"
    if payload["decision"] != expected or payload["reason"]:
        raise InvalidEventError("Capability transfer has an invalid decision", sequence=sequence)


def _validate_probe(payload: Mapping[str, Any], sequence: int) -> None:
    if (
        not payload["run_id"]
        or not payload["boot_id"]
        or payload["probe_kind"] not in {"memory", "environment", "argv", "file", "event", "socket"}
        or payload["probe_result"] not in {"clear", "found", "incomplete", "refused"}
        or payload["decision"] != "recorded"
    ):
        raise InvalidEventError("Capability executor probe evidence is incomplete", sequence=sequence)


def _validate_handle(payload: Mapping[str, Any], sequence: int) -> None:
    record = payload["record"]
    if record == CapabilityRecord.ISSUED.value:
        required = ("run_id", "boot_id", "generation_id", "scope", "peer_identity_digest")
        if any(not payload[field] for field in required) or payload["peer_uid"] < 0:
            raise InvalidEventError("Capability issue has an incomplete trusted binding", sequence=sequence)
        valid = payload["decision"] == "issued" and not payload["reason"]
    elif record == CapabilityRecord.AUTHORIZED.value:
        valid = payload["decision"] == "granted" and not payload["reason"]
    else:
        valid = payload["decision"] == "revoked" and bool(payload["reason"])
    if not valid:
        raise InvalidEventError("Capability handle decision is invalid", sequence=sequence)


def _validate_denial(payload: Mapping[str, Any], sequence: int) -> None:
    if payload["decision"] != "refused" or not payload["reason"]:
        raise InvalidEventError("Capability denial has no internal reason", sequence=sequence)
    if payload["handle_digest"]:
        return _validate_handle_digest(payload, sequence)
    if payload["reason"] != "peer-authentication-unavailable":
        raise InvalidEventError("Capability denial has no handle or peer-auth failure", sequence=sequence)


def _validate_handle_digest(payload: Mapping[str, Any], sequence: int) -> None:
    digest = payload["handle_digest"]
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise InvalidEventError("Capability handle digest is not lowercase SHA-256", sequence=sequence)


__all__ = [
    "BrokerCustody",
    "CapabilityFact",
    "EnvironmentCutover",
    "ExecutorProbe",
    "HandleDecision",
    "PeerAuthenticationFailure",
    "SocketProbe",
    "validate_capability_fact",
]
