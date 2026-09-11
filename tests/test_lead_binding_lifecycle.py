import json
from dataclasses import replace

import pytest

from solver.event_store import EventStore, GenerationAuthority, GenerationDisposition, InvalidEventError
from solver.lead_contracts import (
    LeadBinding,
    LeadClassification,
    LeadInitialContext,
    LeadProposalResult,
    LeadRequest,
    LeadResume,
    ProposalResultStatus,
    ToolProposal,
)
from solver.lead_controller import LeadController
from solver.lead_projection import project_lead
from solver.redaction import Redactor
from solver.work_generation import GenerationFence

from test_lead_controller import ScriptedModel, clock, measured


def binding(generation_id, *, engagement_id="engagement-1"):
    return LeadBinding(
        run_id="run-1",
        boot_id="boot-000001",
        generation_id=generation_id,
        lane_id="lane-1",
        attempt_id="attempt-1",
        work_id="challenge-1",
        engagement_id=engagement_id,
        owner_id="run-controller",
        parent_engagement_id="",
        context_digest="1" * 64,
        evidence_digest="2" * 64,
        prompt_bundle_digest="3" * 64,
        playbook_digest="4" * 64,
        tool_schema_digest="5" * 64,
        capability_digest="6" * 64,
        harness="native-codex",
        requested_route="subscription",
        requested_model="fake-model",
        selected_model="fake-model",
        effective_model="fake-model",
        requested_effort="high",
        effective_effort="high",
        catalog_digest="7" * 64,
        admitted_budget=900,
        deadline="2026-09-12T00:15:00Z",
        started_at="2026-09-12T00:00:00Z",
    )


def make_controller(tmp_path, *turns):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    model = ScriptedModel(*turns)
    return (
        LeadController(tmp_path, "run-1", redactor, clock, model, fence=fence),
        model,
        binding(generation.generation_id),
    )


def test_binding_is_admitted_before_inference_and_each_fact_names_its_exact_fence(tmp_path):
    seen = []
    controller, _model, bound = make_controller(tmp_path)

    def inspect(_request):
        seen.extend(EventStore(tmp_path, run_id="run-1").events())
        return measured("inspect", ToolProposal("strings"))

    controller._model = inspect
    outcome = controller.handle(LeadRequest(bound, 1, LeadInitialContext("observed")))

    assert outcome.classification is LeadClassification.ACCEPTED
    admitted = [event for event in seen if event.event_type == "lead-engagement.recorded"]
    assert [event.payload["record"] for event in admitted] == ["admitted"]
    events = EventStore(tmp_path, run_id="run-1").events()
    lead = [event for event in events if event.event_type == "lead-engagement.recorded"]
    assert [event.payload["record"] for event in lead] == ["admitted", "turn"]
    for event in lead:
        authority = events[event.payload["authority_sequence"] - 1]
        assert authority.sequence + 1 == event.sequence
        assert authority.payload["record"] == "authority"
        assert authority.payload["generation_id"] == bound.generation_id
    assert lead[0].payload["binding"] == bound.document()


def test_one_outstanding_effect_proposal_requires_exact_typed_result_before_resume(tmp_path):
    controller, model, bound = make_controller(
        tmp_path,
        measured("inspect", ToolProposal("strings")),
        measured("use observation", ToolProposal("file")),
    )
    first = controller.handle(LeadRequest(bound, 1, LeadInitialContext("observed")))

    missing = controller.handle(LeadRequest(bound, 2, LeadResume("tool completed")))
    wrong = controller.handle(
        LeadRequest(
            bound,
            2,
            LeadResume(
                "tool completed",
                LeadProposalResult("wrong", ProposalResultStatus.SUCCEEDED, ("observation-1",)),
            ),
        )
    )
    resumed = controller.handle(
        LeadRequest(
            bound,
            2,
            LeadResume(
                "tool completed",
                LeadProposalResult(
                    first.proposal_id,
                    ProposalResultStatus.SUCCEEDED,
                    ("observation-1",),
                ),
            ),
        )
    )

    assert missing.classification is LeadClassification.MALFORMED
    assert wrong.classification is LeadClassification.CONFLICT
    assert resumed.classification is LeadClassification.ACCEPTED
    assert model.calls == 2
    assert resumed.state.transitions[1].result.proposal_id == first.proposal_id


