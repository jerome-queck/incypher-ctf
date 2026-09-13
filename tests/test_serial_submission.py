import json
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest

from solver.board import CORRECT, Verdict
from solver.board_broker_contracts import BoardBrokerResult, BoardOperation, BoardOutcome
from solver.capability import CapabilityBinding
from solver.candidate_admission_contracts import (
    CandidateDisposition,
    CandidateProvenance,
    ReadyAdmission,
    ReadyCandidate,
)
from solver.record import Recorder
from solver.redaction import Redactor
from solver.manifest import generate_manifest
from solver.submission.authority import SerialSubmission
from solver.submission.bridge import CandidateSubmissionBridge
from solver.submission.bridge import ObservedCandidateSubmissionBridge
from solver.submission.receipt import link_manifest, verify_receipt, write_receipt
from solver.write_reservation import EffectIndeterminate
from test_manifest import release_candidate_profile
from test_candidate_admission import CANDIDATE as ADMITTED_FLAG
from test_candidate_admission import service as admission_service
from solver.lead_contracts import CandidateProposal as LeadCandidateProposal
from solver.flag import Candidate, OBSERVED


FLAG = b"zephyr{serial-authority}"
RETAINED = (
    Path(__file__).parent.parent
    / "docs/evidence/runtime-qualification-v2/293-serial-submission/serial-submission.receipt.json"
)


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 1
        return f"2026-09-13T00:00:{self.tick:02d}Z"


class Wire:
    def __init__(self, *, crash_during=False):
        self.posts = 0
        self.active = 0
        self.maximum = 0
        self.lock = threading.Lock()
        self.crash_during = crash_during
        self.candidates = []

    def submit(self, challenge_id, flag):
        with self.lock:
            self.posts += 1
            self.candidates.append(flag)
            self.active += 1
            self.maximum = max(self.maximum, self.active)
        if self.crash_during:
            self.crash_during = False
            raise SystemExit("partial POST crash")
        with self.lock:
            self.active -= 1
        return BoardBrokerResult(BoardOperation.SUBMIT, BoardOutcome.ANSWERED, Verdict(CORRECT, "accepted", 200))

    def close(self):
        pass


BINDING = CapabilityBinding("run-1", "boot-1", "generation-1", "lane-1", "attempt-1", "step-1")
READY_AT = "2026-09-12T23:59:59Z"


def ready(identity="a" * 64, candidate=FLAG):
    return ReadyCandidate(
        identity=identity,
        challenge_id=7,
        generation_id="generation-1",
        candidate=candidate,
        candidate_digest="b" * 64,
        provenance=CandidateProvenance(CandidateDisposition.OBSERVED, ("c" * 64,), ()),
        admission_rule="candidate-admission-v1",
    )


def queued(candidate=None, *, order=1, predecessors=()):
    return ReadyAdmission(candidate or ready(), READY_AT, order, predecessors)


def service(tmp_path, *, hook=None, wire=None):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}), write_authority_hook=hook)
    network = wire or Wire()
    authority = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: network,
    )
    return authority, recorder, network


def test_first_ready_candidate_dispatches_in_the_admission_call_with_one_post(tmp_path):
    authority, recorder, wire = service(tmp_path)

    result = authority.dispatch(queued(), binding=BINDING)

    assert result.candidate_id == "a" * 64
    assert result.verdict.outcome == CORRECT
    assert wire.posts == 1
    assert wire.maximum == 1
    trace = recorder.write_authority.trace(result.reservation_id)
    assert [row["state"] for row in trace] == ["reserved", "started", "committed"]
    times = trace[-1]["observation"]
    assert times["ready_at"] < times["reserved_at"] < times["requested_at"] < times["result_at"]


def test_durable_lead_candidate_is_admitted_and_dispatched_before_adapter_returns(tmp_path):
    admission, generation_id, evidence = admission_service(tmp_path)
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    wire = Wire()
    submission = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
    )
    bridge = CandidateSubmissionBridge(admission, submission)
    incoming = SimpleNamespace(
        run_id="run-1",
        boot_id="boot-1",
        generation_id=generation_id,
        lane_id="lane-1",
        attempt_id="attempt-1",
        work_id="42",
    )
    proposal = LeadCandidateProposal(
        ADMITTED_FLAG.decode(),
        (*evidence.observation_digests, *evidence.tool_receipt_digests),
        evidence.derivation.document(),
    )

    result = bridge(incoming, SimpleNamespace(proposal=proposal, proposal_id="proposal-1"))

    assert result is not None
    assert result.verdict.outcome == CORRECT
    assert wire.posts == 1


def test_observed_flag_sweep_enters_candidate_admission_before_typed_board_dispatch(tmp_path):
    admission, generation_id, evidence = admission_service(tmp_path)
    source = next(event for event in admission.store.events() if event.event_digest in evidence.observation_digests)
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    wire = Wire()
    submission = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
    )
    bridge = ObservedCandidateSubmissionBridge(
        admission,
        submission,
        run_id="run-1",
        boot_id="boot-1",
    )
    observation_ref = "observations/1.bin"
    (admission.store.run_dir / observation_ref).parent.mkdir(exist_ok=True)
    (admission.store.run_dir / observation_ref).write_bytes(source.body)

    result = bridge(
        Candidate(ADMITTED_FLAG.decode(), OBSERVED, ref=observation_ref),
        attempt_id="attempt-1",
        challenge_id=42,
        generation_id=generation_id,
    )

    assert result is not None
    assert result.verdict.outcome == CORRECT
    assert wire.posts == 1


