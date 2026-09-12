"""One coordinator is the public authority seam for every Instance Lease."""

import datetime as dt
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from solver.board import ANSWERED, Reply
from solver.event_store import CommittedEvent
from solver.manifest import canonical_manifest_bytes, generate_manifest, parse_manifest
from solver.instance_lease import LeaseContended, LeaseCoordinator, StaleLease
from solver.instance_lease_contracts import (
    LeaseCloseCause,
    LeasePhase,
    LeaseVerdict,
    RowCorroboration,
)
from solver.instance_lease_receipt import verify_receipt
from solver.work_generation import GenerationIdentity
from solver.write_reservation import Capacity, WriteAuthority, WriteProfile


PROFILE = WriteProfile(
    ordinary=Capacity(0, 0, 0),
    shared=Capacity(128 * 1024, 32, 256),
    terminal=Capacity(4096, 1, 8),
)
NOON = dt.datetime(2026, 9, 13, 12, tzinfo=dt.timezone.utc)
AUTHORITY_EVENT = CommittedEvent(
    sequence=1,
    previous_digest="0" * 64,
    event_digest="1" * 64,
    event_type="work-generation.recorded",
    payload={
        "event_id": "authority-1",
        "generation_id": "generation-000001",
        "work_id": "integer:42",
        "attempt_id": "attempt-1",
        "record": "authority",
        "authority": "authority",
        "classification": "current-generation",
    },
    envelope={},
    blob_digest="2" * 64,
    blob_bytes=0,
)


class BoardPort:
    def __init__(self):
        self.calls = []
        self.fail = None

    def deploy_instance(self, challenge_id):
        self.calls.append(("create", challenge_id))
        if self.fail:
            raise self.fail
        return Reply(ANSWERED, "target:31337")

    def renew_instance(self, challenge_id):
        self.calls.append(("renew", challenge_id))
        return Reply(ANSWERED)

    def terminate_instance(self, challenge_id):
        self.calls.append(("release", challenge_id))
        return Reply(ANSWERED)


def coordinator(tmp_path, board, hook=None, admit=None, events=None):
    authority = WriteAuthority(tmp_path, PROFILE, hook=hook)
    return (
        LeaseCoordinator(
            authority,
            board,
            run_id="run-1",
            board_id="board-1",
            owner_id="team-7",
            corroborate=lambda challenge_id: RowCorroboration(row_id=f"row-{challenge_id}"),
            admit_generation=admit or (lambda _generation_id, effect: effect(1, "authority-1")),
            generation_events=lambda: list(events if events is not None else (AUTHORITY_EVENT,)),
        ),
        authority,
    )


def generation(serial=1, attempt_id=None):
    return GenerationIdentity(f"generation-{serial:06d}", "integer:42", attempt_id or f"attempt-{serial}")


def test_two_contenders_cannot_both_acquire_one_challenge(tmp_path):
    board = BoardPort()
    leases, authority = coordinator(tmp_path, board)

    def contend(attempt):
        try:
            return leases.acquire(42, generation=generation(1 if attempt == "a" else 2, attempt))
        except LeaseContended:
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(contend, ("a", "b")))

    winners = [result for result in results if result and result.phase is LeasePhase.ATTEMPT_BOUND]
    assert len(winners) == 1
    assert board.calls == [("create", 42)]
    assert winners[0].row_id == "row-42"
    trace = authority.trace(winners[0].operation_key)
    assert [row["state"] for row in trace] == ["reserved", "started", "committed"]


def test_ambiguous_create_is_recoverable_and_never_repeated_after_replay(tmp_path):
    board = BoardPort()
    board.fail = TimeoutError("response lost")
    leases, authority = coordinator(tmp_path, board)

    unsettled = leases.acquire(42, generation=generation())

    assert unsettled.phase is LeasePhase.RECOVERABLE
    assert unsettled.verdict is LeaseVerdict.CREATE_AMBIGUOUS
    authority.close()
    board.fail = None
    replayed, reopened = coordinator(tmp_path, board)
    same = replayed.current(unsettled.identity)
    assert same.phase is LeasePhase.RECOVERABLE
    with pytest.raises(LeaseContended):
        replayed.acquire(42, generation=generation(2))
    assert board.calls == [("create", 42)]
    reopened.close()


@pytest.mark.parametrize("crash_point", ("before_reserve", "after_reserve", "after_effect"))
def test_create_fault_boundaries_never_duplicate_the_board_effect(tmp_path, crash_point):
    board = BoardPort()

    def crash(point):
        if point == crash_point:
            raise RuntimeError(point)

    leases, authority = coordinator(tmp_path, board, hook=crash)
    unsettled = leases.acquire(42, generation=generation())
    assert unsettled.phase is LeasePhase.RECOVERABLE
    authority.close()

    replayed, reopened = coordinator(tmp_path, board)
    try:
        replayed.acquire(42, generation=generation(2))
    except LeaseContended:
        pass
    assert board.calls.count(("create", 42)) <= 1
    reopened.close()


