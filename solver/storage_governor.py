"""Deterministic storage-pressure admission and governed retirement."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes, digest_bytes, fsync_directory
from solver.redaction import Redactor
from solver.storage_governor_contracts import (
    AdmissionClass,
    AdmissionDecision,
    PressureState,
    ProtectedStorageClass,
    ReachabilityRecorded,
    ReachabilityRoots,
    RetirementCandidate,
    RetirementRecorded,
    RetirementResult,
    RetirableStorageClass,
    StorageClassified,
    StorageCapacity,
    StorageGovernorProfile,
    StorageGovernorRecorded,
)
from solver.storage_governor_receipt import (
    manifest_receipt,
    persist_profile,
    validate_complete_roots,
    verify_receipt,
    write_receipt,
)
from solver.write_reservation import (
    EffectIdentity,
    Pool,
    ReservationState,
    ReservationUnavailable,
    RetentionPolicy,
    WriteAuthority,
)

PROTECTED_STORAGE_CLASSES = frozenset(item.value for item in ProtectedStorageClass)
RETIREMENT_ORDER = {
    RetirableStorageClass.EPHEMERAL.value: 1,
    RetirableStorageClass.CACHE.value: 1,
    RetirableStorageClass.DUPLICATE.value: 1,
    RetirableStorageClass.RECONSTRUCTIBLE.value: 1,
    RetirableStorageClass.FAILED_WORK.value: 2,
    RetirableStorageClass.UNSELECTED_WORK.value: 2,
    RetirableStorageClass.SUPERSEDED_WORK.value: 2,
    RetirableStorageClass.RAW_OBSERVATION.value: 3,
    RetirableStorageClass.RAW_TELEMETRY.value: 3,
    RetirableStorageClass.TOOL_BODY.value: 3,
    RetirableStorageClass.PRIVATE_LOG.value: 3,
    RetirableStorageClass.CLOSED_INCIDENT_DETAIL.value: 4,
}


class StorageGovernor:
    """One public boundary for storage admission and retirement."""

    def __init__(
        self,
        state: Path,
        run_id: str,
        profile: StorageGovernorProfile,
        *,
        redactor: Redactor | None = None,
        event_store_hook: Callable[[str], None] | None = None,
        retirement_hook: Callable[[str], None] | None = None,
    ) -> None:
        self.run_dir = Path(state) / "runs" / run_id
        self.run_id = run_id
        self.profile = profile
        self._store = EventStore(
            state,
            run_id=run_id,
            redactor=redactor,
            append_hook=event_store_hook,
        )
        self._authority = WriteAuthority(self.run_dir, profile.write_profile(), redactor=redactor)
        persist_profile(self._store.canonical_dir, profile)
        self._retirement_hook = retirement_hook
        self.replay_pending_retirements()

    def close(self) -> None:
        self._authority.close()

    def __del__(self) -> None:
        try:
            self.close()
        except (AttributeError, OSError):
            pass

    def classify(self, candidate: RetirementCandidate) -> None:
        path = self._candidate_path(candidate)
        self._verify_candidate(candidate, path, self._store.events())
        try:
            RetirableStorageClass(candidate.storage_class)
        except ValueError:
            ProtectedStorageClass(candidate.storage_class)
        resource = candidate.resource_identity_dict()
        identity_digest = digest_bytes(canonical_bytes(resource))
        authority = self._authority.reserve(
            f"storage-classification:{identity_digest}",
            EffectIdentity(
                "storage.classification",
                identity_digest,
                digest_bytes(canonical_bytes(candidate.identity_dict())),
            ),
            StorageCapacity(1, 1, 1, 1, 1, 1, 1).as_reservation(),
            pool=Pool.SHARED,
        )
        started = self._authority.start(authority)
        committed = self._store.append(
            StorageClassified(
                event_id=f"classification:{identity_digest}",
                path=candidate.path,
                storage_class=candidate.storage_class,
                digest=candidate.digest,
                length=candidate.length,
                event_sequences=tuple(resource["event_sequences"]),
                reservation_key=authority.key,
            ),
            body=b"",
        )
        self._authority.commit(started, {"event_digest": committed.event_digest})

    def _verify_classification(self, candidate: RetirementCandidate, events) -> None:
        resource = candidate.resource_identity_dict()
        matches = [
            event.payload
            for event in events
            if event.event_type == "storage-governor.recorded"
            and event.payload.get("record") == "storage-classification"
            and {
                "path": event.payload["path"],
                "digest": event.payload["digest"],
                "length": event.payload["length"],
                "event_sequences": event.payload["event_sequences"],
            }
            == resource
        ]
        if not matches:
            raise ValueError("retirement candidate has no authoritative storage classification")
        if matches[-1]["storage_class"] != candidate.storage_class:
            raise ValueError("retirement candidate differs from authoritative storage classification")
        identity_digest = digest_bytes(canonical_bytes(candidate.resource_identity_dict()))
        expected = EffectIdentity(
            "storage.classification",
            identity_digest,
            digest_bytes(canonical_bytes(candidate.identity_dict())),
        )
        reservation = self._authority.current(matches[-1]["reservation_key"])
        if reservation is None or reservation.effect_fingerprint != expected.fingerprint:
            raise ValueError("storage classification has no matching authority reservation")

    def admit(
        self,
        *,
        request_id: str,
        admission_class: AdmissionClass,
        need: StorageCapacity,
        resource: RetirementCandidate | None = None,
    ) -> AdmissionDecision:
        admission_class = AdmissionClass(admission_class)
        pool = {
            AdmissionClass.ORDINARY: Pool.ORDINARY,
            AdmissionClass.AUTHORITY: Pool.SHARED,
            AdmissionClass.TERMINAL: Pool.TERMINAL,
            AdmissionClass.RECOVERY: Pool.RECOVERY,
        }[admission_class]
        floor = None
        if pool is Pool.ORDINARY:
            exclusive = StorageCapacity(1, 1, 1, 1, 1, 1, 1)
            floor = (
                self.profile.stop_admission_remaining - self.profile.authority_only_remaining + exclusive
            ).as_reservation()
        identity = EffectIdentity(
            "storage.admission",
            request_id,
            digest_bytes(canonical_bytes(self._admission_payload(admission_class, need, resource))),
        )
        key = f"storage-admission:{digest_bytes(request_id.encode())}"
        try:
            reservation = self._authority.reserve(
                key,
                identity,
                need.as_reservation(),
                pool=pool,
                retention=RetentionPolicy.RECORD,
                floor=floor,
                refusal_pool=pool if pool is Pool.SHARED else Pool.SHARED,
                capture_headroom=True,
            )
            admitted = reservation.state not in {ReservationState.REFUSED, ReservationState.ABORTED}
        except ReservationUnavailable:
            reservation = self._authority.current(key)
            if reservation is None:
                raise
            admitted = False
        headroom = reservation.grant_headroom or {}
        remaining = self._sum_headroom(headroom.values())
        authority_headroom = self._sum_headroom(
            capacity for name, capacity in headroom.items() if name in {"shared", "terminal", "recovery"}
        )
        pressure = self._pressure(remaining)
        decision = AdmissionDecision(
            request_id=request_id,
            pressure=pressure,
            admission_class=admission_class,
            admitted=admitted,
            remaining=remaining,
            need=need,
            authority_headroom=authority_headroom,
            reservation_key=key,
        )
        authority = reservation
        if not admitted:
            authority = self._authority.reserve(
                f"storage-pressure:{digest_bytes(request_id.encode())}",
                EffectIdentity(
                    "storage.pressure-decision",
                    request_id,
                    digest_bytes(canonical_bytes(decision.as_dict())),
                ),
                StorageCapacity(1, 1, 1, 1, 1, 1, 1).as_reservation(),
                pool=Pool.SHARED,
            )
        started = self._authority.start(authority)
        try:
            committed = self._store.append(
                StorageGovernorRecorded(
                    event_id=f"pressure:{digest_bytes(request_id.encode())}",
                    request_id=request_id,
                    pressure=pressure,
                    admission_class=admission_class,
                    admitted=admitted,
                    remaining=remaining,
                    need=need,
                    authority_headroom=authority_headroom,
                ),
                body=b"",
            )
        except BaseException:
            current = self._authority.current(started.key)
            if current is not None and current.state is ReservationState.STARTED:
                self._authority.possibly_sent(current, "canonical-pressure-append-failed")
            raise
        self._authority.commit(started, {"event_digest": committed.event_digest})
        return decision

    def _admission_payload(
        self,
        admission_class: AdmissionClass,
        need: StorageCapacity,
        resource: RetirementCandidate | None,
    ) -> dict:
        return {
            "admission_class": admission_class.value,
            "need": need.as_dict(),
            "profile": self.profile.as_dict(),
            "resource": None if resource is None else resource.identity_dict(),
        }

    def _verify_release_binding(self, candidate: RetirementCandidate) -> None:
        if not candidate.reservation_key:
            return
        reservation = self._authority.current(candidate.reservation_key)
        if reservation is None or reservation.identity.operation != "storage.admission":
            raise ValueError("retirement capacity reservation is unavailable")
        admission_class = {
            Pool.ORDINARY: AdmissionClass.ORDINARY,
            Pool.SHARED: AdmissionClass.AUTHORITY,
            Pool.TERMINAL: AdmissionClass.TERMINAL,
            Pool.RECOVERY: AdmissionClass.RECOVERY,
        }[reservation.pool]
        resource = RetirementCandidate(
            path=candidate.path,
            storage_class=candidate.storage_class,
            digest=candidate.digest,
            length=candidate.length,
            event_sequences=candidate.event_sequences,
        )
        expected = EffectIdentity(
            "storage.admission",
            reservation.identity.subject,
            digest_bytes(
                canonical_bytes(
                    self._admission_payload(
                        admission_class,
                        StorageCapacity.from_headroom(reservation.need),
                        resource,
                    )
                )
            ),
        )
        if expected.fingerprint != reservation.effect_fingerprint:
            raise ValueError("retirement reservation belongs to a different storage resource")

    @staticmethod
    def _sum_headroom(values) -> StorageCapacity:
        zero = StorageCapacity(0, 0, 0, 0, 0, 0, 0)
        total = zero
        for value in values:
            total += StorageCapacity.from_headroom(value)
        return total

    def retire(
        self,
        candidates: tuple[RetirementCandidate, ...],
        roots: ReachabilityRoots,
        *,
        reason: str,
    ) -> RetirementResult:
        retired = []
        protected = []
        events = self._store.events()
        latest_reservations = {reservation.reservation_id: reservation for reservation in self._store.reservations()}
        in_flight_digests = {
            reservation.blob_digest
            for reservation in latest_reservations.values()
            if reservation.status.value == "reserved"
        }
        for candidate in candidates:
            path = self._candidate_path(candidate)
            self._verify_candidate(candidate, path, events)
            if candidate.digest not in in_flight_digests:
                self._verify_classification(candidate, events)
        validate_complete_roots(self._store, self.profile, roots)
        for candidate in sorted(candidates, key=self._retirement_order):
            if self._is_reachable(candidate, roots, in_flight_digests):
                protected.append(candidate.path)
                continue
            path = self._candidate_path(candidate)
            self._verify_release_binding(candidate)
            retirement = self._reserve_retirement(candidate, reason)
            self._call_retirement_hook("before_tombstone")
            self._record_retirement(
                candidate,
                "retirement-tombstone",
                reason,
                retirement_reservation_key=retirement.key,
            )
            self._call_retirement_hook("after_tombstone")
            path.unlink()
            fsync_directory(path.parent)
            self._call_retirement_hook("after_delete")
            self._record_retirement(
                candidate,
                "retirement-complete",
                reason,
                retirement_reservation_key=retirement.key,
            )
            self._release_capacity(candidate, reason)
            self._complete_retirement(retirement, candidate, reason)
            self._call_retirement_hook("after_completion")
            retired.append(candidate.digest)
        return RetirementResult(tuple(retired), tuple(protected))

    @staticmethod
    def _retirement_order(candidate: RetirementCandidate) -> tuple[int, str]:
        if candidate.storage_class in PROTECTED_STORAGE_CLASSES:
            return 5, candidate.path
        try:
            return RETIREMENT_ORDER[candidate.storage_class], candidate.path
        except KeyError as error:
            raise ValueError(f"unsupported retirement class {candidate.storage_class!r}") from error

    def write_receipt(
        self,
        *,
        roots: ReachabilityRoots,
    ) -> Path:
        self.replay_pending_retirements()
        validate_complete_roots(self._store, self.profile, roots)
        roots_digest = digest_bytes(canonical_bytes(roots.as_dict()))
        self._store.append(
            ReachabilityRecorded(event_id=f"reachability:{roots_digest}", roots=roots),
            body=b"",
        )
        return write_receipt(self._store, self.profile)

    def replay_pending_retirements(self) -> tuple[str, ...]:
        events = self._store.events()
        retirement_keys = {
            event.payload["retirement_reservation_key"]
            for event in events
            if event.event_type == "storage-governor.recorded"
            and event.payload.get("record") in {"retirement-tombstone", "retirement-complete"}
        }
        for reservation in self._authority.reservations():
            if reservation.identity.operation != "storage.retirement" or reservation.key in retirement_keys:
                continue
            if reservation.state is ReservationState.RESERVED:
                self._authority.abort(reservation, "retirement-has-no-tombstone")
            elif reservation.state is ReservationState.STARTED:
                uncertain = self._authority.possibly_sent(reservation, "retirement-has-no-tombstone")
                self._authority.release_retained(uncertain, "retirement-has-no-tombstone")
            elif reservation.retention.retains_object:
                self._authority.release_retained(reservation, "retirement-has-no-tombstone")
        completed = {
            (event.payload["path"], event.payload["target_digest"])
            for event in events
            if event.event_type == "storage-governor.recorded" and event.payload.get("record") == "retirement-complete"
        }
        for event in events:
            if event.event_type != "storage-governor.recorded" or event.payload.get("record") != "retirement-complete":
                continue
            candidate = RetirementCandidate(
                path=event.payload["path"],
                storage_class=event.payload["storage_class"],
                digest=event.payload["target_digest"],
                length=event.payload["target_length"],
                event_sequences=tuple(event.payload["event_sequences"]),
                reservation_key=event.payload["reservation_key"],
            )
            self._verify_classification(candidate, events)
            self._verify_release_binding(candidate)
            self._verify_retirement_binding(
                candidate,
                event.payload["retirement_reservation_key"],
                event.payload["reason"],
            )
            self._release_capacity(candidate, event.payload["reason"])
            self._finish_replayed_retirement(
                event.payload["retirement_reservation_key"], candidate, event.payload["reason"]
            )
        reconciled = []
        for event in events:
            payload = event.payload
            identity = (payload.get("path"), payload.get("target_digest"))
            if (
                event.event_type != "storage-governor.recorded"
                or payload.get("record") != "retirement-tombstone"
                or identity in completed
            ):
                continue
            candidate = RetirementCandidate(
                path=payload["path"],
                storage_class=payload["storage_class"],
                digest=payload["target_digest"],
                length=payload["target_length"],
                event_sequences=tuple(payload["event_sequences"]),
                reservation_key=payload["reservation_key"],
            )
            self._verify_classification(candidate, events)
            path = self._run_path(candidate.path)
            self._verify_release_binding(candidate)
            self._verify_retirement_binding(
                candidate,
                payload["retirement_reservation_key"],
                payload["reason"],
            )
            if path.exists():
                self._verify_candidate(candidate, path, events)
                path.unlink()
                fsync_directory(path.parent)
            self._record_retirement(
                candidate,
                "retirement-complete",
                payload["reason"],
                retirement_reservation_key=payload["retirement_reservation_key"],
            )
            self._release_capacity(candidate, payload["reason"])
            self._finish_replayed_retirement(payload["retirement_reservation_key"], candidate, payload["reason"])
            completed.add(identity)
            reconciled.append(candidate.digest)
        return tuple(reconciled)

    def _is_reachable(
        self,
        candidate: RetirementCandidate,
        roots: ReachabilityRoots,
        in_flight_digests: set[str],
    ) -> bool:
        return bool(
            candidate.storage_class in PROTECTED_STORAGE_CLASSES
            or set(candidate.event_sequences) & roots.event_sequences
            or candidate.digest in roots.blob_digests
            or candidate.digest in roots.in_flight_digests
            or candidate.digest in in_flight_digests
            or candidate.path in roots.paths
            or candidate.path in roots.promoted_paths
            or candidate.path in roots.in_flight_paths
        )

    def _candidate_path(self, candidate: RetirementCandidate) -> Path:
        if candidate.path != f"sealed/sha256/{candidate.digest}":
            raise ValueError("retirement candidate must use its exact sealed digest path")
        unresolved = self.run_dir / candidate.path
        if unresolved.is_symlink():
            raise ValueError("retirement candidate must use its exact sealed digest path")
        path = self._run_path(candidate.path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("retirement candidate is not a regular Run file")
        return path

    def _run_path(self, relative: str) -> Path:
        path = (self.run_dir / relative).resolve()
        try:
            path.relative_to(self.run_dir.resolve())
        except ValueError as error:
            raise ValueError("retirement candidate escapes its Run") from error
        return path

    @staticmethod
    def _verify_candidate(candidate: RetirementCandidate, path: Path, events) -> None:
        body = path.read_bytes()
        if len(body) != candidate.length or digest_bytes(body) != candidate.digest:
            raise ValueError("retirement candidate differs from its declared identity")
        by_sequence = {event.sequence: event for event in events}
        for sequence in candidate.event_sequences:
            event = by_sequence.get(sequence)
            if event is None or event.blob_digest != candidate.digest or event.blob_bytes != candidate.length:
                raise ValueError("retirement candidate does not match its canonical reference")
        all_references = tuple(event.sequence for event in events if event.blob_digest == candidate.digest)
        if tuple(sorted(set(candidate.event_sequences))) != all_references:
            raise ValueError("retirement must release every canonical reference to a sealed blob")

    def _record_retirement(
        self,
        candidate: RetirementCandidate,
        record: str,
        reason: str,
        *,
        retirement_reservation_key: str,
    ) -> None:
        self._store.append(
            RetirementRecorded(
                event_id=f"retirement:{candidate.digest}:{record}",
                record=record,
                path=candidate.path,
                storage_class=candidate.storage_class,
                target_digest=candidate.digest,
                target_length=candidate.length,
                event_sequences=tuple(sorted(set(candidate.event_sequences))),
                reason=reason,
                reservation_key=candidate.reservation_key,
                retirement_reservation_key=retirement_reservation_key,
            ),
            body=b"",
        )

    def _release_capacity(self, candidate: RetirementCandidate, reason: str) -> None:
        if not candidate.reservation_key:
            return
        self._verify_release_binding(candidate)
        reservation = self._authority.current(candidate.reservation_key)
        if reservation is not None:
            self._authority.release_retained(reservation, f"retirement-complete:{reason}")

    @staticmethod
    def _retirement_key(candidate: RetirementCandidate) -> str:
        return f"storage-retirement:{candidate.digest}"

    def _retirement_identity(self, candidate: RetirementCandidate, reason: str) -> EffectIdentity:
        return EffectIdentity(
            "storage.retirement",
            candidate.digest,
            digest_bytes(
                canonical_bytes(
                    {
                        "path": candidate.path,
                        "length": candidate.length,
                        "reason": reason,
                        "event_sequences": sorted(set(candidate.event_sequences)),
                    }
                )
            ),
        )

    def _verify_retirement_binding(self, candidate: RetirementCandidate, key: str, reason: str) -> None:
        reservation = self._authority.current(key)
        expected = self._retirement_identity(candidate, reason)
        if reservation is None or reservation.effect_fingerprint != expected.fingerprint:
            raise ValueError("retirement tombstone has no matching Recovery reservation")

    def _reserve_retirement(self, candidate: RetirementCandidate, reason: str):
        identity = self._retirement_identity(candidate, reason)
        reservation = self._authority.reserve(
            self._retirement_key(candidate),
            identity,
            StorageCapacity(1, 1, 1, 1, 1, 1, 1).as_reservation(),
            pool=Pool.RECOVERY,
            retention=RetentionPolicy.RECORD,
            refusal_pool=Pool.SHARED,
            retry_aborted=True,
            retry_released=True,
        )
        return self._authority.start(reservation)

    def _complete_retirement(self, reservation, candidate: RetirementCandidate, reason: str) -> None:
        current = self._authority.current(reservation.key)
        if current is None:
            raise ValueError("retirement lost its durable Recovery reservation")
        if current.state.value == "started":
            current = self._authority.commit(current, {"retired_digest": candidate.digest})
        self._authority.release_retained(current, f"retirement-complete:{reason}")

    def _finish_replayed_retirement(self, key: str, candidate: RetirementCandidate, reason: str) -> None:
        reservation = self._authority.current(key)
        if reservation is None or reservation.state is ReservationState.RELEASED:
            return
        self._complete_retirement(reservation, candidate, reason)

    def _call_retirement_hook(self, phase: str) -> None:
        if self._retirement_hook is not None:
            self._retirement_hook(phase)

    def _pressure(self, remaining: StorageCapacity) -> PressureState:
        if remaining.at_or_below(self.profile.authority_only_remaining):
            return PressureState.AUTHORITY_ONLY
        if remaining.at_or_below(self.profile.stop_admission_remaining):
            return PressureState.STOP_ADMISSION
        if remaining.at_or_below(self.profile.warning_remaining):
            return PressureState.WARNING
        return PressureState.NORMAL


__all__ = [
    "AdmissionClass",
    "AdmissionDecision",
    "PressureState",
    "ReachabilityRoots",
    "RetirementCandidate",
    "RetirementResult",
    "StorageCapacity",
    "StorageGovernor",
    "StorageGovernorProfile",
    "manifest_receipt",
    "verify_receipt",
]
