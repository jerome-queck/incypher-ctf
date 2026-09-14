"""Canonical publication for complete Order decisions."""

import base64
import datetime as dt
import json
import threading
from dataclasses import replace

import pytest

from solver.event_store import EventStore
from solver.intake_qualification import ChallengeSnapshot, IntakeSnapshot, PresentValue, PriorFence, TypedId, ValueFact
from solver.order_contracts import ORDER_PUBLICATION_RECORDED
from solver.order_journal import OrderJournal, replay_order_publication
from solver.order_policy import CrowdSource, OrderAuthority, OrderFence, OrderInput, OrderRunFacts, decide_order
from solver.redaction import Redactor


UTC = dt.timezone.utc
NOW = dt.datetime(2026, 9, 22, 1, 0, tzinfo=UTC)


def _challenge(index: int) -> ChallengeSnapshot:
    return ChallengeSnapshot(
        TypedId.parse(index),
        f"challenge-{index}",
        "web",
        "standard",
        "",
        ValueFact(100, "list", "answered", NOW),
        ValueFact(index % 7, "list", "answered", NOW),
        ValueFact(False, "list", "answered", NOW),
        PresentValue("value", index),
        0,
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        (),
        f"{index:064x}",
    )


def _decision(size: int = 520, *, boundary_id: str = "boundary-1", prior: OrderFence = OrderFence()):
    return decide_order(_request(size, boundary_id=boundary_id, prior=prior))


def _request(size: int = 520, *, boundary_id: str = "boundary-1", prior: OrderFence = OrderFence()):
    snapshot = IntakeSnapshot(
        "snapshot-1", "a" * 64, NOW, tuple(_challenge(i) for i in range(1, size + 1)), "not-empty"
    )
    fence = PriorFence("a" * 64, "intake-decision:1:settled", "b" * 64, snapshot.digest)
    authority = OrderAuthority(snapshot, fence, (snapshot,), 100_000, CrowdSource(True, False, "c" * 64))
    run = OrderRunFacts(boundary_id, NOW, NOW + dt.timedelta(hours=2), 7200, 1, prior_order_fence=prior)
    return OrderInput(authority, run)


def _journal(tmp_path, hook=lambda _point: None):
    return OrderJournal(tmp_path, "run-1", Redactor({}), timestamp=lambda: NOW.isoformat(), publication_hook=hook)


def test_complete_decision_reserves_boundary_and_every_chunk_before_first_commit(tmp_path):
    observed = []
    holder = {}

    def hook(point):
        if point == "before_first_commit":
            observed.append((len(holder["journal"].store.reservations()), len(holder["journal"].store.events())))

    journal = _journal(tmp_path, hook)
    holder["journal"] = journal
    published = journal.publish(_decision())

    assert observed == [(4, 0)]  # one boundary plus three chunks
    assert published.chunk_count == 3
    assert [event.payload["record"] for event in EventStore(tmp_path, run_id="run-1").events()] == [
        "chunk",
        "chunk",
        "chunk",
        "boundary",
    ]


def test_order_chunks_are_bounded_and_replay_closes_the_exact_decision(tmp_path):
    decision = _decision()

    published = _journal(tmp_path).publish(decision)
    replayed = replay_order_publication(tmp_path, "run-1")

    assert replayed.decision_digest == decision.digest == published.decision_digest
    assert replayed.publication_id == published.publication_id
    assert replayed.rows == tuple(row.document() for row in decision.rows)
    for event in replayed.events[:-1]:
        body = json.loads(event.body)
        assert len(body["rows"]) <= 256
        assert event.blob_bytes <= 1024 * 1024


def test_pending_publication_is_sanitized_before_reservation_and_restart(tmp_path):
    secret = "credential-must-never-reach-staging"
    decision = _decision(1)
    decision = replace(decision, rows=(replace(decision.rows[0], name=f"challenge {secret}"),))

    def crash(point):
        if point == "after_pending_write":
            raise RuntimeError("crash")

    journal = OrderJournal(
        tmp_path,
        "run-1",
        Redactor({"TEAM_KEY": secret}),
        timestamp=lambda: NOW.isoformat(),
        publication_hook=crash,
    )
    with pytest.raises(RuntimeError, match="crash"):
        journal.publish(decision)
    pending = json.loads((journal.store.canonical_dir / "order-publication.pending.json").read_bytes())

    decoded = b"".join(base64.b64decode(item["body"]) for item in pending["entries"])
    assert secret.encode() not in decoded
    assert b"[redacted:TEAM_KEY]" in decoded

    resumed = OrderJournal(
        tmp_path, "run-1", Redactor({"TEAM_KEY": secret}), timestamp=lambda: NOW.isoformat()
    ).resume_pending()
    assert resumed is not None
    assert replay_order_publication(tmp_path, "run-1").rows[0]["name"] == "challenge [redacted:TEAM_KEY]"