def test_ambiguous_release_is_not_repeated_after_replay(tmp_path):
    board = BoardPort()
    enabled = False

    def crash(point):
        if enabled and point == "after_effect":
            raise RuntimeError(point)

    leases, authority = coordinator(tmp_path, board, hook=crash)
    held = leases.acquire(42, generation=generation())
    enabled = True
    result = leases.release(held.identity, held.epoch, generation=generation())
    assert result.phase is LeasePhase.RECOVERABLE
    authority.close()

    replayed, reopened = coordinator(tmp_path, board)
    active = replayed.current(held.identity)
    replayed.release(active.identity, active.epoch, generation=generation())
    assert board.calls.count(("release", 42)) == 1
    reopened.close()


def test_uncorroborated_or_foreign_row_never_grants_solving_authority(tmp_path):
    board = BoardPort()
    authority = WriteAuthority(tmp_path, PROFILE)
    leases = LeaseCoordinator(
        authority,
        board,
        run_id="run-1",
        board_id="board-1",
        owner_id="team-7",
        corroborate=lambda _challenge_id: RowCorroboration(LeaseVerdict.FOREIGN_ROW),
        admit_generation=lambda _generation_id, effect: effect(1, "authority-1"),
    )

    result = leases.acquire(42, generation=generation())

    assert result.phase is LeasePhase.RECOVERABLE
    assert result.verdict is LeaseVerdict.FOREIGN_ROW
    authority.close()
    replayed, reopened = coordinator(tmp_path, board)
    assert replayed.current(result.identity).verdict is LeaseVerdict.FOREIGN_ROW
    reopened.close()


def test_corroborated_natural_absence_has_a_distinct_expired_close(tmp_path):
    board = BoardPort()
    leases, authority = coordinator(tmp_path, board)
    held = leases.acquire(42, generation=generation())

    expired = leases.expire(
        held.identity,
        held.epoch,
        generation=generation(),
        corroborated_absence="ledger-plus-challenge-404-after-60s",
    )

    assert expired.phase is LeasePhase.CLOSED
    assert expired.close_cause is LeaseCloseCause.EXPIRED
    assert board.calls == [("create", 42)]
    authority.close()


def test_stale_generation_cannot_renew_or_release(tmp_path):
    board = BoardPort()
    leases, authority = coordinator(tmp_path, board)
    held = leases.acquire(42, generation=generation())
    stale = generation(2)

    with pytest.raises(StaleLease):
        leases.renew(held.identity, held.epoch, generation=stale, until=NOON)
    with pytest.raises(StaleLease):
        leases.release(held.identity, held.epoch, generation=stale)

    released = leases.release(held.identity, held.epoch, generation=generation())
    assert released.close_cause is LeaseCloseCause.TERMINATED
    assert board.calls == [("create", 42), ("release", 42)]
    authority.close()


def test_successive_renewals_have_distinct_stable_operation_identities(tmp_path):
    board = BoardPort()
    leases, authority = coordinator(tmp_path, board)
    held = leases.acquire(42, generation=generation())
    first_until = NOON + dt.timedelta(minutes=5)
    second_until = NOON + dt.timedelta(minutes=6)

    first = leases.renew(held.identity, held.epoch, generation=generation(), until=first_until)
    second = leases.renew(held.identity, held.epoch, generation=generation(), until=second_until)

    assert first.operation_key.endswith(":renew:1")
    assert second.operation_key.endswith(":renew:2")
    assert board.calls.count(("renew", 42)) == 2
    authority.close()


def test_generation_closure_cannot_cross_an_admitted_board_effect(tmp_path):
    board = BoardPort()
    authority_lock = threading.Lock()
    entered = threading.Event()
    release = threading.Event()

    def admit(_generation_id, effect):
        with authority_lock:
            entered.set()
            release.wait(2)
            return effect(7, "authority-7")

    leases, authority = coordinator(tmp_path, board, admit=admit)
    worker = threading.Thread(target=lambda: leases.acquire(42, generation=generation()))
    worker.start()
    assert entered.wait(1)
    closed = threading.Event()

    def close_generation():
        with authority_lock:
            closed.set()

    closer = threading.Thread(target=close_generation)
    closer.start()
    assert not closed.wait(0.05)
    release.set()
    worker.join()
    closer.join()
    assert closed.is_set()
    assert board.calls == [("create", 42)]
    authority.close()


def test_durably_closed_work_generation_cannot_renew_or_delete(tmp_path):
    board = BoardPort()
    current = True
    authority = WriteAuthority(tmp_path, PROFILE)
    leases = LeaseCoordinator(
        authority,
        board,
        run_id="run-1",
        board_id="board-1",
        owner_id="team-7",
        corroborate=lambda challenge_id: RowCorroboration(row_id=f"row-{challenge_id}"),
        admit_generation=lambda _generation_id, effect: effect(1, "authority-1") if current else None,
    )
    held = leases.acquire(42, generation=generation())
    current = False

    with pytest.raises(StaleLease, match="durably closed"):
        leases.renew(held.identity, held.epoch, generation=generation(), until=NOON)
    with pytest.raises(StaleLease, match="durably closed"):
        leases.release(held.identity, held.epoch, generation=generation())
    assert board.calls == [("create", 42)]
    authority.close()


