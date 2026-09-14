"""The canonical Observation event crosses the v1 compatibility boundary once.

The event store owns the durable event and sealed bytes.  The existing Recorder/stream remains a
read-compatible projection while the migration is deliberately limited to one event family.
"""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from solver.event_store import (
    DuplicateSequenceError,
    EventStoreDamage,
    EventDigestMismatchError,
    EventStore,
    InvalidEventError,
    MissingBlobError,
    ObservationRecorded,
    PreviousDigestMismatchError,
    RunNotTerminalError,
    RunSealedError,
    ReservationStatus,
    TornAppendError,
    UnknownSchemaError,
)
from solver.event_store_contracts import LifecycleRecorded, RunClosed, RunOpened, TerminalDisposition
from solver.record import Recorder, Usage
from solver.redaction import Redactor
from solver.observation import digest_of


SECRET = "team-secret-256"


def test_observation_step_is_sealed_canonical_and_readable_through_v1(tmp_path):
    recorder = Recorder(tmp_path, run_id="run-1", redactor=Redactor({"TEAM_KEY": SECRET}))

    observation = recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw=f"cat {SECRET}",
        command_normalised="cat <file>",
        tool="bash",
    ).end(
        exit_code=0,
        output=f"flag candidate: {SECRET}".encode(),
        usage=Usage(model="gpt-5"),
    )

    events = recorder.event_store.events()
    event = events[0]
    sealed = recorder.event_store.blob(event.blob_digest)
    v1_end = json.loads(recorder.stream_path.read_text().splitlines()[-1])

    assert event.event_type == "observation.recorded"
    assert event.sequence == 1
    assert event.previous_digest == ""
    assert event.blob_digest == digest_of(b"flag candidate: [redacted:TEAM_KEY]")
    assert sealed == b"flag candidate: [redacted:TEAM_KEY]"
    assert event.envelope["payload"]["blob_digest"] == event.blob_digest
    assert "body" not in event.envelope
    assert "path" not in event.envelope
    assert SECRET not in json.dumps(event.envelope)

    assert observation.digest == event.blob_digest
    assert observation.nbytes == len(sealed)
    assert (recorder.run_dir / observation.ref).read_bytes() == sealed
    assert v1_end["record"] == "step-end"
    assert v1_end["observation_digest"] == event.blob_digest
    assert SECRET not in recorder.stream_path.read_text()


def test_concurrent_producers_share_one_monotonic_digest_chain(tmp_path):
    root = tmp_path / "run"

    def append(index):
        store = EventStore(root)
        return store.append(
            ObservationRecorded(
                attempt_id=f"attempt-{index}",
                step_index=1,
                command_raw="printf output",
                command_normalised="printf output",
                tool="bash",
            ),
            body=f"output-{index}".encode(),
        )

    with ThreadPoolExecutor(max_workers=8) as producers:
        committed = list(producers.map(append, range(32)))

    events = EventStore(root).events()

    assert len(events) == 32
    assert [event.sequence for event in events] == list(range(1, 33))
    assert len({event.event_digest for event in events}) == 32
    assert all(
        event.previous_digest == (events[index - 1].event_digest if index else "") for index, event in enumerate(events)
    )

    assert {event.event_digest for event in committed} == {event.event_digest for event in events}


def test_writer_batch_serializes_one_phase_append_across_reserve_and_commit(tmp_path):
    store = EventStore(tmp_path)
    first = ObservationRecorded(
        attempt_id="attempt-batch-1",
        step_index=1,
        command_raw="printf first",
        command_normalised="printf first",
        tool="bash",
    )
    second = ObservationRecorded(
        attempt_id="attempt-batch-2",
        step_index=1,
        command_raw="printf second",
        command_normalised="printf second",
        tool="bash",
    )
    append_started = threading.Event()
    append_finished = threading.Event()
    errors = []

    def append_second() -> None:
        append_started.set()
        try:
            store.append(second, body=b"second")
        except BaseException as error:  # pragma: no cover - asserted below
            errors.append(error)
        finally:
            append_finished.set()

    with store.writer_batch() as writer:
        reservation = writer.reserve(first, blob_digest=digest_of(b"first"), blob_bytes=5, sequence=1)
        thread = threading.Thread(target=append_second)
        thread.start()
        assert append_started.wait(1)
        assert not append_finished.wait(0.05)
        writer.commit(reservation, first, body=b"first")

    assert append_finished.wait(1)
    thread.join()
    assert errors == []
    assert [event.sequence for event in store.events()] == [1, 2]


