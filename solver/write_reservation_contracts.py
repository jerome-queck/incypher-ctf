"""Public contracts for authority-space reservation and at-most-once effects."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from solver.event_store_storage import canonical_bytes


LEDGER_RECORD_BYTES = 2_048
SCHEMA_VERSION = 1
RECEIPT_TYPE = "write-reservation"
MANIFEST_ROW_ID = "core.canonical-state-replay-restart"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"


@dataclass(frozen=True)
class Capacity:
    bytes: int
    objects: int
    operations: int
    create: int = 0
    append: int = 0
    rename: int = 0
    unlink: int = 0
    durability: int = 0

    def __post_init__(self) -> None:
        if min(self._values().values()) < 0:
            raise ValueError("reservation capacity cannot be negative")

    def as_dict(self) -> dict[str, int]:
        values = self._values()
        if not any(values[name] for name in ("create", "append", "rename", "unlink", "durability")):
            return {name: values[name] for name in ("bytes", "objects", "operations")}
        return values

    def _values(self) -> dict[str, int]:
        return {
            "bytes": self.bytes,
            "objects": self.objects,
            "operations": self.operations,
            "create": self.create,
            "append": self.append,
            "rename": self.rename,
            "unlink": self.unlink,
            "durability": self.durability,
        }

    def fits(self, need: Capacity) -> bool:
        return all(self._values()[name] >= value for name, value in need._values().items())

    def __sub__(self, other: Capacity) -> Capacity:
        return Capacity(**{name: value - other._values()[name] for name, value in self._values().items()})

    def __add__(self, other: Capacity) -> Capacity:
        return Capacity(**{name: value + other._values()[name] for name, value in self._values().items()})


@dataclass(frozen=True)
class WriteProfile:
    ordinary: Capacity
    shared: Capacity
    terminal: Capacity
    recovery: Capacity = Capacity(0, 0, 0)

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {
            "ordinary": self.ordinary.as_dict(),
            "shared": self.shared.as_dict(),
            "terminal": self.terminal.as_dict(),
            "recovery": self.recovery.as_dict(),
        }


@dataclass(frozen=True)
class EffectIdentity:
    operation: str
    subject: str
    payload_digest: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "operation": self.operation,
            "subject": self.subject,
            "payload_digest": self.payload_digest,
        }

    @property
    def fingerprint(self) -> str:
        return digest(self.as_dict())


class Pool(str, Enum):
    ORDINARY = "ordinary"
    SHARED = "shared"
    TERMINAL = "terminal"
    RECOVERY = "recovery"


class ReservationState(str, Enum):
    RESERVED = "reserved"
    STARTED = "started"
    COMMITTED = "committed"
    ABORTED = "aborted"
    POSSIBLY_SENT = "possibly-sent"
    REFUSED = "refused"
    TERMINAL = "terminal"
    RELEASED = "released"


class RetentionPolicy(str, Enum):
    RELEASE = "release"
    RECORD = "retain-record"
    RECEIPT = "retain-receipt"

    @property
    def retains_object(self) -> bool:
        return self is not RetentionPolicy.RELEASE

    @property
    def requires_receipt(self) -> bool:
        return self is RetentionPolicy.RECEIPT


FINAL_STATES = frozenset(
    {
        ReservationState.COMMITTED,
        ReservationState.ABORTED,
        ReservationState.POSSIBLY_SENT,
        ReservationState.REFUSED,
        ReservationState.TERMINAL,
        ReservationState.RELEASED,
    }
)


@dataclass(frozen=True)
class WriteReservation:
    key: str
    identity: EffectIdentity
    effect_fingerprint: str
    need: Capacity
    pool: Pool
    state: ReservationState
    object_slots: tuple[str, ...] = ()
    observation: Mapping[str, Any] | None = None
    boot_id: str = ""
    retention: RetentionPolicy = RetentionPolicy.RELEASE
    grant_remaining_bytes: int = 0
    grant_headroom: Mapping[str, Capacity] | None = None

    def transitioned(
        self,
        state: ReservationState,
        observation: Mapping[str, Any] | None = None,
        *,
        boot_id: str | None = None,
        retention: RetentionPolicy | None = None,
    ) -> WriteReservation:
        return replace(
            self,
            state=state,
            observation=dict(observation) if observation is not None else None,
            boot_id=boot_id or self.boot_id,
            retention=self.retention if retention is None else retention,
        )


class ReservationError(RuntimeError):
    pass


class ReservationConflict(ReservationError):
    pass


class ReservationUnavailable(ReservationError):
    def __init__(self, message: str, *, state: ReservationState = ReservationState.REFUSED) -> None:
        super().__init__(message)
        self.state = state


class EffectIndeterminate(ReservationError):
    pass


def digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def profile_from(row: Mapping[str, Any]) -> WriteProfile:
    zero = Capacity(0, 0, 0).as_dict()
    return WriteProfile(*(Capacity(**row.get(name, zero)) for name in ("ordinary", "shared", "terminal", "recovery")))


DEFAULT_WRITE_PROFILE = WriteProfile(
    ordinary=Capacity(bytes=0, objects=0, operations=0),
    shared=Capacity(bytes=512 * 1_024, objects=16, operations=256),
    terminal=Capacity(bytes=64 * 1_024, objects=2, operations=16),
)
