"""Production adapter: canonical Order first, v1 Pick only as projection."""

import datetime as dt

from solver.intake import Sighting, Snapshot
from solver.instance import Terms
from solver.event_store import EventStore, GenerationDisposition, ObservationRecorded
from solver.intake_contracts import IntakeDecisionRecorded, IntakeRecord
from solver.coherent_intake import crowd_source_from_contract
from solver.event_store_storage import canonical_bytes
from solver.intake_qualification import (
    ChallengeSnapshot,
    IntakeContract,
    IntakeSnapshot,
    PresentValue,
    PriorFence,
    TypedId,
    ValueFact,
)
from solver.order_journal import replay_order_publication
from solver.order_policy import AdmissionFact, CrowdSource, OrderAuthority
from solver.order_runtime import CanonicalScheduler, record_admission_facts
from solver.record import Recorder
from solver.redaction import Redactor
from solver.schedule import Dials, Ended, Window


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 22, 1, 0, tzinfo=UTC)


def _authority():
    challenge = ChallengeSnapshot(
        TypedId.parse(42),
        "answer",
        "web",
        "standard",
        "Difficulty: hard",
        ValueFact(500, "list", "answered", NOW),
        ValueFact(3, "list", "answered", NOW),
        ValueFact(False, "list", "answered", NOW),
        PresentValue("value", 1),
        0,
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        (),
        "d" * 64,
    )
    snapshot = IntakeSnapshot("snapshot-1", "a" * 64, NOW, (challenge,), "not-empty")
    return OrderAuthority(
        snapshot,
        PriorFence("a" * 64, "intake:1", "b" * 64, snapshot.digest),
        (snapshot,),
        100_000,
        CrowdSource(True, False, "c" * 64),
    )


def _legacy():
    sighting = Sighting(42, "answer", "web", "standard", 500, 3, 1, "", 0, None, False, Terms(42, "standard"))
    return Snapshot(NOW, 1, challenges=(sighting,))


def _window(tmp_path):
    return Window.opened(tmp_path / "runs" / "run-1", lasting=7200, now=NOW)


def _canonical_authority(tmp_path):
    authority = _authority()
    snapshot = authority.snapshot
    contract = IntakeContract(snapshot.profile_digest, "f" * 64)
    store = EventStore(tmp_path, run_id="run-1")
    store.append(
        IntakeDecisionRecorded(
            "intake:1:started",
            "attempt-1",
            IntakeRecord.STARTED,
            snapshot.profile_digest,
            contract.digest,
            "e" * 64,
            PriorFence.genesis(snapshot.profile_digest),
            ts=NOW.isoformat(),
        ),
        body=canonical_bytes({"contract": contract.document(), "authority": {"test": True}}),
    )
    event = store.append(
        IntakeDecisionRecorded(
            "intake:1",
            "attempt-1",
            IntakeRecord.SETTLED,
            snapshot.profile_digest,
            contract.digest,
            "e" * 64,
            PriorFence.genesis(snapshot.profile_digest),
            reason="settled",
            snapshot_digest=snapshot.digest,
            ts=NOW.isoformat(),
        ),
        body=snapshot.canonical_bytes(),
    )
    return lambda: OrderAuthority(
        snapshot,
        PriorFence(snapshot.profile_digest, "intake:1", event.event_digest, snapshot.digest),
        authority.history,
        authority.effective_max_challenges,
        crowd_source_from_contract(tmp_path, "run-1", contract),
    )


def test_canonical_order_publishes_before_projecting_one_exact_v1_pick(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}), now=lambda: NOW)
    scheduler = CanonicalScheduler(
        _window(tmp_path),
        recorder,
        _canonical_authority(tmp_path),
        dials=Dials(),
        now=lambda: NOW,
    )

    pick = scheduler.acquire(_legacy())

    assert pick is not None
    assert pick.order_attempt_id == "i:42-1"
    assert pick.order_generation_id == "generation-000001"
    assert recorder.event_store.events()[-1].payload.get("record") == "boundary"
    generation = recorder.acquire_order_generation(pick)
    assert generation.generation_id == pick.order_generation_id
    assert recorder.acquire_order_generation(pick) == generation

    scheduler.release(Ended(42, "flag", 20, 0))


def test_caller_lease_and_solve_hints_cannot_override_canonical_intake_or_generation_sources(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}), now=lambda: NOW)
    scheduler = CanonicalScheduler(_window(tmp_path), recorder, _canonical_authority(tmp_path), now=lambda: NOW)

    pick = scheduler.acquire(_legacy(), leased=(42,), solved=(42,))
    run = replay_order_publication(tmp_path, "run-1").boundary["decision"]["input_document"]["run"]

    assert pick is not None
    assert run["leased"] == []
    assert run["solved"] == []