def test_reservation_is_durable_before_sealing_body(tmp_path):
    phases = []
    holder = {}

    def observe(point):
        if point == "after_reserve":
            store = holder["store"]
            phases.append(
                (
                    store.reservations_path.read_text(),
                    (store.sealed_dir / digest_of(b"output")).exists(),
                )
            )

    store = EventStore(tmp_path / "run", append_hook=observe)
    holder["store"] = store
    committed = store.append(_event(), body=b"output")

    assert committed.sequence == 1
    assert len(phases) == 1
    reservation, sealed = phases[0]
    assert '"sequence":1' in reservation
    assert sealed is False


def test_a_reservation_commits_only_its_reserved_body_and_sequence(tmp_path):
    store = EventStore(tmp_path / "run")
    event = _event()
    body = b"output"
    reservation = store.reserve(event, blob_digest=digest_of(body), blob_bytes=len(body))

    committed = store.commit(reservation, event, body=body)

    assert committed.sequence == reservation.sequence == 1
    assert committed.blob_digest == reservation.blob_digest == digest_of(body)
    assert store.events()[0].event_digest == committed.event_digest


def test_concurrent_producers_receive_reservations_before_commit(tmp_path):
    root = tmp_path / "run"

    def reserve(index):
        store = EventStore(root)
        return store.reserve(
            ObservationRecorded(
                attempt_id=f"attempt-{index}",
                step_index=1,
                command_raw="printf output",
                command_normalised="printf output",
                tool="bash",
            ),
            blob_digest=digest_of(f"output-{index}".encode()),
            blob_bytes=len(f"output-{index}"),
        )

    with ThreadPoolExecutor(max_workers=8) as producers:
        reservations = list(producers.map(reserve, range(32)))

    assert sorted(reservation.sequence for reservation in reservations) == list(range(1, 33))
    assert not EventStore(root).events()


def test_outstanding_reservations_commit_in_order_into_one_chain(tmp_path):
    store = EventStore(tmp_path / "run")
    entries = [(_event(step_index=index), f"output-{index}".encode()) for index in range(1, 33)]

    def reserve(entry):
        event, body = entry
        return store.reserve(event, blob_digest=digest_of(body), blob_bytes=len(body))

    with ThreadPoolExecutor(max_workers=8) as producers:
        reservations = list(producers.map(reserve, entries))
    ordered = sorted(zip(reservations, entries), key=lambda item: item[0].sequence)

    committed = [store.commit(reservation, event, body=body) for reservation, (event, body) in ordered]

    events = store.events()
    assert [event.sequence for event in events] == list(range(1, 33))
    assert [event.event_digest for event in committed] == [event.event_digest for event in events]
    assert all(
        event.previous_digest == (events[index - 1].event_digest if index else "") for index, event in enumerate(events)
    )


def test_reservation_status_is_typed_and_unknown_status_is_rejected(tmp_path):
    store = EventStore(tmp_path / "run")
    event = _event()
    reservation = store.reserve(event, blob_digest=digest_of(b"output"), blob_bytes=len(b"output"))

    assert reservation.status is ReservationStatus.RESERVED
    reservation_row = json.loads(store.reservations_path.read_text().splitlines()[0])
    reservation_row["status"] = "unknown"
    store.reservations_path.write_text(json.dumps(reservation_row) + "\n")

    with pytest.raises(InvalidEventError):
        store.events()


def test_crash_before_append_leaves_no_canonical_event(tmp_path):
    event = ObservationRecorded(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="echo output",
        command_normalised="echo output",
        tool="bash",
    )

    def crash(point):
        if point == "before_append":
            raise RuntimeError("injected crash")

    store = EventStore(tmp_path / "run", append_hook=crash)

    try:
        store.append(event, body=b"output")
    except RuntimeError as error:
        assert str(error) == "injected crash"
    else:
        raise AssertionError("the injected crash must interrupt append")

    assert EventStore(tmp_path / "run").events() == []


def test_crash_after_append_is_one_verifiable_idempotent_event(tmp_path):
    event = ObservationRecorded(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="echo output",
        command_normalised="echo output",
        tool="bash",
    )

    def crash(point):
        if point == "after_append":
            raise RuntimeError("injected crash")

    store = EventStore(tmp_path / "run", append_hook=crash)
    try:
        store.append(event, body=b"output")
    except RuntimeError as error:
        assert str(error) == "injected crash"
    else:
        raise AssertionError("the injected crash must interrupt append")

    restarted = EventStore(tmp_path / "run")
    committed = restarted.append(event, body=b"output")

    assert len(restarted.events()) == 1
    assert committed.sequence == 1
    assert restarted.events()[0].event_digest == committed.event_digest


