import datetime as dt
import copy
import json
import subprocess

import pytest

from solver.final_interval import FinalIntervalController, RunInventory, verify_receipt as verify_runtime_receipt
from solver.final_interval_evaluator import verify_receipt as verify_evaluator
from solver.final_interval_evaluator import _verify_production_observation
from solver.final_interval_evaluator import _verify_submission_timing
from solver.event_store_storage import canonical_bytes
from solver.manifest import generate_manifest
from scripts.eval_final_interval import evaluate
from test_manifest import release_candidate_profile
from solver.attempt_executor_contracts import EnvelopeSpec, NetworkPolicy
from solver.instance_lease_contracts import LeaseIdentity, LeasePhase
from solver.submission.ambiguity_types import CompleteSubmissionIdentity
from solver.lane_topology import LaneController
from solver.lane_topology_contracts import LaneOutcome, LaneProfile, OwnerTermination, WorkCandidate
from solver.redaction import Redactor
from solver.work_generation import GenerationFence
from solver.order_runtime import CanonicalScheduler
from solver.record import Recorder
from solver.schedule import Dials, Ended
from solver.event_store_contracts import GenerationDisposition
from solver.event_store import EventStore
from solver.final_interval_contracts import FINAL_INTERVAL_RECORDED, AdmissionMode, SubmissionDeferred
from solver.write_reservation import Capacity, EffectIdentity, ReservationUnavailable, RetentionPolicy
from test_order_runtime import _canonical_authority, _legacy, _window
from test_entry_point import PlayableBoard, env, wired


UTC = dt.timezone.utc
OPEN = dt.datetime(2026, 9, 22, 1, tzinfo=UTC)
END = OPEN + dt.timedelta(hours=1)


def controller(tmp_path, clock, *, lanes=("lane-1", "lane-2"), reserve=60, reservations=None):
    reservations = reservations if reservations is not None else []
    return FinalIntervalController(
        state=tmp_path,
        run_id="run-1",
        opened_at=OPEN,
        ends_at=END,
        final_submission_reserve_seconds=reserve,
        attempt_floor_seconds=300,
        enabled_lanes=lanes,
        now=lambda: clock[0],
        reserve=lambda identity: reservations.append(identity),
    )


def test_boundary_stops_normal_admission_exactly_at_declared_submission_reserve(tmp_path):
    clock = [END - dt.timedelta(seconds=61)]
    final = controller(tmp_path, clock)

    assert final.admission_mode("lane-1") == "final-chance"
    clock[0] = END - dt.timedelta(seconds=60)
    assert final.admission_mode("lane-1") == "submission-reserve"
    assert final.admit_final_chance("lane-1", "challenge-7") is None


def test_reserved_final_chance_closes_if_admission_crosses_exact_cutoff(tmp_path):
    clock = [END - dt.timedelta(seconds=61)]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    grant = final.reserve_final_chance("lane-1", "challenge-7")
    assert grant is not None

    clock[0] = END - dt.timedelta(seconds=60)
    with pytest.raises(ValueError, match="crossed"):
        final.spend_final_chance(grant)

    assert final.receipt()["final_chances"] == {"lane-1": {"challenge_id": "", "state": "closed"}}


