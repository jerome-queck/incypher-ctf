import json
import threading
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path

import pytest

from solver.board import CORRECT, Verdict
from solver.board_broker_contracts import (
    BoardBrokerResult,
    BoardOperation,
    BoardOutcome,
    BoardProvenance,
    SubmissionLedgerValue,
)
from solver.capability import CapabilityBinding
from solver.candidate_admission_contracts import (
    CandidateDisposition,
    CandidateProvenance,
    ReadyAdmission,
    ReadyCandidate,
    SubmissionContext,
)
from solver.__main__ import _submission_identity_composition
from solver.event_store import EventStore
from solver.event_store import InvalidReceiptError
from solver.event_store_storage import canonical_bytes
from solver.instance_ledger import POPULATED, LedgerResult, LedgerRow
from solver.record import Recorder
from solver.redaction import Redactor
from solver.manifest import generate_manifest
from solver.submission.authority import SerialSubmission
from solver.submission.ambiguity import (
    AmbiguityAwareSerialSubmission,
    AmbiguousSubmissionFence,
    CompleteSubmissionIdentity,
    Evidence,
)
from solver.submission.bridge import CandidateSubmissionBridge
from solver.submission.bridge import ObservedCandidateSubmissionBridge
from solver.submission.receipt import link_manifest, verify_receipt, write_receipt
from solver.submission.runtime import compose_submission_runtime
from solver.submission.reconciliation import broker_evidence_probe
from solver.submission.epoch import SubmissionEpochAuthority
from solver.write_reservation import EffectIndeterminate
from solver.write_reservation import Capacity, EffectIdentity, ReservationUnavailable
from test_manifest import release_candidate_profile
from test_candidate_admission import CANDIDATE as ADMITTED_FLAG
from test_candidate_admission import service as admission_service
from solver.lead_contracts import CandidateProposal as LeadCandidateProposal
from solver.flag import Candidate, OBSERVED
from solver.final_candidate_queue import FinalCandidateQueue
from solver.final_interval_contracts import SubmissionDeferred


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
        self.complete_identities = []

    def submit(self, challenge_id, flag, *, candidate_id="", complete_identity=None, timeout_seconds=30):
        with self.lock:
            self.posts += 1
            self.candidates.append(flag)
            self.complete_identities.append((candidate_id, complete_identity))
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

    def submission_ledger(self, _effect_id):
        return BoardBrokerResult(BoardOperation.SUBMISSION_LEDGER, BoardOutcome.UNREACHABLE)


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
        submission_context=SubmissionContext.static("challenge-revision-1", "board-1"),
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
        post_interval_seconds=0,
    )
    return authority, recorder, network


def test_production_identity_composition_replays_epoch_and_binds_canonical_context(tmp_path):
    static = SimpleNamespace(
        challenge_id=SimpleNamespace(value=7),
        revision_digest="revision-from-intake",
        challenge_type="static",
    )
    isolated = SimpleNamespace(
        challenge_id=SimpleNamespace(value=8),
        revision_digest="isolated-revision",
        challenge_type="dynamic_iac",
    )
    intake = SimpleNamespace(
        order_authority=lambda: SimpleNamespace(snapshot=SimpleNamespace(challenges=(static, isolated)))
    )
    ledger = LedgerResult(
        POPULATED,
        "teams",
        1,
        2,
        "ledger-digest",
        owned=(LedgerRow("row-8", 8, team_id=2, response_digest="response-digest"),),
    )
    store = EventStore(tmp_path, run_id="run-1", redactor=Redactor({}))

    context_for, identity_for, epochs = _submission_identity_composition(
        intake, ledger, "https://board.example", store, Clock()
    )
    context = context_for(7)
    candidate = replace(ready(), submission_context=context)

    assert context.challenge_revision == "revision-from-intake"
    assert context.instance_provenance == "static:https://board.example"
    assert context_for(8).instance_provenance == "ledger:row-8:response-digest"
    assert epochs.current("https://board.example") == 1
    assert identity_for(candidate).document() == {
        "board_identity": "https://board.example",
        "challenge_id": 7,
        "challenge_revision": "revision-from-intake",
        "instance_provenance": "static:https://board.example",
        "candidate_digest": "b" * 64,
        "submission_epoch": 1,
        "reservation_id": identity_for(candidate).reservation_id,
        "effect_id": identity_for(candidate).effect_id,
    }

    replayed = EventStore(tmp_path, run_id="run-1", redactor=Redactor({}))
    _, restarted_identity_for, restarted_epochs = _submission_identity_composition(
        intake, ledger, "https://board.example", replayed, Clock()
    )
    assert restarted_epochs.current("https://board.example") == 1
    assert restarted_identity_for(candidate).submission_epoch == 1


