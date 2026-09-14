"""Generate the sanitized seven-class deterministic Recovery observation."""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from solver.event_store_storage import atomic_write, canonical_bytes
from solver.event_store import EventStore, ObservationRecorded
from solver.instance_reconciliation_contracts import AdmissionVerdict, ReconciliationResult
from solver.record import Recorder
from solver.route_and_quota import (
    InferenceRoute,
    RouteAndQuotaController,
    RouteAndQuotaPolicy,
    RouteTransportFailure,
)
from solver.recovery.catalogue import CATALOGUE_VERSION
from solver.recovery.contracts import FaultKind, ProbationOutcome
from solver.recovery.runtime import (
    AuthoritativeChange,
    DeterministicRecovery,
    DomainRecovery,
    RecoveryRegistry,
)
from solver.recovery.safe_read import SafeReadFault, SafeReadRecovery
from solver.recovery.instance import INSTANCE_ADAPTER, InstanceObservation, instance_recovery
from solver.recovery.submission import SUBMISSION_ADAPTER, submission_recovery
from solver.redaction import Redactor
from solver.submission.epoch import SubmissionEpochAuthority
from solver.supervisor import Supervisor
from solver.supervisor_process import SpawnedBoot
from solver.supervisor_services import SupervisorServices
from solver.storage_governor import StorageGovernor
from solver.storage_governor_contracts import (
    ReachabilityRoots,
    RetirementCandidate,
    StorageCapacity,
    StorageGovernorProfile,
)
from solver.recovery.storage import recover_storage_pressure
from solver.write_reservation import Capacity, EffectIdentity, RetentionPolicy

FAULTS = (
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
    (FaultKind.SUBMISSION_AMBIGUITY, "run-shared:submission", "submission-epoch", "epoch-5", "epoch-6"),
    (FaultKind.STORAGE, "run-shared:storage", "storage-revision", "revision-9", "revision-10"),
    (
        FaultKind.FINAL_INTERVAL,
        "run-shared:final-interval",
        "final-interval-phase",
        "draining",
        "reconciling",
    ),
)
NOW = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)
DEADLINE = NOW + dt.timedelta(seconds=180)


def _receipt(result, destination: Path, name: str) -> dict[str, object]:
    document = json.loads(result.receipt_path.read_bytes())
    atomic_write(destination / name, canonical_bytes(document) + b"\n")
    return document


def _changed(root: Path, destination: Path) -> list[dict[str, object]]:
    rows = []
    for kind, scope, dimension, before, after in FAULTS:
        if kind in {FaultKind.WORKER_CRASH, FaultKind.TARGET_RESEARCH, FaultKind.FINAL_INTERVAL}:
            continue
        if kind is FaultKind.ROUTE_LOCAL_INFERENCE:
            result, effects = _changed_route(root)
        elif kind is FaultKind.INSTANCE:
            result, effects = _changed_instance(root)
        elif kind is FaultKind.SUBMISSION_AMBIGUITY:
            result, effects = _changed_submission(root)
        elif kind is FaultKind.STORAGE:
            result, effects = _changed_storage(root)
        receipt_name = f"changed.{kind.value}.receipt.json"
        receipt = _receipt(result, destination, receipt_name)
        rows.append(
            {
                "after": receipt["changed_action"]["after"],
                "before": receipt["changed_action"]["before"],
                "dimension": receipt["changed_action"]["dimension"],
                "effect_count": len(effects),
                "final_outcome": receipt["final_outcome"],
                "kind": kind.value,
                "receipt": receipt_name,
                "source": receipt["changed_action"]["source"],
            }
        )
    return rows


def _changed_route(root):
    run_id = "runtime-qualification-297-changed-route-local-inference"
    recorder = Recorder(root, run_id, Redactor({}))
    recovery = DeterministicRecovery(root, run_id, Redactor({}), now=lambda: NOW, authority=recorder.write_authority)
    calls = []

    def native():
        calls.append("native-codex")
        raise RouteTransportFailure.classified("timeout", InferenceRoute.NATIVE, "a" * 64)

    def cpa():
        calls.append("private-cpa")
        return {"text": "sanitized answer"}

    RouteAndQuotaController(
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE),
        authority=recorder.write_authority,
        recovery=recovery,
        now=lambda: NOW,
    ).execute(
        request_id="request-1",
        generation_id="generation-1",
        payload_digest="b" * 64,
        observations=(),
        transports={InferenceRoute.NATIVE: native, InferenceRoute.CPA: cpa},
        encode=lambda value: value,
        decode=dict,
    )
    path = root / "runs" / run_id / "canonical" / "incident-containment.receipt.json"
    return type("Result", (), {"receipt_path": path})(), calls[1:]