@pytest.mark.parametrize("operation", ("create", "renew", "release", "expire"))
@pytest.mark.parametrize("crash_point", ("before_reserve", "after_reserve", "after_effect"))
def test_every_instance_effect_boundary_is_at_most_once_and_never_false_free(tmp_path, operation, crash_point):
    board = BoardPort()
    armed = operation == "create"

    def crash(point):
        if armed and point == crash_point:
            raise RuntimeError(point)

    leases, authority = coordinator(tmp_path, board, hook=crash)
    if operation == "create":
        leases.acquire(42, generation=generation())
    else:
        held = leases.acquire(42, generation=generation())
        armed = True
        if operation == "renew":
            leases.renew(held.identity, held.epoch, generation=generation(), until=NOON)
        elif operation == "release":
            leases.release(held.identity, held.epoch, generation=generation())
        else:
            expired = leases.expire(
                held.identity,
                held.epoch,
                generation=generation(),
                corroborated_absence="ledger-and-challenge-404",
            )
            assert expired.phase is LeasePhase.RECOVERABLE
            assert expired.verdict is LeaseVerdict.EXPIRY_AMBIGUOUS
    authority.close()

    replayed, reopened = coordinator(tmp_path, board)
    active = replayed.active(42)
    if operation == "expire" and crash_point != "before_reserve":
        assert active is not None
        assert active.phase is LeasePhase.RECOVERABLE
        assert active.verdict is LeaseVerdict.EXPIRY_AMBIGUOUS
    if operation == "create":
        if active is None:
            replayed.acquire(42, generation=generation(2))
    elif operation == "renew":
        replayed.renew(active.identity, active.epoch, generation=generation(), until=NOON)
    elif operation == "expire":
        if active is not None:
            replayed.expire(
                active.identity,
                active.epoch,
                generation=generation(),
                corroborated_absence="ledger-and-challenge-404",
            )
    elif active is not None:
        replayed.release(active.identity, active.epoch, generation=generation())

    if operation == "expire":
        assert board.calls.count(("release", 42)) == 0
    else:
        assert board.calls.count((operation, 42)) <= 1
    if crash_point != "before_reserve":
        assert replayed.active(42) is not None
    reopened.close()


def test_receipt_exposes_identity_generation_effect_order_and_contender(tmp_path):
    board = BoardPort()
    leases, authority = coordinator(tmp_path, board)
    held = leases.acquire(42, generation=generation())
    with pytest.raises(LeaseContended):
        leases.acquire(42, generation=generation(2))

    path = leases.write_receipt(tmp_path / "instance-lease.receipt.json")
    assert verify_receipt(path, authority, (AUTHORITY_EVENT,)) == path
    receipt = json.loads(path.read_text())

    assert receipt["schema_version"] == 1
    assert receipt["leases"][0]["authenticated_row_id"] == "row-42"
    assert receipt["leases"][0]["lease_epoch"] == held.epoch
    assert [step["state"] for step in receipt["leases"][0]["effect_trace"]] == [
        "reserved",
        "started",
        "committed",
    ]
    assert receipt["contender_results"] == [{"attempt_id": "attempt-2", "result": "fenced"}]
    assert "target:31337" not in path.read_text()
    receipt["leases"][0]["lease_epoch"] = 99
    path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(ValueError, match="canonical authority history"):
        verify_receipt(path, authority, (AUTHORITY_EVENT,))
    authority.close()


def test_receipt_verifies_generation_authority_and_links_candidate_manifest(tmp_path):
    from test_manifest import release_candidate_profile

    event = AUTHORITY_EVENT
    board = BoardPort()
    leases, authority = coordinator(tmp_path, board, events=(event,))
    leases.acquire(42, generation=generation())
    manifest_path = tmp_path / "candidate-manifest.json"
    manifest_path.write_bytes(
        canonical_manifest_bytes(
            generate_manifest(
                image_digest="sha256:" + "1" * 64,
                release_candidate_profile=release_candidate_profile(),
            )
        )
        + b"\n"
    )

    receipt_path = leases.write_receipt(tmp_path / "instance-lease.receipt.json", manifest_path=manifest_path)

    assert verify_receipt(receipt_path, authority, (event,)) == receipt_path
    trace = json.loads(receipt_path.read_text())["leases"][0]["effect_trace"]
    assert {(step["generation_authority_sequence"], step["generation_authority_event_id"]) for step in trace} == {
        (1, "authority-1")
    }
    manifest = parse_manifest(manifest_path.read_bytes())
    row = next(item for item in manifest["requirements"] if item["row_id"] == "core.board-target-lease")
    assert row["receipt_ref"] == "receipt:instance-lease"
    authority.close()
