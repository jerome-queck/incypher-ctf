import json

import pytest

from solver.event_store import EventStore, GenerationAuthority, GenerationDisposition, InvalidEventError
from solver.lead_contracts import (
    BoardProposal,
    CandidateProposal,
    LeadClassification,
    LeadRequest,
    MAX_CONTEXT_BYTES,
    MeasuredLeadTurn,
    ProgressProposal,
    ResearchProposal,
    StopProposal,
    TargetProposal,
    ToolProposal,
    TurnMeasure,
)
from solver.lead_controller import LeadController, project_lead
from solver.redaction import Redactor
from solver.work_generation import GenerationFence


class ScriptedModel:
    def __init__(self, *turns):
        self.turns = iter(turns)
        self.calls = 0

    def __call__(self, _request):
        self.calls += 1
        return next(self.turns)


def clock():
    return "2026-09-12T00:00:00Z"


def measured(approach, proposal):
    return MeasuredLeadTurn(
        approach=approach,
        proposal=proposal,
        measure=TurnMeasure(
            model="fake-model",
            duration_ms=10,
            tokens_in=20,
            tokens_out=10,
            usage_known=True,
        ),
    )


def test_fake_model_completes_one_lead_engagement_and_replays_the_same_state(tmp_path):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    controller = LeadController(
        tmp_path,
        "run-1",
        redactor,
        clock,
        ScriptedModel(
            measured("inspect observed service", ToolProposal("nmap", ("target-1",))),
            measured("use the service version", ProgressProposal("service identified")),
            measured("submit derived candidate", CandidateProposal("flag{candidate}", ("observation-7",))),
        ),
        fence=fence,
    )

    outcomes = tuple(
        controller.handle(
            LeadRequest(
                engagement_id="engagement-1",
                generation_id=generation.generation_id,
                turn_index=index,
                context=context,
            )
        )
        for index, context in enumerate(
            ("Observed an open service", "Observed nmap output", "Observed candidate derivation"),
            start=1,
        )
    )

    assert [outcome.classification for outcome in outcomes] == [LeadClassification.ACCEPTED] * 3
    assert isinstance(outcomes[0].proposal, ToolProposal)
    assert isinstance(outcomes[1].proposal, ProgressProposal)
    assert isinstance(outcomes[2].proposal, CandidateProposal)
    assert outcomes[2].state.disposition == "candidate"
    assert outcomes[2].state.turn_count == 3
    events = EventStore(tmp_path, run_id="run-1").events()
    assert [
        event.payload["authority"]
        for event in events
        if event.event_type == "work-generation.recorded" and event.payload["record"] == "authority"
    ] == [GenerationAuthority.AUTHORITY.value] * 3
    assert project_lead(events, "run-1") == outcomes[2].state
    assert (tmp_path / "runs" / "run-1" / "canonical" / "lead-engagement.receipt.json").is_file()


def test_duplicate_is_bounded_and_changed_reuse_is_quarantined(tmp_path):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    model = ScriptedModel(measured("inspect", ToolProposal("strings", ("artifact-1",))))
    controller = LeadController(tmp_path, "run-1", redactor, clock, model, fence=fence)
    request = LeadRequest("engagement-1", generation.generation_id, 1, "observed bytes")

    accepted = controller.handle(request)
    before = len(EventStore(tmp_path, run_id="run-1").events())
    duplicate = controller.handle(request)
    conflict = controller.handle(LeadRequest("engagement-1", generation.generation_id, 1, "changed bytes"))
    events = EventStore(tmp_path, run_id="run-1").events()

    assert accepted.classification is LeadClassification.ACCEPTED
    assert duplicate.classification is LeadClassification.DUPLICATE
    assert conflict.classification is LeadClassification.CONFLICT
    assert model.calls == 1
    assert len(events) == before + 2
    quarantined = events[-1]
    assert quarantined.payload["classification"] == "conflict-quarantined"
    assert (
        quarantined.body
        == b'{"request":{"context":"changed bytes","engagement_id":"engagement-1","generation_id":"generation-000001","turn_index":1}}'
    )
    assert project_lead(events, "run-1") == accepted.state
    receipt = json.loads((tmp_path / "runs" / "run-1" / "canonical" / "lead-engagement.receipt.json").read_text())
    assert receipt["quarantined_conflicts"][0]["evidence_digest"] == quarantined.blob_digest