def test_duplicate_decodes_exact_proposal_and_binding_drift_never_calls_model(tmp_path):
    controller, model, bound = make_controller(tmp_path, measured("inspect", ToolProposal("strings", ("a",))))
    request = LeadRequest(bound, 1, LeadInitialContext("observed"))
    first = controller.handle(request)
    before = len(EventStore(tmp_path, run_id="run-1").events())

    duplicate = controller.handle(request)
    drift = controller.handle(LeadRequest(replace(bound, effective_effort="medium"), 1, request.input))

    assert duplicate.classification is LeadClassification.DUPLICATE
    assert duplicate.proposal == first.proposal
    assert duplicate.proposal_id == first.proposal_id
    assert drift.classification is LeadClassification.CONFLICT
    assert model.calls == 1
    assert len(EventStore(tmp_path, run_id="run-1").events()) == before + 2


def test_one_run_receipt_projects_multiple_engagements(tmp_path):
    controller, model, bound = make_controller(
        tmp_path,
        measured("inspect", ToolProposal("strings")),
        measured("inspect again", ToolProposal("file")),
    )
    first = controller.handle(LeadRequest(bound, 1, LeadInitialContext("one")))
    controller._fence.close(bound.generation_id, GenerationDisposition.COMPLETE)
    second_generation = controller._fence.acquire("challenge-2", "attempt-2")
    second_binding = replace(
        bound,
        generation_id=second_generation.generation_id,
        engagement_id="engagement-2",
        attempt_id="attempt-2",
        work_id="challenge-2",
    )
    second = controller.handle(LeadRequest(second_binding, 1, LeadInitialContext("two")))

    assert first.classification is second.classification is LeadClassification.ACCEPTED
    receipt = json.loads((tmp_path / "runs" / "run-1" / "canonical" / "lead-engagement.receipt.json").read_text())
    assert [item["engagement_id"] for item in receipt["engagements"]] == ["engagement-1", "engagement-2"]
    assert receipt["engagement_count"] == 2
    assert model.calls == 2


def test_unknown_generation_is_rejected_before_inference(tmp_path):
    controller, model, bound = make_controller(tmp_path, measured("must not run", ToolProposal("strings")))

    outcome = controller.handle(
        LeadRequest(replace(bound, generation_id="generation-999999"), 1, LeadInitialContext("observed"))
    )

    assert outcome.classification is LeadClassification.LATE
    assert model.calls == 0
    assert not [
        event
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.event_type == "lead-engagement.recorded"
    ]


def test_committed_turn_survives_receipt_write_failure_and_duplicate_repairs_it(tmp_path, monkeypatch):
    controller, model, bound = make_controller(tmp_path, measured("inspect", ToolProposal("strings")))
    request = LeadRequest(bound, 1, LeadInitialContext("observed"))

    def fail(*_args):
        raise OSError("disk fault")

    monkeypatch.setattr("solver.lead_receipt.write_receipt", fail)
    with pytest.raises(OSError, match="disk fault"):
        controller.handle(request)
    events_after_commit = EventStore(tmp_path, run_id="run-1").events()
    assert [
        event.payload["record"] for event in events_after_commit if event.event_type == "lead-engagement.recorded"
    ] == [
        "admitted",
        "turn",
    ]
    monkeypatch.undo()

    duplicate = controller.handle(request)

    assert duplicate.classification is LeadClassification.DUPLICATE
    assert duplicate.proposal == ToolProposal("strings")
    assert model.calls == 1
    assert (tmp_path / "runs" / "run-1" / "canonical" / "lead-engagement.receipt.json").is_file()


