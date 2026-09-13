"""Incident containment is durable, coalesced, replayable, and inference-free."""

import json
import subprocess
import sys
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from solver.recovery.contracts import FaultKind, IncidentRecorded
from solver.recovery.incident import Fault, IncidentEngine, ModelRemedyRejected, Remedy, verify_receipt
from solver.recovery.supervisor import WorkerSupervisor
from solver.redaction import Redactor
from solver.supervisor_process import SpawnedBoot
from solver.supervisor import Supervisor
from solver.supervisor_services import SupervisorServices
from solver.work_generation import GenerationFence
from solver.write_reservation import WriteAuthority
from solver.write_reservation_contracts import DEFAULT_WRITE_PROFILE, ReservationState

RETAINED = (
    Path(__file__).parent.parent
    / "docs/evidence/runtime-qualification-v2/296-incident-containment/incident-containment.receipt.json"
)


class Ports:
    def __init__(self, fail_after=None):
        self.trace = []
        self.fail_after = fail_after

    def _do(self, name):
        self.trace.append(name)
        if self.fail_after == name:
            raise RuntimeError("injected containment crash")

    def fence(self, _fault):
        self._do("generation-fence")

    def evidence(self, _fault):
        self._do("evidence-capture")
        return b"worker token-secret crashed"

    def teardown(self, _fault):
        self._do("full-teardown")

    def replace(self, _fault):
        self._do("bounded-replacement")
        return True


def fault():
    return Fault("worker-exit:17", "worker", "generation-7", "exit=17")


def _report_from_process(state, ready, start, results):
    ready.put(True)
    start.wait()
    result = IncidentEngine(Path(state), "run-processes", Ports(), Redactor({})).report(fault())
    results.put(result.incident_id)


def test_worker_crash_is_contained_once_and_writes_a_sanitized_verifiable_receipt(tmp_path):
    ports = Ports()
    engine = IncidentEngine(tmp_path, "run-1", ports, Redactor({"CTFD_API_TOKEN": "token-secret"}))

    first = engine.report(fault())
    duplicate = engine.report(fault())

    assert duplicate.incident_id == first.incident_id == "incident-000001"
    assert ports.trace == ["generation-fence", "evidence-capture", "full-teardown", "bounded-replacement"]
    receipt = json.loads(first.receipt_path.read_text())
    assert receipt["trace"] == ports.trace
    assert receipt["duplicate_reports"] == 1
    assert "token-secret" not in first.receipt_path.read_text()
    assert receipt["disposition"] == "replacement-admitted"
    assert verify_receipt(first.receipt_path) == first.receipt_path
    authority = WriteAuthority(tmp_path / "runs/run-1", DEFAULT_WRITE_PROFILE)
    assert authority.reservations()[0].state is ReservationState.COMMITTED
    authority.close()


def test_containment_crash_replays_same_incident_without_refencing_authority(tmp_path):
    crashing = Ports(fail_after="evidence-capture")
    engine = IncidentEngine(tmp_path, "run-1", crashing, Redactor({}))
    with pytest.raises(RuntimeError, match="injected"):
        engine.report(fault())

    replay = Ports()
    result = IncidentEngine(tmp_path, "run-1", replay, Redactor({})).replay()

    assert result[0].incident_id == "incident-000001"
    assert replay.trace == ["evidence-capture", "full-teardown", "bounded-replacement"]
    assert json.loads(result[0].receipt_path.read_text())["replay_count"] == 1


def test_crash_after_replacement_admission_refuses_a_second_launch(tmp_path):
    crashing = Ports(fail_after="bounded-replacement")
    with pytest.raises(RuntimeError, match="injected"):
        IncidentEngine(tmp_path, "run-1", crashing, Redactor({})).report(fault())

    replay = Ports()
    result = IncidentEngine(tmp_path, "run-1", replay, Redactor({})).replay()[0]

    assert result.disposition == "replacement-refused"
    assert "bounded-replacement" not in replay.trace
    authority = WriteAuthority(tmp_path / "runs/run-1", DEFAULT_WRITE_PROFILE)
    assert authority.reservations()[0].state is ReservationState.TERMINAL
    authority.close()


def test_core_recovery_has_fixed_no_inference_authority(tmp_path):
    engine = IncidentEngine(tmp_path, "run-1", Ports(), Redactor({}))
    assert engine.remedy == Remedy.REPLACE_ONCE
    with pytest.raises(ModelRemedyRejected):
        engine.accept_model_remedy("retry with more privileges")


def test_concurrent_engines_coalesce_one_scope_recovery(tmp_path):
    ports = Ports()

    def report(_index):
        return IncidentEngine(tmp_path, "run-1", ports, Redactor({})).report(fault()).incident_id

    with ThreadPoolExecutor(max_workers=8) as workers:
        incident_ids = list(workers.map(report, range(16)))

    assert incident_ids == ["incident-000001"] * 16
    assert ports.trace == ["generation-fence", "evidence-capture", "full-teardown", "bounded-replacement"]