def test_each_enabled_lane_gets_at_most_one_final_chance_across_restart(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    first = controller(tmp_path, clock)

    grant = first.admit_final_chance("lane-1", "challenge-7")
    assert grant and grant.deadline == END - dt.timedelta(seconds=60)
    restarted = controller(tmp_path, clock)
    assert restarted.admit_final_chance("lane-1", "challenge-8") is None
    assert restarted.admit_final_chance("lane-2", "challenge-8") is not None
    assert restarted.receipt()["final_chances"] == {
        "lane-1": {"challenge_id": "challenge-7", "state": "spent"},
        "lane-2": {"challenge_id": "challenge-8", "state": "spent"},
    }
    assert not (tmp_path / "runs" / "run-1" / "final-interval.control.json").exists()
    records = [
        event.payload["record"]
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.event_type == FINAL_INTERVAL_RECORDED
    ]
    assert records == ["transition", "entitlement-reserved", "entitlement-spent"] + [
        "entitlement-reserved",
        "entitlement-spent",
    ]


def test_reserved_entitlement_reacquires_real_authority_after_restart(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]

    def compose(recorder):
        def reserve(identity):
            return recorder.write_authority.reserve(
                f"final-interval:{identity}",
                EffectIdentity("final-interval.authority", identity),
                Capacity(4096, 1, 3),
                retention=RetentionPolicy.RELEASE,
                retry_aborted=True,
            )

        return FinalIntervalController(
            state=tmp_path,
            run_id="run-1",
            opened_at=OPEN,
            ends_at=END,
            final_submission_reserve_seconds=60,
            attempt_floor_seconds=300,
            enabled_lanes=("lane-1",),
            now=lambda: clock[0],
            reserve=reserve,
            event_store=recorder.event_store,
            write_authority=recorder.write_authority,
        )

    first_recorder = Recorder(tmp_path, "run-1", Redactor({}))
    first = compose(first_recorder)
    assert first.reserve_final_chance("lane-1", "challenge-7") is not None
    first_recorder.write_authority.close()

    restarted_recorder = Recorder(tmp_path, "run-1", Redactor({}))
    restarted = compose(restarted_recorder)
    assert restarted.admit_final_chance("lane-1", "challenge-7") is not None
    assert restarted.receipt()["final_chances"] == {"lane-1": {"challenge_id": "challenge-7", "state": "spent"}}


def test_reserved_lane_entitlement_can_follow_fresh_order_after_restart(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    final = controller(tmp_path, clock, lanes=("lane-1",))

    assert final.reserve_final_chance("lane-1", "challenge-a") is not None
    replacement = controller(tmp_path, clock, lanes=("lane-1",)).reserve_final_chance("lane-1", "challenge-b")
    assert replacement is not None
    controller(tmp_path, clock, lanes=("lane-1",)).spend_final_chance(replacement)
    assert controller(tmp_path, clock, lanes=("lane-1",)).receipt()["final_chances"] == {
        "lane-1": {"challenge_id": "challenge-b", "state": "spent"}
    }


def test_final_interval_phase_never_regresses_or_regrants_after_terminal_restart(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    final = controller(tmp_path, clock, lanes=("lane-1",))

    assert final.admission_mode("lane-1") is AdmissionMode.FINAL_CHANCE
    clock[0] = OPEN + dt.timedelta(seconds=1)
    assert final.admission_mode("lane-1") is AdmissionMode.FINAL_CHANCE
    clock[0] = END
    final.close(RunInventory(), lambda: {})
    clock[0] = OPEN + dt.timedelta(seconds=1)
    restarted = controller(tmp_path, clock, lanes=("lane-1",))
    assert restarted.admission_mode("lane-1") is AdmissionMode.CLOSED
    assert restarted.reserve_final_chance("lane-1", "challenge-b") is None


def test_receipt_rejects_duplicate_or_mistimed_final_chance_trace(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    final.admit_final_chance("lane-1", "challenge-7")
    clock[0] = END
    final.close(RunInventory(), lambda: {})
    receipt = final.receipt()
    verify_runtime_receipt(receipt)

    duplicate = copy.deepcopy(receipt)
    duplicate["trace"].insert(1, duplicate["trace"][1].copy())
    duplicate["trace_digest"] = __import__("hashlib").sha256(canonical_bytes(duplicate["trace"])).hexdigest()
    with pytest.raises(ValueError, match="final-chance trace"):
        verify_runtime_receipt(duplicate)

    mistimed = copy.deepcopy(receipt)
    reserved = next(row for row in mistimed["trace"] if row["record"] == "entitlement-reserved")
    reserved["observed_at"] = (END - dt.timedelta(seconds=30)).isoformat()
    mistimed["trace_digest"] = __import__("hashlib").sha256(canonical_bytes(mistimed["trace"])).hexdigest()
    with pytest.raises(ValueError, match="final-chance clock"):
        verify_runtime_receipt(mistimed)

    substituted = copy.deepcopy(receipt)
    accepted = next(iter(substituted["submissions"]), None)
    if accepted is None:
        substituted["submissions"]["invented-candidate"] = {
            "observed_at": END.isoformat(),
            "outcome": "accepted",
        }
    else:
        substituted["submissions"]["invented-candidate"] = substituted["submissions"].pop(accepted)
    with pytest.raises(ValueError, match="submission projection"):
        verify_runtime_receipt(substituted)

    invented_terminal = copy.deepcopy(receipt)
    invented_terminal["terminal"]["closed_at"] = (END + dt.timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="terminal projection"):
        verify_runtime_receipt(invented_terminal)

    changed_challenge = copy.deepcopy(receipt)
    entitlement = [row for row in changed_challenge["trace"] if row["record"].startswith("entitlement-")]
    entitlement[-1]["challenge_id"] = "another-challenge"
    entitlement[-1]["state"] = "invented"
    changed_challenge["trace_digest"] = (
        __import__("hashlib").sha256(canonical_bytes(changed_challenge["trace"])).hexdigest()
    )
    with pytest.raises(ValueError, match="final-chance trace"):
        verify_runtime_receipt(changed_challenge)

    unknown_record = copy.deepcopy(receipt)
    unknown_record["trace"][0]["record"] = "invented"
    unknown_record["trace_digest"] = __import__("hashlib").sha256(canonical_bytes(unknown_record["trace"])).hexdigest()
    with pytest.raises(ValueError, match="trace"):
        verify_runtime_receipt(unknown_record)

    missing_cleanup_request = copy.deepcopy(receipt)
    missing_cleanup_request["trace"] = [
        row for row in missing_cleanup_request["trace"] if row["record"] != "cleanup-requested"
    ]
    missing_cleanup_request["trace_digest"] = (
        __import__("hashlib").sha256(canonical_bytes(missing_cleanup_request["trace"])).hexdigest()
    )
    with pytest.raises(ValueError, match="cleanup"):
        verify_runtime_receipt(missing_cleanup_request)


def test_failed_whole_path_reservation_has_no_event_or_effect(tmp_path):
    clock = [END - dt.timedelta(seconds=60)]
    effects = []

    def refuse(_identity):
        raise ReservationUnavailable("whole path unavailable")

    final = FinalIntervalController(
        state=tmp_path,
        run_id="run-1",
        opened_at=OPEN,
        ends_at=END,
        final_submission_reserve_seconds=60,
        attempt_floor_seconds=300,
        enabled_lanes=("lane-1",),
        now=lambda: clock[0],
        reserve=refuse,
    )
    with pytest.raises(ReservationUnavailable):
        final.drain(("candidate-a",), lambda candidate: effects.append(candidate) or "accepted")
    assert effects == []
    assert not EventStore(tmp_path, run_id="run-1").events()


def test_receipt_binds_phase_drain_and_cleanup_authority(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    final.admit_final_chance("lane-1", "challenge-7")
    clock[0] = END - dt.timedelta(seconds=60)
    final.drain(("candidate-a",), lambda _candidate: "accepted")
    clock[0] = END
    final.close(RunInventory(), lambda: {"instance:lease-1": "released"})
    receipt = final.receipt()
    verify_runtime_receipt(receipt)

    def reseal(document):
        document["trace_digest"] = __import__("hashlib").sha256(canonical_bytes(document["trace"])).hexdigest()
        return document

    wrong_drain = copy.deepcopy(receipt)
    result = next(row for row in wrong_drain["trace"] if row["record"] == "drain-result")
    result["reservation_id"] = "another-reservation"
    with pytest.raises(ValueError, match="drain authority"):
        verify_runtime_receipt(reseal(wrong_drain))

    missing_phase = copy.deepcopy(receipt)
    missing_phase["trace"] = [
        row
        for row in missing_phase["trace"]
        if not (row["record"] == "transition" and row["state"] == "submission-reserve")
    ]
    with pytest.raises(ValueError, match="transition"):
        verify_runtime_receipt(reseal(missing_phase))

    wrong_phase_clock = copy.deepcopy(receipt)
    transition = next(
        row for row in wrong_phase_clock["trace"] if row["record"] == "transition" and row["state"] == "final-chance"
    )
    transition["observed_at"] = OPEN.isoformat()
    with pytest.raises(ValueError, match="transition clock"):
        verify_runtime_receipt(reseal(wrong_phase_clock))

    invented_cleanup = copy.deepcopy(receipt)
    cleanup = next(row for row in invented_cleanup["trace"] if row["record"] == "cleanup-result")
    cleanup["reservation_id"] = "invented"
    invented_cleanup["terminal"]["cleanup"] = {"invented": "released"}
    terminal = next(row for row in invented_cleanup["trace"] if row["record"] == "terminal-inventory")
    terminal["inventory"]["cleanup"] = {"invented": "released"}
    with pytest.raises(ValueError, match="cleanup result"):
        verify_runtime_receipt(reseal(invented_cleanup))


def test_ready_candidates_drain_serially_and_ambiguous_candidate_is_never_resent(tmp_path):
    clock = [END - dt.timedelta(seconds=60)]
    final = controller(tmp_path, clock)
    active = 0
    observed = []

    def submit(candidate):
        nonlocal active
        active += 1
        assert active == 1
        observed.append(candidate)
        active -= 1
        return "possibly-sent" if candidate == "candidate-a" else "accepted"

    assert final.drain(("candidate-a", "candidate-b"), submit) == ("possibly-sent", "accepted")
    restarted = controller(tmp_path, clock)
    assert restarted.drain(("candidate-a",), submit) == ("unknown-and-spent",)
    assert observed == ["candidate-a", "candidate-b"]


def test_candidate_drain_never_starts_another_post_at_the_official_end(tmp_path):
    clock = [END - dt.timedelta(seconds=60)]
    final = controller(tmp_path, clock)
    observed = []

    def submit(candidate):
        observed.append(candidate)
        clock[0] = END
        return "accepted"

    assert final.drain(("candidate-a", "candidate-b"), submit) == ("accepted",)
    assert observed == ["candidate-a"]


def test_terminal_inventory_is_truthful_and_cleanup_waits_for_official_close(tmp_path):
    clock = [END - dt.timedelta(seconds=1)]
    final = controller(tmp_path, clock)
    inventory = RunInventory(attempts=("attempt-1",), instances=("lease-1",), submissions=("candidate-a",))

    with pytest.raises(ValueError, match="official Run window"):
        final.close(inventory, cleanup=lambda: {"instance:lease-1": "released"})
    clock[0] = END
    terminal = final.close(inventory, cleanup=lambda: {"instance:lease-1": "unsettled"})

    assert terminal["remaining"] == {
        "attempts": ["attempt-1"],
        "instances": ["lease-1"],
        "submissions": ["candidate-a"],
    }
    assert terminal["cleanup"] == {
        "run-cleanup": "released",
        "attempt:attempt-1": "unsettled",
        "instance:lease-1": "unsettled",
    }
    assert terminal["closed_at"] == END.isoformat()


def test_terminal_inventory_is_projected_after_cleanup(tmp_path):
    clock = [END]
    final = controller(tmp_path, clock)
    instances = ["lease-1"]

    def cleanup():
        instances.clear()
        return {"instance:lease-1": "released"}

    terminal = final.close(
        lambda: RunInventory(attempts=("attempt-1",), instances=tuple(instances), submissions=()), cleanup
    )

    assert terminal["remaining"]["instances"] == []
    assert terminal["cleanup"] == {
        "run-cleanup": "released",
        "attempt:attempt-1": "unsettled",
        "instance:lease-1": "released",
    }


def test_authority_is_reserved_before_every_effect(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    effects = []
    final = controller(tmp_path, clock, reservations=effects)
    assert final.admit_final_chance("lane-1", "challenge-7") is not None
    clock[0] = END - dt.timedelta(seconds=60)
    final.drain(("candidate-a",), lambda candidate: effects.append(f"effect:{candidate}") or "accepted")
    clock[0] = END
    final.close(RunInventory(), lambda: effects.append("effect:cleanup") or {})

    assert effects == [
        "final-chance:lane-1",
        "submission:candidate-a",
        "effect:candidate-a",
        "run-close",
        "effect:cleanup",
    ]


def test_only_run_close_uses_terminal_authority(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    ordinary, terminal = [], []
    final = FinalIntervalController(
        state=tmp_path,
        run_id="run-1",
        opened_at=OPEN,
        ends_at=END,
        final_submission_reserve_seconds=60,
        attempt_floor_seconds=300,
        enabled_lanes=("lane-1",),
        now=lambda: clock[0],
        reserve=lambda identity: ordinary.append(identity),
        reserve_terminal=lambda identity: terminal.append(identity),
    )
    final.admit_final_chance("lane-1", "challenge-7")
    clock[0] = END - dt.timedelta(seconds=60)
    final.drain(("candidate-a",), lambda _candidate: "accepted")
    clock[0] = END
    final.close(RunInventory(), lambda: {})

    assert ordinary == ["final-chance:lane-1", "submission:candidate-a"]
    assert terminal == ["run-close"]


def test_released_ordinary_authority_does_not_require_a_retained_receipt(tmp_path):
    clock = [END - dt.timedelta(seconds=60)]
    recorder = Recorder(tmp_path, "run-1", Redactor({}))

    def reserve(identity):
        return recorder.write_authority.reserve(
            f"final-interval:{identity}",
            EffectIdentity("final-interval.authority", identity),
            Capacity(4096, 1, 3),
            retention=RetentionPolicy.RELEASE,
        )

    final = FinalIntervalController(
        state=tmp_path,
        run_id="run-1",
        opened_at=OPEN,
        ends_at=END,
        final_submission_reserve_seconds=60,
        attempt_floor_seconds=300,
        enabled_lanes=("lane-1",),
        now=lambda: clock[0],
        reserve=reserve,
        event_store=recorder.event_store,
        write_authority=recorder.write_authority,
    )

    assert final.drain(("candidate-a",), lambda _candidate: "accepted") == ("accepted",)


def test_cleanup_failure_still_records_truthful_terminal_inventory(tmp_path):
    clock = [END]
    final = controller(tmp_path, clock, lanes=("lane-1",))

    terminal = final.close(
        RunInventory(instances=("lease-1",)),
        lambda: (_ for _ in ()).throw(OSError("Board unavailable")),
    )

    assert terminal["disposition"] == "closed-with-unsettled-cleanup"
    assert terminal["remaining"]["instances"] == ["lease-1"]
    assert terminal["cleanup"] == {"run-cleanup": "unsettled", "instance:lease-1": "unsettled"}


def test_crash_after_submit_before_drain_result_reconciles_without_resend(tmp_path):
    clock = [END - dt.timedelta(seconds=60)]
    posts = []
    armed = [True]

    def hook(point):
        if point == "after_submission" and armed[0]:
            armed[0] = False
            raise RuntimeError("crash")

    first = FinalIntervalController(
        state=tmp_path,
        run_id="run-1",
        opened_at=OPEN,
        ends_at=END,
        final_submission_reserve_seconds=60,
        attempt_floor_seconds=300,
        enabled_lanes=("lane-1",),
        now=lambda: clock[0],
        reserve=lambda _identity: None,
        hook=hook,
    )

    def idempotent_submit(candidate):
        if candidate not in posts:
            posts.append(candidate)
        return "accepted"

    with pytest.raises(RuntimeError, match="crash"):
        first.drain(("candidate-a",), idempotent_submit)
    restarted = controller(tmp_path, clock, lanes=("lane-1",))
    assert restarted.drain(("candidate-a",), idempotent_submit) == ("accepted",)
    assert posts == ["candidate-a"]


def test_pre_wire_deferred_drain_retries_after_restart_without_indeterminate_outer_authority(tmp_path):
    clock = [END - dt.timedelta(seconds=60)]

    def compose(recorder):
        def reserve(identity):
            return recorder.write_authority.reserve(
                f"final-interval:{identity}",
                EffectIdentity("final-interval.authority", identity),
                Capacity(4096, 1, 3),
                retention=RetentionPolicy.RELEASE,
                retry_aborted=True,
            )

        return FinalIntervalController(
            state=tmp_path,
            run_id="run-1",
            opened_at=OPEN,
            ends_at=END,
            final_submission_reserve_seconds=60,
            attempt_floor_seconds=300,
            enabled_lanes=("lane-1",),
            now=lambda: clock[0],
            reserve=reserve,
            event_store=recorder.event_store,
            write_authority=recorder.write_authority,
        )

    first_recorder = Recorder(tmp_path, "run-1", Redactor({}))
    first = compose(first_recorder)

    def deferred(_candidate):
        raise SubmissionDeferred("not sent")

    assert first.drain(("candidate-a",), deferred) == ()
    first_recorder.write_authority.close()
    restarted_recorder = Recorder(tmp_path, "run-1", Redactor({}))
    restarted = compose(restarted_recorder)
    assert restarted.drain(("candidate-a",), lambda _candidate: "accepted") == ("accepted",)


def test_terminal_replay_recreates_receipt_after_crash_between_event_and_file(tmp_path, monkeypatch):
    clock = [END]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    monkeypatch.setattr(final, "_write_receipt", lambda: (_ for _ in ()).throw(OSError("crash")))

    with pytest.raises(OSError, match="crash"):
        final.close(RunInventory(), lambda: {})
    assert not final.receipt_path.exists()

    restarted = controller(tmp_path, clock, lanes=("lane-1",))
    terminal = restarted.close(RunInventory(attempts=("invented",)), lambda: {"invented": "unsettled"})
    assert restarted.receipt_path.exists()
    assert terminal["remaining"]["attempts"] == []
    verify_runtime_receipt(json.loads(restarted.receipt_path.read_bytes()))


def test_resolved_ambiguity_updates_terminal_submission_projection(tmp_path):
    clock = [END - dt.timedelta(seconds=60)]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    assert final.drain(("candidate-a",), lambda _candidate: "possibly-sent") == ("possibly-sent",)
    final.reconcile_submission("candidate-a", "unknown-and-spent")
    clock[0] = END
    final.close(RunInventory(), lambda: {})

    receipt = final.receipt()
    assert receipt["submissions"]["candidate-a"]["outcome"] == "unknown-and-spent"
    assert receipt["terminal"]["remaining"]["submissions"] == []
    verify_runtime_receipt(receipt)


def test_lane_scheduler_composition_clips_one_final_attempt_to_submission_cutoff(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    generations = GenerationFence(tmp_path, "run-1", Redactor({}), timestamp=lambda: clock[0].isoformat())
    lanes = LaneController(
        state=tmp_path / "runs",
        run_id="run-1",
        profile=LaneProfile(lanes=1, global_resource_units=1),
        generations=generations,
        timestamp=lambda: clock[0].isoformat(),
        terminate=lambda _binding: OwnerTermination(True, "terminated"),
        final_interval=final,
    )
    candidate = WorkCandidate(
        "challenge-7",
        1,
        600,
        1,
        LeaseIdentity("run-1", 1),
        EnvelopeSpec(10, 50_000, 1000, 1, 1000, NetworkPolicy.DENY, 600, 10),
    )

    result = lanes.run_cycle(lambda _excluded: (candidate,), lambda binding: LaneOutcome.complete(binding, seconds=1))

    assert len(result.timelines) == 1
    assert result.timelines[0].budget_seconds == 60


def test_lane_admission_crash_reuses_reserved_entitlement_and_spends_once(tmp_path):
    clock = [END - dt.timedelta(seconds=120)]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    generations = GenerationFence(tmp_path, "run-1", Redactor({}), timestamp=lambda: clock[0].isoformat())
    armed = [True]

    def crash(point):
        if point == "after_reservation" and armed[0]:
            armed[0] = False
            raise RuntimeError("crash")

    candidate = WorkCandidate(
        "challenge-7",
        1,
        600,
        1,
        None,
        EnvelopeSpec(10, 50_000, 1000, 1, 1000, NetworkPolicy.DENY, 600, 10),
    )
    first = LaneController(
        state=tmp_path / "runs",
        run_id="run-1",
        profile=LaneProfile(lanes=1, global_resource_units=1),
        generations=generations,
        timestamp=lambda: clock[0].isoformat(),
        terminate=lambda _binding: OwnerTermination(True, "terminated"),
        final_interval=final,
        hook=crash,
    )
    with pytest.raises(RuntimeError, match="crash"):
        first.run_cycle(lambda _excluded: (candidate,), lambda binding: LaneOutcome.complete(binding, seconds=1))

    restarted = LaneController(
        state=tmp_path / "runs",
        run_id="run-1",
        profile=LaneProfile(lanes=1, global_resource_units=1),
        generations=generations,
        timestamp=lambda: clock[0].isoformat(),
        terminate=lambda _binding: OwnerTermination(True, "terminated"),
        final_interval=FinalIntervalController(
            state=tmp_path,
            run_id="run-1",
            opened_at=OPEN,
            ends_at=END,
            final_submission_reserve_seconds=60,
            attempt_floor_seconds=300,
            enabled_lanes=("lane-1",),
            now=lambda: clock[0],
            reserve=lambda _identity: None,
        ),
    )
    result = restarted.run_cycle(
        lambda excluded: (candidate,) if not excluded else (),
        lambda binding: LaneOutcome.complete(binding, seconds=1),
    )

    assert len(result.timelines) == 1
    trace = restarted._final_interval.receipt()["trace"]
    assert [row["record"] for row in trace if row["record"].startswith("entitlement-")] == [
        "entitlement-reserved",
        "entitlement-spent",
    ]


def test_ordinary_lane_admission_is_fenced_when_setup_crosses_final_chance_boundary(tmp_path):
    clock = [END - dt.timedelta(seconds=361)]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    generations = GenerationFence(tmp_path, "run-1", Redactor({}), timestamp=lambda: clock[0].isoformat())
    candidate = WorkCandidate(
        "challenge-7",
        1,
        600,
        1,
        None,
        EnvelopeSpec(10, 50_000, 1000, 1, 1000, NetworkPolicy.DENY, 600, 10),
    )

    def cross_boundary(point):
        if point == "after_generation":
            clock[0] = END - dt.timedelta(seconds=359)

    result = LaneController(
        state=tmp_path / "runs",
        run_id="run-1",
        profile=LaneProfile(lanes=1, global_resource_units=1),
        generations=generations,
        timestamp=lambda: clock[0].isoformat(),
        terminate=lambda _binding: OwnerTermination(True, "terminated"),
        final_interval=final,
        hook=cross_boundary,
    ).run_cycle(lambda _excluded: (candidate,), lambda binding: LaneOutcome.complete(binding, seconds=1))

    assert result.timelines == ()
    assert [state.disposition.value for state in generations.projection().generations] == ["interrupt"]


def test_host_evaluator_signs_observed_board_and_cleanup_trace(tmp_path):
    clock = [END - dt.timedelta(seconds=60)]
    final = controller(tmp_path, clock, lanes=("lane-1",))
    final.drain(("candidate-id-a",), lambda _candidate: "accepted")
    clock[0] = END
    final.close(RunInventory(), lambda: {})
    source = tmp_path / "proof"
    source.mkdir()
    (source / "final-interval.receipt.json").write_bytes(canonical_bytes(final.receipt()) + b"\n")
    candidate_id = "candidate-id-a"
    complete = CompleteSubmissionIdentity("board", 1, "revision", "instance", "f" * 64, 1)
    observation = {
        "schema_version": 1,
        "run_id": "run-1",
        "board_posts": [{"body_digest": "d" * 64, "candidate_digest": complete.candidate_digest, "challenge_id": 1}],
        "released_instance_challenges": [],
        "instances_after": [],
        "observed_close_at": END.isoformat(),
    }
    (source / "host-observation.json").write_bytes(canonical_bytes(observation) + b"\n")
    key = tmp_path / "evaluator.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", key], check=True, capture_output=True)
    manifest = generate_manifest(
        image_digest="sha256:" + "a" * 64, release_candidate_profile=release_candidate_profile()
    )
    (source / "final-interval-profile.json").write_bytes(
        canonical_bytes(
            {
                "lifecycle": "sealed",
                "image_digest": manifest["candidate"]["image_digest"],
                "account_post_interval_seconds": 12.0,
                "submission_request_deadline_seconds": 30.0,
                "submission_uncertainty_margin_seconds": 2.0,
            }
        )
        + b"\n"
    )
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            key,
            "-in",
            source / "final-interval-profile.json",
            "-out",
            source / "final-interval-profile.sig",
        ],
        check=True,
    )
    configuration = {
        "kind": "exact-image-qualification-clock",
        "image_digest": manifest["candidate"]["image_digest"],
    }
    (source / "configuration.json").write_bytes(canonical_bytes(configuration) + b"\n")
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            key,
            "-in",
            source / "configuration.json",
            "-out",
            source / "configuration.sig",
        ],
        check=True,
    )
    production = {
        "board_requests": [
            {
                "authenticated": True,
                "body_digest": "d" * 64,
                "candidate_digest": complete.candidate_digest,
                "challenge_id": 1,
                "method": "POST",
                "path": "/api/v1/challenges/attempt",
            }
        ],
        "container": {
            "default_entrypoint": ["python3", "-m", "solver.supervisor"],
            "exit_code": 0,
            "image_id": manifest["candidate"]["image_digest"],
            "image_manifest_digest": manifest["candidate"]["image_digest"],
            "strict_profile": True,
        },
        "instances_after": [],
        "schema_version": 1,
        "submission_count": 1,
    }
    (source / "production-observation.json").write_bytes(canonical_bytes(production) + b"\n")
    effect_id = complete.effect_id
    serial = {
        "schema_version": 2,
        "receipt_type": "serial-submission",
        "run_id": "run-1",
        "manifest_link": {"row_id": "core.submission-tail", "receipt_ref": "receipt:serial-submission"},
        "evidence_class": "controlled-runtime-trace",
        "dispatch_order": [complete.payload_identity],
        "in_flight_maximum": 1,
        "submissions": [
            {
                "reservation_id": complete.reservation_id,
                "candidate_id": complete.payload_identity,
                "effect_id": effect_id,
                "operation": "board.submit-candidate",
                "challenge_id": "1",
                "states": ["reserved", "started", "committed"],
                "ordinals": [1, 2, 3],
                "result": {
                    "candidate_id": candidate_id,
                    "outcome": "correct",
                    "ready_at": "2026-09-22T01:58:00+00:00",
                    "reserved_at": "2026-09-22T01:59:00+00:00",
                    "requested_at": "2026-09-22T01:59:01+00:00",
                    "result_at": "2026-09-22T01:59:02+00:00",
                },
            }
        ],
    }
    (source / "serial-submission.receipt.json").write_bytes(canonical_bytes(serial) + b"\n")
    ambiguity = {
        "schema_version": 1,
        "receipt_type": "ambiguous-submission",
        "run_id": "run-1",
        "evidence_class": "solver-observation",
        "manifest_link": {"row_id": "core.submission-tail", "receipt_ref": "receipt:ambiguous-submission"},
        "fence_seconds": 60.0,
        "events": [
            {
                "boot_id": "boot-1",
                "candidate_id": candidate_id,
                "complete_identity": complete.document(),
                "supplied_value_digest": complete.candidate_digest,
                "deadline": 60.0,
                "effect_id": effect_id,
                "event": "wire-started",
                "generation_id": "generation-1",
                "reservation_id": f"ambiguity-path:{effect_id}",
                "wire_started_at": 0.0,
            }
        ],
        "no_resend_trace": [],
        "final_dispositions": [],
    }
    (source / "ambiguous-submission.receipt.json").write_bytes(canonical_bytes(ambiguity) + b"\n")

    receipt = evaluate(source, manifest, key, source / "final-interval.evaluator.json")

    assert verify_evaluator(receipt, source) == receipt
    document = json.loads(receipt.read_bytes())
    assert document["producer"] == "external-evaluator"

    production["container"]["exit_code"] = 1
    (source / "production-observation.json").write_bytes(canonical_bytes(production) + b"\n")
    with pytest.raises(ValueError, match="successful exact-image"):
        evaluate(source, manifest, key, source / "failed-run.evaluator.json")

    production["container"]["exit_code"] = 0
    (source / "production-observation.json").write_bytes(canonical_bytes(production) + b"\n")
    runtime = json.loads((source / "final-interval.receipt.json").read_bytes())
    runtime["terminal"]["closed_at"] = (END - dt.timedelta(seconds=1)).isoformat()
    (source / "final-interval.receipt.json").write_bytes(canonical_bytes(runtime) + b"\n")
    with pytest.raises(ValueError, match="terminal projection|official close"):
        evaluate(source, manifest, key, source / "early-close.evaluator.json")


def test_evaluator_accepts_truthful_pending_ambiguity_at_official_close():
    candidate_id = "a" * 64
    complete = CompleteSubmissionIdentity("board", 1, "revision", "instance", "d" * 64, 1)
    effect_id = complete.effect_id
    image = "sha256:" + "c" * 64
    runtime = {
        "window": {
            "final_submission_cutoff": (END - dt.timedelta(seconds=60)).isoformat(),
            "ends_at": END.isoformat(),
            "account_post_interval_seconds": 12.0,
            "submission_request_deadline_seconds": 30.0,
            "submission_uncertainty_margin_seconds": 2.0,
        },
        "terminal": {
            "closed_at": END.isoformat(),
            "cleanup": {"instance:1": "released"},
            "remaining": {"attempts": [], "instances": [], "submissions": [candidate_id]},
        },
        "submissions": {candidate_id: {"outcome": "possibly-sent"}},
    }
    production = {
        "schema_version": 1,
        "instances_after": [],
        "submission_count": 1,
        "container": {
            "default_entrypoint": ["python3", "-m", "solver.supervisor"],
            "exit_code": 0,
            "image_id": image,
            "image_manifest_digest": image,
            "strict_profile": True,
        },
        "board_requests": [
            {
                "method": "POST",
                "path": "/api/v1/challenges/attempt",
                "authenticated": True,
                "body_digest": "d" * 64,
                "candidate_digest": complete.candidate_digest,
                "challenge_id": complete.challenge_id,
            }
        ],
    }
    serial = {
        "in_flight_maximum": 1,
        "submissions": [
            {
                "candidate_id": complete.payload_identity,
                "effect_id": effect_id,
                "reservation_id": complete.reservation_id,
                "states": ["reserved", "started", "possibly-sent"],
                "result": {"requested_at": (END - dt.timedelta(seconds=40)).isoformat()},
            }
        ],
    }
    ambiguity = {
        "events": [
            {
                "candidate_id": candidate_id,
                "effect_id": effect_id,
                "event": "wire-started",
                "complete_identity": complete.document(),
                "supplied_value_digest": complete.candidate_digest,
            },
            {"candidate_id": candidate_id, "effect_id": effect_id, "event": "possibly-sent"},
        ],
        "no_resend_trace": [{"candidate_id": candidate_id, "posts": 1}],
        "final_dispositions": [{"candidate_id": candidate_id, "disposition": "pending"}],
    }

    _verify_production_observation(
        runtime,
        production,
        serial,
        ambiguity,
        {"kind": "exact-image-qualification-clock", "image_digest": image},
        {"binding": {"image_digest": image}},
    )

    tampered = dict(runtime)
    tampered["submissions"] = {"invented-candidate": {"outcome": "possibly-sent"}}
    with pytest.raises(ValueError, match="Candidate identities are not bijective"):
        _verify_production_observation(
            tampered,
            production,
            serial,
            ambiguity,
            {"kind": "exact-image-qualification-clock", "image_digest": image},
            {"binding": {"image_digest": image}},
        )


def test_evaluator_rejects_serial_posts_inside_account_interval():
    runtime = {
        "window": {
            "final_submission_cutoff": (END - dt.timedelta(seconds=60)).isoformat(),
            "ends_at": END.isoformat(),
            "account_post_interval_seconds": 12.0,
            "submission_request_deadline_seconds": 30.0,
            "submission_uncertainty_margin_seconds": 2.0,
        }
    }
    serial = {
        "submissions": [
            {
                "states": ["reserved", "started", "committed"],
                "result": {"requested_at": (END - dt.timedelta(seconds=50)).isoformat()},
            },
            {
                "states": ["reserved", "started", "committed"],
                "result": {"requested_at": (END - dt.timedelta(seconds=39)).isoformat()},
            },
        ]
    }

    with pytest.raises(ValueError, match="account pacing"):
        _verify_submission_timing(runtime, serial)


def test_order_does_not_spend_final_chance_before_attempt_admission_and_restart_cannot_regrant(tmp_path):
    window = _window(tmp_path)
    clock = [window.ends_at - dt.timedelta(seconds=301)]
    final = FinalIntervalController(
        state=tmp_path,
        run_id="run-1",
        opened_at=window.opened_at,
        ends_at=window.ends_at,
        final_submission_reserve_seconds=300,
        attempt_floor_seconds=300,
        enabled_lanes=("lane-1",),
        now=lambda: clock[0],
        reserve=lambda _identity: None,
    )
    recorder = Recorder(tmp_path, "run-1", Redactor({}), now=lambda: clock[0])
    scheduler = CanonicalScheduler(
        window,
        recorder,
        _canonical_authority(tmp_path),
        dials=Dials(),
        now=lambda: clock[0],
        final_interval=final,
    )

    pick = scheduler.acquire(_legacy())
    assert pick is not None and pick.budget_s == 1
    assert final.receipt()["final_chances"] == {}
    generation = recorder.acquire_order_generation(pick)
    assert final.admit_final_chance("lane-1", "42") is not None
    recorder.generations.close(generation.generation_id, GenerationDisposition.ABANDON)
    scheduler.release(Ended(42, "cut:budget", 1, 0))
    restarted = CanonicalScheduler(
        window,
        recorder,
        _canonical_authority(tmp_path),
        dials=Dials(),
        now=lambda: clock[0],
        final_interval=FinalIntervalController(
            state=tmp_path,
            run_id="run-1",
            opened_at=window.opened_at,
            ends_at=window.ends_at,
            final_submission_reserve_seconds=300,
            attempt_floor_seconds=300,
            enabled_lanes=("lane-1",),
            now=lambda: clock[0],
            reserve=lambda _identity: None,
        ),
    )
    assert restarted.acquire(_legacy()) is None


@pytest.mark.parametrize("lanes", [1, 2])
def test_boot_object_graph_owns_selected_topology_and_replays_its_final_interval(monkeypatch, tmp_path, lanes):
    import solver.__main__ as entry

    captured = []

    class CapturedRun:
        def __init__(self, **ports):
            captured.append(ports)

        def work(self):
            from solver.run import Ending

            return Ending("window closed")

    wired(monkeypatch, PlayableBoard(count=1))
    monkeypatch.setattr(entry, "Run", CapturedRun)
    home = tmp_path / "codex"
    home.mkdir()
    (home / "auth.json").write_text("{}")
    monkeypatch.setattr(entry.boot, "CODEX_HOME", home)
    boards = tmp_path / "boards"
    boards.mkdir()
    (boards / "one.board.json").write_text(
        json.dumps(
            {
                "event": "offline",
                "url": "https://board.example",
                "flag_wrappers": [r"brunner\{[^}]{1,256}\}"],
                "window_seconds": 3600,
                "prohibitions": ["no broad automated enumeration"],
            }
        )
    )

    selected = env()
    selected[entry.LANES_ENV] = str(lanes)
    entry.main(selected, run_state=tmp_path / "state", boards=boards)
    if lanes == 2:
        captured[0]["recorder"].write_authority.close()
        entry.main(selected, run_state=tmp_path / "state", boards=boards)

    assert len(captured) == lanes
    assert captured[0]["final_interval"].enabled_lanes == tuple(f"lane-{index}" for index in range(1, lanes + 1))
    assert captured[0]["final_interval"].ends_at == captured[0]["scheduler"].window.ends_at
    if lanes == 2:
        assert captured[0]["lane_controller"].profile.lanes == 2
        assert captured[0]["lane_controller"]._final_interval is captured[0]["final_interval"]
        assert captured[1]["final_interval"].ends_at == captured[0]["final_interval"].ends_at
    else:
        assert captured[0]["lane_controller"] is None


def test_boot_seeds_only_live_instance_leases():
    from solver.__main__ import _active_initial_leases

    class Grant:
        def __init__(self, challenge_id, phase):
            self.challenge_id, self.phase = challenge_id, phase

    class Coordinator:
        @staticmethod
        def leases():
            return (Grant(1, LeasePhase.RECOVERABLE), Grant(2, LeasePhase.CLOSED))

    assert set(_active_initial_leases(Coordinator())) == {1}
