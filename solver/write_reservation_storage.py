"""Preallocated files and fixed durable records behind write admission."""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
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
    WriteProfile,
    WriteReservation,
    profile_from,
)


class AuthorityStorage:
    """One flock-serialized store whose future records and objects already exist."""

    def __init__(self, run_root: Path, profile: WriteProfile) -> None:
        self.root = Path(run_root) / "write-authority"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock_path = self.root / "authority.lock"
        self.lock_path.touch(exist_ok=True, mode=0o600)
        self.profile_path = self.root / "profile.json"
        self.receipts_dir = self.root / "receipts"
        self.profile = profile
        with self.locked():
            self._provision_locked()

    def extent_path(self, pool: Pool) -> Path:
        return self.root / f"{Pool(pool).value}.extent"

    def object_path(self, reservation: WriteReservation, index: int = 0) -> Path:
        try:
            name = reservation.object_slots[index]
        except IndexError as error:
            raise ReservationConflict("reservation has no such precreated object slot") from error
        return self._slots_dir(reservation.pool) / name

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
            )
        return latest

    def available_operations_locked(self, pool: Pool) -> int:
        raw = self._ledger_path(pool).read_bytes()
        return sum(
            not raw[offset : offset + LEDGER_RECORD_BYTES].rstrip(b"\0")
            for offset in range(0, len(raw), LEDGER_RECORD_BYTES)
        )

    def available_object_slots_locked(self, pool: Pool, count: int) -> tuple[str, ...]:
        claimed = {
            slot
            for reservation in self.latest_locked().values()
            if reservation.pool is pool and reservation.state in {ReservationState.RESERVED, ReservationState.STARTED}
            for slot in reservation.object_slots
        }
        available = [path for path in sorted(self._slots_dir(pool).glob("*.slot")) if path.name not in claimed]
        return tuple(path.name for path in available[:count])

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
