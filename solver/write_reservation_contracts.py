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

    def __post_init__(self) -> None:
        if min(self.bytes, self.objects, self.operations) < 0:
            raise ValueError("reservation capacity cannot be negative")

    def as_dict(self) -> dict[str, int]:
        return {"bytes": self.bytes, "objects": self.objects, "operations": self.operations}


@dataclass(frozen=True)
class WriteProfile:
    ordinary: Capacity
    shared: Capacity
    terminal: Capacity

    def as_dict(self) -> dict[str, dict[str, int]]:
        return {
            "ordinary": self.ordinary.as_dict(),
            "shared": self.shared.as_dict(),
            "terminal": self.terminal.as_dict(),
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


class ReservationState(str, Enum):
    RESERVED = "reserved"
    STARTED = "started"
    COMMITTED = "committed"
    ABORTED = "aborted"
    POSSIBLY_SENT = "possibly-sent"
    REFUSED = "refused"
    TERMINAL = "terminal"


FINAL_STATES = frozenset(
    {
        ReservationState.COMMITTED,
        ReservationState.ABORTED,
        ReservationState.POSSIBLY_SENT,
        ReservationState.REFUSED,
        ReservationState.TERMINAL,
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
    retain_objects: bool = False

    def transitioned(
        self,
        state: ReservationState,
        observation: Mapping[str, Any] | None = None,
        *,
        boot_id: str | None = None,
        retain_objects: bool | None = None,
    ) -> WriteReservation:
        return replace(
            self,
            state=state,
            observation=dict(observation) if observation is not None else None,
            boot_id=boot_id or self.boot_id,
            retain_objects=self.retain_objects if retain_objects is None else retain_objects,
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
    return WriteProfile(*(Capacity(**row[name]) for name in ("ordinary", "shared", "terminal")))


DEFAULT_WRITE_PROFILE = WriteProfile(
    ordinary=Capacity(bytes=0, objects=0, operations=0),
    shared=Capacity(bytes=512 * 1_024, objects=16, operations=256),
    terminal=Capacity(bytes=64 * 1_024, objects=2, operations=16),
)
