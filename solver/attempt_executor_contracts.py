"""Stable types and canonical facts for one Attempt Resource envelope."""

from __future__ import annotations

import enum
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from solver.event_store_contracts import ATTEMPT_ENVELOPE_RECORDED, EMPTY_BLOB_DIGEST, InvalidEventError


class NetworkPolicy(str, enum.Enum):
    DENY = "deny"


BROKER_NETWORK_CLASSES = frozenset({"deny", "target-broker", "research-broker"})


class ResourceOutcome(str, enum.Enum):
    EXITED = "exited"
    CPU = "cpu-limit"
    MEMORY = "memory-limit"
    PIDS = "pid-limit"
    FILESYSTEM = "filesystem-limit"
    NETWORK = "network-limit"
    DEADLINE = "wall-clock-limit"
    CANCELLED = "cancelled"
    LAUNCH_FAILED = "launch-failed"
    RECONCILED = "reconciled-after-crash"


class EnvelopeRecord(str, enum.Enum):
    OWNER_OPENED = "owner-opened"
    RESERVED = "reserved"
    LAUNCHED = "launched"
    RESULT = "result"
    DISCARDED = "discarded"


@dataclass(frozen=True)
class EnvelopeSpec:
    cpu_seconds: float
    cpu_quota_us: int
    memory_bytes: int
    pids: int
    filesystem_bytes: int
    network: NetworkPolicy
    wall_seconds: float
    cleanup_seconds: float
    network_class: str = "deny"

    def __post_init__(self) -> None:
        numbers = (
            self.cpu_seconds,
            self.cpu_quota_us,
            self.memory_bytes,
            self.pids,
            self.filesystem_bytes,
            self.wall_seconds,
            self.cleanup_seconds,
        )
        if any(isinstance(value, bool) or value <= 0 for value in numbers):
            raise ValueError("every Resource-envelope limit must be positive")
        if self.cpu_quota_us > 100_000:
            raise ValueError("CPU quota cannot exceed one full core per 100ms period")
        if self.network_class not in BROKER_NETWORK_CLASSES:
            raise ValueError("Attempt broker network class is unsupported")

    def document(self) -> dict[str, object]:
        document = asdict(self)
        document["network"] = self.network.value
        return document


@dataclass(frozen=True)
class RuntimeBinding:
    image_id: str
    image_manifest_digest: str
    image_config_digest: str
    platform: str

    def __post_init__(self) -> None:
        digest = re.compile(r"(?:sha256:)?[0-9a-f]{64}\Z")
        if not all(
            digest.fullmatch(value) for value in (self.image_id, self.image_manifest_digest, self.image_config_digest)
        ):
            raise ValueError("Attempt runtime requires immutable image, manifest and config digests")
        if self.platform not in {"linux/arm64", "linux/amd64"}:
            raise ValueError("Attempt runtime platform is unsupported")


NETWORK_PROBE_KINDS = frozenset({"sibling-target", "board", "research", "control", "private", "undeclared-port"})


@dataclass(frozen=True)
class NetworkProbeDeclaration:
    kind: str
    attempted_endpoint_digest: str

    def __post_init__(self) -> None:
        if (
            self.kind not in NETWORK_PROBE_KINDS
            or re.fullmatch(r"[0-9a-f]{64}", self.attempted_endpoint_digest) is None
        ):
            raise ValueError("Attempt network probe declaration is invalid")

    def document(self) -> dict[str, str]:
        return {"kind": self.kind, "attempted_endpoint_digest": self.attempted_endpoint_digest}


@dataclass(frozen=True)
class RuntimeReservation:
    cgroup_path: str
    executor_uid: int
    control_nonce: str = ""


@dataclass(frozen=True)
class AttemptRequest:
    generation_id: str
    attempt_id: str
    step_id: str
    argv: tuple[str, ...]
    workspace: Path
    envelope: EnvelopeSpec
    lane_id: str = "lane-1"
    network_probe: NetworkProbeDeclaration | None = None


@dataclass(frozen=True)
class RuntimeObservation:
    outcome: ResourceOutcome
    exit_code: int | None
    output: bytes
    cgroup_path: str
    executor_uid: int
    observed: Mapping[str, int | float | str | bool]
    cleanup_complete: bool
    process_lifecycle: ProcessLifecycle | None = None

    @classmethod
    def exited(cls, *, exit_code: int, output: bytes, cgroup_path: str, executor_uid: int) -> RuntimeObservation:
        return cls(ResourceOutcome.EXITED, exit_code, output, cgroup_path, executor_uid, {}, True)


@dataclass(frozen=True)
class ProcessLifecycle:
    """One bounded inventory, stream, and teardown observation for an envelope."""

    descendants: tuple[int, ...]
    after_term: tuple[int, ...]
    after_kill: tuple[int, ...]
    term_sent: bool
    kill_sent: bool
    term_grace_seconds: float
    cleanup_seconds: float
    stream_limit_bytes: int
    stream_captured_bytes: int
    stream_total_bytes: int
    stream_truncated: bool
    control_eof: bool
    inventory_complete: bool = True
    teardown_acknowledged: bool = True

    def __post_init__(self) -> None:
        inventories = (self.descendants, self.after_term, self.after_kill)
        if any(any(isinstance(pid, bool) or pid <= 0 for pid in inventory) for inventory in inventories):
            raise ValueError("process inventories require positive PIDs")
        if any(len(set(inventory)) != len(inventory) for inventory in inventories):
            raise ValueError("process inventories cannot contain duplicate PIDs")
        counts = (self.stream_limit_bytes, self.stream_captured_bytes, self.stream_total_bytes)
        if any(isinstance(value, bool) or value < 0 for value in counts):
            raise ValueError("stream measures cannot be negative")
        if self.stream_captured_bytes > self.stream_limit_bytes or self.stream_captured_bytes > self.stream_total_bytes:
            raise ValueError("captured stream bytes exceed their bound")
        if self.term_grace_seconds < 0 or self.cleanup_seconds < 0:
            raise ValueError("process teardown durations cannot be negative")
        booleans = (
            self.term_sent,
            self.kill_sent,
            self.stream_truncated,
            self.control_eof,
            self.inventory_complete,
            self.teardown_acknowledged,
        )
        if any(not isinstance(value, bool) for value in booleans):
            raise ValueError("process lifecycle flags must be booleans")

    def document(self) -> dict[str, object]:
        document = asdict(self)
        for field in ("descendants", "after_term", "after_kill"):
            document[field] = list(document[field])
        return document


