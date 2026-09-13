import json
from pathlib import Path

import pytest

from solver.manifest import generate_manifest
from solver.manifest import parse_manifest
from solver.record import Recorder
from solver.redaction import Redactor
from solver.submission.ambiguity import (
    AmbiguousSubmissionFence,
    AuthenticatedSubmissionEvidence,
    CompleteSubmissionIdentity,
    Evidence,
    FenceClosed,
    link_manifest,
    verify_receipt,
)
from solver.board_broker_contracts import BoardBrokerResult, BoardOperation, BoardOutcome, BoardProvenance
from test_manifest import release_candidate_profile

RETAINED = (
    Path(__file__).parent.parent
    / "docs/evidence/runtime-qualification-v2/294-ambiguous-submission/ambiguous-submission.receipt.json"
)


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


def fence(tmp_path, clock, probes=(), boot="boot-1", wall=None):
    answers = iter(probes)
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    return AmbiguousSubmissionFence(
        tmp_path,
        recorder.write_authority,
        run_id="run-1",
        boot_id=boot,
        monotonic=clock,
        wall_time=wall or clock,
        probe=lambda _pending: next(answers, Evidence.unsettled("authenticated-submission-ledger")),
    )


def exact(verdict, candidate="candidate-1", effect="effect-1"):
    result = BoardBrokerResult(
        BoardOperation.SUBMIT,
        BoardOutcome.ANSWERED,
        provenance=BoardProvenance(
            endpoint="/api/v1/submissions",
            classified_event_id="board-event-1",
            binding_digest="binding-digest",
            peer_identity_digest="peer-digest",
        ),
        request_id="request-1",
    )
    return AuthenticatedSubmissionEvidence.from_broker(
        result,
        verdict=verdict,
        candidate_id=candidate,
        effect_id=effect,
        submission_epoch=1,
    )


def identity(candidate="candidate-1"):
    return CompleteSubmissionIdentity("board-1", 7, "revision-1", "instance-1", f"digest:{candidate}", 1)


def begin(service, candidate="candidate-1"):
    complete = identity(candidate)
    service.reserve_path(candidate, complete)
    return service.begin(candidate, complete)


@pytest.mark.parametrize("verdict,disposition", [("correct", "accepted"), ("incorrect", "rejected")])
def test_exact_authenticated_candidate_evidence_closes_inside_fence(tmp_path, verdict, disposition):
    clock = Clock()
    service = fence(
        tmp_path,
        clock,
        [exact(verdict)],
    )
    complete = identity()
    service._probe = lambda _pending: exact(verdict, effect=complete.effect_id)
    pending = begin(service)

    closed = service.reconcile(pending)

    assert closed.disposition == disposition
    assert closed.provenance == "/api/v1/submissions"


def test_unsettled_and_score_change_never_infer_solved_then_expire_at_exactly_sixty(tmp_path):
    clock = Clock()
    service = fence(
        tmp_path,
        clock,
        [Evidence.score_change("scoreboard"), Evidence.unsettled("authenticated-solve-ledger")],
    )
    pending = begin(service)
    assert service.reconcile(pending).disposition == "pending"
    clock.value = 159.999
    assert service.reconcile(pending).disposition == "pending"
    clock.value = 160.0

    closed = service.reconcile(pending)

    assert closed.disposition == "unknown-and-spent"
    assert service.barrier_open is False
    assert service.can_submit("candidate-2") is True
    assert service.can_submit("candidate-1") is False


@pytest.mark.parametrize("restart_at", [100.0, 114.0, 159.0, 160.0, 190.0])
def test_restart_replays_original_deadline_without_resending(tmp_path, restart_at):
    first_monotonic = Clock(4000.0)
    first_wall = Clock(100.0)
    first = fence(tmp_path, first_monotonic, wall=first_wall)
    pending = begin(first)
    first.close_boot()
    reset_monotonic = Clock(3.0)
    restarted = fence(tmp_path, reset_monotonic, boot="boot-2", wall=Clock(restart_at))

    state = restarted.reconcile(pending)

    assert state.deadline == 160.0
    assert restarted.post_trace("candidate-1") == ("possibly-sent",)
    assert state.disposition == ("unknown-and-spent" if restart_at >= 160.0 else "pending")


def test_receipt_is_sanitized_replay_verified_and_manifest_linked(tmp_path):
    clock = Clock()
    service = fence(tmp_path, clock)
    complete = identity("c")
    service._probe = lambda _pending: exact("correct", "c", complete.effect_id)
    pending = begin(service, "c")
    service.reconcile(pending)
    path = service.write_receipt()

    assert verify_receipt(path) == path
    receipt = json.loads(path.read_text())
    assert receipt["schema_version"] == 1
    assert receipt["receipt_type"] == "ambiguous-submission"
    assert receipt["no_resend_trace"] == [{"candidate_id": "c", "posts": 1}]
    manifest = generate_manifest(
        image_digest=f"sha256:{'d' * 64}", release_candidate_profile=release_candidate_profile()
    )
    linked = link_manifest(manifest, path)
    row = next(row for row in linked["requirements"] if row["row_id"] == "core.submission-tail")
    assert row["receipt_ref"] == "receipt:ambiguous-submission"
    tampered = path.read_text().replace('"posts":1', '"posts":2')
    path.write_text(tampered)
    with pytest.raises(ValueError, match="no-resend"):
        verify_receipt(path)


def test_same_candidate_can_never_begin_twice(tmp_path):
    service = fence(tmp_path, Clock())
    begin(service)
    with pytest.raises(FenceClosed, match="already spent"):
        service.begin("candidate-1", identity())


def test_retained_ambiguity_receipt_verifies_independently():
    assert verify_receipt(RETAINED) == RETAINED
    receipt = json.loads(RETAINED.read_text())
    assert receipt["producer"] == "external-evaluator"
    manifest = parse_manifest(json.loads((RETAINED.parent / "candidate-manifest.json").read_text()))
    row = next(row for row in manifest["requirements"] if row["row_id"] == "core.submission-tail")
    descriptor = next(item for item in manifest["receipts"] if item["ref"] == row["receipt_ref"])
    assert descriptor["digest"] == __import__("hashlib").sha256(RETAINED.read_bytes()).hexdigest()
