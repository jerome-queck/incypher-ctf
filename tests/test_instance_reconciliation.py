"""Boot admission is fenced by deterministic Instance reconciliation."""

import json

import pytest
from types import SimpleNamespace

from solver.instance_lease_contracts import LeaseGrant, LeaseIdentity, LeasePhase
from solver.instance_lease_contracts import grant_document
from solver.instance_lease_archive import persist_reconciled_close, replay_leases_across_runs
from solver.instance_ledger import EMPTY, NOT_OURS, POPULATED, UNSETTLED, LedgerPage, LedgerResult, LedgerRow
from solver.instance_ledger import identify_ledger, write_receipt as write_ledger_receipt
from solver.instance_reconciliation import InstanceReconciler
from solver.instance_reconciliation_contracts import AdmissionVerdict, ReconciliationResult
from solver.instance_reconciliation_contracts import BootOwnership
from solver.instance_reconciliation_receipt import verify_receipt, write_receipt
from solver.instance_reconciliation_receipt import link_manifest
from solver.manifest import generate_manifest
from solver.write_reservation import (
    Capacity,
    EffectIdentity,
    ReservedEffect,
    RetentionPolicy,
    WriteAuthority,
    WriteProfile,
)
from solver.work_generation import GenerationState
from solver.event_store import GenerationDisposition
from solver.recovery.instance import InstanceObservation, instance_recovery
from solver.recovery.runtime import DeterministicRecovery, RecoveryRegistry
from solver.recovery.contracts import FaultKind
from solver.recovery.contracts import ProbationOutcome
from solver.redaction import Redactor


PROFILE = WriteProfile(Capacity(0, 0, 0), Capacity(128 * 1024, 32, 256), Capacity(4096, 1, 8))


def lease(generation_id="generation-1", run_id="run-1"):
    return LeaseGrant(
        LeaseIdentity(run_id, 1),
        42,
        "board-1",
        "team:7",
        1,
        generation_id,
        "attempt-1",
        LeasePhase.ATTEMPT_BOUND,
        "lease:run-1:1:1:create",
        row_id="row-42",
    )


def ledger(outcome, *, owned=(), foreign=(), reason=""):
    return LedgerResult(outcome, "teams", 9, 7, "a" * 64, tuple(owned), tuple(foreign), reason=reason)


def recording(changed):
    return lambda grant, _snapshot: changed.append(grant)


def ownership(proved=True):
    return BootOwnership(("predecessor-close",) if proved else (), ("attempt-close",) if proved else (), proved)


def generation(active):
    return GenerationState(
        "generation-1",
        "integer:42",
        "attempt-1",
        None if active else GenerationDisposition.INTERRUPT,
        1,
        None if active else 2,
    )