def _changed_instance(root):
    effects = []
    observations = iter(("join-2", "join-3"))

    def observe():
        join = next(observations)
        return InstanceObservation(
            join,
            lambda: (
                effects.append(join)
                or ReconciliationResult("boot-3", "snapshot-3", join, (), (), AdmissionVerdict.OPEN)
            ),
            join.encode(),
        )

    runtime = DeterministicRecovery(root, "runtime-qualification-297-changed-instance", Redactor({}), now=lambda: NOW)
    runtime.handle(
        kind=FaultKind.INSTANCE,
        fault_id="instance:changed",
        scope="external:instance",
        generation_id="all-active",
        evidence="sanitized Instance join",
        failed_action_value="join-2",
        original_deadline=DEADLINE,
        recovery=instance_recovery("join-2", observe),
    )
    registry = RecoveryRegistry()
    registry.register(INSTANCE_ADAPTER, lambda config: instance_recovery(config["failed_join_digest"], observe))
    return runtime.replay(registry)[0], effects


def _changed_submission(root):
    run_id = "runtime-qualification-297-changed-submission-ambiguity"
    recorder = Recorder(root, run_id, Redactor({}))
    epochs = SubmissionEpochAuthority(recorder.event_store, lambda: NOW.isoformat())
    epochs.ensure("board-1")
    effect = "closed-effect-1"
    fence = type("ClosedFence", (), {"closed_effect_at_epoch": lambda _self, _epoch: effect})()
    runtime = DeterministicRecovery(root, run_id, Redactor({}), now=lambda: NOW, authority=recorder.write_authority)

    def domain():
        return submission_recovery(
            pending_effect=effect,
            closed_epoch=1,
            board_identity="board-1",
            epochs=epochs,
            fence=fence,
            authority=recorder.write_authority,
        )

    runtime.handle(
        kind=FaultKind.SUBMISSION_AMBIGUITY,
        fault_id="submission:changed",
        scope="run-shared:submission",
        generation_id="submission-reconciliation",
        evidence="sanitized closed ambiguity",
        failed_action_value="epoch-1",
        original_deadline=DEADLINE,
        recovery=domain(),
    )
    successor = recorder.write_authority.reserve(
        "qualification-successor",
        EffectIdentity("board.submit-candidate", "7", "successor-epoch-2"),
        Capacity(1024, 1, 3),
        retention=RetentionPolicy.RECORD,
    )
    recorder.write_authority.commit(recorder.write_authority.start(successor), {"submission_epoch": 2})
    registry = RecoveryRegistry()
    registry.register(SUBMISSION_ADAPTER, lambda _config: domain())
    result = runtime.replay(registry)[0]
    return result, ["epoch-2"]


def _changed_storage(root):
    run_id = "runtime-qualification-297-changed-storage"

    def capacity(value):
        return StorageCapacity(value, value, value, value, value, value, value)

    profile = StorageGovernorProfile(
        capacity(10_000),
        capacity(9_000),
        capacity(8_000),
        capacity(7_000),
        capacity(6_000),
        capacity(500),
        capacity(500),
    )
    store = EventStore(root, run_id=run_id)
    sealed = store.append(ObservationRecorded("attempt-1", 1, "printf output", "printf output", "bash"), body=b"retire")
    candidate = RetirementCandidate(
        f"sealed/sha256/{sealed.blob_digest}",
        "raw-observation",
        sealed.blob_digest,
        sealed.blob_bytes,
        (sealed.sequence,),
    )
    governor = StorageGovernor(root, run_id, profile)
    governor.classify(candidate)
    result = recover_storage_pressure(
        governor,
        governor.recovery_composition(root, Redactor({}), now=lambda: NOW),
        (candidate,),
        ReachabilityRoots(),
        failed_revision="revision-9",
        reason="sanitized-pressure",
        now=lambda: NOW,
    )
    return result, ["revision-10"]


def _no_inference(root: Path, destination: Path) -> list[dict[str, object]]:
    rows = []
    for kind, scope, dimension, before, _after in FAULTS:
        effects = []
        runtime = DeterministicRecovery(
            root, f"runtime-qualification-297-unsettled-{kind.value}", Redactor({}), now=lambda: NOW
        )
        if kind is FaultKind.TARGET_RESEARCH:
            SafeReadRecovery(runtime).contain(
                SafeReadFault(
                    "target:unsettled",
                    scope,
                    "generation-7",
                    "sanitized unsettled observation",
                    before,
                    NOW,
                )
            )
            result = type(
                "Result",
                (),
                {
                    "receipt_path": root
                    / "runs"
                    / f"runtime-qualification-297-unsettled-{kind.value}"
                    / "canonical"
                    / "incident-containment.receipt.json"
                },
            )()
        else:
            result = runtime.handle(
                kind=kind,
                fault_id=f"{kind.value}:unsettled",
                scope=scope,
                generation_id="generation-7",
                evidence="sanitized unsettled observation",
                failed_action_value=before,
                original_deadline=DEADLINE,
                recovery=DomainRecovery(
                    dimension,
                    lambda: None,
                    lambda: effects.append("forbidden") is None,
                    lambda: ProbationOutcome.UNSETTLED,
                ),
            )
        receipt_name = f"unsettled.{kind.value}.receipt.json"
        receipt = _receipt(result, destination, receipt_name)
        rows.append(
            {
                "authority_state": receipt["authority_state"],
                "effect_count": len(effects),
                "final_outcome": receipt["final_outcome"],
                "kind": kind.value,
                "receipt": receipt_name,
            }
        )
    return rows