def test_restart_reconciles_canonical_commit_into_one_matching_v1_projection(tmp_path):
    def crash(point):
        if point == "after_append":
            raise RuntimeError("injected crash")

    recorder = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}), event_store_hook=crash)
    step = recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="echo output",
        command_normalised="echo output",
        tool="bash",
    )

    with pytest.raises(RuntimeError, match="injected crash"):
        step.end(exit_code=0, output=b"output", usage=Usage(model="gpt-5"))
    recorder.write_authority.close()
    restarted = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}))
    event = restarted.event_store.events()[0]
    rows = [json.loads(line) for line in restarted.stream_path.read_text().splitlines()]
    ends = [row for row in rows if row["record"] == "step-end"]

    assert len(ends) == 1
    row = ends[0]
    assert row["attempt_id"] == event.payload["attempt_id"]
    assert row["step_index"] == event.payload["step_index"]
    assert row["command_raw"] == event.payload["command_raw"]
    assert row["command_normalised"] == event.payload["command_normalised"]
    assert row["tool"] == event.payload["tool"]
    assert row["exit_code"] == event.payload["exit_code"]
    assert row["duration_ms"] == event.payload["duration_ms"]
    assert row["observation_digest"] == event.blob_digest
    assert row["observation_bytes"] == event.blob_bytes
    assert (restarted.run_dir / row["observation_ref"]).read_bytes() == event.body

    # Reopening and repeating the same producer completion is idempotent at both boundaries.
    step.end(exit_code=0, output=b"output", usage=Usage(model="gpt-5"))
    assert len(restarted.event_store.events()) == 1
    assert len([one for one in rows if one["record"] == "step-end"]) == 1

    receipt_path = restarted.event_store.write_receipt()
    assert json.loads(receipt_path.read_text())["crash_point"] == "after_append"
    assert restarted.event_store.verify_receipt(receipt_path).compatibility_projection is True


def test_receipt_is_derived_and_independently_verified(tmp_path):
    recorder = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}))
    recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="echo output",
        command_normalised="echo output",
        tool="bash",
    ).end(exit_code=0, output=b"output", usage=Usage(model="gpt-5"))

    receipt_path = recorder.event_store.write_receipt()
    receipt = json.loads(receipt_path.read_text())
    verified = recorder.event_store.verify_receipt(receipt_path)

    assert receipt["compatibility_projection"] is True
    assert receipt["crash_point"] == "none"
    assert receipt["event_chain_head"] == recorder.event_store.events()[-1].event_digest
    assert verified.event_chain_head == receipt["event_chain_head"]
    assert verified.sealed_blob_digest == receipt["sealed_blob_digest"]
    assert verified.reservation_status is ReservationStatus.COMMITTED

    receipt["sealed_blob_digest"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(EventStoreDamage):
        recorder.event_store.verify_receipt(receipt_path)


def _event(attempt_id="attempt-1", step_index=1):
    return ObservationRecorded(
        attempt_id=attempt_id,
        step_index=step_index,
        command_raw="echo output",
        command_normalised="echo output",
        tool="bash",
    )


def _write_envelopes(store, envelopes):
    store.events_path.write_text("\n".join(json.dumps(envelope) for envelope in envelopes) + "\n")


def test_canonical_damage_is_typed_and_stable(tmp_path):
    cases = []

    digest_store = EventStore(tmp_path / "digest")
    digest_store.append(_event(), body=b"one")
    digest_envelope = json.loads(digest_store.events_path.read_text().splitlines()[0])
    digest_envelope["event_digest"] = "f" * 64
    _write_envelopes(digest_store, [digest_envelope])
    cases.append((digest_store, EventDigestMismatchError))

    predecessor_store = EventStore(tmp_path / "predecessor")
    predecessor_store.append(_event(), body=b"one")
    predecessor_store.append(_event(step_index=2), body=b"two")
    predecessor_envelopes = [json.loads(line) for line in predecessor_store.events_path.read_text().splitlines()]
    predecessor_envelopes[1]["prev_digest"] = "0" * 64
    _write_envelopes(predecessor_store, predecessor_envelopes)
    cases.append((predecessor_store, PreviousDigestMismatchError))

    missing_store = EventStore(tmp_path / "missing")
    missing = missing_store.append(_event(), body=b"one")
    (missing_store.sealed_dir / missing.blob_digest).unlink()
    cases.append((missing_store, MissingBlobError))

    schema_store = EventStore(tmp_path / "schema")
    schema_store.append(_event(), body=b"one")
    schema_envelope = json.loads(schema_store.events_path.read_text().splitlines()[0])
    schema_envelope["schema_version"] = 999
    _write_envelopes(schema_store, [schema_envelope])
    cases.append((schema_store, UnknownSchemaError))

    duplicate_store = EventStore(tmp_path / "duplicate")
    duplicate_store.append(_event(), body=b"one")
    duplicate_store.append(_event(step_index=2), body=b"two")
    duplicate_envelopes = [json.loads(line) for line in duplicate_store.events_path.read_text().splitlines()]
    duplicate_envelopes[1]["seq"] = 1
    _write_envelopes(duplicate_store, duplicate_envelopes)
    cases.append((duplicate_store, DuplicateSequenceError))

    for store, error_type in cases:
        with pytest.raises(error_type) as failure:
            store.events()
        assert failure.value.classification == failure.value.kind.value

    torn_store = EventStore(tmp_path / "torn")
    torn_store.append(_event(), body=b"one")
    with torn_store.events_path.open("ab") as stream:
        stream.write(b'{"seq": 2, "event_type": "observation.recorded"')
    with pytest.raises(TornAppendError):
        torn_store.events()


def test_canonical_event_store_receipt_is_versioned_sanitized_and_checkable(tmp_path):
    recorder = Recorder(tmp_path, run_id="run", redactor=Redactor({"TEAM_KEY": SECRET}))
    recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw=f"cat {SECRET}",
        command_normalised="cat <file>",
        tool="bash",
    ).end(exit_code=0, output=SECRET.encode(), usage=Usage(model="gpt-5"))

    receipt_path = recorder.event_store.write_receipt()
    receipt = json.loads(receipt_path.read_text())

    assert receipt["schema_version"] == 1
    assert receipt["receipt_type"] == "canonical-event-store"
    assert receipt["run_id"] == "run"
    assert receipt["crash_point"] == "none"
    assert receipt["compatibility_projection"] is True
    assert SECRET not in receipt_path.read_text()
    assert str(tmp_path) not in receipt_path.read_text()
    assert recorder.event_store.verify_receipt(receipt_path).event_chain_head == receipt["event_chain_head"]