@pytest.mark.parametrize(
    ("factory", "source"),
    [
        (lambda: (_ for _ in ()).throw(OSError("secret path")), "board-broker:open-failed"),
        (
            lambda: SimpleNamespace(
                submission_ledger=lambda _effect: (_ for _ in ()).throw(OSError("secret row")),
                close=lambda: None,
            ),
            "board-broker:read-failed",
        ),
        (
            lambda: SimpleNamespace(
                submission_ledger=lambda _effect: BoardBrokerResult(
                    BoardOperation.SUBMISSION_LEDGER,
                    BoardOutcome.TIMEOUT,
                    None,
                    "request",
                ),
                close=lambda: (_ for _ in ()).throw(OSError("secret close")),
            ),
            "board-broker:close-failed",
        ),
    ],
)
def test_reconciliation_probe_sanitizes_transport_failures(factory, source):
    probe = broker_evidence_probe(
        lambda *_args, **_kwargs: factory(),
        Path("board.sock"),
        BINDING,
    )
    evidence = probe(SimpleNamespace(effect_id="effect"))
    assert evidence.source == source
    assert "secret" not in evidence.source


def test_close_failure_disqualifies_an_exact_authenticated_answer():
    complete = CompleteSubmissionIdentity("board-1", 7, "revision-1", "static:board-1", "b" * 64, 1)
    row = {
        "board_row_id": "row-1",
        "row_type": "submission",
        "submitted_at": "2026-09-13T00:00:00Z",
        "request_id": "request-1",
        "verdict": "correct",
        "candidate_id": "a" * 64,
        "supplied_value_digest": complete.candidate_digest,
        "effect_id": complete.effect_id,
        "submission_epoch": 1,
        "complete_identity": complete.document(),
    }
    result = BoardBrokerResult(
        BoardOperation.SUBMISSION_LEDGER,
        BoardOutcome.ANSWERED,
        SubmissionLedgerValue(row),
        BoardProvenance(
            endpoint="submission-ledger",
            classified_event_id="classified-1",
            binding_digest="binding-1",
            peer_identity_digest="peer-1",
        ),
        "request-1",
    )
    client = SimpleNamespace(
        submission_ledger=lambda _effect: result,
        close=lambda: (_ for _ in ()).throw(OSError("secret close")),
    )
    evidence = broker_evidence_probe(lambda *_args, **_kwargs: client, Path("board.sock"), BINDING)(
        SimpleNamespace(effect_id=complete.effect_id)
    )
    assert evidence == Evidence.unsettled("board-broker:close-failed")


