"""Canonical final-interval authority and deterministic replay projection."""

from __future__ import annotations

import datetime as dt
import hashlib
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from solver.event_store import EventStore
from solver.event_store_contracts import CommittedEvent
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.final_interval_contracts import (
    FINAL_INTERVAL_RECORDED,
    AdmissionMode,
    CleanupDisposition,
    FinalChanceState,
    FinalIntervalRecord,
    FinalIntervalRecorded,
    RunDisposition,
    SubmissionDisposition,
    SubmissionDeferred,
)
from solver.write_reservation import EffectIndeterminate, WriteAuthority
from solver.write_reservation_contracts import ReservationState, WriteReservation

SCHEMA_VERSION = 2
RECEIPT_KIND = "final-interval"
RECEIPT = "final-interval.receipt.json"


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


def project_final_interval(events: Sequence[CommittedEvent]) -> dict[str, object]:
    """Replay final-interval facts from the already verified canonical chain."""
    result: dict[str, object] = {"final_chances": {}, "submissions": {}, "cleanup": {}, "terminal": None, "trace": []}
    for event in events:
        if event.event_type != FINAL_INTERVAL_RECORDED:
            continue
        row = dict(event.payload)
        record = FinalIntervalRecord(row["record"])
        trace = result["trace"]
        assert isinstance(trace, list)
        trace.append(
            {
                key: value
                for key, value in row.items()
                if key not in {"blob_digest", "blob_bytes", "event_id"} and value != ""
            }
        )
        chances, submissions, cleanup = result["final_chances"], result["submissions"], result["cleanup"]
        assert isinstance(chances, dict) and isinstance(submissions, dict) and isinstance(cleanup, dict)
        if record in {
            FinalIntervalRecord.ENTITLEMENT_RESERVED,
            FinalIntervalRecord.ENTITLEMENT_SPENT,
            FinalIntervalRecord.ENTITLEMENT_CLOSED,
        }:
            chances[row["lane_id"]] = {"challenge_id": row["challenge_id"], "state": row["state"]}
        elif record is FinalIntervalRecord.DRAIN_RESULT:
            submissions[row["candidate_id"]] = {"outcome": row["outcome"], "observed_at": row["observed_at"]}
        elif record is FinalIntervalRecord.CLEANUP_RESULT:
            cleanup[row["reservation_id"]] = row["outcome"]
        elif record is FinalIntervalRecord.TERMINAL_INVENTORY:
            result["terminal"] = dict(row["inventory"])
    return result


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
            or final_submission_reserve_seconds >= (ends_at - opened_at).total_seconds()
            or not enabled_lanes
            or len(set(enabled_lanes)) != len(enabled_lanes)
        ):
            raise ValueError("final interval requires a valid Run window, reserve, floor, and Lane profile")
        self.state, self.run_id = Path(state), run_id
        self.opened_at, self.ends_at = opened_at, ends_at
        self.reserve_seconds, self.floor_seconds = final_submission_reserve_seconds, attempt_floor_seconds
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
            chance is None or chance["state"] != FinalChanceState.SPENT.value
        )

    def admission_mode(self, lane_id: str) -> AdmissionMode:
        self._require_lane(lane_id)
        now = self._observed_now()
        if now >= self.ends_at:
            return AdmissionMode.CLOSED
        if now >= self.cutoff:
            return AdmissionMode.SUBMISSION_RESERVE
        if (self.cutoff - now).total_seconds() < self.floor_seconds:
            return AdmissionMode.FINAL_CHANCE
        return AdmissionMode.ORDINARY

    def reserve_final_chance(self, lane_id: str, challenge_id: str) -> FinalChanceGrant | None:
        with self._lock:
            existing = self._document["final_chances"].get(lane_id)
            if self.admission_mode(lane_id) is not AdmissionMode.FINAL_CHANCE or (
                existing is not None and existing["state"] == FinalChanceState.SPENT.value
            ):
                return None
            if not challenge_id:
                raise ValueError("final chance requires a Challenge identity")
            now = self._observed_now()
            if existing is not None and existing["state"] == FinalChanceState.RESERVED.value:
                if str(existing["challenge_id"]) != challenge_id:
                    return None
                return FinalChanceGrant(lane_id, str(existing["challenge_id"]), now, self.cutoff)
            grant = self._reserve(f"final-chance:{lane_id}")
            reservation_id = str(getattr(grant, "key", f"final-chance:{lane_id}"))
            self._record(
                FinalIntervalRecord.ENTITLEMENT_RESERVED,
                now,
                lane_id=lane_id,
                challenge_id=challenge_id,
                reservation_id=reservation_id,
                state=FinalChanceState.RESERVED.value,
            )
            self._chance_transactions[lane_id] = grant
            return FinalChanceGrant(lane_id, challenge_id, now, self.cutoff, grant, True)

    def spend_final_chance(self, grant: FinalChanceGrant) -> FinalChanceGrant:
        with self._lock:
            current = self._document["final_chances"].get(grant.lane_id)
            if current is None or current["challenge_id"] != grant.challenge_id:
                raise ValueError("final chance has no matching durable reservation")
            if current["state"] == FinalChanceState.SPENT.value:
                return grant
            reservation = self._begin(
                grant.transaction if grant.reservation_acquired else self._reserve(f"final-chance:{grant.lane_id}")
            )
            reservation_id = str(getattr(reservation, "key", f"final-chance:{grant.lane_id}"))
            now = self._observed_now()
            self._record(
                FinalIntervalRecord.ENTITLEMENT_SPENT,
                now,
                lane_id=grant.lane_id,
                challenge_id=grant.challenge_id,
                reservation_id=reservation_id,
                state=FinalChanceState.SPENT.value,
            )
            self._finish(reservation, {"state": FinalChanceState.SPENT.value})
            self._chance_transactions.pop(grant.lane_id, None)
            return grant

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
                    outcomes.append(
                        SubmissionDisposition.UNKNOWN_AND_SPENT.value
                        if prior == SubmissionDisposition.POSSIBLY_SENT.value
                        else prior
                    )
                    continue
                grant = self._reserve(f"submission:{candidate}")
                grant = self._begin(grant)
                reservation_id = str(getattr(grant, "key", f"submission:{candidate}"))
                self._record(
                    FinalIntervalRecord.DRAIN_REQUESTED, now, candidate_id=candidate, reservation_id=reservation_id
                )
                try:
                    outcome = SubmissionDisposition(submit(candidate))
                    self._hook("after_submission")
                except SubmissionDeferred:
                    continue
                except EffectIndeterminate:
                    outcome = SubmissionDisposition.POSSIBLY_SENT
                self._record(
                    FinalIntervalRecord.DRAIN_RESULT,
                    now,
                    candidate_id=candidate,
                    reservation_id=reservation_id,
                    outcome=outcome.value,
                )
                self._finish(grant, {"outcome": outcome.value})
                outcomes.append(outcome.value)
            return tuple(outcomes)

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
                return dict(terminal)
            grant = self._reserve_terminal("run-close")
            grant = self._begin(grant)
            reservation_id = str(getattr(grant, "key", "run-close"))
            chances = self._document["final_chances"]
            for lane_id, chance in tuple(chances.items()):
                if chance["state"] == FinalChanceState.RESERVED.value:
                    transaction = (
                        self._chance_transactions[lane_id]
                        if lane_id in self._chance_transactions
                        else self._reserve(f"final-chance:{lane_id}")
                    )
                    transaction = self._begin(transaction)
                    self._record(
                        FinalIntervalRecord.ENTITLEMENT_CLOSED,
                        now,
                        lane_id=lane_id,
                        challenge_id=str(chance["challenge_id"]),
                        reservation_id=f"final-interval:final-chance:{lane_id}",
                        state=FinalChanceState.CLOSED.value,
                    )
                    self._finish(transaction, {"state": FinalChanceState.CLOSED.value})
                    self._chance_transactions.pop(lane_id, None)
            self._record(FinalIntervalRecord.CLEANUP_REQUESTED, now, reservation_id=reservation_id)
            try:
                cleanup_outcomes = dict(cleanup())
            except Exception:
                cleanup_outcomes = {"cleanup": CleanupDisposition.UNSETTLED.value}
            remaining = inventory() if callable(inventory) else inventory
            for identity, value in cleanup_outcomes.items():
                outcome = (
                    CleanupDisposition.RELEASED
                    if value == CleanupDisposition.RELEASED.value
                    else CleanupDisposition.UNSETTLED
                )
                self._record(FinalIntervalRecord.CLEANUP_RESULT, now, reservation_id=identity, outcome=outcome.value)
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
            self._finish(grant, {"disposition": disposition.value})
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

    def _begin(self, grant: object) -> object:
        if self._write_authority is not None and isinstance(grant, WriteReservation):
            if grant.state is ReservationState.STARTED:
                return grant
            if grant.state is not ReservationState.RESERVED:
                raise EffectIndeterminate(f"final interval transaction is already {grant.state.value}")
            return self._write_authority.start(grant)
        return grant

    def _finish(self, grant: object, observation: Mapping[str, object]) -> None:
        if self._write_authority is not None and isinstance(grant, WriteReservation):
            committed = self._write_authority.commit(grant, observation)
            if committed.retention.requires_receipt:
                self._write_authority.write_receipt(committed.key)
            if committed.retention.retains_object:
                self._write_authority.release_retained(committed, "final-interval transaction durably closed")

    def _observed_now(self) -> dt.datetime:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() != dt.timedelta(0) or now < self.opened_at:
            raise ValueError("final-interval clock must be UTC and inside the declared Run")
        return now

    def _require_lane(self, lane_id: str) -> None:
        if lane_id not in self.enabled_lanes:
            raise ValueError("Lane is not enabled by the selected profile")


