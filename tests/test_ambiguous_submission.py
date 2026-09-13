import json
from pathlib import Path

import pytest

from solver.manifest import generate_manifest
from solver.submission.ambiguity import (
    AmbiguousSubmissionFence,
    Evidence,
    FenceClosed,
    link_manifest,
    verify_receipt,
)
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


def fence(tmp_path, clock, probes=(), boot="boot-1"):
    answers = iter(probes)
    return AmbiguousSubmissionFence(
        tmp_path,
        run_id="run-1",
        boot_id=boot,
        monotonic=clock,
        probe=lambda _pending: next(answers, Evidence.unsettled("authenticated-submission-ledger")),
    )


@pytest.mark.parametrize("verdict,disposition", [("correct", "accepted"), ("incorrect", "rejected")])
def test_exact_authenticated_candidate_evidence_closes_inside_fence(tmp_path, verdict, disposition):
    clock = Clock()
    service = fence(
        tmp_path,
        clock,
        [Evidence.exact_verdict(verdict, source="authenticated-submission-ledger", candidate_id="candidate-1")],
    )
    pending = service.begin("candidate-1", "effect-1", challenge_id=7, wire_started_at=clock())

    closed = service.reconcile(pending)

    assert closed.disposition == disposition
    assert closed.provenance == "authenticated-submission-ledger"


def test_unsettled_and_score_change_never_infer_solved_then_expire_at_exactly_sixty(tmp_path):
    clock = Clock()
    service = fence(
        tmp_path,
        clock,
        [Evidence.score_change("scoreboard"), Evidence.unsettled("authenticated-solve-ledger")],
    )
    pending = service.begin("candidate-1", "effect-1", challenge_id=7, wire_started_at=clock())
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
    clock = Clock(100.0)
    pending = fence(tmp_path, clock).begin("candidate-1", "effect-1", challenge_id=7, wire_started_at=100.0)
    clock.value = restart_at
    restarted = fence(tmp_path, clock, boot="boot-2")

    state = restarted.reconcile(pending)

    assert state.deadline == 160.0
    assert restarted.post_trace("candidate-1") == ("possibly-sent",)
    assert state.disposition == ("unknown-and-spent" if restart_at >= 160.0 else "pending")


def test_receipt_is_sanitized_replay_verified_and_manifest_linked(tmp_path):
    clock = Clock()
    service = fence(tmp_path, clock, [Evidence.exact_verdict("correct", source="auth-ledger", candidate_id="c")])
    pending = service.begin("c", "e", challenge_id=7, wire_started_at=clock())
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
    service.begin("candidate-1", "effect-1", challenge_id=7, wire_started_at=100.0)
    with pytest.raises(FenceClosed, match="already spent"):
        service.begin("candidate-1", "effect-1", challenge_id=7, wire_started_at=100.0)


def test_retained_ambiguity_receipt_verifies_independently():
    assert verify_receipt(RETAINED) == RETAINED
