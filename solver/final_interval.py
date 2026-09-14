"""Canonical final-interval authority and deterministic replay projection."""

from __future__ import annotations

import datetime as dt
import hashlib
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from solver.event_store import EventStore
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.final_interval_contracts import (
    FINAL_INTERVAL_RECORDED,
    AdmissionMode,
    CleanupDisposition,
    FinalChanceState,
    FinalIntervalRecord,
    FinalIntervalRecorded,
    RECEIPT,
    RECEIPT_KIND,
    SCHEMA_VERSION,
    RunDisposition,
    SubmissionDisposition,
    SubmissionDeferred,
)
from solver.final_interval_projection import project_final_interval
from solver.final_interval_receipt import verify_receipt
from solver.submission.authority import (
    ACCOUNT_POST_INTERVAL_SECONDS,
    SUBMISSION_REQUEST_DEADLINE_SECONDS,
    SUBMISSION_UNCERTAINTY_MARGIN_SECONDS,
)
from solver.write_reservation import EffectIndeterminate, WriteAuthority
from solver.write_reservation_contracts import ReservationState, WriteReservation


@dataclass(frozen=True)
class FinalChanceGrant:
    lane_id: str
    challenge_id: str
    admitted_at: dt.datetime
    deadline: dt.datetime
    transaction: object = field(default=None, compare=False, repr=False)
    reservation_acquired: bool = field(default=False, compare=False, repr=False)


@dataclass(frozen=True)
class RunInventory:
    attempts: tuple[str, ...] = ()
    instances: tuple[str, ...] = ()
    submissions: tuple[str, ...] = ()

    def document(self) -> dict[str, list[str]]:
        return {
            "attempts": list(self.attempts),
            "instances": list(self.instances),
            "submissions": list(self.submissions),
        }