def test_unreadable_or_foreign_rows_never_trigger_cleanup_or_admission(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    changed = []
    cleanup = recording(changed)
    reconciler = InstanceReconciler(authority, run_id="run-1", board_id="board-1", cleanup=cleanup)

    result = reconciler.reconcile(
        boot_id="boot-1",
        ledger=ledger(UNSETTLED, reason="unreadable"),
        leases=(lease(),),
        generations=(),
        ownership=ownership(),
    )

    assert result.verdict is AdmissionVerdict.CLOSED
    assert result.unsettled == ("ledger:unreadable",)
    assert changed == []
    authority.close()


def test_instance_adapter_reconciles_only_after_authoritative_join_changes_and_replays_by_identity(tmp_path):
    joins = ["join-1", "join-2"]
    reconciled = []

    def observe():
        current = joins.pop(0)
        return InstanceObservation(
            current,
            lambda: (
                reconciled.append(current)
                or ReconciliationResult("boot-2", "snapshot-2", current, (), (), AdmissionVerdict.OPEN)
            ),
            current.encode(),
        )

    runtime = DeterministicRecovery(
        tmp_path,
        "run-1",
        Redactor({}),
        now=lambda: __import__("datetime").datetime(2026, 9, 14, tzinfo=__import__("datetime").timezone.utc),
    )
    runtime.handle(
        kind=FaultKind.INSTANCE,
        fault_id="instance:boot-1",
        scope="external:instance",
        generation_id="all-active",
        evidence="ledger unsettled",
        failed_action_value="join-1",
        original_deadline=__import__("datetime").datetime(
            2026, 9, 14, 0, 3, tzinfo=__import__("datetime").timezone.utc
        ),
        recovery=instance_recovery("join-1", observe),
    )
    registry = RecoveryRegistry()
    registry.register(
        "instance-reconciliation-v1", lambda config: instance_recovery(config["failed_join_digest"], observe)
    )

    result = runtime.replay(registry)[0]

    assert result.disposition == "resolved"
    assert reconciled == ["join-2"]


def test_instance_replay_expires_at_original_bound_without_effect(tmp_path):
    import datetime as dt

    clock = [dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)]
    deadline = clock[0] + dt.timedelta(seconds=180)
    effects = []

    def observe():
        return InstanceObservation(
            "join-1",
            lambda: (
                effects.append("forbidden")
                or ReconciliationResult("boot-2", "snapshot-2", "join-1", (), (), AdmissionVerdict.OPEN)
            ),
            b"same join",
        )

    runtime = DeterministicRecovery(tmp_path, "run-bound", Redactor({}), now=lambda: clock[0])
    runtime.handle(
        kind=FaultKind.INSTANCE,
        fault_id="instance:bound",
        scope="external:instance",
        generation_id="all-active",
        evidence="unsettled",
        failed_action_value="join-1",
        original_deadline=deadline,
        recovery=instance_recovery("join-1", observe),
    )
    clock[0] = deadline
    registry = RecoveryRegistry()
    registry.register(
        "instance-reconciliation-v1", lambda config: instance_recovery(config["failed_join_digest"], observe)
    )
    result = runtime.replay(registry)[0]
    receipt = json.loads(result.receipt_path.read_text())

    assert result.disposition == "contained"
    assert receipt["original_deadline"] == deadline.isoformat()
    assert receipt["consumed_allowance"] == 0
    assert effects == []


def test_repeated_reconciliation_cycles_retain_boot_identity_and_verify_receipt(tmp_path):
    authority = WriteAuthority(tmp_path / "runs" / "run-1", PROFILE)
    authenticated = identify_ledger(
        mode="teams",
        user_id=9,
        team_id=7,
        pages=(LedgerPage(200, page=1, total_pages=1, total_rows=0, response_digest="b" * 64),),
    )
    owner = BootOwnership((), (), True, True)
    reconciler = InstanceReconciler(
        authority, run_id="run-1", board_id="board-1", cleanup=lambda _lease, _snapshot: None
    )

    first = reconciler.reconcile(
        boot_id="boot-1",
        cycle_id="initial",
        ledger=authenticated,
        leases=(),
        generations=(),
        ownership=owner,
    )
    second = reconciler.reconcile(
        boot_id="boot-1",
        cycle_id="recovery-1",
        ledger=authenticated,
        leases=(),
        generations=(),
        ownership=owner,
    )

    assert first.boot_id == second.boot_id == "boot-1"
    assert first.snapshot_id != second.snapshot_id
    assert first.cycle_id == "initial"
    assert second.cycle_id == "recovery-1"
    assert second.completion_key == "instance-reconciliation:run-1:recovery-1:complete"

    receipt = write_receipt(second, authority, tmp_path / "instance-reconciliation.receipt.json")
    ledger_receipt = write_ledger_receipt(tmp_path, "run-1", authenticated)
    events = [SimpleNamespace(payload={"event_id": "boot-open", "record": "boot-open", "boot_id": "boot-1"})]
    assert verify_receipt(receipt, authority, events, ledger_receipt) == receipt
    authority.close()


