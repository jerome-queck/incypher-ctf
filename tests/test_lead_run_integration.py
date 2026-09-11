from solver.event_store_storage import digest_bytes
from solver.lead_contracts import CandidateProposal, MeasuredLeadTurn, TurnMeasure
from solver.lead_controller import LeadController
from solver.lead_v1_adapter import V1LeadAdapter
from solver.redaction import Redactor

from test_run_loop import Agent, Clock, Wire, solver


def test_v1_run_migrates_one_real_prompt_and_carry_through_fake_lead(tmp_path):
    clock = Clock()
    wire = Wire(count=1)
    requests = []

    def fake_model(request):
        requests.append(request)
        clock.tick(1100)
        return MeasuredLeadTurn(
            "derive from real Run context",
            CandidateProposal(wire.flags[1], ("v1-prompt",)),
            TurnMeasure("gpt-5", duration_ms=7, tokens_in=11, tokens_out=5, usage_known=True),
        )

    controller = LeadController(
        tmp_path / "state",
        "gate",
        Redactor({}),
        lambda: clock().isoformat(),
        fake_model,
    )
    run, _recorder = solver(
        tmp_path,
        wire,
        Agent(clock, wire=wire),
        clock,
        lasting=1000.0,
        board_broker_boot_id="boot-000001",
        lead_adapter=V1LeadAdapter(controller),
    )

    ending = run.work()

    assert ending.flags == (wire.flags[1],)
    assert wire.submitted == [(1, wire.flags[1])]
    assert len(requests) == 1
    request = requests[0]
    assert "challenge-1" in request.context
    assert "What the Board gave us" in request.context
    assert request.binding.context_digest == digest_bytes(request.context.encode())
    assert request.binding.boot_id == "boot-000001"
    assert request.binding.generation_id == "generation-000001"
    assert request.binding.harness == "native-codex"
    assert request.binding.requested_route == "codex-subscription"
    assert (tmp_path / "state" / "runs" / "gate" / "canonical" / "lead-engagement.receipt.json").is_file()