def verify_receipt(receipt: Mapping[str, object]) -> None:
    required = {
        "schema_version",
        "kind",
        "producer",
        "run_id",
        "window",
        "enabled_lanes",
        "final_chances",
        "submissions",
        "terminal",
        "trace",
        "trace_digest",
    }
    if (
        set(receipt) != required
        or receipt.get("schema_version") != SCHEMA_VERSION
        or receipt.get("kind") != RECEIPT_KIND
    ):
        raise ValueError("final-interval receipt shape is invalid")
    window = receipt["window"]
    if not isinstance(window, Mapping):
        raise ValueError("final-interval window is invalid")
    opened, cutoff, ends = (
        dt.datetime.fromisoformat(str(window[key])) for key in ("opened_at", "final_submission_cutoff", "ends_at")
    )
    floor = window.get("attempt_floor_seconds")
    if (
        not opened < cutoff < ends
        or (ends - cutoff).total_seconds() != window.get("reserve_seconds")
        or type(floor) is not int
        or floor <= 0
    ):
        raise ValueError("final-interval boundaries are invalid")
    lanes, chances = receipt["enabled_lanes"], receipt["final_chances"]
    if (
        not isinstance(lanes, list)
        or not lanes
        or len(lanes) != len(set(lanes))
        or not isinstance(chances, Mapping)
        or not set(chances).issubset(lanes)
    ):
        raise ValueError("final-chance ledger is invalid")
    submissions = receipt["submissions"]
    trace = receipt["trace"]
    if not isinstance(submissions, Mapping):
        raise ValueError("final-interval submission projection is invalid")
    if not isinstance(trace, list) or digest_bytes(canonical_bytes(trace)) != receipt["trace_digest"]:
        raise ValueError("final-interval trace is invalid")
    prior = opened
    chance_trace: dict[str, list[Mapping[str, object]]] = {lane: [] for lane in lanes}
    drains: dict[str, list[str]] = {}
    projected_submissions: dict[str, dict[str, str]] = {}
    projected_cleanup: dict[str, str] = {}
    projected_terminals: list[Mapping[str, object]] = []
    for event in trace:
        if not isinstance(event, Mapping):
            raise ValueError("final-interval trace is invalid")
        observed = dt.datetime.fromisoformat(str(event.get("observed_at", "")))
        if observed < prior:
            raise ValueError("final-interval trace clock is invalid")
        prior = observed
        record = str(event.get("record", ""))
        if record.startswith("entitlement-"):
            lane = str(event.get("lane_id", ""))
            if lane not in chance_trace:
                raise ValueError("final-chance trace names a disabled Lane")
            chance_trace[lane].append(event)
            if record in {
                FinalIntervalRecord.ENTITLEMENT_RESERVED.value,
                FinalIntervalRecord.ENTITLEMENT_SPENT.value,
            } and not (cutoff - dt.timedelta(seconds=floor) <= observed):
                raise ValueError("final-chance clock is invalid")
            if (
                record
                in {
                    FinalIntervalRecord.ENTITLEMENT_RESERVED.value,
                    FinalIntervalRecord.ENTITLEMENT_SPENT.value,
                }
                and observed >= cutoff
            ):
                raise ValueError("final-chance clock is invalid")
        elif record in {FinalIntervalRecord.DRAIN_REQUESTED.value, FinalIntervalRecord.DRAIN_RESULT.value}:
            candidate = str(event.get("candidate_id", ""))
            if not candidate:
                raise ValueError("Candidate drain trace is invalid")
            drains.setdefault(candidate, []).append(record)
            if record == FinalIntervalRecord.DRAIN_REQUESTED.value and not cutoff <= observed < ends:
                raise ValueError("Candidate drain clock is invalid")
            if record == FinalIntervalRecord.DRAIN_RESULT.value:
                outcome = str(event.get("outcome", ""))
                if outcome not in {item.value for item in SubmissionDisposition}:
                    raise ValueError("Candidate submission projection is invalid")
                projected_submissions[candidate] = {"outcome": outcome, "observed_at": observed.isoformat()}
        elif record == FinalIntervalRecord.CLEANUP_RESULT.value:
            projected_cleanup[str(event.get("reservation_id", ""))] = str(event.get("outcome", ""))
        elif record == FinalIntervalRecord.TERMINAL_INVENTORY.value:
            inventory = event.get("inventory")
            if not isinstance(inventory, Mapping):
                raise ValueError("final-interval terminal projection is invalid")
            projected_terminals.append(inventory)
    for lane, events in chance_trace.items():
        records = [str(event["record"]) for event in events]
        if records not in (
            [],
            ["entitlement-reserved", "entitlement-spent"],
            ["entitlement-reserved", "entitlement-closed"],
        ):
            raise ValueError("final-chance trace is not zero-or-one")
        if records:
            projected = chances.get(lane)
            expected = {
                "challenge_id": str(events[-1].get("challenge_id", "")),
                "state": str(events[-1].get("state", "")),
            }
            if projected != expected:
                raise ValueError("final-chance trace contradicts its ledger")
        elif lane in chances:
            raise ValueError("final-chance ledger lacks its trace")
    if any(records not in (["drain-requested"], ["drain-requested", "drain-result"]) for records in drains.values()):
        raise ValueError("Candidate drain trace is not replay-monotone")
    if dict(submissions) != projected_submissions:
        raise ValueError("final-interval submission projection contradicts its trace")
    terminal = receipt["terminal"]
    if (
        len(projected_terminals) != 1
        or not isinstance(terminal, Mapping)
        or dict(terminal) != dict(projected_terminals[0])
    ):
        raise ValueError("final-interval terminal projection contradicts its trace")
    remaining, cleanup = terminal.get("remaining"), terminal.get("cleanup")
    if (
        set(terminal) != {"closed_at", "official_ends_at", "remaining", "cleanup", "disposition"}
        or terminal.get("official_ends_at") != ends.isoformat()
        or not opened <= dt.datetime.fromisoformat(str(terminal.get("closed_at", ""))) <= ends
        or not isinstance(remaining, Mapping)
        or set(remaining) != {"attempts", "instances", "submissions"}
        or any(
            not isinstance(remaining[name], list) or len(remaining[name]) != len(set(remaining[name]))
            for name in remaining
        )
        or not isinstance(cleanup, Mapping)
        or dict(cleanup) != projected_cleanup
    ):
        raise ValueError("final-interval terminal projection is invalid")
    expected_disposition = (
        RunDisposition.CLOSED_WITH_UNSETTLED_CLEANUP.value
        if any(value != CleanupDisposition.RELEASED.value for value in cleanup.values())
        else RunDisposition.CLOSED.value
    )
    if terminal.get("disposition") != expected_disposition or not {
        candidate for candidate, row in submissions.items() if row.get("outcome") == "possibly-sent"
    }.issubset(remaining["submissions"]):
        raise ValueError("final-interval terminal projection is invalid")


__all__ = [
    "AdmissionMode",
    "FinalChanceGrant",
    "FinalIntervalController",
    "RunInventory",
    "project_final_interval",
    "verify_receipt",
]