def test_instance_replay_reads_durable_probation_without_reconciling_again(tmp_path):
    authority = WriteAuthority(tmp_path / "runs" / "run-1", PROFILE)
    authenticated = identify_ledger(
        mode="teams",
        user_id=9,
        team_id=7,
        pages=(LedgerPage(200, page=1, total_pages=1, total_rows=0, response_digest="b" * 64),),
    )
    owner = BootOwnership((), (), True, True)
    reconciler = InstanceReconciler(
        authority, run_id="run-1", board_id="board-1", cleanup=lambda _lease, _snapshot: None
    )
    committed = reconciler.reconcile(
        boot_id="boot-1",
        cycle_id="recovery-1",
        ledger=authenticated,
        leases=(),
        generations=(),
        ownership=owner,
    )
    calls = []
    probation_calls = [0]

    def observe():
        def reconcile_again():
            calls.append("reconcile")
            return committed

        def probation():
            probation_calls[0] += 1
            if probation_calls[0] == 1:
                raise RuntimeError("probation crash")
            return reconciler.probation(committed.join_digest)

        return InstanceObservation(committed.join_digest, reconcile_again, b"", probation)

    runtime = DeterministicRecovery(
        tmp_path, "run-1", Redactor({}), authority=authority, now=lambda: __import__("datetime").datetime.now()
    )
    with pytest.raises(RuntimeError, match="probation crash"):
        runtime.handle(
            kind=FaultKind.INSTANCE,
            fault_id="instance:probation",
            scope="external:instance",
            generation_id="all-active",
            evidence="unsettled",
            failed_action_value="prior-join",
            original_deadline=__import__("datetime").datetime(2099, 1, 1),
            recovery=instance_recovery("prior-join", observe),
        )

    registry = RecoveryRegistry()
    registry.register(
        "instance-reconciliation-v1", lambda config: instance_recovery(config["failed_join_digest"], observe)
    )
    replayed = runtime.replay(registry)

    assert replayed[0].disposition == "resolved"
    assert calls == ["reconcile"]
    authority.close()


def test_instance_probation_can_fence_result_to_the_current_boot(tmp_path):
    authority = WriteAuthority(tmp_path / "runs" / "run-1", PROFILE)
    authenticated = identify_ledger(
        mode="teams",
        user_id=9,
        team_id=7,
        pages=(LedgerPage(200, page=1, total_pages=1, total_rows=0, response_digest="b" * 64),),
    )
    result = InstanceReconciler(
        authority, run_id="run-1", board_id="board-1", cleanup=lambda _lease, _snapshot: None
    ).reconcile(
        boot_id="boot-1",
        ledger=authenticated,
        leases=(),
        generations=(),
        ownership=BootOwnership((), (), True, True),
    )

    assert result.verdict is AdmissionVerdict.OPEN
    assert (
        InstanceReconciler(authority, run_id="run-1", board_id="board-1", cleanup=lambda *_: None).probation(
            result.join_digest, boot_id="boot-2"
        )
        is ProbationOutcome.UNSETTLED
    )
    authority.close()


