"""Physical authority-space admission and one at-most-once external-effect boundary."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
import uuid
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, TypeVar

from solver.event_store_storage import atomic_write, canonical_bytes, fsync_directory


LEDGER_RECORD_BYTES = 2_048
SCHEMA_VERSION = 1
RECEIPT_TYPE = "write-reservation"
MANIFEST_ROW_ID = "core.canonical-state-replay-restart"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"
CONTROLLED_CRASH_OUTCOMES = {
    "pre-reserve": {"effect_count": 0, "durable_state": "absent"},
    "post-reserve-pre-effect": {"effect_count": 0, "durable_state": "aborted"},
    "post-effect-pre-commit": {"effect_count": 1, "durable_state": "possibly-sent"},
}


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
    effect_fingerprint: str
    identity: Mapping[str, Any]
    need: Capacity
    pool: Pool
    state: ReservationState
    object_slots: tuple[str, ...] = ()
    observation: Mapping[str, Any] | None = None
    boot_id: str = ""


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


def _profile_from(row: Mapping[str, Any]) -> WriteProfile:
    return WriteProfile(*(Capacity(**row[name]) for name in ("ordinary", "shared", "terminal")))


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class WriteAuthority:
    """One locked writer over physically provisioned, non-borrowable capacity pools."""

    def __init__(
        self,
        root: Path,
        profile: WriteProfile,
        *,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self.root = Path(root) / "write-authority"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = self.root / "authority.lock"
        self.lock_path.touch(exist_ok=True, mode=0o600)
        self.profile_path = self.root / "profile.json"
        self.receipts_dir = self.root / "receipts"
        self._profile = profile
        self._hook = hook
        self._boot_id = uuid.uuid4().hex
        with self._locked():
            self._provision_locked()
            self._reconcile_prior_boots_locked()

    @property
    def profile_digest(self) -> str:
        return _digest(self._profile.as_dict())

    def extent_path(self, pool: Pool) -> Path:
        return self.root / f"{Pool(pool).value}.extent"

    def object_path(self, reservation: WriteReservation, index: int = 0) -> Path:
        """Return one precreated object slot already charged to this reservation."""
        try:
            name = reservation.object_slots[index]
        except IndexError as error:
            raise ReservationConflict("reservation has no such precreated object slot") from error
        return self._slots_dir(reservation.pool) / name

    def reserve(
        self,
        key: str,
        identity: Mapping[str, Any],
        need: Capacity,
        *,
        pool: Pool = Pool.SHARED,
    ) -> WriteReservation:
        pool = Pool(pool)
        if not key or need.operations < 3:
            raise ValueError("a reservation needs a key and at least three durability operations")
        fingerprint = _digest(dict(identity))
        self._call_hook("before_reserve")
        with self._locked():
            if current := self._current_locked(key):
                self._assert_same_effect(current, fingerprint)
                return current
            slots = self._available_object_slots_locked(pool, need.objects)
            extent = self.extent_path(pool)
            available_bytes = extent.stat().st_size
            available_operations = self._available_operations_locked(pool)
            if available_bytes < need.bytes or len(slots) < need.objects or available_operations < need.operations:
                self._record_refusal_locked(key, fingerprint, identity, need, pool)
                raise ReservationUnavailable(
                    "whole effect path does not fit reserved bytes, objects, and durability operations"
                )
            reservation = WriteReservation(
                key=key,
                effect_fingerprint=fingerprint,
                identity=dict(identity),
                need=need,
                pool=pool,
                state=ReservationState.RESERVED,
                object_slots=slots,
                boot_id=self._boot_id,
            )
            self._append_locked(reservation)
            try:
                self._shrink_extent_locked(pool, need.bytes)
            except BaseException:
                self._append_locked(WriteReservation(**{**reservation.__dict__, "state": ReservationState.ABORTED}))
                raise
        self._call_hook("after_reserve")
        return reservation

    def start(self, reservation: WriteReservation) -> WriteReservation:
        return self._claim_start(reservation)[0]

    def _claim_start(self, reservation: WriteReservation) -> tuple[WriteReservation, bool]:
        with self._locked():
            current = self._require_current_locked(reservation)
            if current.state is not ReservationState.RESERVED:
                return current, False
            started = WriteReservation(**{**current.__dict__, "state": ReservationState.STARTED})
            self._append_locked(started)
            return started, True

    def commit(self, reservation: WriteReservation, observation: Mapping[str, Any]) -> WriteReservation:
        return self._finish(reservation, ReservationState.COMMITTED, observation)

    def possibly_sent(self, reservation: WriteReservation, reason: str) -> WriteReservation:
        return self._finish(reservation, ReservationState.POSSIBLY_SENT, {"reason": reason})

    def abort(self, reservation: WriteReservation, reason: str) -> WriteReservation:
        return self._finish(reservation, ReservationState.ABORTED, {"reason": reason})

    def record_terminal(
        self,
        key: str,
        identity: Mapping[str, Any],
        need: Capacity,
        *,
        classification: str,
    ) -> WriteReservation:
        reservation = self.reserve(key, identity, need, pool=Pool.TERMINAL)
        if reservation.state is ReservationState.TERMINAL:
            return reservation
        if reservation.state is not ReservationState.RESERVED:
            raise EffectIndeterminate(f"terminal transaction {key!r} is already {reservation.state.value}")
        return self._finish(reservation, ReservationState.TERMINAL, {"classification": classification})

    def current(self, key: str) -> WriteReservation | None:
        with self._locked():
            return self._current_locked(key)

    def trace(self, key: str) -> list[dict[str, Any]]:
        with self._locked():
            return [row for row in self._rows_locked() if row["key"] == key]

    def write_receipt(self, key: str, destination: Path | None = None) -> Path:
        with self._locked():
            current = self._current_locked(key)
            if current is None:
                raise ValueError(f"unknown reservation {key!r}")
            document = self._receipt_document_locked(current)
            document["receipt_digest"] = _digest(document)
        destination = destination or self.receipts_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
        atomic_write(destination, canonical_bytes(document) + b"\n")
        return destination

    def verify_receipt(self, path: Path) -> dict[str, Any]:
        raw = Path(path).read_bytes()
        document = json.loads(raw)
        if raw != canonical_bytes(document) + b"\n":
            raise ValueError("write-reservation receipt is not canonical JSON")
        supplied = document.pop("receipt_digest", None)
        if supplied != _digest(document):
            raise ValueError("write-reservation receipt digest mismatch")
        current = self.current(str(document.get("key", "")))
        if current is None:
            raise ValueError("write-reservation receipt effect identity mismatch")
        with self._locked():
            expected = self._receipt_document_locked(current)
        if document != expected:
            raise ValueError("write-reservation receipt differs from durable authority")
        return document

    def _receipt_document_locked(self, current: WriteReservation) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "receipt_type": RECEIPT_TYPE,
            "profile_digest": self.profile_digest,
            "key": current.key,
            "effect_fingerprint": current.effect_fingerprint,
            "pool": current.pool.value,
            "need": current.need.as_dict(),
            "object_slots": list(current.object_slots),
            "state": current.state.value,
            "trace": [row for row in self._rows_locked() if row["key"] == current.key],
            "extent_remaining": self.extent_path(current.pool).stat().st_size,
            "crash_point_outcomes": CONTROLLED_CRASH_OUTCOMES,
            "manifest_link": {
                "row_id": MANIFEST_ROW_ID,
                "receipt_ref": MANIFEST_RECEIPT_REF,
            },
        }

    def _finish(
        self,
        reservation: WriteReservation,
        state: ReservationState,
        observation: Mapping[str, Any],
    ) -> WriteReservation:
        with self._locked():
            current = self._require_current_locked(reservation)
            if current.state in FINAL_STATES:
                return current
            if state is ReservationState.COMMITTED and current.state is not ReservationState.STARTED:
                raise ReservationConflict("only a started effect can commit")
            if state is ReservationState.POSSIBLY_SENT and current.state is not ReservationState.STARTED:
                raise ReservationConflict("only a started effect can become possibly sent")
            if state is ReservationState.ABORTED and current.state is not ReservationState.RESERVED:
                raise ReservationConflict("only a pre-effect reservation can abort")
            closed = WriteReservation(**{**current.__dict__, "state": state, "observation": dict(observation)})
            self._append_locked(closed)
            return closed

    def _provision_locked(self) -> None:
        encoded_profile = canonical_bytes(self._profile.as_dict()) + b"\n"
        if self.profile_path.exists():
            held = _profile_from(json.loads(self.profile_path.read_text()))
            if held != self._profile:
                raise ReservationConflict("sealed write profile differs from the existing Run profile")
        else:
            atomic_write(self.profile_path, encoded_profile)
        for pool in Pool:
            capacity = getattr(self._profile, pool.value)
            ledger = self._ledger_path(pool)
            if not ledger.exists():
                self._allocate_file(ledger, capacity.operations * LEDGER_RECORD_BYTES, fill=b"\0")
            elif ledger.stat().st_size != capacity.operations * LEDGER_RECORD_BYTES:
                raise ReservationConflict(f"{pool.value} ledger size differs from the sealed profile")
            extent = self.extent_path(pool)
            if not extent.exists():
                self._allocate_file(extent, capacity.bytes, fill=b"\xa5")
            elif extent.stat().st_size > capacity.bytes:
                raise ReservationConflict(f"{pool.value} extent exceeds the sealed profile")
            slots = self._slots_dir(pool)
            slots.mkdir(exist_ok=True, mode=0o700)
            for index in range(capacity.objects):
                (slots / f"{index:04d}.slot").touch(exist_ok=True, mode=0o600)
        fsync_directory(self.root)

    def _reconcile_prior_boots_locked(self) -> None:
        latest = self._latest_by_key_locked()
        for reservation in latest.values():
            if reservation.boot_id == self._boot_id:
                continue
            if reservation.state is ReservationState.RESERVED:
                recovered = WriteReservation(
                    **{
                        **reservation.__dict__,
                        "state": ReservationState.ABORTED,
                        "observation": {"reason": "restart-before-effect"},
                        "boot_id": self._boot_id,
                    }
                )
                self._append_locked(recovered)
            elif reservation.state is ReservationState.STARTED:
                recovered = WriteReservation(
                    **{
                        **reservation.__dict__,
                        "state": ReservationState.POSSIBLY_SENT,
                        "observation": {"reason": "restart-after-effect-admission"},
                        "boot_id": self._boot_id,
                    }
                )
                self._append_locked(recovered)

    def _record_refusal_locked(
        self,
        key: str,
        fingerprint: str,
        identity: Mapping[str, Any],
        need: Capacity,
        pool: Pool,
    ) -> None:
        if self._available_operations_locked(pool) < 1:
            return
        self._append_locked(
            WriteReservation(
                key=key,
                effect_fingerprint=fingerprint,
                identity=dict(identity),
                need=need,
                pool=pool,
                state=ReservationState.REFUSED,
                observation={"reason": "capacity-exhausted"},
                boot_id=self._boot_id,
            )
        )

    def _append_locked(self, reservation: WriteReservation) -> None:
        ledger = self._ledger_path(reservation.pool)
        slot = self._first_empty_record(ledger)
        if slot is None:
            raise ReservationUnavailable(f"{reservation.pool.value} durability-operation reserve is exhausted")
        rows = self._rows_locked()
        row = {
            "schema_version": SCHEMA_VERSION,
            "ordinal": max((int(item["ordinal"]) for item in rows), default=0) + 1,
            "key": reservation.key,
            "effect_fingerprint": reservation.effect_fingerprint,
            "identity": dict(reservation.identity),
            "need": reservation.need.as_dict(),
            "pool": reservation.pool.value,
            "state": reservation.state.value,
            "object_slots": list(reservation.object_slots),
            "observation": dict(reservation.observation) if reservation.observation is not None else None,
            "boot_id": reservation.boot_id,
        }
        encoded = canonical_bytes(row) + b"\n"
        if len(encoded) > LEDGER_RECORD_BYTES:
            raise ReservationUnavailable("authority record exceeds its preallocated durable slot")
        descriptor = os.open(ledger, os.O_RDWR)
        try:
            os.pwrite(descriptor, encoded.ljust(LEDGER_RECORD_BYTES, b"\0"), slot * LEDGER_RECORD_BYTES)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _rows_locked(self) -> list[dict[str, Any]]:
        rows = []
        for pool in Pool:
            ledger = self._ledger_path(pool)
            if not ledger.exists():
                continue
            raw = ledger.read_bytes()
            for offset in range(0, len(raw), LEDGER_RECORD_BYTES):
                encoded = raw[offset : offset + LEDGER_RECORD_BYTES].rstrip(b"\0")
                if not encoded:
                    continue
                try:
                    row = json.loads(encoded)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ReservationConflict(f"{pool.value} authority ledger is damaged") from error
                if row.get("schema_version") != SCHEMA_VERSION:
                    raise ReservationConflict("authority ledger schema is unsupported")
                rows.append(row)
        return sorted(rows, key=lambda row: int(row["ordinal"]))

    def _latest_by_key_locked(self) -> dict[str, WriteReservation]:
        latest: dict[str, WriteReservation] = {}
        for row in self._rows_locked():
            latest[str(row["key"])] = self._from_row(row)
        return latest

    def _current_locked(self, key: str) -> WriteReservation | None:
        return self._latest_by_key_locked().get(key)

    def _require_current_locked(self, proposed: WriteReservation) -> WriteReservation:
        current = self._current_locked(proposed.key)
        if current is None:
            raise ReservationConflict(f"unknown reservation {proposed.key!r}")
        self._assert_same_effect(current, proposed.effect_fingerprint)
        return current

    @staticmethod
    def _assert_same_effect(current: WriteReservation, fingerprint: str) -> None:
        if current.effect_fingerprint != fingerprint:
            raise ReservationConflict("idempotency key is already bound to a different semantic effect")

    @staticmethod
    def _from_row(row: Mapping[str, Any]) -> WriteReservation:
        return WriteReservation(
            key=str(row["key"]),
            effect_fingerprint=str(row["effect_fingerprint"]),
            identity=dict(row["identity"]),
            need=Capacity(**row["need"]),
            pool=Pool(row["pool"]),
            state=ReservationState(row["state"]),
            object_slots=tuple(row["object_slots"]),
            observation=dict(row["observation"]) if row["observation"] is not None else None,
            boot_id=str(row["boot_id"]),
        )

    def _available_operations_locked(self, pool: Pool) -> int:
        ledger = self._ledger_path(pool)
        raw = ledger.read_bytes()
        return sum(
            not raw[offset : offset + LEDGER_RECORD_BYTES].rstrip(b"\0")
            for offset in range(0, len(raw), LEDGER_RECORD_BYTES)
        )

    def _available_object_slots_locked(self, pool: Pool, count: int) -> tuple[str, ...]:
        claimed = {
            slot
            for reservation in self._latest_by_key_locked().values()
            if reservation.pool is pool
            for slot in reservation.object_slots
        }
        available = [path for path in sorted(self._slots_dir(pool).glob("*.slot")) if path.name not in claimed]
        return tuple(path.name for path in available[:count])

    def _shrink_extent_locked(self, pool: Pool, amount: int) -> None:
        path = self.extent_path(pool)
        size = path.stat().st_size
        if size < amount:
            raise ReservationUnavailable(f"{pool.value} physical byte reserve is exhausted")
        with path.open("r+b") as extent:
            extent.truncate(size - amount)
            extent.flush()
            os.fsync(extent.fileno())
        fsync_directory(path.parent)

    @staticmethod
    def _allocate_file(path: Path, size: int, *, fill: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            block = fill * min(65_536, size)
            remaining = size
            while remaining:
                written = min(len(block), remaining)
                output.write(block[:written])
                remaining -= written
            output.flush()
            os.fsync(output.fileno())
        path.chmod(0o600)

    def _first_empty_record(self, ledger: Path) -> int | None:
        raw = ledger.read_bytes()
        for index, offset in enumerate(range(0, len(raw), LEDGER_RECORD_BYTES)):
            if not raw[offset : offset + LEDGER_RECORD_BYTES].rstrip(b"\0"):
                return index
        return None

    def _ledger_path(self, pool: Pool) -> Path:
        return self.root / f"{pool.value}.ledger"

    def _slots_dir(self, pool: Pool) -> Path:
        return self.root / f"{pool.value}-objects"

    def _call_hook(self, phase: str) -> None:
        if self._hook is not None:
            self._hook(phase)

    @contextmanager
    def _locked(self):
        with self.lock_path.open("r+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


Result = TypeVar("Result")


class ReservedEffect:
    """Invoke one effect only after its complete durable outcome path is admitted."""

    def __init__(self, authority: WriteAuthority) -> None:
        self._authority = authority

    def execute(
        self,
        key: str,
        identity: Mapping[str, Any],
        need: Capacity,
        effect: Callable[[], Result],
        *,
        encode: Callable[[Result], Mapping[str, Any]],
        decode: Callable[[Mapping[str, Any]], Result],
        observe: Callable[[Result], None] | None = None,
    ) -> Result:
        reservation = self._authority.reserve(key, identity, need)
        if reservation.state is ReservationState.COMMITTED:
            return decode(reservation.observation or {})
        if reservation.state is ReservationState.STARTED:
            reservation = self._await_terminal(reservation)
            if reservation.state is ReservationState.COMMITTED:
                return decode(reservation.observation or {})
        if reservation.state is not ReservationState.RESERVED:
            raise EffectIndeterminate(f"effect {key!r} is {reservation.state.value} and cannot be repeated")
        started, acquired = self._authority._claim_start(reservation)
        if not acquired:
            started = self._await_terminal(started)
            if started.state is ReservationState.COMMITTED:
                return decode(started.observation or {})
            raise EffectIndeterminate(f"effect {key!r} was not admitted exactly once")
        try:
            result = effect()
            self._authority._call_hook("after_effect")
            if observe is not None:
                observe(result)
            self._authority.commit(started, encode(result))
            return result
        except BaseException as error:
            current = self._authority.current(key)
            if current is not None and current.state is ReservationState.STARTED:
                self._authority.possibly_sent(started, type(error).__name__)
            raise

    def _await_terminal(self, reservation: WriteReservation) -> WriteReservation:
        deadline = time.monotonic() + 5.0
        current = reservation
        while current.state in {ReservationState.RESERVED, ReservationState.STARTED} and time.monotonic() < deadline:
            time.sleep(0.005)
            current = self._authority.current(reservation.key) or current
        return current


DEFAULT_WRITE_PROFILE = WriteProfile(
    ordinary=Capacity(bytes=0, objects=0, operations=0),
    shared=Capacity(bytes=512 * 1_024, objects=16, operations=64),
    terminal=Capacity(bytes=64 * 1_024, objects=2, operations=16),
)


def manifest_receipt(path: Path) -> dict[str, str]:
    """Verify and describe one reservation proof for the provisional manifest."""
    receipt_path = Path(path)
    try:
        profile = _profile_from(json.loads((receipt_path.parents[1] / "profile.json").read_text()))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("write-reservation receipt does not identify a sealed profile") from error
    authority = WriteAuthority(receipt_path.parents[2], profile)
    authority.verify_receipt(receipt_path)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    }


__all__ = [
    "Capacity",
    "CONTROLLED_CRASH_OUTCOMES",
    "DEFAULT_WRITE_PROFILE",
    "EffectIndeterminate",
    "MANIFEST_RECEIPT_REF",
    "MANIFEST_ROW_ID",
    "Pool",
    "ReservationConflict",
    "ReservationState",
    "ReservationUnavailable",
    "ReservedEffect",
    "WriteAuthority",
    "WriteProfile",
    "WriteReservation",
    "manifest_receipt",
]