def test_independent_processes_coalesce_one_scope_recovery(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [context.Process(target=_report_from_process, args=(tmp_path, ready, start, results)) for _ in range(6)]
    for process in processes:
        process.start()
    for _ in processes:
        ready.get(timeout=10)
    start.set()
    incident_ids = [results.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(timeout=20)
        assert process.exitcode == 0

    assert incident_ids == ["incident-000001"] * 6
    receipt = json.loads((tmp_path / "runs/run-processes/canonical/incident-containment.receipt.json").read_text())
    assert receipt["duplicate_reports"] == 5


@pytest.mark.parametrize("kind", [FaultKind.UNCLASSIFIED, FaultKind.AMBIGUOUS])
def test_uncertain_fault_has_no_replacement_authority(tmp_path, kind):
    ports = Ports()
    uncertain = Fault("unknown", "run-controller", "all-active", "insufficient evidence", kind)

    result = IncidentEngine(tmp_path, "run-1", ports, Redactor({})).report(uncertain)

    assert result.disposition == "replacement-refused"
    assert ports.trace == []
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["fault_kind"] == kind.value
    assert receipt["reason"] == f"{kind.value}-fault-refused"
    assert receipt["authority_state"] == "aborted"
    assert verify_receipt(result.receipt_path) == result.receipt_path


def test_incident_contract_rejects_invalid_domain_values_and_counts():
    valid = IncidentRecorded(
        event_id="event",
        incident_id="incident-1",
        fault_identity="a" * 64,
        fault_id="fault",
        generation_id="generation-1",
        scope="worker",
        fault_kind="worker-crash",
        reason="classified-worker-process-crash",
        authority_state="committed",
        disposition="replacement-admitted",
        steps=("generation-fence",),
        completed_steps=("generation-fence",),
        duplicate_reports=0,
        replay_count=0,
        terminal=True,
    ).payload(blob_digest="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", blob_bytes=0)
    for field, value in (
        ("fault_kind", "invented"),
        ("disposition", "done"),
        ("steps", [7]),
        ("duplicate_reports", True),
        ("replay_count", -1),
        ("fault_identity", "bad"),
    ):
        damaged = dict(valid)
        damaged[field] = value
        with pytest.raises(ValueError):
            IncidentRecorded.validate_payload(damaged, sequence=1)


def test_real_worker_crash_is_contained_before_one_bounded_replacement(tmp_path):
    ports = Ports()
    engine = IncidentEngine(tmp_path, "run-real-recovery", ports, Redactor({}))
    launches = []

    def launch(_worker_id):
        code = 17 if not launches else 0
        process = subprocess.Popen([sys.executable, "-c", f"raise SystemExit({code})"], start_new_session=True)
        launches.append(process.pid)
        return SpawnedBoot(process)

    result = WorkerSupervisor(engine, launch, lambda: 0, lambda: "generation-7", grace_seconds=0.05).run()

    assert result.boots == 2
    assert result.exit_code == 0
    assert ports.trace == ["generation-fence", "evidence-capture", "full-teardown", "bounded-replacement"]


def test_production_supervisor_fences_real_crash_before_one_real_replacement(tmp_path):
    fence = GenerationFence(tmp_path, "run-production", Redactor({}), timestamp=lambda: "2026-09-13T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    launches = []

    def launch(_boot_id):
        code = 17 if not launches else 0
        process = subprocess.Popen([sys.executable, "-c", f"raise SystemExit({code})"], start_new_session=True)
        launches.append(process.pid)
        return SpawnedBoot(process)

    class Custody:
        def close(self):
            pass

    services = SupervisorServices(
        verify_replay=lambda: None,
        admit_storage=lambda: None,
        preflight_isolation=lambda: None,
        bootstrap_custody=lambda _boot_id: Custody(),
        launch_controller=launch,
        reap_children=lambda: 0,
    )
    result = Supervisor(
        state=tmp_path,
        run_id="run-production",
        redactor=Redactor({}),
        services=services,
        grace_seconds=0.05,
    ).run()

    assert result.disposition == "normal"
    assert len(launches) == 2
    state = next(item for item in fence.projection().generations if item.generation_id == generation.generation_id)
    assert state.active is False
    receipt = json.loads((tmp_path / "runs/run-production/canonical/incident-containment.receipt.json").read_text())
    assert receipt["disposition"] == "replacement-admitted"
    assert json.loads(receipt["evidence"]["projection"])["exit_code"] == 17


def test_receipt_verifier_rejects_a_false_containment_order(tmp_path):
    result = IncidentEngine(tmp_path, "run-1", Ports(), Redactor({})).report(fault())
    receipt = json.loads(result.receipt_path.read_text())
    receipt["trace"].reverse()
    unsigned = dict(receipt)
    unsigned.pop("receipt_digest")
    from solver.event_store_storage import canonical_bytes, digest_bytes

    receipt["receipt_digest"] = digest_bytes(canonical_bytes(unsigned))
    result.receipt_path.write_bytes(canonical_bytes(receipt) + b"\n")

    with pytest.raises(ValueError, match="order"):
        verify_receipt(result.receipt_path)


def test_retained_real_process_crash_and_replay_receipt_verifies():
    assert verify_receipt(RETAINED) == RETAINED
    receipt = json.loads(RETAINED.read_text())
    assert receipt["replay_count"] > 0
    assert receipt["disposition"] == "replacement-admitted"
