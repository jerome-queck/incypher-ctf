"""Preallocated files and fixed durable records behind write admission."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solver.event_store_storage import atomic_write, canonical_bytes, fsync_directory
from solver.write_reservation_contracts import (
    LEDGER_RECORD_BYTES,
    SCHEMA_VERSION,
    Capacity,
    EffectIdentity,
    Pool,
    ReservationConflict,
    ReservationState,
    ReservationUnavailable,
    RetentionPolicy,
    WriteProfile,
    WriteReservation,
    profile_from,
)


_WRITER_REGISTRY_LOCK = threading.Lock()
_HELD_WRITERS: set[Path] = set()


@dataclass(frozen=True)
class AuthoritySnapshot:
    reservation: WriteReservation
    trace: tuple[dict[str, Any], ...]


class AuthorityStorage:
    """One flock-serialized store whose future records and objects already exist."""

    def __init__(self, run_root: Path, profile: WriteProfile, *, provision: bool = True) -> None:
        self.root = Path(run_root) / "write-authority"
        self.lock_path = self.root / "authority.lock"
        if provision:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            self.lock_path.touch(exist_ok=True, mode=0o600)
        elif not self.root.is_dir() or not self.lock_path.is_file():
            raise ReservationConflict("sealed write authority is unavailable")
        self.profile_path = self.root / "profile.json"
        self.profile = profile
        with self.locked():
            if provision:
                self._provision_locked()
            else:
                self._validate_locked()

    def acquire_writer(self) -> int:
        path = self.root.resolve()
        with _WRITER_REGISTRY_LOCK:
            if path in _HELD_WRITERS:
                raise ReservationConflict("this Run already has a live canonical writer")
            descriptor = os.open(self.root / "writer.lock", os.O_RDWR | os.O_CREAT, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                os.close(descriptor)
                raise ReservationConflict("this Run already has a live canonical writer") from error
            _HELD_WRITERS.add(path)
        return descriptor

    def release_writer(self, descriptor: int) -> None:
        path = self.root.resolve()
        with _WRITER_REGISTRY_LOCK:
            if path not in _HELD_WRITERS:
                return
            _HELD_WRITERS.remove(path)
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def extent_path(self, pool: Pool) -> Path:
        return self.root / f"{Pool(pool).value}.extent"

    def object_path(self, reservation: WriteReservation, index: int = 0) -> Path:
        try:
            name = reservation.object_slots[index]
        except IndexError as error:
            raise ReservationConflict("reservation has no such precreated object slot") from error
        return self._slots_dir(reservation.pool) / name

    def snapshot(self, key: str) -> AuthoritySnapshot | None:
        with self.locked():
            reservation = self.latest_locked().get(key)
            if reservation is None:
                return None
            trace = tuple(row for row in self.rows_locked() if row["key"] == key)
            return AuthoritySnapshot(
                reservation=reservation,
                trace=trace,
            )

    def append_locked(self, reservation: WriteReservation) -> None:
        ledger = self._ledger_path(reservation.pool)
        slot = self._first_empty_record(ledger)
        if slot is None:
            raise ReservationUnavailable(f"{reservation.pool.value} durability-operation reserve is exhausted")
        row = {
            "schema_version": SCHEMA_VERSION,
            "ordinal": max((int(item["ordinal"]) for item in self.rows_locked()), default=0) + 1,
            "key": reservation.key,
            "effect_fingerprint": reservation.effect_fingerprint,
            "identity": reservation.identity.as_dict(),
            "need": reservation.need.as_dict(),
            "pool": reservation.pool.value,
            "state": reservation.state.value,
            "object_slots": list(reservation.object_slots),
            "observation": dict(reservation.observation) if reservation.observation is not None else None,
            "boot_id": reservation.boot_id,
            "retention": reservation.retention.value,
            "grant_remaining_bytes": reservation.grant_remaining_bytes,
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

    def rows_locked(self) -> list[dict[str, Any]]:
        rows = []
        for pool in Pool:
            ledger = self._ledger_path(pool)
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

    def latest_locked(self) -> dict[str, WriteReservation]:
        latest = {}
        for row in self.rows_locked():
            identity = row["identity"]
            latest[str(row["key"])] = WriteReservation(
                key=str(row["key"]),
                identity=EffectIdentity(
                    operation=str(identity["operation"]),
                    subject=str(identity["subject"]),
                    payload_digest=str(identity["payload_digest"]),
                ),
                effect_fingerprint=str(row["effect_fingerprint"]),
                need=Capacity(**row["need"]),
                pool=Pool(row["pool"]),
                state=ReservationState(row["state"]),
                object_slots=tuple(row["object_slots"]),
                observation=dict(row["observation"]) if row["observation"] is not None else None,
                boot_id=str(row["boot_id"]),
                retention=RetentionPolicy(row.get("retention", RetentionPolicy.RELEASE.value)),
                grant_remaining_bytes=int(row.get("grant_remaining_bytes", 0)),
            )
        return latest

    def available_operations_locked(self, pool: Pool) -> int:
        raw = self._ledger_path(pool).read_bytes()
        return sum(
            not raw[offset : offset + LEDGER_RECORD_BYTES].rstrip(b"\0")
            for offset in range(0, len(raw), LEDGER_RECORD_BYTES)
        )

    def available_object_slots_locked(self, pool: Pool, count: int) -> tuple[str, ...]:
        if count == 0:
            return ()
        claimed = {
            slot
            for reservation in self.latest_locked().values()
            if reservation.pool is pool
            and (
                reservation.state in {ReservationState.RESERVED, ReservationState.STARTED}
                or reservation.retention.retains_object
            )
            for slot in reservation.object_slots
        }
        available = []
        for path in sorted(self._slots_dir(pool).glob("*.slot")):
            if path.name in claimed or not self._clear_slot(path):
                continue
            available.append(path.name)
            if len(available) == count:
                break
        return tuple(available)

    def release_object_slots_locked(self, reservation: WriteReservation) -> bool:
        released = True
        for name in reservation.object_slots:
            released = self._clear_slot(self._slots_dir(reservation.pool) / name) and released
        return released

    def write_object(self, reservation: WriteReservation, body: bytes) -> Path:
        if not reservation.retention.retains_object or not reservation.object_slots:
            raise ReservationConflict("reservation retained no object capacity for this record")
        if len(body) > reservation.need.bytes:
            raise ReservationUnavailable("record exceeds its reserved byte capacity")
        path = self.object_path(reservation)
        with self.locked():
            current = self.latest_locked().get(reservation.key)
            if (
                current is None
                or current.effect_fingerprint != reservation.effect_fingerprint
                or not current.retention.retains_object
                or current.object_slots != reservation.object_slots
            ):
                raise ReservationConflict("record reservation no longer matches durable authority")
            descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC)
            try:
                remaining = memoryview(body)
                while remaining:
                    remaining = remaining[os.write(descriptor, remaining) :]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            fsync_directory(path.parent)
        return path

    def shrink_extent_locked(self, pool: Pool, amount: int) -> None:
        path = self.extent_path(pool)
        size = path.stat().st_size
        if size < amount:
            raise ReservationUnavailable(f"{pool.value} physical byte reserve is exhausted")
        with path.open("r+b") as extent:
            extent.truncate(size - amount)
            extent.flush()
            os.fsync(extent.fileno())
        fsync_directory(path.parent)

    def replenish_extent_locked(self, pool: Pool, amount: int) -> None:
        path = self.extent_path(pool)
        original = None
        try:
            limit = getattr(self.profile, pool.value).bytes
            original = path.stat().st_size
            wanted = min(amount, limit - original)
            if wanted <= 0:
                return
            with path.open("ab") as extent:
                remaining = wanted
                block = b"\xa5" * min(65_536, wanted)
                while remaining:
                    written = min(len(block), remaining)
                    extent.write(block[:written])
                    remaining -= written
                extent.flush()
                os.fsync(extent.fileno())
            fsync_directory(path.parent)
        except OSError:
            if original is None:
                return
            try:
                with path.open("r+b") as extent:
                    extent.truncate(original)
                    extent.flush()
                    os.fsync(extent.fileno())
            except OSError:
                pass

    @contextmanager
    def locked(self):
        with self.lock_path.open("r+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _provision_locked(self) -> None:
        encoded_profile = canonical_bytes(self.profile.as_dict()) + b"\n"
        if self.profile_path.exists():
            if profile_from(json.loads(self.profile_path.read_text())) != self.profile:
                raise ReservationConflict("sealed write profile differs from the existing Run profile")
        else:
            atomic_write(self.profile_path, encoded_profile)
        for pool in Pool:
            capacity = getattr(self.profile, pool.value)
            ledger = self._ledger_path(pool)
            if not ledger.exists():
                self._allocate_file(ledger, capacity.operations * LEDGER_RECORD_BYTES, b"\0")
            elif ledger.stat().st_size != capacity.operations * LEDGER_RECORD_BYTES:
                raise ReservationConflict(f"{pool.value} ledger size differs from the sealed profile")
            extent = self.extent_path(pool)
            if not extent.exists():
                self._allocate_file(extent, capacity.bytes, b"\xa5")
            elif extent.stat().st_size > capacity.bytes:
                raise ReservationConflict(f"{pool.value} extent exceeds the sealed profile")
            slots = self._slots_dir(pool)
            slots.mkdir(exist_ok=True, mode=0o700)
            for index in range(capacity.objects):
                (slots / f"{index:04d}.slot").touch(exist_ok=True, mode=0o600)
            fsync_directory(slots)
        fsync_directory(self.root)

    def _validate_locked(self) -> None:
        try:
            held = profile_from(json.loads(self.profile_path.read_text()))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
            raise ReservationConflict("sealed write profile is unavailable") from error
        if held != self.profile:
            raise ReservationConflict("sealed write profile differs from the requested profile")
        for pool in Pool:
            capacity = getattr(self.profile, pool.value)
            if self._ledger_path(pool).stat().st_size != capacity.operations * LEDGER_RECORD_BYTES:
                raise ReservationConflict(f"{pool.value} ledger size differs from the sealed profile")
            if self.extent_path(pool).stat().st_size > capacity.bytes:
                raise ReservationConflict(f"{pool.value} extent exceeds the sealed profile")

    @staticmethod
    def _clear_slot(path: Path) -> bool:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            fsync_directory(path.parent)
        except OSError:
            return False
        return True

    @staticmethod
    def _allocate_file(path: Path, size: int, fill: bytes) -> None:
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

    @staticmethod
    def _first_empty_record(ledger: Path) -> int | None:
        raw = ledger.read_bytes()
        for index, offset in enumerate(range(0, len(raw), LEDGER_RECORD_BYTES)):
            if not raw[offset : offset + LEDGER_RECORD_BYTES].rstrip(b"\0"):
                return index
        return None

    def _ledger_path(self, pool: Pool) -> Path:
        return self.root / f"{pool.value}.ledger"

    def _slots_dir(self, pool: Pool) -> Path:
        return self.root / f"{pool.value}-objects"