@pytest.mark.parametrize(
    ("model_output", "context", "expected"),
    [
        (object(), "observed", LeadClassification.MALFORMED),
        (
            MeasuredLeadTurn("inspect", ToolProposal("strings"), None),
            "observed",
            LeadClassification.UNMEASURED,
        ),
        (
            measured("inspect", ToolProposal("strings")),
            "x" * (MAX_CONTEXT_BYTES + 1),
            LeadClassification.OVERSIZED,
        ),
        (
            measured("inspect", ProgressProposal("x" * (MAX_CONTEXT_BYTES + 1))),
            "observed",
            LeadClassification.OVERSIZED,
        ),
    ],
)
def test_invalid_model_turns_are_explicit_and_cannot_emit_a_proposal(tmp_path, model_output, context, expected):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    controller = LeadController(tmp_path, "run-1", redactor, clock, ScriptedModel(model_output), fence=fence)

    outcome = controller.handle(LeadRequest("engagement-1", generation.generation_id, 1, context))

    assert outcome.classification is expected
    assert outcome.proposal is None
    assert outcome.state.turn_count == 1
    assert outcome.state.disposition is None
    lead_event = next(
        event
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.event_type == "lead-engagement.recorded"
    )
    assert lead_event.payload["classification"] == expected.value
    assert lead_event.body == b""


def test_closed_generation_classifies_late_output_without_a_lead_transition(tmp_path):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    fence.close(generation.generation_id, GenerationDisposition.SUPERSEDE)
    model = ScriptedModel(measured("inspect", ToolProposal("strings")))
    controller = LeadController(tmp_path, "run-1", redactor, clock, model, fence=fence)

    outcome = controller.handle(LeadRequest("engagement-1", generation.generation_id, 1, "late observation"))

    assert outcome.classification is LeadClassification.LATE
    assert outcome.state.turn_count == 0
    events = EventStore(tmp_path, run_id="run-1").events()
    assert all(event.event_type != "lead-engagement.recorded" for event in events)
    assert events[-1].payload["record"] == "late-event"
    assert events[-1].payload["classification"] == "superseded-generation"


def test_model_output_arriving_after_generation_close_is_bounded_late_evidence(tmp_path):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")

    def late_model(_request):
        fence.close(generation.generation_id, GenerationDisposition.SUPERSEDE)
        return measured("derive", CandidateProposal("flag{late}", ("observation-1",)))

    controller = LeadController(tmp_path, "run-1", redactor, clock, late_model, fence=fence)

    outcome = controller.handle(LeadRequest("engagement-1", generation.generation_id, 1, "observed"))

    assert outcome.classification is LeadClassification.LATE
    late = EventStore(tmp_path, run_id="run-1").events()[-1]
    assert late.payload["record"] == "late-event"
    evidence = json.loads(late.body)
    assert set(evidence) == {"output_bytes", "output_digest", "request_digest"}
    assert "flag{late}" not in late.body.decode()


def test_replay_refuses_a_lead_transition_without_its_generation_fence(tmp_path):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    controller = LeadController(
        tmp_path,
        "run-1",
        redactor,
        clock,
        ScriptedModel(measured("inspect", ToolProposal("strings"))),
        fence=fence,
    )
    controller.handle(LeadRequest("engagement-1", generation.generation_id, 1, "observed"))
    unfenced = [
        event
        for event in EventStore(tmp_path, run_id="run-1").events()
        if not (event.event_type == "work-generation.recorded" and event.payload["record"] == "authority")
    ]

    with pytest.raises(InvalidEventError, match="generation fence"):
        project_lead(unfenced, "run-1")


@pytest.mark.parametrize(
    "proposal",
    [
        BoardProposal("refresh-challenge", "challenge-1"),
        TargetProposal("request", "target-1", "GET /"),
        ResearchProposal("documented format signature"),
        StopProposal("no productive next action"),
    ],
)
def test_every_lead_port_is_a_typed_proposal_without_effect_authority(tmp_path, proposal):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    controller = LeadController(
        tmp_path,
        "run-1",
        redactor,
        clock,
        ScriptedModel(measured("reason about observation", proposal)),
        fence=fence,
    )

    outcome = controller.handle(LeadRequest("engagement-1", generation.generation_id, 1, "observed"))

    assert outcome.classification is LeadClassification.ACCEPTED
    assert outcome.proposal == proposal


def test_unknown_vendor_usage_is_recorded_not_mistaken_for_an_unmeasured_turn(tmp_path):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    turn = MeasuredLeadTurn(
        "inspect",
        ToolProposal("strings"),
        TurnMeasure("fake-model", duration_ms=10, tokens_in=0, tokens_out=0, usage_known=False),
    )
    controller = LeadController(tmp_path, "run-1", redactor, clock, ScriptedModel(turn), fence=fence)

    outcome = controller.handle(LeadRequest("engagement-1", generation.generation_id, 1, "observed"))

    assert outcome.classification is LeadClassification.ACCEPTED
    assert outcome.state.transitions[0].measure.usage_known is False