def test_closed_ambiguity_advances_epoch_once_before_concurrent_successor(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire(crash_during=True)
    mono, wall = [0.0], [100.0]
    epochs = SubmissionEpochAuthority(recorder.event_store, Clock())
    epochs.ensure("board-1")

    def identity_for(candidate):
        return CompleteSubmissionIdentity(
            "board-1",
            candidate.challenge_id,
            candidate.submission_context.challenge_revision,
            candidate.submission_context.instance_provenance,
            candidate.candidate_digest,
            epochs.current("board-1"),
        )

    runtime = compose_submission_runtime(
        state=tmp_path / "state",
        recorder=recorder,
        run_id="run-1",
        boot_id="boot-1",
        board_broker_path=tmp_path / "board.sock",
        timestamp=Clock(),
        identity_for=identity_for,
        epoch_authority=epochs,
        board_identity="board-1",
        monotonic=lambda: mono[0],
        wall_time=lambda: wall[0],
        open_client=lambda *_args, **_kwargs: wire,
        reconcile_interval=100,
        post_interval_seconds=0,
    )
    successor = replace(ready(identity="d" * 64, candidate=b"zephyr{next}"), candidate_digest="e" * 64)
    other = replace(ready(identity="f" * 64, candidate=b"zephyr{other}"), candidate_digest="1" * 64)
    try:
        with pytest.raises(SystemExit):
            runtime.submission.dispatch(queued(), binding=BINDING)
        mono[0], wall[0] = 60.0, 160.0
        runtime.reconciler.cycle()
        failures = []

        def dispatch(candidate):
            try:
                runtime.submission.dispatch(queued(candidate), binding=BINDING)
            except Exception as error:
                failures.append(error)

        threads = [threading.Thread(target=dispatch, args=(candidate,)) for candidate in (successor, other)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert failures == []
        assert epochs.current("board-1") == 2
        assert [row[1]["submission_epoch"] for row in wire.complete_identities[-2:]] == [2, 2]
    finally:
        runtime.close()

    replayed = SubmissionEpochAuthority(EventStore(tmp_path / "state", run_id="run-1", redactor=Redactor({})), Clock())
    assert replayed.current("board-1") == 2


def test_serial_submission_sends_complete_identity_to_board_broker(tmp_path):
    complete = CompleteSubmissionIdentity(
        "https://board.example",
        7,
        "revision-from-intake",
        "static:https://board.example",
        "b" * 64,
        1,
    )
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire()
    authority = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
        identity_for=lambda _candidate: complete,
    )

    authority.dispatch(queued(), binding=BINDING)

    assert wire.complete_identities == [(ready().identity, complete.__dict__)]


def test_production_identity_predecessor_does_not_deadlock_after_success(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire()

    def identity_for(candidate):
        return CompleteSubmissionIdentity(
            "board-1",
            candidate.challenge_id,
            candidate.submission_context.challenge_revision,
            candidate.submission_context.instance_provenance,
            candidate.candidate_digest,
            1,
        )

    runtime = compose_submission_runtime(
        state=tmp_path / "state",
        recorder=recorder,
        run_id="run-1",
        boot_id="boot-1",
        board_broker_path=tmp_path / "board.sock",
        timestamp=Clock(),
        identity_for=identity_for,
        open_client=lambda _path, _binding, *, scope: wire,
        reconcile_interval=100,
        post_interval_seconds=0,
    )
    first = queued(ready("1" * 64, b"zephyr{one}"), order=1)
    second_candidate = replace(
        ready("2" * 64, b"zephyr{two}"),
        candidate_digest="d" * 64,
    )
    second = queued(second_candidate, order=2, predecessors=(first.candidate.identity,))
    results = []
    try:
        runtime.submission.dispatch(first, binding=BINDING)
        worker = threading.Thread(
            target=lambda: results.append(runtime.submission.dispatch(second, binding=BINDING)), daemon=True
        )
        worker.start()
        worker.join(timeout=0.2)

        assert not worker.is_alive()
        assert len(results) == 1
        assert wire.posts == 2
    finally:
        runtime.close()


def test_production_identity_predecessor_can_arrive_after_its_successor(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire()

    def identity_for(candidate):
        return CompleteSubmissionIdentity(
            "board-1",
            candidate.challenge_id,
            candidate.submission_context.challenge_revision,
            candidate.submission_context.instance_provenance,
            candidate.candidate_digest,
            1,
        )

    runtime = compose_submission_runtime(
        state=tmp_path / "state",
        recorder=recorder,
        run_id="run-1",
        boot_id="boot-1",
        board_broker_path=tmp_path / "board.sock",
        timestamp=Clock(),
        identity_for=identity_for,
        open_client=lambda _path, _binding, *, scope: wire,
        reconcile_interval=100,
        post_interval_seconds=0,
    )
    first = queued(ready("1" * 64, b"zephyr{one}"), order=1)
    second_candidate = replace(ready("2" * 64, b"zephyr{two}"), candidate_digest="d" * 64)
    second = queued(second_candidate, order=2, predecessors=(first.candidate.identity,))
    results = []
    try:
        successor = threading.Thread(
            target=lambda: results.append(runtime.submission.dispatch(second, binding=BINDING)), daemon=True
        )
        successor.start()
        while runtime.submission.pending_count() != 1:
            pass
        predecessor = threading.Thread(
            target=lambda: results.append(runtime.submission.dispatch(first, binding=BINDING)), daemon=True
        )
        predecessor.start()
        predecessor.join(timeout=0.5)
        successor.join(timeout=0.5)

        assert not predecessor.is_alive()
        assert not successor.is_alive()
        assert len(results) == 2
        assert wire.posts == 2
    finally:
        runtime.close()


def test_production_runtime_fences_ambiguous_post_then_releases_unrelated_candidate(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire(crash_during=True)
    mono, wall = [10.0], [100.0]
    opened_bindings = []

    def open_client(_path, binding, *, scope):
        opened_bindings.append((scope, binding))
        return wire

    def identity_for(candidate):
        return CompleteSubmissionIdentity(
            "board-1",
            candidate.challenge_id,
            candidate.submission_context.challenge_revision,
            candidate.submission_context.instance_provenance,
            candidate.candidate_digest,
            1,
        )

    runtime = compose_submission_runtime(
        state=tmp_path / "state",
        recorder=recorder,
        run_id="run-1",
        boot_id="boot-1",
        board_broker_path=tmp_path / "board.sock",
        timestamp=Clock(),
        identity_for=identity_for,
        monotonic=lambda: mono[0],
        wall_time=lambda: wall[0],
        open_client=open_client,
        reconcile_interval=100,
        post_interval_seconds=0,
    )
    first = queued()
    second_candidate = replace(
        ready(identity="d" * 64, candidate=b"zephyr{unrelated}"),
        candidate_digest="e" * 64,
    )
    try:
        with pytest.raises(SystemExit):
            runtime.submission.dispatch(first, binding=BINDING)
        with pytest.raises(EffectIndeterminate):
            runtime.submission.dispatch(queued(second_candidate), binding=BINDING)

        mono[0], wall[0] = 70.0, 160.0
        runtime.reconciler.cycle()
        assert opened_bindings[-1][1].generation_id == BINDING.generation_id
        probes = [row for row in runtime.fence._events() if row["event"] == "evidence-probe"]
        assert [row["scheduled_offset"] for row in probes] == [0.0, 15.0, 30.0, 60.0]
        assert all(row["source"] == "board-broker:unreachable" for row in probes)
        assert runtime.reconciler.is_alive
        result = runtime.submission.dispatch(queued(second_candidate), binding=BINDING)

        assert result is not None
        assert wire.posts == 2
        assert runtime.fence.can_submit(first.candidate.identity, identity_for(first.candidate).effect_id) is False
        assert runtime.fence.barrier_open is False
    finally:
        runtime.close()
    assert not runtime.reconciler.is_alive
    assert (recorder.run_dir / "canonical" / "ambiguous-submission.receipt.json").is_file()


def test_production_runtime_resumes_open_fence_with_reset_monotonic_epoch(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire(crash_during=True)
    mono, wall = [10.0], [100.0]

    def identity_for(candidate):
        return CompleteSubmissionIdentity(
            "board-1",
            candidate.challenge_id,
            candidate.submission_context.challenge_revision,
            candidate.submission_context.instance_provenance,
            candidate.candidate_digest,
            1,
        )

    def boot(boot_id):
        return compose_submission_runtime(
            state=tmp_path / "state",
            recorder=recorder,
            run_id="run-1",
            boot_id=boot_id,
            board_broker_path=tmp_path / "board.sock",
            timestamp=Clock(),
            identity_for=identity_for,
            monotonic=lambda: mono[0],
            wall_time=lambda: wall[0],
            open_client=lambda _path, _binding, *, scope: wire,
            reconcile_interval=100,
            post_interval_seconds=0,
        )

    first_boot = boot("boot-1")
    try:
        with pytest.raises(SystemExit):
            first_boot.submission.dispatch(queued(), binding=BINDING)
    finally:
        first_boot.close()

    mono[0], wall[0] = 0.0, 115.0
    second_boot = boot("boot-2")
    try:
        second_boot.reconciler.cycle()
        assert second_boot.fence.barrier_open
        mono[0], wall[0] = 45.0, 160.0
        second_boot.reconciler.cycle()
        assert not second_boot.fence.barrier_open
        assert len(second_boot.fence.post_trace(ready().identity)) == 1
    finally:
        second_boot.close()


def test_first_ready_candidate_dispatches_in_the_admission_call_with_one_post(tmp_path):
    authority, recorder, wire = service(tmp_path)
    assert authority.pending_count() == 0

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
    bridge = CandidateSubmissionBridge(
        admission, submission, lambda _challenge_id: SubmissionContext.static("r1", "b1")
    )
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
    opened = []
    submission = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, binding: opened.append(binding) or wire,
    )
    bridge = ObservedCandidateSubmissionBridge(
        admission,
        submission,
        run_id="run-1",
        boot_id="boot-1",
        context_for=lambda _challenge_id: SubmissionContext.static("r1", "b1"),
        lane_for_attempt=lambda _attempt_id: "lane-2",
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
    assert opened[0].lane_id == "lane-2"


def test_serial_submission_paces_posts_durably_across_restart(tmp_path):
    class PacingClock:
        def __init__(self):
            self.at = __import__("datetime").datetime(2026, 9, 13, tzinfo=__import__("datetime").timezone.utc)
            self.sleeps = []

        def timestamp(self):
            self.at += __import__("datetime").timedelta(milliseconds=1)
            return self.at.isoformat()

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.at += __import__("datetime").timedelta(seconds=seconds)

    pacing = PacingClock()
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire()

    def serial():
        return SerialSubmission(
            recorder.run_dir / "canonical",
            recorder.write_authority,
            pacing.timestamp,
            tmp_path / "board.sock",
            open_client=lambda _path, _binding: wire,
            sleep=pacing.sleep,
            post_interval_seconds=12,
        )

    serial().dispatch(queued(ready("1" * 64, b"zephyr{one}")), binding=BINDING)
    serial().dispatch(queued(ready("2" * 64, b"zephyr{two}")), binding=BINDING)

    posts = sorted(
        __import__("datetime").datetime.fromisoformat(row.observation["requested_at"])
        for row in recorder.write_authority.reservations()
        if row.identity.operation == "board.submit-candidate"
    )
    assert (posts[1] - posts[0]).total_seconds() >= 12
    assert pacing.sleeps and pacing.sleeps[0] > 0


def test_final_submission_defers_before_pacing_can_cross_request_deadline(tmp_path):
    class DeadlineClock:
        def __init__(self):
            self.at = __import__("datetime").datetime(2026, 9, 13, tzinfo=__import__("datetime").timezone.utc)
            self.sleeps = []

        def timestamp(self):
            self.at += __import__("datetime").timedelta(milliseconds=1)
            return self.at.isoformat()

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.at += __import__("datetime").timedelta(seconds=seconds)

    clock = DeadlineClock()
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire()
    serial = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        clock.timestamp,
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
        sleep=clock.sleep,
    )
    serial.dispatch(queued(ready("1" * 64, b"zephyr{one}")), binding=BINDING)
    deadline = clock.at + __import__("datetime").timedelta(seconds=35)

    with pytest.raises(TimeoutError, match="pacing"):
        serial.dispatch(queued(ready("2" * 64, b"zephyr{two}")), binding=BINDING, deadline=deadline)

    assert wire.posts == 1
    assert clock.sleeps == []
    deferred = recorder.write_authority.current("serial-submit:" + "2" * 64)
    assert deferred is not None and deferred.state.value == "aborted"


def test_observed_bridge_cache_is_generation_scoped(tmp_path):
    admission, generation_id, evidence = admission_service(tmp_path)
    source = next(event for event in admission.store.events() if event.event_digest in evidence.observation_digests)
    calls = []
    submission = SimpleNamespace(dispatch=lambda ready, *, binding: calls.append((ready, binding)))
    bridge = ObservedCandidateSubmissionBridge(
        admission,
        submission,
        run_id="run-1",
        boot_id="boot-1",
        context_for=lambda _challenge_id: SubmissionContext.static("r1", "b1"),
    )
    observation_ref = "observations/1.bin"
    (admission.store.run_dir / observation_ref).parent.mkdir(exist_ok=True)
    (admission.store.run_dir / observation_ref).write_bytes(source.body)
    candidate = Candidate(ADMITTED_FLAG.decode(), OBSERVED, ref=observation_ref)

    bridge.prepare(candidate, attempt_id="attempt-1", challenge_id=42, generation_id=generation_id)
    assert bridge._prepared[(42, candidate.text, generation_id)].candidate.generation_id == generation_id
    assert (42, candidate.text, "generation-new") not in bridge._prepared


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
    "crash_point,replayable,expected_posts",
    [("after_reserve", True, 1), ("during_post", False, 1), ("after_effect", False, 1)],
)
def test_crash_before_wire_retries_but_started_post_never_replays(tmp_path, crash_point, replayable, expected_posts):
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
    if replayable:
        assert replay.dispatch(queued(), binding=BINDING).verdict.outcome == CORRECT
    else:
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
    assert [row["states"][-1] for row in trace["submissions"]] == [
        "committed",
        "committed",
        "committed",
        "possibly-sent",
        "aborted",
    ]


def _serial_receipt(tmp_path, states, ordinals):
    candidate_id = "a" * 64
    identity = EffectIdentity("board.submit-candidate", "7", candidate_id)
    row = {
        "reservation_id": "submission:7",
        "candidate_id": candidate_id,
        "effect_id": identity.fingerprint,
        "operation": identity.operation,
        "challenge_id": "7",
        "states": states,
        "ordinals": ordinals,
        "result": (
            {
                "ready_at": "2026-09-13T00:00:00Z",
                "reserved_at": "2026-09-13T00:00:01Z",
                "requested_at": "2026-09-13T00:00:02Z",
                "result_at": "2026-09-13T00:00:03Z",
            }
            if states[-1] == "committed"
            else {}
        ),
    }
    document = {
        "schema_version": 2,
        "receipt_type": "serial-submission",
        "run_id": "run-1",
        "manifest_link": {"row_id": "core.submission-tail", "receipt_ref": "receipt:serial-submission"},
        "evidence_class": "controlled-runtime-trace",
        "dispatch_order": [candidate_id] if "started" in states else [],
        "in_flight_maximum": 1 if "started" in states else 0,
        "submissions": [row],
    }
    path = tmp_path / "serial-submission.receipt.json"
    path.write_bytes(canonical_bytes(document) + b"\n")
    return path


def test_receipt_accepts_repeated_before_wire_retries_before_one_dispatch(tmp_path):
    path = _serial_receipt(
        tmp_path,
        ["reserved", "aborted", "reserved", "aborted", "reserved", "started", "committed"],
        [1, 2, 4, 5, 7, 8, 9],
    )

    assert verify_receipt(path) == path


def test_receipt_accepts_repeated_before_wire_deferrals_without_dispatch(tmp_path):
    path = _serial_receipt(
        tmp_path,
        ["reserved", "aborted", "reserved", "aborted"],
        [1, 2, 4, 5],
    )

    assert verify_receipt(path) == path


@pytest.mark.parametrize(
    "states, ordinals",
    (
        (["reserved", "started", "committed", "reserved", "aborted"], [1, 2, 3, 4, 5]),
        (["reserved", "aborted", "reserved", "started", "possibly-sent", "reserved"], [1, 2, 4, 5, 6, 7]),
        (["reserved", "aborted", "reserved", "aborted"], [1, 1, 2, 3]),
        (["reserved", "aborted"], [0, 1]),
        (["reserved", "started", []], [1, 2, 3]),
    ),
)
def test_receipt_rejects_post_wire_reservation_and_illegal_ordinals(tmp_path, states, ordinals):
    path = _serial_receipt(tmp_path, states, ordinals)

    with pytest.raises(InvalidReceiptError):
        verify_receipt(path)


def test_ambiguous_post_blocks_serial_posts_then_releases_other_work_at_sixty(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire, mono, wall = Wire(crash_during=True), [10.0], [100.0]
    fence = AmbiguousSubmissionFence(
        tmp_path / "state",
        recorder.write_authority,
        run_id="run-1",
        boot_id="boot-1",
        monotonic=lambda: mono[0],
        wall_time=lambda: wall[0],
        probe=lambda _: Evidence.unsettled("authenticated-submission-ledger"),
    )

    def identity(candidate):
        return CompleteSubmissionIdentity(
            "board-identity",
            candidate.challenge_id,
            "revision-1",
            "instance-1",
            candidate.candidate_digest,
            1,
        )

    serial = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
    )
    submission = AmbiguityAwareSerialSubmission(serial, fence, identity)
    with pytest.raises(SystemExit):
        submission.dispatch(queued(), binding=BINDING)
    budget = recorder.write_authority.trace(f"ambiguity-path:{identity(ready()).effect_id}")
    post = recorder.write_authority.trace(identity(ready()).reservation_id)
    assert budget[0]["ordinal"] < post[0]["ordinal"]
    assert budget[0]["need"] == {"bytes": 12800, "objects": 1, "operations": 24}
    other = replace(ready("2" * 64), candidate_digest="d" * 64)
    with pytest.raises(EffectIndeterminate, match="barrier"):
        submission.dispatch(queued(other), binding=BINDING)
    assert wire.posts == 1
    mono[0], wall[0] = 70.0, 160.0
    assert fence.reconcile(fence.pending()[0]).disposition == "unknown-and-spent"
    submission.dispatch(queued(other), binding=BINDING)
    assert wire.posts == 2


def test_complete_ambiguity_reservation_failure_prevents_board_post(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    recorder.write_authority.reserve(
        "controlled-exhaustion",
        EffectIdentity("test.exhaust", "shared"),
        Capacity(500 * 1024, 1, 3),
    )
    wire = Wire()
    fence = AmbiguousSubmissionFence(
        tmp_path / "state",
        recorder.write_authority,
        run_id="run-1",
        boot_id="boot-1",
        monotonic=lambda: 1.0,
        probe=lambda _: Evidence.unsettled("ledger"),
    )
    serial = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
    )
    wrapped = AmbiguityAwareSerialSubmission(
        serial,
        fence,
        lambda candidate: CompleteSubmissionIdentity(
            "board", candidate.challenge_id, "revision", "instance", candidate.candidate_digest, 1
        ),
    )

    with pytest.raises(ReservationUnavailable):
        wrapped.dispatch(queued(), binding=BINDING)

    assert wire.posts == 0


def test_last_call_candidate_reserves_serial_and_ambiguity_paths_before_dispatch(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    wire = Wire()
    fence = AmbiguousSubmissionFence(
        tmp_path / "state",
        recorder.write_authority,
        run_id="run-1",
        boot_id="boot-1",
        monotonic=lambda: 1.0,
        probe=lambda _: Evidence.unsettled("ledger"),
    )
    serial = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=lambda _path, _binding: wire,
    )
    wrapped = AmbiguityAwareSerialSubmission(
        serial,
        fence,
        lambda candidate: CompleteSubmissionIdentity(
            "board", candidate.challenge_id, "revision", "instance", candidate.candidate_digest, 1
        ),
    )
    admission = queued()

    identity = wrapped.prepare(admission)

    assert recorder.write_authority.current(identity.reservation_id) is not None
    assert recorder.write_authority.current(f"ambiguity-path:{identity.effect_id}") is not None
    assert wire.posts == 0


def test_pre_wire_paths_are_reacquired_after_restart_without_resend(tmp_path):
    state = tmp_path / "state"
    wire = Wire()

    def composition(recorder, boot_id):
        fence = AmbiguousSubmissionFence(
            state,
            recorder.write_authority,
            run_id="run-1",
            boot_id=boot_id,
            monotonic=lambda: 1.0,
            probe=lambda _: Evidence.unsettled("ledger"),
        )
        serial = SerialSubmission(
            recorder.run_dir / "canonical",
            recorder.write_authority,
            Clock(),
            tmp_path / "board.sock",
            open_client=lambda _path, _binding: wire,
        )
        return AmbiguityAwareSerialSubmission(
            serial,
            fence,
            lambda candidate: CompleteSubmissionIdentity(
                "board", candidate.challenge_id, "revision", "instance", candidate.candidate_digest, 1
            ),
        )

    first = Recorder(state, "run-1", Redactor({}))
    composition(first, "boot-1").prepare(queued())
    first.write_authority.close()
    restarted = Recorder(state, "run-1", Redactor({}))

    result = composition(restarted, "boot-2").dispatch(queued(), binding=replace(BINDING, boot_id="boot-2"))
    receipt = write_receipt(restarted.run_dir / "canonical", "run-1", restarted.write_authority)

    verify_receipt(receipt, restarted.write_authority)
    assert result.verdict.outcome == CORRECT
    assert wire.posts == 1


def test_pre_wire_deferred_predecessor_does_not_deadlock_final_successor(tmp_path):
    recorder = Recorder(tmp_path / "state", "run-1", Redactor({}))
    first_candidate = replace(ready("1" * 64, b"zephyr{one}"), candidate_digest="a" * 64)
    second_candidate = replace(ready("2" * 64, b"zephyr{two}"), candidate_digest="d" * 64)
    first = queued(first_candidate, order=1)
    second = queued(second_candidate, order=2, predecessors=(first_candidate.identity,))
    admissions = SimpleNamespace(ready_admissions=lambda: (first, second))
    wire = Wire()
    opens = 0

    def open_client(_path, _binding):
        nonlocal opens
        opens += 1
        if opens == 1:
            raise OSError("pre-wire refusal")
        return wire

    fence = AmbiguousSubmissionFence(
        tmp_path / "state",
        recorder.write_authority,
        run_id="run-1",
        boot_id="boot-1",
        monotonic=lambda: 1.0,
        probe=lambda _: Evidence.unsettled("ledger"),
    )
    serial = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        Clock(),
        tmp_path / "board.sock",
        open_client=open_client,
    )
    wrapped = AmbiguityAwareSerialSubmission(
        serial,
        fence,
        lambda candidate: CompleteSubmissionIdentity(
            "board", candidate.challenge_id, "revision", "instance", candidate.candidate_digest, 1
        ),
    )
    final = FinalCandidateQueue(admissions, wrapped, run_id="run-1", boot_id="boot-1")
    final.prepare_all()

    with pytest.raises(SubmissionDeferred):
        final.submit(first_candidate.identity)
    results, errors = [], []
    successor = threading.Thread(
        target=lambda: _capture(lambda: final.submit(second_candidate.identity), results, errors),
        daemon=True,
    )
    successor.start()
    successor.join(timeout=0.5)

    assert not successor.is_alive()
    assert errors == []
    assert results == ["accepted"]
    assert wire.posts == 1


def _capture(call, results, errors):
    try:
        results.append(call())
    except BaseException as error:
        errors.append(error)
