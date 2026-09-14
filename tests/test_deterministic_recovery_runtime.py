"""Production deterministic Recovery routes every Core fault through real authority."""

import datetime as dt
import json

import pytest

from solver.recovery.contracts import FaultKind, ProbationOutcome
from solver.recovery.incident import ModelRemedyRejected, verify_receipt
from solver.recovery.runtime import AuthoritativeChange, DeterministicRecovery, DomainRecovery, RecoveryRegistry
from solver.redaction import Redactor

NOW = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)
DEADLINE = NOW + dt.timedelta(seconds=180)

DOMAINS = (
    (FaultKind.WORKER_CRASH, "owner-local:worker", "process-generation", "boot-1", "boot-2"),
    (
        FaultKind.ROUTE_LOCAL_INFERENCE,
        "owner-local:inference-native",
        "inference-route",
        "native-codex",
        "private-cpa",
    ),
    (FaultKind.TARGET_RESEARCH, "external:target", "request-attempt", "request-4", "request-5"),
    (FaultKind.INSTANCE, "external:instance", "instance-authority-join", "join-2", "join-3"),
    (
        FaultKind.SUBMISSION_AMBIGUITY,
        "run-shared:submission",
        "submission-epoch",
        "epoch-5",
        "epoch-6",
    ),
    (FaultKind.STORAGE, "run-shared:storage", "storage-revision", "revision-9", "revision-10"),
    (
        FaultKind.FINAL_INTERVAL,
        "run-shared:final-interval",
        "final-interval-phase",
        "draining",
        "reconciling",
    ),
)


def runtime(tmp_path):
    return DeterministicRecovery(tmp_path, "run-1", Redactor({}), now=lambda: NOW)


def recover(runtime, kind, scope, dimension, before, after, *, projection=True, probation=ProbationOutcome.PASSED):
    effects = []
    fences = []
    domain = DomainRecovery(
        dimension,
        lambda: AuthoritativeChange(before, after, "authoritative-domain-projection") if projection else None,
        lambda: effects.append((kind.value, after)) is None,
        lambda: probation,
        fence=lambda: fences.append(scope),
        capture=lambda: b"bounded external observation",
    )
    result = runtime.handle(
        kind=kind,
        fault_id=f"{kind.value}:fixture",
        scope=scope,
        generation_id="generation-7",
        evidence="controlled fault",
        failed_action_value=before,
        original_deadline=DEADLINE,
        recovery=domain,
    )
    return result, effects, fences


@pytest.mark.parametrize(("kind", "scope", "dimension", "before", "after"), DOMAINS)
def test_production_runtime_routes_every_core_fault_to_one_changed_remedy(
    tmp_path, kind, scope, dimension, before, after
):
    result, effects, fences = recover(runtime(tmp_path), kind, scope, dimension, before, after)

    receipt = json.loads(result.receipt_path.read_text())
    assert effects == [(kind.value, after)]
    assert fences == [scope]
    assert receipt["changed_action"] == {
        "accepted": True,
        "after": after,
        "before": before,
        "dimension": dimension,
        "source": "authoritative-domain-projection",
    }
    assert receipt["final_outcome"] == "resolved"
    assert verify_receipt(result.receipt_path) == result.receipt_path


@pytest.mark.parametrize(("kind", "scope", "dimension", "before", "after"), DOMAINS)
def test_unsettled_production_probe_keeps_domain_fenced_and_admits_no_remedy(
    tmp_path, kind, scope, dimension, before, after
):
    result, effects, fences = recover(runtime(tmp_path), kind, scope, dimension, before, after, projection=False)

    receipt = json.loads(result.receipt_path.read_text())
    assert effects == []
    assert fences == [scope]
    assert receipt["authority_state"] == "aborted"
    assert receipt["probe"]["outcome"] == "unsettled"
    assert receipt["final_outcome"] == ""
    assert receipt["disposition"] == "probation"


def test_production_runtime_rejects_unchanged_and_expired_remedies(tmp_path):
    production = runtime(tmp_path)
    unchanged, effects, _fences = recover(
        production,
        FaultKind.WORKER_CRASH,
        "owner-local:worker",
        "process-generation",
        "boot-1",
        "boot-1",
    )
    assert effects == []
    assert json.loads(unchanged.receipt_path.read_text())["consumed_allowance"] == 0

    expired_effects = []
    expired = DeterministicRecovery(tmp_path, "run-2", Redactor({}), now=lambda: NOW).handle(
        kind=FaultKind.STORAGE,
        fault_id="storage:expired",
        scope="run-shared:storage",
        generation_id="generation-7",
        evidence="hard pressure",
        failed_action_value="revision-9",
        original_deadline=NOW,
        recovery=DomainRecovery(
            "storage-revision",
            lambda: AuthoritativeChange("revision-9", "revision-10", "storage-governor"),
            lambda: expired_effects.append("retire") is None,
            lambda: ProbationOutcome.PASSED,
        ),
    )
    assert expired_effects == []
    assert json.loads(expired.receipt_path.read_text())["consumed_allowance"] == 0
    verify_receipt(expired.receipt_path)