@dataclass(frozen=True)
class AttemptResult:
    envelope_id: str
    generation_id: str
    outcome: ResourceOutcome
    exit_code: int | None
    output: bytes
    observed: Mapping[str, int | float | str | bool]
    cleanup_complete: bool


@dataclass(frozen=True)
class AttemptEnvelopeRecorded:
    event_id: str
    record: EnvelopeRecord
    owner_epoch: str
    envelope_id: str = ""
    generation_id: str = ""
    attempt_id: str = ""
    step_id: str = ""
    profile_digest: str = ""
    binding: RuntimeBinding | None = None
    cgroup_path: str = ""
    executor_uid: int = -1
    outcome: ResourceOutcome | None = None
    exit_code: int | None = None
    cleanup_complete: bool = False
    declared: Mapping[str, object] | None = None
    observed: Mapping[str, object] | None = None
    network_probe: NetworkProbeDeclaration | None = None
    ts: str = ""

    @property
    def event_type(self) -> str:
        return ATTEMPT_ENVELOPE_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        binding = self.binding
        return {
            "event_id": self.event_id,
            "record": self.record.value,
            "owner_epoch": self.owner_epoch,
            "envelope_id": self.envelope_id,
            "generation_id": self.generation_id,
            "attempt_id": self.attempt_id,
            "step_id": self.step_id,
            "profile_digest": self.profile_digest,
            "image_id": binding.image_id if binding else "",
            "image_manifest_digest": binding.image_manifest_digest if binding else "",
            "image_config_digest": binding.image_config_digest if binding else "",
            "platform": binding.platform if binding else "",
            "cgroup_path": self.cgroup_path,
            "executor_uid": self.executor_uid,
            "outcome": self.outcome.value if self.outcome else "",
            "exit_code": self.exit_code,
            "cleanup_complete": self.cleanup_complete,
            "declared": dict(self.declared or {}),
            "observed": dict(self.observed or {}),
            "network_probe": self.network_probe.document() if self.network_probe else {},
            "ts": self.ts,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        required = {
            "event_id": str,
            "record": str,
            "owner_epoch": str,
            "envelope_id": str,
            "generation_id": str,
            "attempt_id": str,
            "step_id": str,
            "profile_digest": str,
            "image_id": str,
            "image_manifest_digest": str,
            "image_config_digest": str,
            "platform": str,
            "cgroup_path": str,
            "executor_uid": int,
            "outcome": str,
            "cleanup_complete": bool,
            "declared": dict,
            "observed": dict,
            "network_probe": dict,
            "ts": str,
            "blob_digest": str,
            "blob_bytes": int,
        }
        missing = [name for name in (*required, "exit_code") if name not in payload]
        if missing:
            raise InvalidEventError(f"Attempt-envelope payload is missing: {', '.join(missing)}", sequence=sequence)
        for name, expected in required.items():
            value = payload[name]
            if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
                raise InvalidEventError(f"Attempt-envelope field {name!r} has the wrong type", sequence=sequence)
        exit_code = payload["exit_code"]
        if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
            raise InvalidEventError("Attempt-envelope exit code has the wrong type", sequence=sequence)
        if payload["record"] not in {record.value for record in EnvelopeRecord}:
            raise InvalidEventError("Attempt-envelope record is unsupported", sequence=sequence)
        if not payload["event_id"] or not payload["owner_epoch"]:
            raise InvalidEventError("Attempt-envelope identity is incomplete", sequence=sequence)
        if payload["blob_digest"] != EMPTY_BLOB_DIGEST or payload["blob_bytes"] != 0:
            raise InvalidEventError("Attempt-envelope facts cannot carry a sealed body", sequence=sequence)
        if payload["record"] != EnvelopeRecord.OWNER_OPENED.value and not all(
            payload[name] for name in ("envelope_id", "generation_id", "attempt_id", "step_id")
        ):
            raise InvalidEventError("Attempt-envelope ownership is incomplete", sequence=sequence)
        if payload["record"] == EnvelopeRecord.RESULT.value:
            if payload["outcome"] not in {outcome.value for outcome in ResourceOutcome}:
                raise InvalidEventError("Attempt-envelope result is untyped", sequence=sequence)
        elif payload["outcome"]:
            raise InvalidEventError("Non-result Attempt-envelope fact carries an outcome", sequence=sequence)
        probe = payload["network_probe"]
        if probe:
            try:
                NetworkProbeDeclaration(**probe)
            except (TypeError, ValueError) as error:
                raise InvalidEventError("Attempt-envelope network probe is invalid", sequence=sequence) from error


__all__ = [
    "AttemptEnvelopeRecorded",
    "AttemptRequest",
    "AttemptResult",
    "BROKER_NETWORK_CLASSES",
    "EnvelopeRecord",
    "EnvelopeSpec",
    "NetworkPolicy",
    "NetworkProbeDeclaration",
    "ProcessLifecycle",
    "ResourceOutcome",
    "RuntimeBinding",
    "RuntimeObservation",
    "RuntimeReservation",
]