def test_boundary_clock_advances_without_intake_refresh_and_enters_final_interval(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}), now=lambda: clock[0])
    clock = [NOW + dt.timedelta(minutes=1)]
    window = _window(tmp_path)
    scheduler = CanonicalScheduler(window, recorder, _canonical_authority(tmp_path), now=lambda: clock[0])
    first = scheduler.acquire(_legacy())
    generation = recorder.acquire_order_generation(first)
    recorder.generations.close(generation.generation_id, GenerationDisposition.ABANDON)
    scheduler.release(Ended(42, "cut:budget", 20, 0))

    cutoff = window.ends_at - dt.timedelta(seconds=scheduler.dials.tail_seconds)
    clock[0] = cutoff - dt.timedelta(seconds=scheduler.dials.floor_seconds - 1)
    second = scheduler.acquire(_legacy())
    decision = replay_order_publication(tmp_path, "run-1").boundary["decision"]

    assert second is None
    assert decision["interval"] == "final-interval"
    assert decision["input_document"]["run"]["boundary_at"] == clock[0].isoformat()


def test_canonical_complete_generation_keeps_solved_work_out_before_intake_refresh(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}), now=lambda: NOW)
    scheduler = CanonicalScheduler(_window(tmp_path), recorder, _canonical_authority(tmp_path), now=lambda: NOW)
    first = scheduler.acquire(_legacy())
    generation = recorder.acquire_order_generation(first)
    recorder.generations.close(generation.generation_id, GenerationDisposition.COMPLETE)
    scheduler.release(Ended(42, "flag", 20, 0))

    assert scheduler.acquire(_legacy()) is None
    run = replay_order_publication(tmp_path, "run-1").boundary["decision"]["input_document"]["run"]
    assert run["solved"] == [{"type": "integer", "value": "42"}]


def test_restart_replays_an_active_matching_generation_instead_of_publishing_a_new_boundary(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}), now=lambda: NOW)
    authority = _canonical_authority(tmp_path)
    scheduler = CanonicalScheduler(_window(tmp_path), recorder, authority, now=lambda: NOW)
    first = scheduler.acquire(_legacy())
    recorder.acquire_order_generation(first)

    restarted = CanonicalScheduler(_window(tmp_path), recorder, authority, now=lambda: NOW)
    retried = restarted.acquire(_legacy())

    assert retried == first
    assert sum(event.payload.get("record") == "boundary" for event in recorder.event_store.events()) == 1


def test_runtime_replays_durable_tier_checkpoint_and_canonical_admission_facts(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}), now=lambda: NOW)
    authority = _canonical_authority(tmp_path)
    scheduler = CanonicalScheduler(_window(tmp_path), recorder, authority, now=lambda: NOW)
    first = scheduler.acquire(_legacy())
    recorder.acquire_order_generation(first)
    observation = recorder.event_store.append(
        ObservationRecorded(
            first.order_attempt_id,
            1,
            "checkpoint",
            "checkpoint",
            "bash",
            checkpoint="progress",
            ts=NOW.isoformat(),
        ),
        body=b"{}",
    )
    recorder.generations.close(first.order_generation_id, GenerationDisposition.ABANDON)
    scheduler.release(Ended(42, "complete", 20, 1))
    record_admission_facts(
        recorder,
        (AdmissionFact(TypedId.parse(42), False, "unsafe-runtime", observation.event_digest),),
        at=NOW,
    )

    assert scheduler.acquire(_legacy()) is None
    replayed = replay_order_publication(tmp_path, "run-1")
    run = replayed.boundary["decision"]["input_document"]["run"]
    assert run["attempts"][0]["checkpoints"] == 1
    assert run["durable_tiers"] == []  # extracted/solve/unjudged Triage is not frozen as model evidence
    assert replayed.rows[0]["deferred_reason"] == "unsafe-runtime"


def test_model_judged_tier_has_canonical_evidence_and_replays_across_boots(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}), now=lambda: NOW)
    authority = _canonical_authority(tmp_path)
    calls = []

    def judge(_prompt):
        calls.append(1)
        recorder.event_store.append(
            ObservationRecorded("triage-1", 1, "judge", "judge", "codex", ts=NOW.isoformat()),
            body=b"{}",
        )
        return "42 4"

    scheduler = CanonicalScheduler(_window(tmp_path), recorder, authority, now=lambda: NOW, judge=judge)
    first = scheduler.acquire(_legacy())
    recorder.acquire_order_generation(first)
    recorder.generations.close(first.order_generation_id, GenerationDisposition.ABANDON)
    scheduler.release(Ended(42, "complete", 20, 0))

    restarted = CanonicalScheduler(_window(tmp_path), recorder, authority, now=lambda: NOW, judge=judge)
    assert restarted.acquire(_legacy()) is not None
    run = replay_order_publication(tmp_path, "run-1").boundary["decision"]["input_document"]["run"]
    assert calls == [1]
    assert run["durable_tiers"][0]["tier"] == 4