@pytest.mark.parametrize(("kind", "scope", "dimension", "before", "after"), DOMAINS)
def test_every_core_fault_replays_the_same_bound_across_boot(tmp_path, kind, scope, dimension, before, after):
    production = runtime(tmp_path)
    with pytest.raises(RuntimeError, match="probe crash"):
        production.handle(
            kind=kind,
            fault_id=f"{kind.value}:cross-boot",
            scope=scope,
            generation_id="generation-7",
            evidence="controlled fault",
            failed_action_value=before,
            original_deadline=DEADLINE,
            recovery=DomainRecovery(
                dimension,
                lambda: (_ for _ in ()).throw(RuntimeError("probe crash")),
                lambda: True,
                lambda: ProbationOutcome.PASSED,
            ),
        )
    effects = []

    registry = RecoveryRegistry()
    registry.register(
        f"{kind.value}:{scope}:v1",
        lambda _config: DomainRecovery(
            dimension,
            lambda: AuthoritativeChange(before, after, "authoritative-domain-projection"),
            lambda: effects.append(after) is None,
            lambda: ProbationOutcome.PASSED,
        ),
    )
    result = production.replay(registry)[0]

    receipt = json.loads(result.receipt_path.read_text())
    assert effects == [after]
    assert receipt["fault_kind"] == kind.value
    assert receipt["original_deadline"] == DEADLINE.isoformat()
    assert receipt["allowance"] == receipt["consumed_allowance"] == 1
    assert receipt["final_outcome"] == "resolved"


def test_replay_after_original_deadline_cannot_renew_remedy_admission(tmp_path):
    before_deadline = DeterministicRecovery(tmp_path, "run-1", Redactor({}), now=lambda: NOW)
    with pytest.raises(RuntimeError, match="probe crash"):
        before_deadline.handle(
            kind=FaultKind.STORAGE,
            fault_id="storage:deadline",
            scope="run-shared:storage",
            generation_id="storage-governor",
            evidence="controlled pressure",
            failed_action_value="revision-9",
            original_deadline=DEADLINE,
            recovery=DomainRecovery(
                "storage-revision",
                lambda: (_ for _ in ()).throw(RuntimeError("probe crash")),
                lambda: True,
                lambda: ProbationOutcome.PASSED,
            ),
        )
    effects = []
    after_deadline = DeterministicRecovery(
        tmp_path,
        "run-1",
        Redactor({}),
        now=lambda: DEADLINE + dt.timedelta(seconds=1),
    )

    registry = RecoveryRegistry()
    registry.register(
        "storage:run-shared:storage:v1",
        lambda _config: DomainRecovery(
            "storage-revision",
            lambda: AuthoritativeChange("revision-9", "revision-10", "storage-governor"),
            lambda: effects.append("retire") is None,
            lambda: ProbationOutcome.PASSED,
        ),
    )
    result = after_deadline.replay(registry)[0]

    receipt = json.loads(result.receipt_path.read_text())
    assert effects == []
    assert receipt["original_deadline"] == DEADLINE.isoformat()
    assert receipt["consumed_allowance"] == 0
    assert receipt["final_outcome"] == "contained"


@pytest.mark.parametrize(
    ("probation", "final_outcome"),
    [(ProbationOutcome.PASSED, "resolved"), (ProbationOutcome.FAILED, "contained")],
)
def test_production_probation_replays_without_renewing_the_bound(tmp_path, probation, final_outcome):
    applied = []
    crashing = DomainRecovery(
        "process-generation",
        lambda: AuthoritativeChange("boot-1", "boot-2", "supervisor-lifecycle"),
        lambda: applied.append("boot-2") is None,
        lambda: (_ for _ in ()).throw(RuntimeError("probation crash")),
    )
    production = runtime(tmp_path)
    with pytest.raises(RuntimeError, match="probation crash"):
        production.handle(
            kind=FaultKind.WORKER_CRASH,
            fault_id="process:crash",
            scope="owner-local:worker",
            generation_id="generation-7",
            evidence="exit 17",
            failed_action_value="boot-1",
            original_deadline=DEADLINE,
            recovery=crashing,
        )
    replay_effects = []
    registry = RecoveryRegistry()
    registry.register(
        "worker-crash:owner-local:worker:v1",
        lambda _config: DomainRecovery(
            "process-generation",
            lambda: AuthoritativeChange("boot-1", "boot-2", "supervisor-lifecycle"),
            lambda: replay_effects.append("boot-2") is None,
            lambda: probation,
        ),
    )
    result = production.replay(registry)[0]

    receipt = json.loads(result.receipt_path.read_text())
    assert applied == ["boot-2"]
    assert replay_effects == []
    assert receipt["original_deadline"] == DEADLINE.isoformat()
    assert receipt["allowance"] == receipt["consumed_allowance"] == 1
    assert receipt["probation_outcome"] == probation.value
    assert receipt["final_outcome"] == final_outcome


