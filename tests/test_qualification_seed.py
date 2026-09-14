from types import SimpleNamespace

import pytest

from solver.candidate_admission import CandidateAdmission
from solver.candidate_admission_contracts import SubmissionContext
from solver.qualification_seed import seed_final_interval
from solver.record import Recorder
from solver.redaction import Redactor


def test_signed_seed_uses_generation_candidate_and_lease_authorities(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    admission = CandidateAdmission(
        tmp_path,
        "run-1",
        Redactor({}),
        lambda: "2026-09-22T01:00:00+00:00",
        (r"qualification\{[^}]+\}",),
        b"qualification-vault-key-material-32",
        recorder.generations,
    )
    acquired = []

    class Leases:
        def acquire(self, challenge_id, *, generation):
            acquired.append((challenge_id, generation))

    seed_final_interval(
        recorder,
        admission,
        lambda _challenge: SubmissionContext.static("revision-1", "board-1"),
        Leases(),
        selected_profile={"lanes": 2},
    )

    assert len(admission.ready_admissions()) == 2
    assert recorder.generations.projection().generations[0].active is True
    assert acquired[0][0] == 1
    assert acquired[0][1].attempt_id == "qualification-attempt-occupied"


def test_final_candidate_queue_replays_canonical_ready_order():
    from solver.board import Verdict
    from solver.final_candidate_queue import FinalCandidateQueue

    ready = SimpleNamespace(candidate=SimpleNamespace(identity="candidate-1", generation_id="generation-1"))
    admission = SimpleNamespace(ready_admissions=lambda: (ready,))
    calls = []
    submission = SimpleNamespace(
        dispatch=lambda value, *, binding: (
            calls.append((value, binding)) or SimpleNamespace(verdict=Verdict("correct", "accepted", 200))
        )
    )
    queue = FinalCandidateQueue(admission, submission, run_id="run-1", boot_id="boot-1")

    assert queue.candidate_ids() == ("candidate-1",)
    assert queue.submit("candidate-1") == "accepted"
    assert calls[0][1].generation_id == "generation-1"


def test_final_candidate_queue_replays_a_dispatched_candidate_for_truthful_drain_reconciliation():
    from solver.final_candidate_queue import FinalCandidateQueue

    sent = SimpleNamespace(candidate=SimpleNamespace(identity="candidate-sent", generation_id="generation-1"))
    held = SimpleNamespace(candidate=SimpleNamespace(identity="candidate-held", generation_id="generation-1"))
    submission = SimpleNamespace(
        was_dispatched=lambda admission: admission is sent,
        pending_candidate_ids=lambda: (),
    )
    queue = FinalCandidateQueue(
        SimpleNamespace(ready_admissions=lambda: (sent, held)),
        submission,
        run_id="run-1",
        boot_id="boot-1",
    )

    assert queue.candidate_ids() == ("candidate-sent", "candidate-held")


def test_seed_refuses_a_profile_without_selected_two_lane_topology(tmp_path):
    with pytest.raises(ValueError, match="signed two-Lane"):
        seed_final_interval(None, None, None, None, selected_profile={"lanes": 1})


def test_final_queue_leaves_ambiguity_to_reconciler_without_resend():
    from solver.final_candidate_queue import FinalCandidateQueue
    from solver.write_reservation import EffectIndeterminate

    ready = SimpleNamespace(candidate=SimpleNamespace(identity="candidate-1", generation_id="generation-1"))
    queue = FinalCandidateQueue(
        SimpleNamespace(ready_admissions=lambda: (ready,)),
        SimpleNamespace(
            dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(EffectIndeterminate("pending")),
            was_possibly_sent=lambda _candidate_id: True,
        ),
        run_id="run-1",
        boot_id="boot-1",
    )

    assert queue.submit("candidate-1") == "possibly-sent"


def test_final_queue_does_not_call_a_pre_wire_refusal_possibly_sent():
    from solver.final_candidate_queue import FinalCandidateQueue, SubmissionDeferred
    from solver.write_reservation import EffectIndeterminate

    ready = SimpleNamespace(candidate=SimpleNamespace(identity="candidate-1", generation_id="generation-1"))
    submission = SimpleNamespace(
        dispatch=lambda *_args, **_kwargs: (_ for _ in ()).throw(EffectIndeterminate("barrier")),
        was_possibly_sent=lambda _candidate_id: False,
        defer=lambda _candidate_id: None,
    )
    queue = FinalCandidateQueue(
        SimpleNamespace(ready_admissions=lambda: (ready,)),
        submission,
        run_id="run-1",
        boot_id="boot-1",
    )

    with pytest.raises(SubmissionDeferred):
        queue.submit("candidate-1")


def test_final_queue_uses_dedicated_active_final_interval_generation():
    from solver.board import Verdict
    from solver.final_candidate_queue import FinalCandidateQueue

    ready = SimpleNamespace(candidate=SimpleNamespace(identity="candidate-1", generation_id="closed-work"))
    calls = []
    submission = SimpleNamespace(
        dispatch=lambda value, *, binding: (
            calls.append((value, binding)) or SimpleNamespace(verdict=Verdict("correct", "accepted", 200))
        )
    )
    queue = FinalCandidateQueue(
        SimpleNamespace(ready_admissions=lambda: (ready,)),
        submission,
        run_id="run-1",
        boot_id="boot-1",
        generation_id="active-final-interval",
    )

    assert queue.submit("candidate-1") == "accepted"
    assert calls[0][1].generation_id == "active-final-interval"