def test_projection_refuses_binding_drift_even_with_a_valid_chain(tmp_path):
    controller, _model, bound = make_controller(tmp_path, measured("inspect", ToolProposal("strings")))
    controller.handle(LeadRequest(bound, 1, LeadInitialContext("observed")))
    events = EventStore(tmp_path, run_id="run-1").events()
    lead_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type == "lead-engagement.recorded" and event.payload["record"] == "turn"
    )
    payload = dict(events[lead_index].payload)
    payload["binding"] = {**payload["binding"], "selected_model": "drifted"}
    events[lead_index] = replace(events[lead_index], payload=payload)

    with pytest.raises(InvalidEventError, match="binding drifted"):
        project_lead(events, "run-1", "engagement-1")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", "wrong-run"),
        ("attempt_id", "wrong-attempt"),
        ("work_id", "wrong-work"),
        ("owner_id", "wrong-owner"),
    ],
)
def test_forged_authoritative_binding_is_rejected_before_any_lead_write_or_inference(
    tmp_path, monkeypatch, field, value
):
    controller, model, bound = make_controller(tmp_path, measured("must not run", ToolProposal("strings")))
    receipt_calls = 0

    def receipt_must_not_run(*_args):
        nonlocal receipt_calls
        receipt_calls += 1
        raise AssertionError("invalid binding reached receipt writing")

    monkeypatch.setattr("solver.lead_receipt.write_receipt", receipt_must_not_run)

    outcome = controller.handle(LeadRequest(replace(bound, **{field: value}), 1, LeadInitialContext("observed")))

    assert outcome.classification is LeadClassification.CONFLICT
    assert model.calls == 0
    assert receipt_calls == 0
    events = EventStore(tmp_path, run_id="run-1").events()
    assert len(events) == 1
    assert not [event for event in events if event.event_type == "lead-engagement.recorded"]


def test_lead_fact_keeps_its_exact_authority_when_an_unrelated_fence_interleaves(tmp_path, monkeypatch):
    controller, _model, bound = make_controller(tmp_path, measured("inspect", ToolProposal("strings")))
    unrelated = GenerationFence(tmp_path, "run-1", Redactor({}), clock)
    original = controller._fence.authorize_and_commit

    def interleave(generation_id, authority, commit, evidence=b""):
        def after_exact_grant(grant):
            unrelated.authorize(generation_id, GenerationAuthority.AUTHORITY)
            return commit(grant)

        return original(generation_id, authority, after_exact_grant, evidence)

    monkeypatch.setattr(controller._fence, "authorize_and_commit", interleave)

    outcome = controller.handle(LeadRequest(bound, 1, LeadInitialContext("observed")))

    assert outcome.classification is LeadClassification.ACCEPTED
    events = EventStore(tmp_path, run_id="run-1").events()
    lead = [event for event in events if event.event_type == "lead-engagement.recorded"]
    for fact in lead:
        exact = next(event for event in events if event.sequence == fact.payload["authority_sequence"])
        between = next(event for event in events if event.sequence == exact.sequence + 1)
        assert fact.payload["authority_run_id"] == "run-1"
        assert exact.payload["event_id"] == fact.payload["authority_event_id"]
        assert exact.payload["authority"] == GenerationAuthority.AUTHORITY.value
        assert between.payload["authority"] == GenerationAuthority.AUTHORITY.value
        assert between.payload["event_id"] != fact.payload["authority_event_id"]
        assert fact.sequence == between.sequence + 1
    assert project_lead(events, "run-1", "engagement-1") == outcome.state


def test_projection_refuses_a_forged_cross_run_authority_claim(tmp_path):
    controller, _model, bound = make_controller(tmp_path, measured("inspect", ToolProposal("strings")))
    controller.handle(LeadRequest(bound, 1, LeadInitialContext("observed")))
    events = EventStore(tmp_path, run_id="run-1").events()
    fact_index = next(
        index
        for index, event in enumerate(events)
        if event.event_type == "lead-engagement.recorded" and event.payload["record"] == "turn"
    )
    forged = dict(events[fact_index].payload)
    forged["authority_run_id"] = "run-2"
    events[fact_index] = replace(events[fact_index], payload=forged)

    with pytest.raises(InvalidEventError, match="authority binding disagrees"):
        project_lead(events, "run-1", "engagement-1")
