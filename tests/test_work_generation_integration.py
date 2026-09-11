"""The v1 Attempt path projects one fenced canonical Work generation."""

import json

import pytest

from solver.event_store import EventStore, GenerationAuthority, WORK_GENERATION_RECORDED
from solver.record import LateGenerationEvent, Recorder, Usage
from solver.redaction import Redactor
from solver.replay import verify_and_materialize_run_state
from solver.work_generation import GenerationFence
from test_run_loop import Agent, Clock, Wire, solver


def test_attempt_effects_are_fenced_while_legacy_attempt_rows_keep_their_v1_shape(tmp_path):
    clock = Clock()
    wire = Wire(count=1)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock, lasting=1_000.0)

    run.work()

    events = EventStore(tmp_path / "state", run_id="gate").events()
    acquired = next(
        event
        for event in events
        if event.event_type == WORK_GENERATION_RECORDED and event.payload["record"] == "acquire"
    )
    closed_event = next(
        event for event in events if event.event_type == WORK_GENERATION_RECORDED and event.payload["record"] == "close"
    )
    first_attempt_effect = next(event for event in events if event.event_type == "observation.recorded")
    last_attempt_effect = max(
        (event for event in events if event.event_type == "observation.recorded"),
        key=lambda event: event.sequence,
    )
    rows = [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]
    opened = next(row for row in rows if row["record"] == "attempt-open")
    closed_row = next(row for row in rows if row["record"] == "attempt-close")

    assert acquired.sequence < first_attempt_effect.sequence
    assert last_attempt_effect.sequence < closed_event.sequence
    assert acquired.payload["attempt_id"] == opened["attempt_id"]
    assert closed_row["attempt_id"] == acquired.payload["attempt_id"]
    assert "generation_id" not in opened
    assert "generation_id" not in closed_row


def test_a_crashed_attempt_closes_its_generation_as_interrupted(tmp_path):
    clock = Clock()
    wire = Wire(count=1)

    def explodes(_argv, _workdir, _environment, _prompt):
        raise RuntimeError("the adapter vanished")

    run, _recorder = solver(tmp_path, wire, explodes, clock)

    run.work()

    generation_events = [
        event.payload
        for event in EventStore(tmp_path / "state", run_id="gate").events()
        if event.event_type == WORK_GENERATION_RECORDED
    ]
    transitions = [event for event in generation_events if event["record"] in {"acquire", "close"}]
    assert [event["record"] for event in transitions] == ["acquire", "close"]
    assert transitions[-1]["disposition"] == "interrupt"


def test_staging_failure_interrupts_the_already_acquired_generation(tmp_path):
    clock = Clock()
    wire = Wire(count=1)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    def staging_fails(_challenge, _attempt_id):
        raise RuntimeError("the staging boundary vanished")

    run._staged = staging_fails

    run.work()

    generation_events = [
        event.payload
        for event in EventStore(tmp_path / "state", run_id="gate").events()
        if event.event_type == WORK_GENERATION_RECORDED
    ]
    transitions = [event for event in generation_events if event["record"] in {"acquire", "close"}]
    assert [event["record"] for event in transitions] == ["acquire", "close"]
    assert transitions[-1]["disposition"] == "interrupt"


def test_verified_restart_interrupts_open_work_before_a_replacement_can_acquire_it(tmp_path):
    fence = GenerationFence(tmp_path, "run-1", Redactor({}), timestamp=lambda: "2026-09-11T00:00:00Z")
    first = fence.acquire("challenge-42", "attempt-1")

    verify_and_materialize_run_state(tmp_path, "run-1")
    verify_and_materialize_run_state(tmp_path, "run-1")
    second = fence.acquire("challenge-42", "attempt-2")

    projection = fence.projection()
    first_state = next(state for state in projection.generations if state.generation_id == first.generation_id)
    assert first_state.disposition.value == "interrupt"
    assert second.generation_id != first.generation_id
    assert projection.active_by_work["challenge-42"].attempt_id == "attempt-2"
    assert (tmp_path / "runs" / "run-1" / "canonical" / "work-generation-fence.receipt.json").is_file()


def test_candidate_authority_is_reserved_before_flag_submission_effects(tmp_path):
    clock = Clock()
    wire = Wire(count=1)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire, solving=(1,)), clock)

    run.work()

    events = EventStore(tmp_path / "state", run_id="gate").events()
    candidate = next(
        event
        for event in events
        if event.event_type == WORK_GENERATION_RECORDED
        and event.payload["record"] == "authority"
        and event.payload["authority"] == GenerationAuthority.CANDIDATE.value
    )
    submission = next(
        event
        for event in events
        if event.event_type == "observation.recorded" and event.payload["tool"] == "flag-submit"
    )
    assert candidate.sequence < submission.sequence


def test_a_step_finishing_after_close_is_quarantined_instead_of_becoming_an_observation(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    generation_id = recorder.attempt_open(
        attempt_id="attempt-1",
        challenge_id=42,
        challenge_name="Late",
        category="misc",
        challenge_type="standard",
        solves_at_open=0,
        tier=1,
        budget_s=300,
        attempt_sequence=1,
        instance_until=None,
        order_ranks={"42": 1},
    )
    step = recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="printf late",
        command_normalised="printf late",
        tool="bash",
    )
    recorder.attempt_close(
        attempt_id="attempt-1",
        cause="cut:budget",
        approach_label="inspect bytes",
        solves_at_close=0,
        extensions_granted=0,
        flag=None,
        generation_id=generation_id,
    )

    with pytest.raises(LateGenerationEvent):
        step.end(exit_code=0, output=b"late output", usage=Usage(model=""))

    events = EventStore(tmp_path, run_id="run-1").events()
    assert not [event for event in events if event.event_type == "observation.recorded"]
    assert events[-1].payload["record"] == "late-event"
    assert events[-1].body == b"late output"