def test_owned_row_with_dead_generation_gets_one_fixed_cleanup_then_admits(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    changed = []
    cleanup = recording(changed)
    clock = [0.0]
    reconciler = InstanceReconciler(
        authority, run_id="run-1", board_id="board-1", cleanup=cleanup, now=lambda: clock[0]
    )
    row = LedgerRow("row-42", 42, team_id=7)

    first = reconciler.reconcile(
        boot_id="boot-1",
        ledger=ledger(POPULATED, owned=(row,)),
        leases=(lease(),),
        generations=(generation(False),),
        ownership=ownership(),
    )
    clock[0] = 15.0
    result = reconciler.reconcile(
        boot_id="boot-2",
        ledger=ledger(POPULATED, owned=(row,)),
        leases=(lease(),),
        generations=(generation(False),),
        ownership=ownership(),
    )

    assert first.verdict is AdmissionVerdict.CLOSED
    assert result.verdict is AdmissionVerdict.OPEN
    assert changed == [lease()]
    assert result.actions == ("terminate:row-42",)
    assert result.document()["ownership"]["interrupted_attempt_close_event_ids"] == ["attempt-close"]
    authority.close()


def test_predecessor_and_interrupted_attempt_proof_precedes_cleanup(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    changed = []
    row = LedgerRow("row-42", 42, team_id=7)
    reconciler = InstanceReconciler(
        authority, run_id="run-1", board_id="board-1", cleanup=recording(changed), now=lambda: 20.0
    )

    result = reconciler.reconcile(
        boot_id="boot-1",
        ledger=ledger(POPULATED, owned=(row,)),
        leases=(lease(),),
        generations=(generation(False),),
        ownership=ownership(False),
    )

    assert result.verdict is AdmissionVerdict.CLOSED
    assert result.unsettled == ("predecessor-or-attempt-ownership-unproved",)
    assert changed == []
    authority.close()


def test_conclusively_attributed_earlier_run_lease_enters_the_same_fixed_cleanup(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    changed = []
    clock = [0.0]
    row = LedgerRow("row-42", 42, team_id=7)
    prior = lease(run_id="run-earlier")
    reconciler = InstanceReconciler(
        authority,
        run_id="run-1",
        board_id="board-1",
        cleanup=recording(changed),
        now=lambda: clock[0],
    )
    first = reconciler.reconcile(
        boot_id="boot-1",
        ledger=ledger(POPULATED, owned=(row,)),
        leases=(prior,),
        generations=(),
        ownership=ownership(),
    )
    clock[0] = 15.0
    second = reconciler.reconcile(
        boot_id="boot-2",
        ledger=ledger(POPULATED, owned=(row,)),
        leases=(prior,),
        generations=(),
        ownership=ownership(),
    )

    assert first.verdict is AdmissionVerdict.CLOSED
    assert second.verdict is AdmissionVerdict.OPEN
    assert changed == [prior]
    authority.close()


def test_earlier_run_cleanup_is_replay_persistent_in_its_original_authority(tmp_path):
    prior = lease(run_id="run-earlier")
    run_dir = tmp_path / "runs" / "run-earlier"
    authority = WriteAuthority(run_dir, PROFILE)
    ReservedEffect(authority).execute(
        prior.operation_key,
        EffectIdentity("lease.create", '{"board_id":"board-1","run_id":"run-earlier"}'),
        Capacity(2048, 1, 3),
        lambda: None,
        encode=lambda _result: grant_document(prior),
        decode=lambda _document: prior,
        retention=RetentionPolicy.RECORD,
    )
    authority.close()

    closed = persist_reconciled_close(tmp_path, prior, "snapshot-1")
    replayed = replay_leases_across_runs(tmp_path, "board-1")

    assert closed.phase is LeasePhase.CLOSED
    assert replayed == (closed,)


@pytest.mark.parametrize("fault", ("before_archive_write", "after_reserve", "after_archive_write"))
def test_delete_settled_archive_fault_replays_close_without_another_delete(tmp_path, fault):
    prior = lease(run_id="run-earlier")
    run_dir = tmp_path / "runs" / "run-earlier"
    authority = WriteAuthority(run_dir, PROFILE)
    ReservedEffect(authority).execute(
        prior.operation_key,
        EffectIdentity("lease.create", '{"board_id":"board-1","run_id":"run-earlier"}'),
        Capacity(2048, 1, 3),
        lambda: None,
        encode=lambda _result: grant_document(prior),
        decode=lambda _document: prior,
        retention=RetentionPolicy.RECORD,
    )
    authority.close()
    fired = False

    def crash(point):
        nonlocal fired
        if point == fault and not fired:
            fired = True
            raise RuntimeError(point)

    with pytest.raises(RuntimeError, match=fault):
        persist_reconciled_close(tmp_path, prior, "snapshot-1", hook=crash)

    closed = persist_reconciled_close(tmp_path, prior, "snapshot-2")

    assert closed.phase is LeasePhase.CLOSED
    assert replay_leases_across_runs(tmp_path, "board-1") == (closed,)


def test_absent_row_after_delete_finalizes_archive_without_repeating_delete_or_early_admission(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    deletes = []
    finalized = []
    clock = [0.0]
    reconciler = InstanceReconciler(
        authority,
        run_id="run-1",
        board_id="board-1",
        cleanup=lambda grant, _snapshot: deletes.append(grant),
        finalize=lambda grant, _snapshot: finalized.append(grant),
        now=lambda: clock[0],
    )
    first = reconciler.reconcile(
        boot_id="boot-1",
        ledger=ledger(EMPTY),
        leases=(lease(run_id="run-earlier"),),
        generations=(),
        ownership=ownership(),
    )
    clock[0] = 15.0
    second = reconciler.reconcile(
        boot_id="boot-2",
        ledger=ledger(EMPTY),
        leases=(lease(run_id="run-earlier"),),
        generations=(),
        ownership=ownership(),
    )

    assert first.verdict is AdmissionVerdict.CLOSED
    assert second.verdict is AdmissionVerdict.OPEN
    assert deletes == []
    assert finalized == [lease(run_id="run-earlier")]


@pytest.mark.parametrize(
    ("ledger_result", "leases", "generations", "expected"),
    (
        (ledger(EMPTY), (), (), AdmissionVerdict.OPEN),
        (ledger(NOT_OURS, foreign=(LedgerRow("foreign", 42, team_id=8),)), (), (), AdmissionVerdict.CLOSED),
        (
            ledger(POPULATED, owned=(LedgerRow("row-42", 42, team_id=7),)),
            (lease(),),
            (generation(True),),
            AdmissionVerdict.CLOSED,
        ),
        (ledger(EMPTY), (lease(),), (generation(False),), AdmissionVerdict.CLOSED),
        (ledger(EMPTY), (lease(),), (), AdmissionVerdict.CLOSED),
    ),
)
def test_join_matrix_distinguishes_ledger_lease_and_generation_states(
    tmp_path, ledger_result, leases, generations, expected
):
    authority = WriteAuthority(tmp_path, PROFILE)
    changed = []
    cleanup = recording(changed)
    result = InstanceReconciler(authority, run_id="run-1", board_id="board-1", cleanup=cleanup).reconcile(
        boot_id="boot-1",
        ledger=ledger_result,
        leases=leases,
        generations=generations,
        ownership=ownership(),
    )
    assert result.verdict is expected
    assert all(row.row_id != "foreign" for row in changed)
    authority.close()


@pytest.mark.parametrize("crash_point", ("before_reserve", "after_reserve", "after_effect"))
def test_cleanup_crash_replays_without_duplicate_effect_or_early_admission(tmp_path, crash_point):
    changed = []
    seen = 0

    def crash(point):
        nonlocal seen
        if point == crash_point:
            seen += 1
        if seen == 2 and point == crash_point:
            raise RuntimeError(point)

    row = LedgerRow("row-42", 42, team_id=7)
    seeded = WriteAuthority(tmp_path, PROFILE)
    InstanceReconciler(
        seeded, run_id="run-1", board_id="board-1", cleanup=recording(changed), now=lambda: 0.0
    ).reconcile(
        boot_id="boot-0",
        ledger=ledger(POPULATED, owned=(row,)),
        leases=(lease(),),
        generations=(),
        ownership=ownership(),
    )
    seeded.close()
    authority = WriteAuthority(tmp_path, PROFILE, hook=crash)
    cleanup = recording(changed)
    reconciler = InstanceReconciler(authority, run_id="run-1", board_id="board-1", cleanup=cleanup, now=lambda: 15.0)
    first = reconciler.reconcile(
        boot_id="boot-1",
        ledger=ledger(POPULATED, owned=(row,)),
        leases=(lease(),),
        generations=(),
        ownership=ownership(),
    )
    assert first.verdict is AdmissionVerdict.CLOSED
    authority.close()

    reopened = WriteAuthority(tmp_path, PROFILE)
    cleanup = recording(changed)
    replayed = InstanceReconciler(reopened, run_id="run-1", board_id="board-1", cleanup=cleanup).reconcile(
        boot_id="boot-2",
        ledger=ledger(POPULATED, owned=(row,)),
        leases=(lease(),),
        generations=(),
        ownership=ownership(),
    )
    assert changed.count(lease()) <= 1
    assert replayed.verdict is (AdmissionVerdict.OPEN if crash_point == "before_reserve" else AdmissionVerdict.CLOSED)
    reopened.close()


def test_authenticated_empty_snapshot_with_no_leases_records_complete_admission(tmp_path):
    authority = WriteAuthority(tmp_path / "runs" / "run-1", PROFILE)
    authenticated = identify_ledger(
        mode="teams",
        user_id=9,
        team_id=7,
        pages=(LedgerPage(200, page=1, total_pages=1, total_rows=0, response_digest="b" * 64),),
    )
    result = InstanceReconciler(
        authority, run_id="run-1", board_id="board-1", cleanup=lambda _lease, _snapshot: None
    ).reconcile(
        boot_id="boot-1",
        ledger=authenticated,
        leases=(),
        generations=(),
        ownership=ownership(),
    )
    assert result.verdict is AdmissionVerdict.OPEN
    assert "cycle_id" not in result.document()
    assert result.completion_key == "instance-reconciliation:run-1:boot-1:complete"
    receipt = write_receipt(result, authority, tmp_path / "instance-reconciliation.receipt.json")
    ledger_receipt = write_ledger_receipt(tmp_path, "run-1", authenticated)
    events = [
        SimpleNamespace(payload={"event_id": "boot-current-open", "record": "boot-open", "boot_id": "boot-1"}),
        SimpleNamespace(payload={"event_id": "boot-prior-open", "record": "boot-open", "boot_id": "boot-0"}),
        SimpleNamespace(
            payload={
                "event_id": "predecessor-close",
                "record": "boot-close",
                "boot_id": "boot-0",
                "disposition": "crashed",
            }
        ),
        SimpleNamespace(payload={"event_id": "attempt-open", "record": "attempt-open", "attempt_id": "attempt-1"}),
        SimpleNamespace(
            payload={
                "event_id": "generation-close",
                "record": "close",
                "attempt_id": "attempt-1",
                "disposition": "interrupt",
            }
        ),
        SimpleNamespace(payload={"event_id": "attempt-close", "record": "attempt-close", "attempt_id": "attempt-1"}),
    ]
    assert verify_receipt(receipt, authority, events, ledger_receipt) == receipt
    with pytest.raises(ValueError, match="Boot identity is not canonical"):
        verify_receipt(receipt, authority, (), ledger_receipt)
    from test_manifest import release_candidate_profile

    manifest = link_manifest(
        generate_manifest(
            image_digest="sha256:" + "1" * 64,
            release_candidate_profile=release_candidate_profile(),
        ),
        receipt,
        authority,
        events,
        ledger_receipt,
    )
    row = next(item for item in manifest["requirements"] if item["row_id"] == "core.board-target-lease")
    assert row["receipt_ref"] == "receipt:instance-reconciliation"
    authority.close()


@pytest.mark.parametrize(
    ("declared", "message"),
    (
        (BootOwnership((), ("attempt-close",), True, True), "predecessor closure set is incomplete"),
        (BootOwnership(("predecessor-close",), (), True, True), "Attempt closure set is incomplete"),
    ),
)
def test_receipt_rejects_omitted_required_owner_close(tmp_path, declared, message):
    authority = WriteAuthority(tmp_path / "runs" / "run-1", PROFILE)
    authenticated = identify_ledger(
        mode="teams",
        user_id=9,
        team_id=7,
        pages=(LedgerPage(200, page=1, total_pages=1, total_rows=0, response_digest="b" * 64),),
    )
    result = InstanceReconciler(
        authority, run_id="run-1", board_id="board-1", cleanup=lambda _lease, _snapshot: None
    ).reconcile(boot_id="boot-1", ledger=authenticated, leases=(), generations=(), ownership=declared)
    receipt = write_receipt(result, authority, tmp_path / "reconciliation.json")
    ledger_receipt = write_ledger_receipt(tmp_path, "run-1", authenticated)
    events = [
        SimpleNamespace(payload={"event_id": "current-open", "record": "boot-open", "boot_id": "boot-1"}),
        SimpleNamespace(payload={"event_id": "prior-open", "record": "boot-open", "boot_id": "boot-0"}),
        SimpleNamespace(
            payload={
                "event_id": "predecessor-close",
                "record": "boot-close",
                "boot_id": "boot-0",
                "disposition": "crashed",
            }
        ),
        SimpleNamespace(payload={"event_id": "attempt-open", "record": "attempt-open", "attempt_id": "attempt-1"}),
        SimpleNamespace(
            payload={
                "event_id": "generation-close",
                "record": "close",
                "attempt_id": "attempt-1",
                "disposition": "interrupt",
            }
        ),
        SimpleNamespace(payload={"event_id": "attempt-close", "record": "attempt-close", "attempt_id": "attempt-1"}),
    ]

    with pytest.raises(ValueError, match=message):
        verify_receipt(receipt, authority, events, ledger_receipt)


@pytest.mark.parametrize("crash_point", ("before_reserve", "after_reserve", "after_effect"))
def test_completion_crash_never_opens_admission(tmp_path, crash_point):
    seen = 0

    def crash(point):
        nonlocal seen
        if point == crash_point:
            seen += 1
        if seen == 2 and point == crash_point:
            raise RuntimeError(point)

    authority = WriteAuthority(tmp_path, PROFILE, hook=crash)
    result = InstanceReconciler(
        authority, run_id="run-1", board_id="board-1", cleanup=lambda _lease, _snapshot: None
    ).reconcile(boot_id="boot-1", ledger=ledger(EMPTY), leases=(), generations=(), ownership=ownership())

    assert result.verdict is AdmissionVerdict.CLOSED
    assert result.completion_key == ""
    authority.close()
