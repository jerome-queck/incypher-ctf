"""Public authority-space reservation and one at-most-once effect boundary."""

from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TypeVar

from solver.redaction import Redactor
from solver.write_reservation_contracts import (
    DEFAULT_WRITE_PROFILE,
    FINAL_STATES,
    Capacity,
    EffectIdentity,
    EffectIndeterminate,
    Pool,
    ReservationConflict,
    ReservationError,
    ReservationState,
    ReservationUnavailable,
    RetentionPolicy,
    WriteProfile,
    WriteReservation,
    digest,
)
from solver.write_reservation_storage import AuthorityStorage


MAX_OBSERVATION_TEXT = 512


def _bounded_text(value: str, redactor: Redactor) -> str:
    sanitized = redactor.redact(value.encode())
    if len(sanitized) <= MAX_OBSERVATION_TEXT:
        return sanitized.decode("utf-8", "replace")
    body_digest = hashlib.sha256(sanitized).hexdigest()
    head = sanitized[:192].decode("utf-8", "replace")
    tail = sanitized[-64:].decode("utf-8", "replace")
    return f"{head}[truncated:{len(sanitized)}:{body_digest}]{tail}"


def _bounded(value: Any, redactor: Redactor) -> Any:
    if isinstance(value, str):
        return _bounded_text(value, redactor)
    if isinstance(value, Mapping):
        return {str(key): _bounded(item, redactor) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bounded(item, redactor) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _bounded_text(str(value), redactor)


class WriteAuthority:
    """Own effect admission, lifecycle, restart reconciliation, and physical pools."""

    def __init__(
        self,
        root: Path,
        profile: WriteProfile,
        *,
        redactor: Redactor | None = None,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self._storage = AuthorityStorage(root, profile)
        self._writer_descriptor: int | None = None
        self._profile = profile
        self._redactor = redactor or Redactor({})
        self._hook = hook
        self._boot_id = uuid.uuid4().hex
        self._writer_descriptor = self._storage.acquire_writer()
        try:
            with self._storage.locked():
                self._reconcile_prior_boots_locked()
                retained = [
                    reservation.key
                    for reservation in self._storage.latest_locked().values()
                    if reservation.retention.requires_receipt and reservation.state in FINAL_STATES
                ]
            for key in retained:
                self.write_receipt(key)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._writer_descriptor is None:
            return
        descriptor, self._writer_descriptor = self._writer_descriptor, None
        self._storage.release_writer(descriptor)

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, OSError):
            pass

    @property
    def profile_digest(self) -> str:
        return digest(self._profile.as_dict())

    def extent_path(self, pool: Pool) -> Path:
        return self._storage.extent_path(pool)

    def object_path(self, reservation: WriteReservation, index: int = 0) -> Path:
        return self._storage.object_path(reservation, index)

    def persist_reserved_record(self, reservation: WriteReservation, body: bytes) -> Path:
        return self._storage.write_object(reservation, body)

    def reserve(
        self,
        key: str,
        identity: EffectIdentity,
        need: Capacity,
        *,
        pool: Pool = Pool.SHARED,
        retention: RetentionPolicy = RetentionPolicy.RELEASE,
    ) -> WriteReservation:
        pool = Pool(pool)
        retention = RetentionPolicy(retention)
        if not key or need.operations < 3:
            raise ValueError("a reservation needs a key and at least three durability operations")
        self._call_hook("before_reserve")
        with self._storage.locked():
            if current := self._current_locked(key):
                self._assert_same_effect(current, identity.fingerprint)
                if current.state not in {ReservationState.ABORTED, ReservationState.REFUSED} and (
                    current.retention is not retention
                ):
                    raise ReservationConflict("idempotency key changed its reserved-record policy")
                return current
            slots = self._storage.available_object_slots_locked(pool, need.objects)
            extent = self.extent_path(pool)
            if (
                extent.stat().st_size < need.bytes
                or len(slots) < need.objects
                or self._storage.available_operations_locked(pool) < need.operations
            ):
                self._record_refusal_locked(key, identity, need, pool)
                raise ReservationUnavailable(
                    "whole effect path does not fit reserved bytes, objects, and durability operations"
                )
            reservation = WriteReservation(
                key=key,
                identity=self._sanitized_identity(identity),
                effect_fingerprint=identity.fingerprint,
                need=need,
                pool=pool,
                state=ReservationState.RESERVED,
                object_slots=slots,
                boot_id=self._boot_id,
                retention=retention,
                grant_remaining_bytes=extent.stat().st_size - need.bytes,
            )
            self._storage.append_locked(reservation)
            try:
                self._storage.shrink_extent_locked(pool, need.bytes)
            except BaseException:
                aborted = reservation.transitioned(ReservationState.ABORTED, retention=RetentionPolicy.RELEASE)
                self._storage.append_locked(aborted)
                if self._storage.release_object_slots_locked(aborted):
                    self._storage.replenish_extent_locked(pool, need.bytes)
                raise
        self._call_hook("after_reserve")
        return reservation

    def start(self, reservation: WriteReservation) -> WriteReservation:
        return self._claim_start(reservation)[0]

    def commit(
        self,
        reservation: WriteReservation,
        observation: Mapping[str, Any],
    ) -> WriteReservation:
        return self._finish(reservation, ReservationState.COMMITTED, observation)

    def possibly_sent(self, reservation: WriteReservation, reason: str) -> WriteReservation:
        return self._finish(reservation, ReservationState.POSSIBLY_SENT, {"reason": reason})

    def abort(self, reservation: WriteReservation, reason: str) -> WriteReservation:
        return self._finish(reservation, ReservationState.ABORTED, {"reason": reason})

    def record_terminal(
        self,
        key: str,
        identity: EffectIdentity,
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
        with self._storage.locked():
            return self._current_locked(key)

    def trace(self, key: str) -> list[dict[str, Any]]:
        with self._storage.locked():
            return [row for row in self._storage.rows_locked() if row["key"] == key]

    def write_receipt(self, key: str, destination: Path | None = None) -> Path:
        from solver.write_reservation_receipt import write_receipt

        return write_receipt(self, key, destination)

    def verify_receipt(self, path: Path) -> dict[str, Any]:
        from solver.write_reservation_receipt import verify_receipt

        return verify_receipt(self, path)

    def _claim_start(self, reservation: WriteReservation) -> tuple[WriteReservation, bool]:
        with self._storage.locked():
            current = self._require_current_locked(reservation)
            if current.state is not ReservationState.RESERVED:
                return current, False
            started = current.transitioned(ReservationState.STARTED)
            self._storage.append_locked(started)
            return started, True

    def _finish(
        self,
        reservation: WriteReservation,
        state: ReservationState,
        observation: Mapping[str, Any],
    ) -> WriteReservation:
        with self._storage.locked():
            current = self._require_current_locked(reservation)
            if current.state in FINAL_STATES:
                return current
            allowed = {
                ReservationState.COMMITTED: ReservationState.STARTED,
                ReservationState.POSSIBLY_SENT: ReservationState.STARTED,
                ReservationState.ABORTED: ReservationState.RESERVED,
                ReservationState.TERMINAL: ReservationState.RESERVED,
            }
            if allowed.get(state) is not current.state:
                raise ReservationConflict(f"{current.state.value} cannot transition to {state.value}")
            retention = RetentionPolicy.RELEASE if state is ReservationState.ABORTED else current.retention
            closed = current.transitioned(
                state,
                _bounded(observation, self._redactor),
                retention=retention,
            )
            self._storage.append_locked(closed)
            released = not closed.retention.retains_object and self._storage.release_object_slots_locked(closed)
            if released:
                self._storage.replenish_extent_locked(closed.pool, closed.need.bytes)
            return closed

    def _reconcile_prior_boots_locked(self) -> None:
        for reservation in self._storage.latest_locked().values():
            if reservation.boot_id == self._boot_id:
                continue
            if reservation.state is ReservationState.RESERVED:
                recovered = reservation.transitioned(
                    ReservationState.ABORTED,
                    {"reason": "restart-before-effect"},
                    boot_id=self._boot_id,
                    retention=RetentionPolicy.RELEASE,
                )
                self._storage.append_locked(recovered)
                if self._storage.release_object_slots_locked(recovered):
                    self._storage.replenish_extent_locked(recovered.pool, recovered.need.bytes)
            elif reservation.state is ReservationState.STARTED:
                recovered = reservation.transitioned(
                    ReservationState.POSSIBLY_SENT,
                    {"reason": "restart-after-effect-admission"},
                    boot_id=self._boot_id,
                )
                self._storage.append_locked(recovered)
                if not recovered.retention.retains_object:
                    if self._storage.release_object_slots_locked(recovered):
                        self._storage.replenish_extent_locked(recovered.pool, recovered.need.bytes)

    def _record_refusal_locked(
        self,
        key: str,
        identity: EffectIdentity,
        need: Capacity,
        pool: Pool,
    ) -> None:
        if self._storage.available_operations_locked(pool) < 1:
            return
        self._storage.append_locked(
            WriteReservation(
                key=key,
                identity=self._sanitized_identity(identity),
                effect_fingerprint=identity.fingerprint,
                need=need,
                pool=pool,
                state=ReservationState.REFUSED,
                observation={"reason": "capacity-exhausted"},
                boot_id=self._boot_id,
            )
        )

    def _current_locked(self, key: str) -> WriteReservation | None:
        return self._storage.latest_locked().get(key)

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

    def _sanitized_identity(self, identity: EffectIdentity) -> EffectIdentity:
        return EffectIdentity(
            operation=_bounded_text(identity.operation, self._redactor),
            subject=_bounded_text(identity.subject, self._redactor),
            payload_digest=_bounded_text(identity.payload_digest, self._redactor),
        )

    def _call_hook(self, phase: str) -> None:
        if self._hook is not None:
            self._hook(phase)


Result = TypeVar("Result")


class ReservedEffect:
    """Persist a bounded result before any best-effort compatibility observation."""

    def __init__(self, authority: WriteAuthority) -> None:
        self._authority = authority

    def execute(
        self,
        key: str,
        identity: EffectIdentity,
        need: Capacity,
        effect: Callable[[], Result],
        *,
        encode: Callable[[Result], Mapping[str, Any]],
        decode: Callable[[Mapping[str, Any]], Result],
        observe: Callable[[Result], None] | None = None,
        retention: RetentionPolicy = RetentionPolicy.RELEASE,
    ) -> Result:
        retention = RetentionPolicy(retention)
        reservation = self._authority.reserve(key, identity, need, retention=retention)
        if reservation.state is ReservationState.COMMITTED:
            if reservation.retention.requires_receipt:
                self._authority.write_receipt(key)
            return decode(reservation.observation or {})
        if reservation.state is ReservationState.STARTED:
            reservation = self._await_terminal(reservation)
            if reservation.state is ReservationState.COMMITTED:
                if reservation.retention.requires_receipt:
                    self._authority.write_receipt(key)
                return decode(reservation.observation or {})
        if reservation.state is not ReservationState.RESERVED:
            if reservation.retention.requires_receipt and reservation.state in FINAL_STATES:
                self._authority.write_receipt(key)
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
            committed = self._authority.commit(started, encode(result))
        except BaseException as error:
            current = self._authority.current(key)
            if current is not None and current.state is ReservationState.STARTED:
                current = self._authority.possibly_sent(started, type(error).__name__)
                if current.retention.requires_receipt:
                    try:
                        self._authority.write_receipt(key)
                    except (OSError, ReservationError):
                        pass
            raise
        if committed.retention.requires_receipt:
            self._authority.write_receipt(key)
        if observe is not None:
            try:
                observe(result)
            except OSError:
                pass
        return decode(committed.observation or {})

    def _await_terminal(self, reservation: WriteReservation) -> WriteReservation:
        deadline = time.monotonic() + 5.0
        current = reservation
        while current.state in {ReservationState.RESERVED, ReservationState.STARTED} and time.monotonic() < deadline:
            time.sleep(0.005)
            current = self._authority.current(reservation.key) or current
        return current


def manifest_receipt(path: Path) -> dict[str, str]:
    from solver.write_reservation_receipt import manifest_receipt as adapt_receipt

    return adapt_receipt(path)


__all__ = [
    "Capacity",
    "DEFAULT_WRITE_PROFILE",
    "EffectIdentity",
    "EffectIndeterminate",
    "Pool",
    "ReservationConflict",
    "ReservationState",
    "ReservationUnavailable",
    "RetentionPolicy",
    "ReservedEffect",
    "WriteAuthority",
    "WriteProfile",
    "WriteReservation",
    "manifest_receipt",
]
