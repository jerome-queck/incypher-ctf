"""Validation for final-interval receipts."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.final_interval_contracts import (
    AdmissionMode,
    CleanupDisposition,
    FinalChanceState,
    FinalIntervalRecord,
    RECEIPT_KIND,
    RunDisposition,
    SCHEMA_VERSION,
    SubmissionDisposition,
)


@dataclass(frozen=True)
class _ReceiptShape:
    window: Mapping[str, object]
    opened: dt.datetime
    cutoff: dt.datetime
    ends: dt.datetime
    floor: int
    lanes: list[object]
    chances: Mapping[object, object]
    submissions: Mapping[object, object]
    terminal: object
    trace: list[object]


@dataclass
class _TraceState:
    transitions: dict[AdmissionMode, tuple[int, dt.datetime]]
    chance_trace: dict[object, list[Mapping[str, object]]]
    drains: dict[str, list[str]]
    drain_authorities: dict[str, str]
    projected_submissions: dict[str, dict[str, str]]
    projected_cleanup: dict[str, str]
    cleanup_history: dict[str, list[str]]
    projected_terminals: list[Mapping[str, object]]
    cleanup_requests: int
    cleanup_authority: str
    cleanup_targets: tuple[str, ...]


def verify_receipt(receipt: Mapping[str, object]) -> None:
    shape = _validate_receipt_shape(receipt)
    state = _replay_trace(shape)
    _validate_trace_projection(shape, state)
    _validate_submission_projection(shape, state)
    _validate_terminal(shape, state)


def _validate_receipt_shape(receipt: Mapping[str, object]) -> _ReceiptShape:
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
        or any(
            not isinstance(window.get(name), (int, float))
            or isinstance(window.get(name), bool)
            or window.get(name) <= 0
            for name in (
                "account_post_interval_seconds",
                "submission_request_deadline_seconds",
                "submission_uncertainty_margin_seconds",
            )
        )
        or window["submission_request_deadline_seconds"] + window["submission_uncertainty_margin_seconds"]
        >= window["reserve_seconds"]
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
    return _ReceiptShape(
        window=window,
        opened=opened,
        cutoff=cutoff,
        ends=ends,
        floor=floor,
        lanes=lanes,
        chances=chances,
        submissions=submissions,
        terminal=receipt["terminal"],
        trace=trace,
    )


def _replay_trace(shape: _ReceiptShape) -> _TraceState:
    state = _TraceState(
        transitions={},
        chance_trace={lane: [] for lane in shape.lanes},
        drains={},
        drain_authorities={},
        projected_submissions={},
        projected_cleanup={},
        cleanup_history={},
        projected_terminals=[],
        cleanup_requests=0,
        cleanup_authority="",
        cleanup_targets=(),
    )
    prior = shape.opened
    allowed_records = {item.value for item in FinalIntervalRecord}
    transition_rank = -1
    for event_index, event in enumerate(shape.trace):
        if not isinstance(event, Mapping):
            raise ValueError("final-interval trace is invalid")
        try:
            observed = dt.datetime.fromisoformat(str(event.get("observed_at", "")))
        except ValueError:
            raise ValueError("final-interval trace clock is invalid") from None
        if observed < prior:
            raise ValueError("final-interval trace clock is invalid")
        prior = observed
        record = str(event.get("record", ""))
        if record not in allowed_records:
            raise ValueError("final-interval trace record is invalid")
        if record == FinalIntervalRecord.TRANSITION.value:
            try:
                mode = AdmissionMode(str(event.get("state", "")))
            except ValueError:
                raise ValueError("final-interval transition is invalid") from None
            rank = tuple(AdmissionMode).index(mode)
            if mode is AdmissionMode.ORDINARY or rank <= transition_rank:
                raise ValueError("final-interval transition is not monotone")
            if mode is AdmissionMode.FINAL_CHANCE and not (
                shape.cutoff - dt.timedelta(seconds=shape.floor) < observed < shape.cutoff
            ):
                raise ValueError("final-chance transition clock is invalid")
            if mode is AdmissionMode.SUBMISSION_RESERVE and not shape.cutoff <= observed < shape.ends:
                raise ValueError("submission-reserve transition clock is invalid")
            transition_rank = rank
            state.transitions[mode] = (event_index, observed)
        elif record.startswith("entitlement-"):
            lane = str(event.get("lane_id", ""))
            if lane not in state.chance_trace:
                raise ValueError("final-chance trace names a disabled Lane")
            state.chance_trace[lane].append(event)
            final_transition = state.transitions.get(AdmissionMode.FINAL_CHANCE)
            if final_transition is None or final_transition[0] >= event_index:
                raise ValueError("final chance lacks its transition")
            if record in {
                FinalIntervalRecord.ENTITLEMENT_RESERVED.value,
                FinalIntervalRecord.ENTITLEMENT_SPENT.value,
            } and not (shape.cutoff - dt.timedelta(seconds=shape.floor) <= observed):
                raise ValueError("final-chance clock is invalid")
            if (
                record
                in {
                    FinalIntervalRecord.ENTITLEMENT_RESERVED.value,
                    FinalIntervalRecord.ENTITLEMENT_SPENT.value,
                }
                and observed >= shape.cutoff
            ):
                raise ValueError("final-chance clock is invalid")
        elif record in {
            FinalIntervalRecord.DRAIN_REQUESTED.value,
            FinalIntervalRecord.DRAIN_RESULT.value,
            FinalIntervalRecord.SUBMISSION_RECONCILED.value,
        }:
            candidate = str(event.get("candidate_id", ""))
            if not candidate:
                raise ValueError("Candidate drain trace is invalid")
            state.drains.setdefault(candidate, []).append(record)
            submission_transition = state.transitions.get(AdmissionMode.SUBMISSION_RESERVE)
            if submission_transition is None or submission_transition[0] >= event_index:
                raise ValueError("Candidate drain lacks its transition")
            reservation_id = str(event.get("reservation_id", ""))
            if not reservation_id:
                raise ValueError("Candidate drain authority is invalid")
            if record == FinalIntervalRecord.DRAIN_REQUESTED.value and not shape.cutoff <= observed < shape.ends:
                raise ValueError("Candidate drain clock is invalid")
            if record == FinalIntervalRecord.DRAIN_REQUESTED.value:
                state.drain_authorities[candidate] = reservation_id
            elif state.drain_authorities.get(candidate) != reservation_id:
                raise ValueError("Candidate drain authority is invalid")
            if record in {
                FinalIntervalRecord.DRAIN_RESULT.value,
                FinalIntervalRecord.SUBMISSION_RECONCILED.value,
            }:
                outcome = str(event.get("outcome", ""))
                if outcome not in {item.value for item in SubmissionDisposition}:
                    raise ValueError("Candidate submission projection is invalid")
                if record == FinalIntervalRecord.SUBMISSION_RECONCILED.value and (
                    outcome == SubmissionDisposition.POSSIBLY_SENT.value
                    or state.projected_submissions.get(candidate, {}).get("outcome")
                    != SubmissionDisposition.POSSIBLY_SENT.value
                ):
                    raise ValueError("Candidate reconciliation trace is invalid")
                state.projected_submissions[candidate] = {
                    "outcome": outcome,
                    "observed_at": observed.isoformat(),
                }
        elif record == FinalIntervalRecord.CLEANUP_REQUESTED.value:
            closed_transition = state.transitions.get(AdmissionMode.CLOSED)
            request_inventory = event.get("inventory")
            targets = request_inventory.get("targets") if isinstance(request_inventory, Mapping) else None
            if (
                closed_transition is None
                or closed_transition[0] >= event_index
                or not event.get("reservation_id")
                or not isinstance(targets, list)
                or not targets
                or "run-cleanup" not in targets
                or len(targets) != len(set(targets))
                or any(not _valid_cleanup_identity(str(target)) for target in targets)
            ):
                raise ValueError("cleanup request lacks its closed transition")
            state.cleanup_requests += 1
            state.cleanup_authority = str(event["reservation_id"])
            state.cleanup_targets = tuple(str(target) for target in targets)
        elif record == FinalIntervalRecord.CLEANUP_RESULT.value:
            if state.cleanup_requests != 1:
                raise ValueError("cleanup result lacks its request")
            reservation_id = str(event.get("reservation_id", ""))
            target = str(event.get("challenge_id", ""))
            outcome = str(event.get("outcome", ""))
            if (
                reservation_id != state.cleanup_authority
                or target not in state.cleanup_targets
                or outcome not in {item.value for item in CleanupDisposition}
            ):
                raise ValueError("cleanup result is invalid")
            state.cleanup_history.setdefault(target, []).append(outcome)
            state.projected_cleanup[target] = outcome
        elif record == FinalIntervalRecord.TERMINAL_INVENTORY.value:
            if (
                state.cleanup_requests != 1
                or event_index != len(shape.trace) - 1
                or event.get("reservation_id") != state.cleanup_authority
            ):
                raise ValueError("terminal inventory lacks its cleanup request")
            inventory = event.get("inventory")
            if not isinstance(inventory, Mapping):
                raise ValueError("final-interval terminal projection is invalid")
            state.projected_terminals.append(inventory)
    return state


def _validate_trace_projection(shape: _ReceiptShape, state: _TraceState) -> None:
    for lane, events in state.chance_trace.items():
        records = [str(event["record"]) for event in events]
        if records not in (
            [],
            ["entitlement-reserved", "entitlement-spent"],
            ["entitlement-reserved", "entitlement-closed"],
        ):
            raise ValueError("final-chance trace is not zero-or-one")
        if records:
            reservation_ids = {str(event.get("reservation_id", "")) for event in events}
            if "" in reservation_ids or len(reservation_ids) != 1:
                raise ValueError("final-chance trace has inconsistent authority")
            first, last = events
            if (
                first.get("state") != FinalChanceState.RESERVED.value
                or str(first.get("challenge_id", ""))
                or last.get("state") not in {FinalChanceState.SPENT.value, FinalChanceState.CLOSED.value}
                or (last.get("state") == FinalChanceState.SPENT.value and not str(last.get("challenge_id", "")))
                or (last.get("state") == FinalChanceState.CLOSED.value and str(last.get("challenge_id", "")))
            ):
                raise ValueError("final-chance trace state is invalid")
            projected = shape.chances.get(lane)
            expected = {
                "challenge_id": str(events[-1].get("challenge_id", "")),
                "state": str(events[-1].get("state", "")),
            }
            if projected != expected:
                raise ValueError("final-chance trace contradicts its ledger")
        elif lane in shape.chances:
            raise ValueError("final-chance ledger lacks its trace")
    if any(
        records
        not in (
            ["drain-requested"],
            ["drain-requested", "drain-result"],
            ["drain-requested", "drain-result", "submission-reconciled"],
        )
        for records in state.drains.values()
    ):
        raise ValueError("Candidate drain trace is not replay-monotone")


def _validate_submission_projection(shape: _ReceiptShape, state: _TraceState) -> None:
    if dict(shape.submissions) != state.projected_submissions:
        raise ValueError("final-interval submission projection contradicts its trace")


def _validate_terminal(shape: _ReceiptShape, state: _TraceState) -> None:
    if state.cleanup_requests != 1:
        raise ValueError("final-interval cleanup request is invalid")
    if any(
        outcomes
        not in (
            [CleanupDisposition.RELEASED.value],
            [CleanupDisposition.UNSETTLED.value],
            [
                CleanupDisposition.UNSETTLED.value,
                CleanupDisposition.RELEASED.value,
            ],
        )
        for outcomes in state.cleanup_history.values()
    ):
        raise ValueError("cleanup result is not replay-monotone")
    if AdmissionMode.CLOSED not in state.transitions:
        raise ValueError("final-interval closed transition is absent")
    terminal = shape.terminal
    if (
        len(state.projected_terminals) != 1
        or not isinstance(terminal, Mapping)
        or dict(terminal) != dict(state.projected_terminals[0])
    ):
        raise ValueError("final-interval terminal projection contradicts its trace")
    remaining, cleanup = terminal.get("remaining"), terminal.get("cleanup")
    if (
        set(terminal) != {"closed_at", "official_ends_at", "remaining", "cleanup", "disposition"}
        or terminal.get("official_ends_at") != shape.ends.isoformat()
        or not shape.opened <= dt.datetime.fromisoformat(str(terminal.get("closed_at", ""))) <= shape.ends
        or not isinstance(remaining, Mapping)
        or set(remaining) != {"attempts", "instances", "submissions"}
        or any(
            not isinstance(remaining[name], list) or len(remaining[name]) != len(set(remaining[name]))
            for name in remaining
        )
        or not isinstance(cleanup, Mapping)
        or dict(cleanup) != state.projected_cleanup
        or set(cleanup) != set(state.cleanup_targets)
    ):
        raise ValueError("final-interval terminal projection is invalid")
    expected_disposition = (
        RunDisposition.CLOSED_WITH_UNSETTLED_CLEANUP.value
        if any(value != CleanupDisposition.RELEASED.value for value in cleanup.values())
        else RunDisposition.CLOSED.value
    )
    if terminal.get("disposition") != expected_disposition or not {
        candidate for candidate, row in shape.submissions.items() if row.get("outcome") == "possibly-sent"
    }.issubset(remaining["submissions"]):
        raise ValueError("final-interval terminal projection is invalid")
    for kind in ("attempt", "instance"):
        left = set(remaining[f"{kind}s"])
        if any(f"{kind}:{identity}" not in state.cleanup_targets for identity in left):
            raise ValueError("terminal inventory contains an unauthorized cleanup target")
        for target, outcome in cleanup.items():
            prefix = f"{kind}:"
            if target.startswith(prefix) and ((outcome == "released") == (target[len(prefix) :] in left)):
                raise ValueError("cleanup outcome contradicts terminal inventory")


def _valid_cleanup_identity(identity: str) -> bool:
    return identity == "run-cleanup" or any(
        identity.startswith(prefix) and len(identity) > len(prefix) for prefix in ("attempt:", "instance:")
    )


__all__ = ["verify_receipt"]