def test_production_runtime_keeps_model_remedies_rejected(tmp_path):
    from solver.recovery.incident import IncidentEngine

    engine = IncidentEngine(tmp_path, "run-1", object(), Redactor({}), now=lambda: NOW)
    with pytest.raises(ModelRemedyRejected):
        engine.accept_model_remedy("retry with broader authority")


def test_committed_remedy_unsettled_probation_expires_without_repeating_effect(tmp_path):
    clock = [NOW]
    effects = []
    production = DeterministicRecovery(tmp_path, "run-bound", Redactor({}), now=lambda: clock[0])
    domain = DomainRecovery(
        "process-generation",
        lambda: AuthoritativeChange("boot-1", "boot-2", "supervisor-lifecycle"),
        lambda: effects.append("boot-2") is None,
        lambda: ProbationOutcome.UNSETTLED,
        adapter_id="bounded-process",
    )
    production.handle(
        kind=FaultKind.WORKER_CRASH,
        fault_id="process:unsettled",
        scope="owner-local:worker",
        generation_id="generation-7",
        evidence="exit 17",
        failed_action_value="boot-1",
        original_deadline=DEADLINE,
        recovery=domain,
    )
    clock[0] = DEADLINE
    registry = RecoveryRegistry()
    registry.register("bounded-process", lambda config: domain)
    result = production.replay(registry)[0]
    receipt = json.loads(result.receipt_path.read_text())
    assert effects == ["boot-2"]
    assert receipt["original_deadline"] == DEADLINE.isoformat()
    assert receipt["consumed_allowance"] == 1
    assert receipt["final_outcome"] == "contained"
    assert production.replay(registry) == ()


def test_recovery_reopens_the_runs_sealed_write_profile(tmp_path):
    from solver.write_reservation import Capacity, WriteAuthority, WriteProfile

    profile = WriteProfile(Capacity(0, 0, 0), Capacity(128 * 1024, 32, 256), Capacity(4096, 1, 8))
    root = tmp_path / "runs" / "run-1"
    authority = WriteAuthority(root, profile)
    authority.close()
    result, effects, _ = recover(
        runtime(tmp_path), FaultKind.WORKER_CRASH, "owner-local:worker", "process-generation", "boot-1", "boot-2"
    )
    assert result.disposition == "resolved"
    assert effects == [("worker-crash", "boot-2")]
    reopened = WriteAuthority(root, profile)
    reopened.close()


def test_different_fault_class_in_same_scope_opens_a_successor_incident(tmp_path):
    production = runtime(tmp_path)
    first, _, _ = recover(
        production, FaultKind.STORAGE, "run-shared:authority", "storage-revision", "rev-1", "rev-2", projection=False
    )
    second, _, _ = recover(
        production,
        FaultKind.FINAL_INTERVAL,
        "run-shared:authority",
        "final-interval-phase",
        "draining",
        "reconciling",
        projection=False,
    )
    assert second.incident_id != first.incident_id
    receipt = json.loads(second.receipt_path.read_text())
    assert receipt["fault_kind"] == "final-interval"
    assert receipt["successor_of"] == first.incident_id


def test_completed_probe_replays_its_recorded_change_after_storage_crash(tmp_path, monkeypatch):
    import os

    production = runtime(tmp_path)
    probes = []
    effects = []
    events = tmp_path / "runs/run-1/canonical/events.jsonl"
    fsync = os.fsync
    crashed = [False]

    def crash_after_probe(descriptor):
        fsync(descriptor)
        if not crashed[0] and events.exists():
            rows = events.read_bytes().splitlines()
            if rows and "fixed-probe" in json.loads(rows[-1]).get("payload", {}).get("completed_steps", ()):
                crashed[0] = True
                raise RuntimeError("probe recorded before process loss")

    domain = DomainRecovery(
        "process-generation",
        lambda: probes.append("observed") or AuthoritativeChange("boot-1", "boot-2", "process-table"),
        lambda: effects.append("boot-2") is None,
        lambda: ProbationOutcome.PASSED,
        adapter_id="durable-probe",
    )
    monkeypatch.setattr(os, "fsync", crash_after_probe)
    with pytest.raises(RuntimeError, match="probe recorded"):
        production.handle(
            kind=FaultKind.WORKER_CRASH,
            fault_id="probe:crash",
            scope="owner-local:worker",
            generation_id="generation-7",
            evidence="exit 17",
            failed_action_value="boot-1",
            original_deadline=DEADLINE,
            recovery=domain,
        )
    registry = RecoveryRegistry()
    registry.register("durable-probe", lambda config: domain)
    result = production.replay(registry)[0]
    assert probes == ["observed"]
    assert effects == ["boot-2"]
    assert result.disposition == "resolved"


def test_production_boot_refuses_an_open_incident_with_an_unknown_adapter(tmp_path):
    production = runtime(tmp_path)
    recover(production, FaultKind.STORAGE, "run-shared:storage", "storage-revision", "rev-1", "rev-2", projection=False)
    with pytest.raises(ValueError, match="unavailable Recovery adapter"):
        production.validate_boot_adapters()