def test_terminal_snapshot_preserves_exact_retries_and_fences_novel_writes(tmp_path):
    store = EventStore(tmp_path / "terminal", run_id="terminal")
    store.append(LifecycleRecorded("run:open", RunOpened()), body=b"")
    closing = LifecycleRecorded("run:close", RunClosed(TerminalDisposition.NORMAL))
    reserved = store.reserve(
        closing,
        blob_digest=hashlib.sha256(b"").hexdigest(),
        blob_bytes=0,
    )
    closed = store.commit(reserved, closing, body=b"")

    snapshot = store.terminal_snapshot()

    assert store.append(closing, body=b"") == closed
    retried = store.reserve(closing, blob_digest=hashlib.sha256(b"").hexdigest(), blob_bytes=0)
    assert (retried.sequence, retried.status) == (reserved.sequence, ReservationStatus.COMMITTED)
    assert store.commit(reserved, closing, body=b"") == closed

    altered_close = LifecycleRecorded("run:close", RunClosed(TerminalDisposition.CRASHED, "different"))
    with pytest.raises(RunSealedError):
        store.append(altered_close, body=b"")
    with pytest.raises(RunSealedError):
        store.reserve(altered_close, blob_digest=hashlib.sha256(b"").hexdigest(), blob_bytes=0)
    with pytest.raises(RunSealedError):
        store.commit(reserved, altered_close, body=b"")

    def append(index):
        with pytest.raises(RunSealedError):
            store.append(_event(step_index=index + 1), body=f"late-{index}".encode())

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(append, range(16)))

    assert snapshot.chain_head == closed.event_digest
    assert len(snapshot.events) == len(store.events()) == 2

    late = _event(step_index=3)
    with pytest.raises(RunSealedError):
        store.reserve(late, blob_digest=hashlib.sha256(b"late").hexdigest(), blob_bytes=4)
    with pytest.raises(RunSealedError):
        store.commit(reserved, late, body=b"late")


def test_terminal_snapshot_uses_a_precise_unclosed_run_error(tmp_path):
    empty = EventStore(tmp_path / "empty", run_id="empty")
    with pytest.raises(RunNotTerminalError, match="no Run genesis"):
        empty.terminal_snapshot()

    open_run = EventStore(tmp_path / "open", run_id="open")
    open_run.append(LifecycleRecorded("run:open", RunOpened()), body=b"")
    with pytest.raises(RunNotTerminalError, match="complete closed Run"):
        open_run.terminal_snapshot()