def test_concurrent_proposals_serialize_and_keep_distinct_stable_identities(tmp_path):
    authority, recorder, wire = service(tmp_path)
    candidates = (
        queued(ready("1" * 64, b"zephyr{one}"), order=1),
        queued(ready("2" * 64, b"zephyr{two}"), order=2, predecessors=("1" * 64,)),
    )
    results = []
    threads = [
        threading.Thread(target=lambda one=one: results.append(authority.dispatch(one, binding=BINDING)))
        for one in candidates
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert wire.posts == 2
    assert wire.maximum == 1
    assert wire.candidates == ["zephyr{one}", "zephyr{two}"]
    assert {item.candidate_id for item in results} == {"1" * 64, "2" * 64}
    assert len({item.effect_id for item in results}) == 2
    assert (
        len([r for r in recorder.write_authority.reservations() if r.identity.operation == "board.submit-candidate"])
        == 2
    )


@pytest.mark.parametrize(
    "crash_point,expected_posts",
    [("after_reserve", 0), ("during_post", 1), ("after_effect", 1)],
)
def test_crash_before_during_or_after_post_never_replays_the_effect(tmp_path, crash_point, expected_posts):
    armed = True

    def crash(point):
        nonlocal armed
        if armed and point == crash_point:
            armed = False
            raise RuntimeError("crash")

    wire = Wire(crash_during=crash_point == "during_post")
    authority, recorder, wire = service(tmp_path, hook=crash, wire=wire)
    with pytest.raises((RuntimeError, SystemExit)):
        authority.dispatch(queued(), binding=BINDING)
    recorder.write_authority.close()
    restarted = Recorder(tmp_path / "state", "run-1", Redactor({}))
    replay = SerialSubmission(
        restarted.run_dir / "canonical",
        restarted.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
    )
    with pytest.raises(EffectIndeterminate):
        replay.dispatch(queued(), binding=BINDING)
    assert wire.posts == expected_posts


def test_candidate_generation_must_match_the_board_capability(tmp_path):
    authority, _recorder, wire = service(tmp_path)
    wrong = CapabilityBinding("run-1", "boot-1", "generation-2", "lane-1", "attempt-1", "step-1")

    with pytest.raises(ValueError, match="generation"):
        authority.dispatch(queued(), binding=wrong)

    assert wire.posts == 0


def test_concurrent_duplicate_candidate_replays_one_effect_identity(tmp_path):
    authority, _recorder, wire = service(tmp_path)
    results = []
    threads = [
        threading.Thread(target=lambda: results.append(authority.dispatch(queued(), binding=BINDING))) for _ in range(2)
    ]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert wire.posts == 1
    assert len({result.candidate_id for result in results}) == 1
    assert len({result.effect_id for result in results}) == 1


def test_crash_after_definitive_response_replays_result_without_a_second_post(tmp_path):
    authority, recorder, wire = service(tmp_path)
    first = authority.dispatch(queued(), binding=BINDING)
    recorder.write_authority.close()
    restarted = Recorder(tmp_path / "state", "run-1", Redactor({}))
    replay = SerialSubmission(
        restarted.run_dir / "canonical",
        restarted.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
    )

    second = replay.dispatch(queued(), binding=BINDING)

    assert wire.posts == 1
    assert second.effect_id == first.effect_id
    assert second.verdict.outcome == CORRECT


def test_versioned_sanitized_receipt_replays_and_links_candidate_manifest(tmp_path):
    authority, recorder, _wire = service(tmp_path)
    result = authority.dispatch(queued(), binding=BINDING)
    path = write_receipt(recorder.run_dir / "canonical", "run-1", recorder.write_authority)

    verify_receipt(path, recorder.write_authority)
    receipt = json.loads(path.read_text())
    assert receipt["schema_version"] == 2
    assert receipt["receipt_type"] == "serial-submission"
    assert FLAG.decode() not in path.read_text()
    assert receipt["submissions"][0]["effect_id"] == result.effect_id
    manifest = generate_manifest(
        image_digest=f"sha256:{'d' * 64}", release_candidate_profile=release_candidate_profile()
    )
    linked = link_manifest(manifest, path, recorder.write_authority)
    row = next(item for item in linked["requirements"] if item["row_id"] == "core.submission-tail")
    assert row["receipt_ref"] == "receipt:serial-submission"
    assert row["status"] == "planned"
    assert "final-window lifecycle remains planned" in row["reason"]


def test_retained_serial_and_crash_trace_verifies_without_live_authority():
    assert verify_receipt(RETAINED) == RETAINED
    trace = json.loads(RETAINED.read_text())
    assert [row["states"][-1] for row in trace["submissions"]] == ["committed", "possibly-sent", "aborted"]
