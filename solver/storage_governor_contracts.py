"""Public contracts for deterministic storage admission."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from solver.event_store_contracts import EMPTY_BLOB_DIGEST, InvalidEventError
from solver.write_reservation_contracts import Capacity, WriteProfile

STORAGE_GOVERNOR_RECORDED = "storage-governor.recorded"


class PressureState(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    STOP_ADMISSION = "stop-admission"
    AUTHORITY_ONLY = "authority-only"


class AdmissionClass(str, Enum):
    ORDINARY = "ordinary"
    AUTHORITY = "authority"
    TERMINAL = "terminal"
    RECOVERY = "recovery"


class RetirableStorageClass(str, Enum):
    EPHEMERAL = "ephemeral"
    CACHE = "cache"
    DUPLICATE = "duplicate"
    RECONSTRUCTIBLE = "reconstructible"
    FAILED_WORK = "failed-work"
    UNSELECTED_WORK = "unselected-work"
    SUPERSEDED_WORK = "superseded-work"
    RAW_OBSERVATION = "raw-observation"
    RAW_TELEMETRY = "raw-telemetry"
    TOOL_BODY = "tool-body"
    PRIVATE_LOG = "private-log"
    CLOSED_INCIDENT_DETAIL = "closed-incident-detail"


class ProtectedStorageClass(str, Enum):
    CANONICAL_AUTHORITY = "canonical-authority"
    RESTART_PRIVATE_BROKER = "restart-private-broker"
    CANDIDATE_VAULT = "candidate-vault"
    SOLVE_RECEIPT = "solve-receipt"
    SELECTED_EVIDENCE = "selected-evidence"
    OPEN_INCIDENT_EVIDENCE = "open-incident-evidence"
    PROMOTED_EVIDENCE = "promoted-evidence"
    V1_EVIDENCE = "v1-evidence"


STORAGE_CLASSES = frozenset(item.value for item in (*RetirableStorageClass, *ProtectedStorageClass))


@dataclass(frozen=True)
class StorageCapacity:
    bytes: int
    filesystem_objects: int
    create: int
    append: int
    rename: int
    unlink: int
    durability: int

    def __post_init__(self) -> None:
        if min(self.as_dict().values()) < 0:
            raise ValueError("storage capacity cannot be negative")

    def as_dict(self) -> dict[str, int]:
        return {
            "bytes": self.bytes,
            "filesystem_objects": self.filesystem_objects,
            "create": self.create,
            "append": self.append,
            "rename": self.rename,
            "unlink": self.unlink,
            "durability": self.durability,
        }

    def fits(self, need: StorageCapacity) -> bool:
        return all(self.as_dict()[name] >= value for name, value in need.as_dict().items())

    def at_or_below(self, threshold: StorageCapacity) -> bool:
        return any(self.as_dict()[name] <= value for name, value in threshold.as_dict().items())

    def __add__(self, other: StorageCapacity) -> StorageCapacity:
        return StorageCapacity(**{name: value + other.as_dict()[name] for name, value in self.as_dict().items()})

    def __sub__(self, other: StorageCapacity) -> StorageCapacity:
        return StorageCapacity(**{name: value - other.as_dict()[name] for name, value in self.as_dict().items()})

    def as_reservation(self) -> Capacity:
        return Capacity(
            bytes=self.bytes,
            objects=self.filesystem_objects,
            operations=self.create + self.append + self.rename + self.unlink + self.durability,
            create=self.create,
            append=self.append,
            rename=self.rename,
            unlink=self.unlink,
            durability=self.durability,
        )

    @classmethod
    def from_headroom(cls, value: Capacity) -> StorageCapacity:
        return cls(
            bytes=value.bytes,
            filesystem_objects=value.objects,
            create=value.create,
            append=value.append,
            rename=value.rename,
            unlink=value.unlink,
            durability=value.durability,
        )

    @classmethod
    def from_reservation(cls, value: Mapping[str, Any]) -> StorageCapacity:
        operations = value["operations"]
        return cls(
            bytes=value["bytes"],
            filesystem_objects=value["filesystem_objects"],
            create=operations["create"],
            append=operations["append"],
            rename=operations["rename"],
            unlink=operations["unlink"],
            durability=operations["durability"],
        )


@dataclass(frozen=True)
class StorageGovernorProfile:
    writable_envelope: StorageCapacity
    warning_remaining: StorageCapacity
    stop_admission_remaining: StorageCapacity
    authority_only_remaining: StorageCapacity
    shared_authority_pool: StorageCapacity
    terminal_floor: StorageCapacity
    recovery_floor: StorageCapacity

    def __post_init__(self) -> None:
        ordered = (
            self.authority_only_remaining,
            self.stop_admission_remaining,
            self.warning_remaining,
            self.writable_envelope,
        )
        if any(not larger.fits(smaller) for smaller, larger in zip(ordered, ordered[1:])):
            raise ValueError("storage thresholds must be authority-only <= stop <= warning <= envelope")
        authority = self.shared_authority_pool + self.terminal_floor + self.recovery_floor
        if authority != self.authority_only_remaining:
            raise ValueError("authority-only headroom must equal shared, terminal, and Recovery pools")

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {
            "writable_envelope": self.writable_envelope.as_dict(),
            "warning_remaining": self.warning_remaining.as_dict(),
            "stop_admission_remaining": self.stop_admission_remaining.as_dict(),
            "authority_only_remaining": self.authority_only_remaining.as_dict(),
            "shared_authority_pool": self.shared_authority_pool.as_dict(),
            "terminal_floor": self.terminal_floor.as_dict(),
            "recovery_floor": self.recovery_floor.as_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> StorageGovernorProfile:
        return cls(**{name: StorageCapacity(**value[name]) for name in cls.__dataclass_fields__})

    @classmethod
    def from_release_candidate(cls, storage: Mapping[str, Any]) -> StorageGovernorProfile:
        zero = StorageCapacity(0, 0, 0, 0, 0, 0, 0)
        authority_only = sum(
            (
                StorageCapacity.from_reservation(storage[name])
                for name in ("shared_authority_pool", "terminal_floor", "recovery_floor")
            ),
            start=zero,
        )
        return cls(
            writable_envelope=StorageCapacity.from_reservation(storage["writable_envelope"]),
            warning_remaining=StorageCapacity.from_reservation(storage["pressure_thresholds"]["soft_remaining"]),
            stop_admission_remaining=StorageCapacity.from_reservation(storage["pressure_thresholds"]["hard_remaining"]),
            authority_only_remaining=authority_only,
            shared_authority_pool=StorageCapacity.from_reservation(storage["shared_authority_pool"]),
            terminal_floor=StorageCapacity.from_reservation(storage["terminal_floor"]),
            recovery_floor=StorageCapacity.from_reservation(storage["recovery_floor"]),
        )

    def write_profile(self) -> WriteProfile:
        ordinary = self.writable_envelope - self.authority_only_remaining
        return WriteProfile(
            ordinary=ordinary.as_reservation(),
            shared=self.shared_authority_pool.as_reservation(),
            terminal=self.terminal_floor.as_reservation(),
            recovery=self.recovery_floor.as_reservation(),
        )


@dataclass(frozen=True)
class AdmissionDecision:
    request_id: str
    pressure: PressureState
    admission_class: AdmissionClass
    admitted: bool
    remaining: StorageCapacity
    need: StorageCapacity
    authority_headroom: StorageCapacity
    reservation_key: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "pressure": self.pressure.value,
            "admission_class": self.admission_class.value,
            "admitted": self.admitted,
            "remaining": self.remaining.as_dict(),
            "need": self.need.as_dict(),
            "authority_headroom": self.authority_headroom.as_dict(),
            "reservation_key": self.reservation_key,
        }


@dataclass(frozen=True)
class ReachabilityRoots:
    event_sequences: frozenset[int] = frozenset()
    blob_digests: frozenset[str] = frozenset()
    paths: frozenset[str] = frozenset()
    promoted_paths: frozenset[str] = frozenset()
    in_flight_paths: frozenset[str] = frozenset()
    in_flight_digests: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, list[str] | list[int]]:
        return {
            "event_sequences": sorted(self.event_sequences),
            "blob_digests": sorted(self.blob_digests),
            "paths": sorted(self.paths),
            "promoted_paths": sorted(self.promoted_paths),
            "in_flight_paths": sorted(self.in_flight_paths),
            "in_flight_digests": sorted(self.in_flight_digests),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> ReachabilityRoots:
        return cls(
            event_sequences=frozenset(value["event_sequences"]),
            blob_digests=frozenset(value["blob_digests"]),
            paths=frozenset(value["paths"]),
            promoted_paths=frozenset(value["promoted_paths"]),
            in_flight_paths=frozenset(value["in_flight_paths"]),
            in_flight_digests=frozenset(value["in_flight_digests"]),
        )


@dataclass(frozen=True)
class RetirementCandidate:
    path: str
    storage_class: str
    digest: str
    length: int
    event_sequences: tuple[int, ...] = ()
    reservation_key: str = ""

    def resource_identity_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "digest": self.digest,
            "length": self.length,
            "event_sequences": sorted(set(self.event_sequences)),
        }

    def identity_dict(self) -> dict[str, Any]:
        return {
            **self.resource_identity_dict(),
            "storage_class": self.storage_class,
        }


@dataclass(frozen=True)
class RetirementResult:
    retired_digests: tuple[str, ...]
    protected_paths: tuple[str, ...]


@dataclass(frozen=True)
class StorageGovernorRecorded:
    exact_payload_identity = True

    event_id: str
    request_id: str
    pressure: PressureState
    admission_class: AdmissionClass
    admitted: bool
    remaining: StorageCapacity
    need: StorageCapacity
    authority_headroom: StorageCapacity

    @property
    def event_type(self) -> str:
        return STORAGE_GOVERNOR_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "record": "pressure-decision",
            "request_id": self.request_id,
            "pressure": self.pressure.value,
            "admission_class": self.admission_class.value,
            "admitted": self.admitted,
            "remaining": self.remaining.as_dict(),
            "need": self.need.as_dict(),
            "authority_headroom": self.authority_headroom.as_dict(),
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        if payload.get("record") in {"retirement-tombstone", "retirement-complete"}:
            RetirementRecorded.validate_payload(payload, sequence=sequence)
            return
        if payload.get("record") == "reachability-snapshot":
            ReachabilityRecorded.validate_payload(payload, sequence=sequence)
            return
        if payload.get("record") == "storage-classification":
            StorageClassified.validate_payload(payload, sequence=sequence)
            return
        expected = {
            "event_id",
            "record",
            "request_id",
            "pressure",
            "admission_class",
            "admitted",
            "remaining",
            "need",
            "authority_headroom",
            "blob_digest",
            "blob_bytes",
        }
        if set(payload) != expected:
            raise InvalidEventError("storage-governor pressure payload has wrong fields", sequence=sequence)
        try:
            PressureState(payload["pressure"])
            AdmissionClass(payload["admission_class"])
            StorageCapacity(**payload["remaining"])
            StorageCapacity(**payload["need"])
            StorageCapacity(**payload["authority_headroom"])
        except (TypeError, ValueError) as error:
            raise InvalidEventError("storage-governor pressure payload is invalid", sequence=sequence) from error
        if (
            payload["record"] != "pressure-decision"
            or not isinstance(payload["event_id"], str)
            or not payload["event_id"]
            or not isinstance(payload["request_id"], str)
            or not payload["request_id"]
            or not isinstance(payload["admitted"], bool)
            or payload["blob_digest"] != EMPTY_BLOB_DIGEST
            or payload["blob_bytes"] != 0
        ):
            raise InvalidEventError("storage-governor pressure payload is unsupported", sequence=sequence)


@dataclass(frozen=True)
class ReachabilityRecorded:
    event_id: str
    roots: ReachabilityRoots

    @property
    def event_type(self) -> str:
        return STORAGE_GOVERNOR_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "record": "reachability-snapshot",
            "roots": self.roots.as_dict(),
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        try:
            roots = ReachabilityRoots.from_dict(payload["roots"])
        except (KeyError, TypeError, ValueError) as error:
            raise InvalidEventError("storage-governor reachability payload is invalid", sequence=sequence) from error
        if (
            set(payload) != {"event_id", "record", "roots", "blob_digest", "blob_bytes"}
            or payload.get("record") != "reachability-snapshot"
            or not isinstance(payload.get("event_id"), str)
            or not payload["event_id"]
            or roots.as_dict() != payload["roots"]
            or payload.get("blob_digest") != EMPTY_BLOB_DIGEST
            or payload.get("blob_bytes") != 0
        ):
            raise InvalidEventError("storage-governor reachability payload is invalid", sequence=sequence)


@dataclass(frozen=True)
class StorageClassified:
    event_id: str
    path: str
    storage_class: str
    digest: str
    length: int
    event_sequences: tuple[int, ...]
    reservation_key: str

    @property
    def event_type(self) -> str:
        return STORAGE_GOVERNOR_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "record": "storage-classification",
            "path": self.path,
            "storage_class": self.storage_class,
            "digest": self.digest,
            "length": self.length,
            "event_sequences": list(self.event_sequences),
            "reservation_key": self.reservation_key,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        expected = {
            "event_id",
            "record",
            "path",
            "storage_class",
            "digest",
            "length",
            "event_sequences",
            "reservation_key",
            "blob_digest",
            "blob_bytes",
        }
        digest = payload.get("digest")
        references = payload.get("event_sequences")
        if (
            set(payload) != expected
            or payload.get("record") != "storage-classification"
            or not isinstance(payload.get("event_id"), str)
            or not payload["event_id"]
            or payload.get("storage_class") not in STORAGE_CLASSES
            or not isinstance(payload.get("reservation_key"), str)
            or not payload["reservation_key"]
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or payload.get("path") != f"sealed/sha256/{digest}"
            or not isinstance(payload.get("length"), int)
            or payload["length"] < 0
            or not isinstance(references, list)
            or not references
            or any(not isinstance(item, int) or item < 1 for item in references)
            or references != sorted(set(references))
            or payload.get("blob_digest") != EMPTY_BLOB_DIGEST
            or payload.get("blob_bytes") != 0
        ):
            raise InvalidEventError("storage classification payload is invalid", sequence=sequence)


@dataclass(frozen=True)
class RetirementRecorded:
    event_id: str
    record: str
    path: str
    storage_class: str
    target_digest: str
    target_length: int
    event_sequences: tuple[int, ...]
    reason: str
    reservation_key: str = ""
    retirement_reservation_key: str = ""

    @property
    def event_type(self) -> str:
        return STORAGE_GOVERNOR_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "record": self.record,
            "path": self.path,
            "storage_class": self.storage_class,
            "target_digest": self.target_digest,
            "target_length": self.target_length,
            "event_sequences": list(self.event_sequences),
            "reason": self.reason,
            "reservation_key": self.reservation_key,
            "retirement_reservation_key": self.retirement_reservation_key,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        expected = {
            "event_id",
            "record",
            "path",
            "storage_class",
            "target_digest",
            "target_length",
            "event_sequences",
            "reason",
            "reservation_key",
            "retirement_reservation_key",
            "blob_digest",
            "blob_bytes",
        }
        digest = payload.get("target_digest")
        references = payload.get("event_sequences")
        if (
            set(payload) != expected
            or payload.get("record") not in {"retirement-tombstone", "retirement-complete"}
            or any(
                not isinstance(payload.get(name), str) or not payload[name]
                for name in ("event_id", "path", "storage_class", "reason")
            )
            or payload.get("storage_class") not in {item.value for item in RetirableStorageClass}
            or not isinstance(payload.get("reservation_key"), str)
            or not isinstance(payload.get("retirement_reservation_key"), str)
            or not payload["retirement_reservation_key"]
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(payload.get("target_length"), int)
            or payload["target_length"] < 0
            or not isinstance(references, list)
            or any(not isinstance(item, int) or item < 1 for item in references)
            or references != sorted(set(references))
            or payload.get("blob_digest") != EMPTY_BLOB_DIGEST
            or payload.get("blob_bytes") != 0
        ):
            raise InvalidEventError("storage-governor retirement payload is invalid", sequence=sequence)