@pytest.mark.parametrize("crash_at", ["after_reservations", "after_boundary_commit", "after_chunk_commit:1"])
def test_crash_retries_finish_the_same_publication_without_duplicate_grant(tmp_path, crash_at):
    decision = _decision()
    crashed = False

    def hook(point):
        nonlocal crashed
        if not crashed and point == crash_at:
            crashed = True
            raise RuntimeError("process-loss")

    with pytest.raises(RuntimeError, match="process-loss"):
        _journal(tmp_path, hook).publish(decision)

    recovered = _journal(tmp_path).publish(decision)
    events = EventStore(tmp_path, run_id="run-1").events()

    assert replay_order_publication(tmp_path, "run-1").decision_digest == decision.digest
    assert recovered.publication_id == f"order-publication:{decision.boundary_id}:{decision.digest}"
    assert (
        sum(
            event.event_type == ORDER_PUBLICATION_RECORDED and event.payload["record"] == "boundary" for event in events
        )
        == 1
    )
    assert len(events) == 1 + recovered.chunk_count


def test_a_pending_publication_finishes_before_a_new_boundary(tmp_path):
    first = _decision(257)

    def crash(point):
        if point == "after_boundary_commit":
            raise RuntimeError("process-loss")

    with pytest.raises(RuntimeError):
        _journal(tmp_path, crash).publish(first)

    replayed = replay_order_publication(tmp_path, "run-1")
    second = _decision(
        3,
        boundary_id="boundary-2",
        prior=OrderFence(replayed.publication_id, replayed.decision_digest, replayed.events[-1].event_digest),
    )
    _journal(tmp_path).publish(second)
    events = EventStore(tmp_path, run_id="run-1").events()
    assert [event.payload["boundary_id"] for event in events if event.payload["record"] == "boundary"] == [
        "boundary-1",
        "boundary-2",
    ]
    assert replay_order_publication(tmp_path, "run-1").decision_digest == second.digest


@pytest.mark.parametrize("failure", ["before_reserve:0", "before_reserve:1", "before_reserve:2"])
def test_reservation_failure_commits_no_partial_publication_and_same_decision_recovers(tmp_path, failure):
    decision = _decision(257)

    def fail(point):
        if point == failure:
            raise OSError("ENOSPC")

    with pytest.raises(OSError, match="ENOSPC"):
        _journal(tmp_path, fail).publish(decision)
    assert EventStore(tmp_path, run_id="run-1").events() == []

    recovered = _journal(tmp_path).publish(decision)
    assert len(EventStore(tmp_path, run_id="run-1").events()) == 1 + recovered.chunk_count


def test_staging_exists_before_every_reservation_and_partial_chunks_are_not_authority(tmp_path):
    observed = []
    holder = {}

    def hook(point):
        if point == "before_reserve:0":
            observed.append(holder["journal"]._pending_path.exists())
        if point == "after_chunk_commit:1":
            with pytest.raises(LookupError):
                replay_order_publication(tmp_path, "run-1")

    journal = _journal(tmp_path, hook)
    holder["journal"] = journal
    journal.publish(_decision(257))

    assert observed == [True]


def test_concurrent_writers_from_one_prior_fence_publish_exactly_one_boundary(tmp_path):
    decisions = (_decision(3, boundary_id="boundary-a"), _decision(4, boundary_id="boundary-b"))
    barrier = threading.Barrier(3)
    results = []

    def publish(decision):
        barrier.wait()
        try:
            results.append(_journal(tmp_path).publish(decision))
        except ValueError as error:
            results.append(error)

    threads = [threading.Thread(target=publish, args=(decision,)) for decision in decisions]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert sum(isinstance(item, ValueError) for item in results) == 1
    events = EventStore(tmp_path, run_id="run-1").events()
    assert sum(event.payload.get("record") == "boundary" for event in events) == 1
    assert sum(event.payload.get("record") == "fence-conflict" for event in events) == 1
    reservations = EventStore(tmp_path, run_id="run-1").reservations()
    assert len({item.reservation_id for item in reservations}) == len(events)


def test_other_event_producer_waits_for_reserved_order_batch(tmp_path):
    from solver.event_store import ObservationRecorded

    started = threading.Event()
    finished = threading.Event()
    failures = []
    workers = []

    def append_observation():
        started.set()
        try:
            EventStore(tmp_path, run_id="run-1").append(
                ObservationRecorded(
                    attempt_id="parallel-attempt",
                    step_index=1,
                    command_raw="observe",
                    command_normalised="observe",
                    tool="test",
                ),
                body=b"parallel observation",
            )
        except Exception as error:
            failures.append(error)
        finally:
            finished.set()

    def hook(point):
        if point == "after_chunk_commit:1":
            worker = threading.Thread(target=append_observation, daemon=True)
            workers.append(worker)
            worker.start()
            assert started.wait(2)
            assert not finished.wait(0.1)

    _journal(tmp_path, hook).publish(_decision(257))
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert not failures
    events = EventStore(tmp_path, run_id="run-1").events()
    assert events[-2].payload["record"] == "boundary"
    assert events[-1].event_type == "observation.recorded"
