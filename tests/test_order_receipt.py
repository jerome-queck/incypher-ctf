"""Order decision receipts and controlled policy qualification."""

import json
import datetime as dt
from dataclasses import replace

import pytest

from solver.event_store import EventStore, ObservationRecorded
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.intake_contracts import IntakeDecisionRecorded, IntakeRecord
from solver.coherent_intake import crowd_source_from_contract
from solver.intake_qualification import IntakeContract, PriorFence, TypedId
from solver.order_journal import OrderJournal
from solver.order_input_contracts import OrderInputRecord, OrderInputRecorded
from solver.order_policy import CrowdSource, DurableTier, OrderInput, decide_order, order_input_from_document
from solver.order_policy_proof import load_controlled_proof
from solver.order_receipt import (
    MANIFEST_RECEIPT_REF,
    capsule_contract,
    link_manifest,
    manifest_receipt,
    verify_decision_receipt,
    write_decision_receipt,
)
from solver.redaction import Redactor
from solver.triage_judgement_contracts import TriageJudgementRecorded
from test_manifest import draft
from test_order_publication import NOW, _request


def _append_snapshot(tmp_path, snapshot, serial):
    contract = IntakeContract(snapshot.profile_digest, "f" * 64)
    store = EventStore(tmp_path, run_id="run-1")
    store.append(
        IntakeDecisionRecorded(
            f"intake:{serial}:started",
            f"attempt-{serial}",
            IntakeRecord.STARTED,
            snapshot.profile_digest,
            contract.digest,
            "e" * 64,
            PriorFence.genesis(snapshot.profile_digest),
            ts=NOW.isoformat(),
        ),
        body=canonical_bytes({"contract": contract.document(), "authority": {"test": True}}),
    )
    terminal = store.append(
        IntakeDecisionRecorded(
            f"intake:{serial}",
            f"attempt-{serial}",
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
    return terminal, contract


def _canonical_decision(tmp_path, size=3):
    request = _request(size)
    snapshot = request.authority.snapshot
    event, contract = _append_snapshot(tmp_path, snapshot, 1)
    fence = PriorFence(snapshot.profile_digest, "intake:1", event.event_digest, snapshot.digest)
    authority = replace(
        request.authority,
        fence=fence,
        crowd_source=crowd_source_from_contract(tmp_path, "run-1", contract),
    )
    return decide_order(_with_boundary_source(tmp_path, OrderInput(authority, request.run, request.dials)))


def _with_boundary_source(tmp_path, request):
    window_path = tmp_path / "runs" / "run-1" / "window.json"
    window_path.parent.mkdir(parents=True, exist_ok=True)
    opened_at = request.run.final_submission_cutoff - dt.timedelta(seconds=request.run.window_seconds)
    window_path.write_text(
        json.dumps(
            {
                "opened_at": opened_at.isoformat(),
                "ends_at": request.run.final_submission_cutoff.isoformat(),
            }
        )
        + "\n"
    )
    from solver.work_generation import _project_events

    projection = _project_events(EventStore(tmp_path, run_id="run-1").events(), "run-1")
    clock_body = canonical_bytes(
        {"schema_version": 1, "record": "boundary-clock", "observed_at": request.run.boundary_at.isoformat()}
    )
    clock = EventStore(tmp_path, run_id="run-1").append(
        OrderInputRecorded(
            f"order-input:boundary-clock:{request.run.boundary_id}",
            OrderInputRecord.BOUNDARY_CLOCK,
            request.run.boundary_at.isoformat(),
        ),
        body=clock_body,
    )
    run = replace(
        request.run,
        window_digest=digest_bytes(window_path.read_bytes()),
        generation_projection_digest=projection.digest,
        intake_event_digest=request.authority.fence.event_digest,
        boundary_clock_event_digest=clock.event_digest,
    )
    body = canonical_bytes(run.boundary_source_document())
    event = EventStore(tmp_path, run_id="run-1").append(
        OrderInputRecorded(
            f"order-input:boundary:{run.boundary_id}",
            OrderInputRecord.BOUNDARY_FACTS,
            run.boundary_at.isoformat(),
        ),
        body=body,
    )
    return replace(request, run=replace(run, boundary_fact_event_digest=event.event_digest))


def test_runtime_receipt_is_immutable_sanitized_and_rebuilt_from_canonical_publication(tmp_path):
    decision = _canonical_decision(tmp_path)
    published = OrderJournal(tmp_path, "run-1", Redactor({}), timestamp=lambda: NOW.isoformat()).publish(decision)

    path = write_decision_receipt(tmp_path, "run-1", published.publication_id)
    supplied = json.loads(path.read_text())

    assert verify_decision_receipt(path) == path
    assert supplied["decision_digest"] == decision.digest
    assert supplied["order_value_basis"] == "coherent-challenge-values"
    assert supplied["manifest_link"] is None
    assert "park" not in path.read_text().lower()

    supplied["decision_digest"] = "0" * 64
    path.write_bytes(canonical_bytes(supplied) + b"\n")
    with pytest.raises(ValueError, match="canonical publication"):
        verify_decision_receipt(path)


def test_semantically_wrong_published_output_cannot_produce_a_receipt(tmp_path):
    decision = _canonical_decision(tmp_path)
    wrong_row = replace(decision.rows[0], budget_s=decision.rows[0].budget_s + 1)
    wrong = replace(decision, rows=(wrong_row, *decision.rows[1:]))
    published = OrderJournal(tmp_path, "run-1", Redactor({}), timestamp=lambda: NOW.isoformat()).publish(wrong)

    with pytest.raises(ValueError, match="does not recompute"):
        write_decision_receipt(tmp_path, "run-1", published.publication_id)


def test_terminal_and_receipt_reference_chunked_rows_instead_of_embedding_snapshot_population(tmp_path):
    decision = _canonical_decision(tmp_path, 520)
    published = OrderJournal(tmp_path, "run-1", Redactor({}), timestamp=lambda: NOW.isoformat()).publish(decision)
    path = write_decision_receipt(tmp_path, "run-1", published.publication_id)
    terminal = published.events[-1]

    assert terminal.blob_bytes < 1024 * 1024
    assert path.stat().st_size < 1024 * 1024
    assert b"challenge-520" not in terminal.body
    assert "order_rows" not in json.loads(path.read_text())


def test_forged_historical_snapshot_cannot_qualify_crowd_against_a_genuine_current_boundary(tmp_path):
    request = _request(3)
    current = request.authority.snapshot
    older = replace(current, snapshot_id="snapshot-older", observed_at=NOW - dt.timedelta(minutes=10))
    current_event, contract = _append_snapshot(tmp_path, current, 2)
    fence = PriorFence(current.profile_digest, "intake:2", current_event.event_digest, current.digest)
    forged = replace(older, snapshot_id="snapshot-forged")
    authority = replace(
        request.authority,
        fence=fence,
        history=(current, forged),
        crowd_source=crowd_source_from_contract(tmp_path, "run-1", contract),
    )
    decision = decide_order(_with_boundary_source(tmp_path, OrderInput(authority, request.run, request.dials)))
    published = OrderJournal(tmp_path, "run-1", Redactor({}), timestamp=lambda: NOW.isoformat()).publish(decision)

    with pytest.raises(ValueError, match="history is absent"):
        write_decision_receipt(tmp_path, "run-1", published.publication_id)


@pytest.mark.parametrize("forged_id", [1, 2])
def test_forged_durable_tier_cannot_hide_behind_a_real_model_answer(tmp_path, forged_id):
    request = _request(1)
    snapshot = request.authority.snapshot
    (intake, contract) = _append_snapshot(tmp_path, snapshot, 1)
    evidence = EventStore(tmp_path, run_id="run-1").append(
        ObservationRecorded("triage", 1, "judge", "judge", "codex", ts=NOW.isoformat()), body=b"{}"
    )
    judgement_body = canonical_bytes(
        {
            "schema_version": 1,
            "asked": [snapshot.challenges[0].challenge_id.document()],
            "response": "1 3",
            "observation_event_digests": [evidence.event_digest],
        }
    )
    judgement = EventStore(tmp_path, run_id="run-1").append(
        TriageJudgementRecorded("triage-judgement:1", "a" * 64, NOW.isoformat()), body=judgement_body
    )
    body = canonical_bytes(
        {
            "schema_version": 1,
            "record": "durable-tiers",
            "assessed": [snapshot.challenges[0].challenge_id.document()],
            "evidence_event_digests": [evidence.event_digest],
            "judgement_event_digest": judgement.event_digest,
            "tiers": [
                {
                    "challenge_id": {"type": "integer", "value": str(forged_id)},
                    "tier": 4,
                    "provenance": "judged",
                }
            ],
        }
    )
    EventStore(tmp_path, run_id="run-1").append(
        OrderInputRecorded("order-input:tiers", OrderInputRecord.DURABLE_TIERS, NOW.isoformat()), body=body
    )
    authority = replace(
        request.authority,
        fence=PriorFence(snapshot.profile_digest, "intake:1", intake.event_digest, snapshot.digest),
        crowd_source=crowd_source_from_contract(tmp_path, "run-1", contract),
    )
    run = replace(
        request.run,
        durable_tiers=(DurableTier(TypedId.parse(forged_id), 4, judgement.event_digest),),
    )
    decision = decide_order(_with_boundary_source(tmp_path, OrderInput(authority, run, request.dials)))
    published = OrderJournal(tmp_path, "run-1", Redactor({}), timestamp=lambda: NOW.isoformat()).publish(decision)

    with pytest.raises(ValueError, match="not among|differs from"):
        write_decision_receipt(tmp_path, "run-1", published.publication_id)


@pytest.mark.parametrize("field", ["authority", "boundary"])
def test_caller_authored_authority_or_clock_cannot_hide_behind_real_sources(tmp_path, field):
    base = _canonical_decision(tmp_path)
    snapshot_event = next(
        event
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.event_type == "intake-decision.recorded" and event.payload.get("record") == "settled"
    )
    from solver.intake_qualification import snapshot_from_document

    snapshot = snapshot_from_document(json.loads(snapshot_event.body))
    request = order_input_from_document(base.input_document, {snapshot.digest: snapshot})
    if field == "authority":
        request = replace(
            request,
            authority=replace(request.authority, crowd_source=CrowdSource(True, False, "0" * 64)),
        )
        match = "authority differs"
    else:
        request = replace(
            request,
            run=replace(
                request.run, final_submission_cutoff=request.run.final_submission_cutoff + dt.timedelta(minutes=1)
            ),
        )
        match = "boundary facts differ"
    decision = decide_order(request)
    published = OrderJournal(tmp_path, "run-1", Redactor({}), timestamp=lambda: NOW.isoformat()).publish(decision)

    with pytest.raises(ValueError, match=match):
        write_decision_receipt(tmp_path, "run-1", published.publication_id)


def test_only_controlled_aggregate_qualification_links_the_manifest_row():
    proof = load_controlled_proof()
    descriptor = manifest_receipt()
    linked = link_manifest(draft())
    row = next(item for item in linked["requirements"] if item["row_id"] == "core.triage-order")

    assert proof["verdict"] == "pass"
    assert {item["scenario_id"] for item in proof["scenarios"]} == {
        "qualified-crowd",
        "unit-fallback",
        "floor-and-final-interval",
        "typed-id-ties",
        "active-grant-freeze",
        "working-set-no-park",
    }
    assert descriptor["ref"] == MANIFEST_RECEIPT_REF
    assert row["status"] == "implemented"
    assert row["receipt_ref"] == MANIFEST_RECEIPT_REF
    assert linked["receipts"][-1] == descriptor
    contract = capsule_contract()
    assert contract.kind == "order-policy"
    assert tuple(contract.validate(proof, None)) == ()