def _supervisor_cross_boot(root: Path, destination: Path, *, label: str, replacement_exit: int):
    run_id = f"runtime-qualification-297-supervisor-{label}"
    exits = iter((1, replacement_exit))
    launches = []

    def launch(boot_id):
        launches.append(boot_id)
        return SpawnedBoot(
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "from pathlib import Path; import sys; from solver.record import Recorder; "
                    "from solver.redaction import Redactor; "
                    "recorder = Recorder(Path(sys.argv[1]), sys.argv[2], Redactor({})); "
                    "recorder.write_authority.close(); sys.exit(int(sys.argv[3]))",
                    str(root),
                    run_id,
                    str(next(exits)),
                ],
                start_new_session=True,
            )
        )

    class Custody:
        def close(self):
            return None

    def services():
        return SupervisorServices(
            verify_replay=lambda: None,
            admit_storage=lambda: None,
            preflight_isolation=lambda: None,
            bootstrap_custody=lambda _boot: Custody(),
            launch_controller=launch,
            reap_children=lambda: 0,
        )

    original = os.fsync
    interrupted = [False]
    events = root / "runs" / run_id / "canonical" / "events.jsonl"

    def crash(descriptor):
        original(descriptor)
        if not interrupted[0] and events.exists():
            rows = events.read_bytes().splitlines()
            if rows and "fixed-probe" in json.loads(rows[-1]).get("payload", {}).get("steps", ()):
                interrupted[0] = True
                raise RuntimeError("controlled probe loss")

    with patch.object(os, "fsync", crash):
        try:
            Supervisor(state=root, run_id=run_id, redactor=Redactor({}), services=services()).run()
        except RuntimeError as error:
            if str(error) != "controlled probe loss":
                raise
    Supervisor(state=root, run_id=run_id, redactor=Redactor({}), services=services()).run()
    source = root / "runs" / run_id / "canonical" / "incident-containment.receipt.json"
    receipt_name = f"replay.{label}.receipt.json"
    receipt = _receipt(type("Result", (), {"receipt_path": source})(), destination, receipt_name)
    return {
        "allowance": receipt["allowance"],
        "consumed_allowance": receipt["consumed_allowance"],
        "effect_count": len(launches) - 1,
        "final_outcome": receipt["final_outcome"],
        "kind": receipt["fault_kind"],
        "original_deadline": receipt["original_deadline"],
        "probation_outcome": receipt["probation_outcome"],
        "receipt": receipt_name,
        "replay_count": receipt["replay_count"],
    }


def _route_cross_boot(root: Path, destination: Path):
    run_id = "runtime-qualification-297-route-boot"
    recorder = Recorder(root, run_id, Redactor({}))
    recovery = DeterministicRecovery(root, run_id, Redactor({}), now=lambda: NOW, authority=recorder.write_authority)
    calls = []

    def native():
        calls.append("native")
        raise RouteTransportFailure.classified("timeout", InferenceRoute.NATIVE, "a" * 64)

    def cpa():
        calls.append("cpa")
        return {"text": "sanitized answer"}

    original = os.fsync
    interrupted = [True]
    events = root / "runs" / run_id / "canonical" / "events.jsonl"

    def crash(descriptor):
        original(descriptor)
        if interrupted[0] and events.exists():
            rows = events.read_bytes().splitlines()
            if rows:
                row = json.loads(rows[-1]).get("payload", {})
                if "changed-remedy" in row.get("completed_steps", ()) and not row.get("terminal"):
                    interrupted[0] = False
                    raise RuntimeError("controlled route probation loss")

    with patch.object(os, "fsync", crash):
        controller = RouteAndQuotaController(
            RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE),
            authority=recorder.write_authority,
            recovery=recovery,
            now=lambda: NOW,
        )
        try:
            controller.execute(
                request_id="request-boot",
                generation_id="generation-boot",
                payload_digest="b" * 64,
                observations=(),
                transports={InferenceRoute.NATIVE: native, InferenceRoute.CPA: cpa},
                encode=lambda value: value,
                decode=dict,
            )
        except RuntimeError as error:
            if str(error) != "controlled route probation loss":
                raise
        RouteAndQuotaController(
            RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE),
            authority=recorder.write_authority,
            recovery=recovery,
            now=lambda: NOW,
        )
    source = root / "runs" / run_id / "canonical" / "incident-containment.receipt.json"
    receipt_name = "replay.route.receipt.json"
    receipt = _receipt(type("Result", (), {"receipt_path": source})(), destination, receipt_name)
    recorder.write_authority.close()
    return {
        "allowance": receipt["allowance"],
        "consumed_allowance": receipt["consumed_allowance"],
        "effect_count": calls.count("cpa"),
        "final_outcome": receipt["final_outcome"],
        "kind": receipt["fault_kind"],
        "original_deadline": receipt["original_deadline"],
        "probation_outcome": receipt["probation_outcome"],
        "receipt": receipt_name,
        "replay_count": receipt["replay_count"],
    }


