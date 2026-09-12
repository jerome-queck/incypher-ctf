import json
import runpy
from dataclasses import replace

import pytest

from solver.triage_judge import TriageJudgeController, evidence_digest
from solver.triage_judge_contracts import (
    MeasuredTriageJudgement,
    TriageEvidence,
    TriageJudgeClassification,
    TriageJudgeMeasure,
    TriageJudgeRequest,
    TriageProposal,
)
from solver.triage_judge_receipt import (
    MANIFEST_RECEIPT_REF,
    MANIFEST_ROW_ID,
    link_manifest,
    manifest_receipt,
    verify_receipt,
)
from solver.redaction import Redactor


def request(*, batch_id="triage-arrivals-1", deadline="2026-09-13T00:01:00Z"):
    evidence = (TriageEvidence("observation", "challenge-7", "b" * 64, "new unresolved Challenge"),)
    return TriageJudgeRequest(
        batch_id=batch_id,
        evidence_digest=evidence_digest(evidence),
        evidence=evidence,
        deterministic=TriageProposal("triage", "unsettled", 1.0, "deterministic-rule"),
        deadline=deadline,
    )


def turn(*, confidence=0.9, completed_at="2026-09-13T00:00:10Z", proposal=None):
    return MeasuredTriageJudgement(
        proposal or TriageProposal("triage", "tier-3", confidence, "native"),
        TriageJudgeMeasure("native", "gpt-test", 12, 100, 20, True, completed_at),
    )


class Model:
    def __init__(self, answer):
        self.answer = answer
        self.calls = 0

    def __call__(self, evidence):
        self.calls += 1
        self.seen = evidence
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.mark.parametrize(
    ("answer", "classification", "source", "reason"),
    [
        (turn(), TriageJudgeClassification.ACCEPTED, "model", "measured model Triage proposal accepted"),
        (turn(confidence=0.2), TriageJudgeClassification.REJECTED, "deterministic", "confidence below threshold"),
        (RuntimeError("offline"), TriageJudgeClassification.UNAVAILABLE, "deterministic", "inference unavailable"),
        (object(), TriageJudgeClassification.MALFORMED, "deterministic", "model output malformed"),
        (
            replace(turn(), measure=replace(turn().measure, usage_known=False)),
            TriageJudgeClassification.UNMEASURED,
            "deterministic",
            "model output was not measured",
        ),
        (
            turn(completed_at="2026-09-13T00:02:00Z"),
            TriageJudgeClassification.LATE,
            "deterministic",
            "model output arrived after Triage batch deadline",
        ),
    ],
)
def test_synthetic_checkpoint_acceptance_and_fallbacks_are_explicit(tmp_path, answer, classification, source, reason):
    model = Model(answer)
    controller = TriageJudgeController(tmp_path, "run-1", Redactor({}), model)

    outcome = controller.evaluate(request())

    assert outcome.classification is classification
    assert outcome.accepted_source == source
    assert outcome.acceptance_reason == reason
    assert outcome.proposal.kind == ("triage" if source == "model" else "triage")
    assert not hasattr(outcome.proposal, "execute")
    assert model.seen == request().evidence


def test_checkpoint_input_is_typed_bounded_and_contains_no_history_or_capability(tmp_path):
    secret = "board-secret"
    oversized_evidence = (TriageEvidence("observation", "step", "b" * 64, "x" * 1025),)
    oversized = replace(request(), evidence=oversized_evidence, evidence_digest=evidence_digest(oversized_evidence))
    controller = TriageJudgeController(tmp_path, "run-1", Redactor({"BOARD_TOKEN": secret}), Model(turn()))

    with pytest.raises(ValueError, match="summary exceeds"):
        controller.evaluate(oversized)
    with pytest.raises(TypeError):
        controller.evaluate(replace(request(), evidence=(secret,)))


def test_accepted_identity_replays_once_without_reinvocation_and_conflicts_refuse(tmp_path):
    model = Model(turn())
    first = TriageJudgeController(tmp_path, "run-1", Redactor({}), model).evaluate(request())
    restarted_model = Model(RuntimeError("must not run"))
    restarted = TriageJudgeController(tmp_path, "run-1", Redactor({}), restarted_model)

    duplicate = restarted.evaluate(request())

    assert first.verdict_id == duplicate.verdict_id
    assert duplicate.classification is TriageJudgeClassification.DUPLICATE
    assert restarted_model.calls == 0
    with pytest.raises(ValueError, match="arrival-batch identity conflict"):
        restarted.evaluate(replace(request(), deadline="2026-09-13T00:03:00Z"))


@pytest.mark.parametrize(
    "evidence",
    [
        (TriageEvidence("", "challenge", "b" * 64, "summary"),),
        (TriageEvidence("observation", "", "b" * 64, "summary"),),
        (TriageEvidence("observation", "challenge", "B" * 64, "summary"),),
        (TriageEvidence("observation", "challenge", "b" * 63, "summary"),),
        (TriageEvidence("x" * 33, "challenge", "b" * 64, "summary"),),
        (TriageEvidence("observation", "x" * 129, "b" * 64, "summary"),),
    ],
)
def test_hostile_typed_evidence_is_rejected_before_inference(tmp_path, evidence):
    model = Model(turn())
    invalid = replace(request(), evidence=evidence, evidence_digest=evidence_digest(evidence))
    with pytest.raises(ValueError):
        TriageJudgeController(tmp_path, "run-1", Redactor({}), model).evaluate(invalid)
    assert model.calls == 0


def test_claimed_evidence_digest_must_equal_canonical_typed_evidence(tmp_path):
    with pytest.raises(ValueError, match="differs"):
        TriageJudgeController(tmp_path, "run-1", Redactor({}), Model(turn())).evaluate(
            replace(request(), evidence_digest="c" * 64)
        )


def test_receipt_is_sanitized_verifiable_and_links_the_candidate_manifest(tmp_path):
    controller = TriageJudgeController(tmp_path, "run-1", Redactor({"BOARD_TOKEN": "secret"}), Model(turn()))
    controller.evaluate(request())

    path = controller.write_receipt()
    receipt = json.loads(path.read_text())

    assert verify_receipt(path) == path
    assert receipt["receipt_type"] == "triage-judge"
    assert receipt["arrival_batches"][0]["evidence_digest"] == request().evidence_digest
    assert receipt["arrival_batches"][0]["measured_route"] == "native"
    assert receipt["arrival_batches"][0]["deterministic_fallback_comparison"]["same_proposal"] is False
    assert "secret" not in path.read_text()
    assert receipt["manifest_link"] == {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF}
    assert manifest_receipt(path)["ref"] == "receipt:triage-judge"
    draft = runpy.run_path("tests/test_manifest.py")["draft"]()
    linked = link_manifest(draft, path)
    row = next(item for item in linked["requirements"] if item["row_id"] == MANIFEST_ROW_ID)
    descriptor = next(item for item in linked["receipts"] if item["ref"] == MANIFEST_RECEIPT_REF)
    assert row["status"] == "implemented"
    assert row["receipt_ref"] == MANIFEST_RECEIPT_REF
    assert descriptor == manifest_receipt(path)