class FinalIntervalController:
    """One append-only authority for the monotone end-of-Run lifecycle."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        opened_at: dt.datetime,
        ends_at: dt.datetime,
        final_submission_reserve_seconds: int,
        attempt_floor_seconds: int,
        enabled_lanes: Sequence[str],
        now: Callable[[], dt.datetime],
        reserve: Callable[[str], object],
        account_post_interval_seconds: float = ACCOUNT_POST_INTERVAL_SECONDS,
        submission_request_deadline_seconds: float = SUBMISSION_REQUEST_DEADLINE_SECONDS,
        submission_uncertainty_margin_seconds: float = SUBMISSION_UNCERTAINTY_MARGIN_SECONDS,
        reserve_terminal: Callable[[str], object] | None = None,
        event_store: EventStore | None = None,
        write_authority: WriteAuthority | None = None,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        if (
            not run_id
            or opened_at.tzinfo is None
            or ends_at <= opened_at
            or min(final_submission_reserve_seconds, attempt_floor_seconds) <= 0
            or min(
                account_post_interval_seconds,
                submission_request_deadline_seconds,
                submission_uncertainty_margin_seconds,
            )
            <= 0
            or submission_request_deadline_seconds + submission_uncertainty_margin_seconds
            >= final_submission_reserve_seconds
            or final_submission_reserve_seconds >= (ends_at - opened_at).total_seconds()
            or not enabled_lanes
            or len(set(enabled_lanes)) != len(enabled_lanes)
        ):
            raise ValueError("final interval requires a valid Run window, reserve, floor, and Lane profile")
        self.state, self.run_id = Path(state), run_id
        self.opened_at, self.ends_at = opened_at, ends_at
        self.reserve_seconds, self.floor_seconds = final_submission_reserve_seconds, attempt_floor_seconds
        self.post_interval_seconds = account_post_interval_seconds
        self.request_deadline_seconds = submission_request_deadline_seconds
        self.uncertainty_margin_seconds = submission_uncertainty_margin_seconds
        self.enabled_lanes = tuple(enabled_lanes)
        self._now, self._reserve = now, reserve
        self._reserve_terminal = reserve_terminal or reserve
        self._hook = hook or (lambda _point: None)
        self._write_authority = write_authority
        self._chance_transactions: dict[str, object] = {}
        self._lock = threading.RLock()
        self._store = event_store or EventStore(self.state, run_id=run_id)
        self.receipt_path = self.state / "runs" / run_id / RECEIPT
        self._document = project_final_interval(self._store.events())
        observed = [
            dt.datetime.fromisoformat(str(row["observed_at"]))
            for row in self._document["trace"]
            if isinstance(row, Mapping) and row.get("observed_at")
        ]
        self._observed_floor = max((self.opened_at, *observed))
        if not set(self._document["final_chances"]).issubset(self.enabled_lanes):
            raise ValueError("final-interval authority names a disabled Lane")

    @property
    def cutoff(self) -> dt.datetime:
        return self.ends_at - dt.timedelta(seconds=self.reserve_seconds)

    def scoreable_seconds(self) -> float:
        return max(0.0, (self.cutoff - self._observed_now()).total_seconds())

    def chance_available(self, lane_id: str) -> bool:
        self._require_lane(lane_id)
        chance = self._document["final_chances"].get(lane_id)
        return self.admission_mode(lane_id) is AdmissionMode.FINAL_CHANCE and (
            chance is None or chance["state"] == FinalChanceState.RESERVED.value
        )

    def admission_mode(self, lane_id: str) -> AdmissionMode:
        with self._lock:
            self._require_lane(lane_id)
            now = self._observed_now()
            observed = self._mode_at(now)
            durable = AdmissionMode(str(self._document["mode"]))
            if self._document.get("terminal") is not None:
                observed = AdmissionMode.CLOSED
            if self._mode_rank(observed) > self._mode_rank(durable):
                self._record(FinalIntervalRecord.TRANSITION, now, state=observed.value)
                durable = observed
            return durable

    def reserve_final_chance(self, lane_id: str, challenge_id: str) -> FinalChanceGrant | None:
        with self._lock:
            existing = self._document["final_chances"].get(lane_id)
            if self.admission_mode(lane_id) is not AdmissionMode.FINAL_CHANCE or (
                existing is not None and existing["state"] != FinalChanceState.RESERVED.value
            ):
                return None
            if not challenge_id:
                raise ValueError("final chance requires a Challenge identity")
            now = self._observed_now()
            if existing is not None and existing["state"] == FinalChanceState.RESERVED.value:
                return FinalChanceGrant(lane_id, challenge_id, now, self.cutoff)
            grant = self._reserve(f"final-chance:{lane_id}")
            reservation_id = str(getattr(grant, "key", f"final-chance:{lane_id}"))
            self._record(
                FinalIntervalRecord.ENTITLEMENT_RESERVED,
                now,
                lane_id=lane_id,
                challenge_id="",
                reservation_id=reservation_id,
                state=FinalChanceState.RESERVED.value,
            )
            self._chance_transactions[lane_id] = grant
            return FinalChanceGrant(lane_id, challenge_id, now, self.cutoff, grant, True)

    def spend_final_chance(self, grant: FinalChanceGrant) -> FinalChanceGrant:
        with self._lock:
            current = self._document["final_chances"].get(grant.lane_id)
            if current is None:
                raise ValueError("final chance has no matching durable reservation")
            if current["state"] == FinalChanceState.SPENT.value:
                if current["challenge_id"] != grant.challenge_id:
                    raise ValueError("final chance was spent on another Challenge")
                return grant
            if current["state"] != FinalChanceState.RESERVED.value:
                raise ValueError("final chance is already closed")
            reservation = (
                grant.transaction if grant.reservation_acquired else self._reserve(f"final-chance:{grant.lane_id}")
            )
            reservation_id = str(getattr(reservation, "key", f"final-chance:{grant.lane_id}"))
            now = self._observed_now()
            if now >= self.cutoff:
                self._record(
                    FinalIntervalRecord.ENTITLEMENT_CLOSED,
                    now,
                    lane_id=grant.lane_id,
                    challenge_id="",
                    reservation_id=reservation_id,
                    state=FinalChanceState.CLOSED.value,
                )
                self._settle_local(reservation, {"state": FinalChanceState.CLOSED.value})
                self._chance_transactions.pop(grant.lane_id, None)
                raise ValueError("final chance crossed the submission cutoff before admission")
            self._record(
                FinalIntervalRecord.ENTITLEMENT_SPENT,
                now,
                lane_id=grant.lane_id,
                challenge_id=grant.challenge_id,
                reservation_id=reservation_id,
                state=FinalChanceState.SPENT.value,
            )
            self._settle_local(reservation, {"state": FinalChanceState.SPENT.value})
            self._chance_transactions.pop(grant.lane_id, None)
            return FinalChanceGrant(
                grant.lane_id,
                grant.challenge_id,
                now,
                grant.deadline,
                grant.transaction,
                grant.reservation_acquired,
            )

    def admit_final_chance(self, lane_id: str, challenge_id: str) -> FinalChanceGrant | None:
        grant = self.reserve_final_chance(lane_id, challenge_id)
        return self.spend_final_chance(grant) if grant is not None else None

    def drain(self, candidates: Sequence[str], submit: Callable[[str], str]) -> tuple[str, ...]:
        with self._lock:
            now = self._observed_now()
            if not self.cutoff <= now < self.ends_at:
                raise ValueError("Candidate drain belongs to the final-submission reserve")
            outcomes: list[str] = []
            submissions = self._document["submissions"]
            assert isinstance(submissions, dict)
            for candidate in candidates:
                now = self._observed_now()
                if now >= self.ends_at:
                    break
                if candidate in submissions:
                    prior = submissions[candidate]["outcome"]
                    if self._write_authority is not None:
                        self._settle_local(
                            self._reserve(f"submission:{candidate}"),
                            {"outcome": prior},
                        )
                    outcomes.append(
                        SubmissionDisposition.UNKNOWN_AND_SPENT.value
                        if prior == SubmissionDisposition.POSSIBLY_SENT.value
                        else prior
                    )
                    continue
                grant = self._reserve(f"submission:{candidate}")
                self.admission_mode(self.enabled_lanes[0])
                reservation_id = str(getattr(grant, "key", f"submission:{candidate}"))
                drain_requests = self._document["drain_requests"]
                assert isinstance(drain_requests, Mapping)
                if candidate not in drain_requests:
                    self._record(
                        FinalIntervalRecord.DRAIN_REQUESTED,
                        now,
                        candidate_id=candidate,
                        reservation_id=reservation_id,
                    )
                try:
                    outcome = SubmissionDisposition(submit(candidate))
                    self._hook("after_submission")
                except SubmissionDeferred:
                    self._abort(grant, "deferred-before-wire")
                    continue
                except EffectIndeterminate:
                    outcome = SubmissionDisposition.POSSIBLY_SENT
                result_at = self._observed_now()
                self._record(
                    FinalIntervalRecord.DRAIN_RESULT,
                    result_at,
                    candidate_id=candidate,
                    reservation_id=reservation_id,
                    outcome=outcome.value,
                )
                self._settle_local(grant, {"outcome": outcome.value})
                outcomes.append(outcome.value)
            return tuple(outcomes)

    def reconcile_submission(self, candidate_id: str, outcome: str) -> None:
        """Project a definitive ambiguity-fence result before terminal inventory is recorded."""

        with self._lock:
            submissions = self._document["submissions"]
            assert isinstance(submissions, Mapping)
            current = submissions.get(candidate_id)
            resolved = SubmissionDisposition(outcome)
            if current is None or current.get("outcome") != SubmissionDisposition.POSSIBLY_SENT.value:
                raise ValueError("only a possibly-sent Candidate can be reconciled")
            if resolved is SubmissionDisposition.POSSIBLY_SENT:
                return
            self._record(
                FinalIntervalRecord.SUBMISSION_RECONCILED,
                self._observed_now(),
                candidate_id=candidate_id,
                reservation_id=str(self._document["drain_requests"].get(candidate_id, "")),
                outcome=resolved.value,
            )

    def close(
        self,
        inventory: RunInventory | Callable[[], RunInventory],
        cleanup: Callable[[], Mapping[str, str]],
        *,
        allow_early_terminal: bool = False,
    ) -> dict[str, object]:
        with self._lock:
            now = self._observed_now()
            if now < self.ends_at and not allow_early_terminal:
                raise ValueError("cleanup cannot consume the official Run window")
            if terminal := self._document.get("terminal"):
                self._write_receipt()
                self._settle_local(
                    self._reserve_terminal("run-close"),
                    {"disposition": terminal["disposition"]},
                )
                return dict(terminal)
            if AdmissionMode(str(self._document["mode"])) is not AdmissionMode.CLOSED:
                self._record(FinalIntervalRecord.TRANSITION, now, state=AdmissionMode.CLOSED.value)
            grant = self._reserve_terminal("run-close")
            reservation_id = str(getattr(grant, "key", "run-close"))
            chances = self._document["final_chances"]
            for lane_id, chance in tuple(chances.items()):
                if chance["state"] == FinalChanceState.RESERVED.value:
                    transaction = (
                        self._chance_transactions[lane_id]
                        if lane_id in self._chance_transactions
                        else self._reserve(f"final-chance:{lane_id}")
                    )
                    self._record(
                        FinalIntervalRecord.ENTITLEMENT_CLOSED,
                        now,
                        lane_id=lane_id,
                        challenge_id=str(chance["challenge_id"]),
                        reservation_id=str(getattr(transaction, "key", f"final-chance:{lane_id}")),
                        state=FinalChanceState.CLOSED.value,
                    )
                    self._settle_local(transaction, {"state": FinalChanceState.CLOSED.value})
                    self._chance_transactions.pop(lane_id, None)
            before = inventory() if callable(inventory) else inventory
            cleanup_targets = tuple(
                dict.fromkeys(
                    (
                        "run-cleanup",
                        *(f"attempt:{identity}" for identity in before.attempts),
                        *(f"instance:{identity}" for identity in before.instances),
                    )
                )
            )
            if not self._document["cleanup_requested"]:
                self._record(
                    FinalIntervalRecord.CLEANUP_REQUESTED,
                    now,
                    reservation_id=reservation_id,
                    inventory={"targets": list(cleanup_targets)},
                )
            else:
                cleanup_targets = tuple(self._document["cleanup_targets"])
                if self._document["cleanup_authority"] != reservation_id:
                    raise ValueError("cleanup and terminal authority disagree")
            try:
                supplied_outcomes = dict(cleanup())
            except Exception:
                supplied_outcomes = {"run-cleanup": CleanupDisposition.UNSETTLED.value}
            if not set(supplied_outcomes).issubset(cleanup_targets):
                supplied_outcomes = {"run-cleanup": CleanupDisposition.UNSETTLED.value}
            cleanup_outcomes = {
                identity: (
                    CleanupDisposition.RELEASED.value
                    if supplied_outcomes.get(identity) == CleanupDisposition.RELEASED.value
                    else CleanupDisposition.UNSETTLED.value
                )
                for identity in cleanup_targets
            }
            if "run-cleanup" not in supplied_outcomes:
                cleanup_outcomes["run-cleanup"] = CleanupDisposition.RELEASED.value
            remaining = inventory() if callable(inventory) else inventory
            for kind, identities in (("attempt", remaining.attempts), ("instance", remaining.instances)):
                left = set(identities)
                for target in cleanup_targets:
                    prefix = f"{kind}:"
                    if target.startswith(prefix):
                        cleanup_outcomes[target] = (
                            CleanupDisposition.UNSETTLED.value
                            if target[len(prefix) :] in left
                            else CleanupDisposition.RELEASED.value
                        )
                if any(f"{kind}:{identity}" not in cleanup_targets for identity in left):
                    cleanup_outcomes["run-cleanup"] = CleanupDisposition.UNSETTLED.value
            durable_cleanup = dict(self._document["cleanup"])
            for identity, value in cleanup_outcomes.items():
                if durable_cleanup.get(identity) == value:
                    continue
                if durable_cleanup.get(identity) == CleanupDisposition.RELEASED.value:
                    raise ValueError("released cleanup target reappeared")
                outcome = (
                    CleanupDisposition.RELEASED
                    if value == CleanupDisposition.RELEASED.value
                    else CleanupDisposition.UNSETTLED
                )
                self._record(
                    FinalIntervalRecord.CLEANUP_RESULT,
                    now,
                    challenge_id=identity,
                    reservation_id=reservation_id,
                    outcome=outcome.value,
                )
                durable_cleanup[identity] = outcome.value
            cleanup_outcomes = durable_cleanup
            disposition = (
                RunDisposition.CLOSED_WITH_UNSETTLED_CLEANUP
                if any(value != CleanupDisposition.RELEASED.value for value in cleanup_outcomes.values())
                else RunDisposition.CLOSED
            )
            terminal = {
                "closed_at": min(now, self.ends_at).isoformat(),
                "official_ends_at": self.ends_at.isoformat(),
                "remaining": remaining.document(),
                "cleanup": cleanup_outcomes,
                "disposition": disposition.value,
            }
            self._record(
                FinalIntervalRecord.TERMINAL_INVENTORY,
                now,
                reservation_id=reservation_id,
                outcome=disposition.value,
                inventory=terminal,
            )
            self._write_receipt()
            self._settle_local(grant, {"disposition": disposition.value})
            return terminal

    def receipt(self) -> dict[str, object]:
        trace = list(self._document["trace"])
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": RECEIPT_KIND,
            "producer": "run-controller",
            "run_id": self.run_id,
            "window": {
                "opened_at": self.opened_at.isoformat(),
                "final_submission_cutoff": self.cutoff.isoformat(),
                "ends_at": self.ends_at.isoformat(),
                "reserve_seconds": self.reserve_seconds,
                "attempt_floor_seconds": self.floor_seconds,
                "account_post_interval_seconds": self.post_interval_seconds,
                "submission_request_deadline_seconds": self.request_deadline_seconds,
                "submission_uncertainty_margin_seconds": self.uncertainty_margin_seconds,
            },
            "enabled_lanes": list(self.enabled_lanes),
            "final_chances": dict(self._document["final_chances"]),
            "submissions": dict(self._document["submissions"]),
            "terminal": self._document.get("terminal"),
            "trace": trace,
            "trace_digest": digest_bytes(canonical_bytes(trace)),
        }

    def _record(self, record: FinalIntervalRecord, at: dt.datetime, **facts: object) -> None:
        ordinal = sum(event.event_type == FINAL_INTERVAL_RECORDED for event in self._store.events()) + 1
        event_id = hashlib.sha256(f"{self.run_id}:{ordinal}:{record.value}".encode()).hexdigest()
        inventory = facts.get("inventory")
        event = FinalIntervalRecorded(
            event_id,
            record,
            at.isoformat(),
            lane_id=str(facts.get("lane_id", "")),
            challenge_id=str(facts.get("challenge_id", "")),
            candidate_id=str(facts.get("candidate_id", "")),
            reservation_id=str(facts.get("reservation_id", "")),
            state=str(facts.get("state", "")),
            outcome=str(facts.get("outcome", "")),
            inventory=inventory if isinstance(inventory, Mapping) else None,
        )
        self._store.append(event, body=b"")
        self._document = project_final_interval(self._store.events())

    def _write_receipt(self) -> None:
        atomic_write(self.receipt_path, canonical_bytes(self.receipt()) + b"\n")

    def _settle_local(self, grant: object, observation: Mapping[str, object]) -> None:
        if self._write_authority is not None and isinstance(grant, WriteReservation):
            current = self._write_authority.current(grant.key)
            if current is None or current.state in {
                ReservationState.COMMITTED,
                ReservationState.TERMINAL,
                ReservationState.RELEASED,
            }:
                return
            if current.state is ReservationState.POSSIBLY_SENT:
                committed = self._write_authority.refuse_indeterminate(
                    current,
                    "canonical final-interval event proves the local transaction",
                )
            else:
                if current.state is not ReservationState.RESERVED:
                    raise EffectIndeterminate(f"final interval transaction is already {current.state.value}")
                committed = self._write_authority.commit(self._write_authority.start(current), observation)
            if committed.retention.requires_receipt:
                self._write_authority.write_receipt(committed.key)
            if committed.retention.retains_object:
                self._write_authority.release_retained(committed, "final-interval transaction durably closed")

    def _abort(self, grant: object, reason: str) -> None:
        if self._write_authority is not None and isinstance(grant, WriteReservation):
            current = self._write_authority.current(grant.key)
            if current is not None and current.state is ReservationState.RESERVED:
                self._write_authority.abort(current, reason)

    def _observed_now(self) -> dt.datetime:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() != dt.timedelta(0) or now < self.opened_at:
            raise ValueError("final-interval clock must be UTC and inside the declared Run")
        self._observed_floor = max(self._observed_floor, now)
        return self._observed_floor

    def _mode_at(self, now: dt.datetime) -> AdmissionMode:
        if now >= self.ends_at:
            return AdmissionMode.CLOSED
        if now >= self.cutoff:
            return AdmissionMode.SUBMISSION_RESERVE
        if (self.cutoff - now).total_seconds() < self.floor_seconds:
            return AdmissionMode.FINAL_CHANCE
        return AdmissionMode.ORDINARY

    @staticmethod
    def _mode_rank(mode: AdmissionMode) -> int:
        return tuple(AdmissionMode).index(mode)

    def _require_lane(self, lane_id: str) -> None:
        if lane_id not in self.enabled_lanes:
            raise ValueError("Lane is not enabled by the selected profile")


__all__ = [
    "AdmissionMode",
    "FinalChanceGrant",
    "FinalIntervalController",
    "RunInventory",
    "project_final_interval",
    "verify_receipt",
]