def _expired(root: Path, destination: Path) -> dict[str, object]:
    run_id = "runtime-qualification-297-replay-expired"
    first = DeterministicRecovery(root, run_id, Redactor({}), now=lambda: NOW)
    try:
        first.handle(
            kind=FaultKind.STORAGE,
            fault_id="storage:expired-replay",
            scope="run-shared:storage",
            generation_id="storage-governor",
            evidence="sanitized controlled pressure",
            failed_action_value="revision-9",
            original_deadline=DEADLINE,
            recovery=DomainRecovery(
                "storage-revision",
                lambda: (_ for _ in ()).throw(RuntimeError("controlled probe loss")),
                lambda: True,
                lambda: ProbationOutcome.PASSED,
            ),
        )
    except RuntimeError as error:
        if str(error) != "controlled probe loss":
            raise
    effects = []
    after = DeterministicRecovery(root, run_id, Redactor({}), now=lambda: DEADLINE + dt.timedelta(seconds=1))
    registry = RecoveryRegistry()
    registry.register(
        "storage:run-shared:storage:v1",
        lambda _config: DomainRecovery(
            "storage-revision",
            lambda: AuthoritativeChange("revision-9", "revision-10", "canonical-storage-authority"),
            lambda: effects.append("forbidden") is None,
            lambda: ProbationOutcome.PASSED,
        ),
    )
    result = after.replay(registry)[0]
    receipt_name = "replay.expired.receipt.json"
    receipt = _receipt(result, destination, receipt_name)
    return {
        "consumed_allowance": receipt["consumed_allowance"],
        "effect_count": len(effects),
        "final_outcome": receipt["final_outcome"],
        "original_deadline": receipt["original_deadline"],
        "receipt": receipt_name,
        "replay_count": receipt["replay_count"],
    }


def _expired_probation(root: Path, destination: Path):
    run_id = "runtime-qualification-297-expired-probation"
    clock = [NOW]
    effects = []
    recovery = DeterministicRecovery(root, run_id, Redactor({}), now=lambda: clock[0])
    domain = DomainRecovery(
        "process-generation",
        lambda: AuthoritativeChange("boot-1", "boot-2", "supervisor-lifecycle"),
        lambda: effects.append("boot-2") is None,
        lambda: ProbationOutcome.UNSETTLED,
        adapter_id="bounded-probation-fixture",
    )
    recovery.handle(
        kind=FaultKind.WORKER_CRASH,
        fault_id="process:unsettled-probation",
        scope="owner-local:worker",
        generation_id="generation-7",
        evidence="controlled unresolved probation",
        failed_action_value="boot-1",
        original_deadline=DEADLINE,
        recovery=domain,
    )
    clock[0] = DEADLINE
    registry = RecoveryRegistry()
    registry.register(domain.adapter_id, lambda _config: domain)
    result = recovery.replay(registry)[0]
    receipt_name = "replay.expired-probation.receipt.json"
    receipt = _receipt(result, destination, receipt_name)
    return {
        "effect_count": len(effects),
        "consumed_allowance": receipt["consumed_allowance"],
        "final_outcome": receipt["final_outcome"],
        "original_deadline": receipt["original_deadline"],
        "receipt": receipt_name,
        "replay_count": receipt["replay_count"],
    }


def qualify(root: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    success = _supervisor_cross_boot(root, destination, label="success", replacement_exit=0)
    failure = _supervisor_cross_boot(root, destination, label="failure", replacement_exit=1)
    trace = destination / "deterministic-recovery.trace.json"
    atomic_write(
        trace,
        canonical_bytes(
            {
                "catalogue_version": CATALOGUE_VERSION,
                "changed_actions": _changed(root, destination),
                "cross_boot": [success, failure, _route_cross_boot(root, destination)],
                "expired_bound": _expired(root, destination),
                "expired_probation": _expired_probation(root, destination),
                "no_inference": _no_inference(root, destination),
                "schema_version": 1,
            }
        )
        + b"\n",
    )
    return trace


__all__ = ["qualify"]
