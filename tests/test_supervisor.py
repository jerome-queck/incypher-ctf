"""The PID-1 Supervisor owns one Run across its durably identified Boots."""

import copy
import json
import signal
import subprocess
import sys

import pytest

from solver import supervisor as supervisor_module
from solver.event_store import EventStore, LifecycleRecorded
from solver.event_store_contracts import BootOpened, RunOpened, TerminalDisposition
from solver.manifest import generate_manifest
from solver.redaction import Redactor
from solver.supervisor import (
    CRASHED,
    INTERRUPTED,
    NORMAL,
    REFUSED,
    RunAlreadySupervised,
    Supervisor,
    SupervisorLock,
)
from solver.supervisor_lifecycle import manifest_receipt, verify_receipt
from solver.supervisor_process import SpawnedBoot
from solver.supervisor_services import SupervisorServices
from test_manifest import release_candidate_profile


class ExitedBoot:
    """One already-finished Boot process at the process-control seam."""

    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code

    def wait(self, _timeout: float | None = None) -> int:
        return self.exit_code

    def forward(self, _signal_number: int) -> None:
        raise AssertionError("a normal Boot receives no signal")

    def kill(self) -> None:
        raise AssertionError("a normal Boot is not killed")

    def group_alive(self) -> bool:
        return False


class EmptyCustody:
    def close(self) -> None:
        pass


def services(*, verify=lambda: None, admit=lambda: None, isolate=lambda: None, launch, reap=lambda: 0):
    return SupervisorServices(
        verify_replay=verify,
        admit_storage=admit,
        preflight_isolation=isolate,
        bootstrap_custody=lambda _boot_id: EmptyCustody(),
        launch_controller=launch,
        reap_children=reap,
    )


def test_replay_and_storage_admission_precede_the_effect_capable_boot(tmp_path):
    trace = []

    def verify() -> None:
        trace.append("verified-replay")

    def admit() -> None:
        trace.append("storage-admission")

    def isolate() -> None:
        trace.append("strict-isolation")

    def launch(boot_id: str) -> ExitedBoot:
        trace.append(f"run-controller:{boot_id}")
        return ExitedBoot(0)

    result = Supervisor(
        state=tmp_path,
        run_id="run-1",
        redactor=Redactor({}),
        services=services(verify=verify, admit=admit, isolate=isolate, launch=launch),
    ).run()

    assert trace == ["verified-replay", "storage-admission", "strict-isolation", "run-controller:boot-000001"]
    assert result.run_id == "run-1"
    assert result.boot_id == "boot-000001"
    assert result.disposition == NORMAL


def test_one_normal_boot_leaves_canonical_lifecycle_and_a_verifiable_receipt(tmp_path):
    result = Supervisor(
        state=tmp_path,
        run_id="run-1",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: ExitedBoot(0), reap=lambda: 2),
    ).run()

    events = EventStore(tmp_path, run_id="run-1").events()
    lifecycle = [event.payload for event in events if event.event_type == "lifecycle.recorded"]
    assert [event["record"] for event in lifecycle] == [
        "run-open",
        "service-started",
        "service-started",
        "service-started",
        "boot-open",
        "service-started",
        "service-started",
        "child-reaped",
        "boot-close",
        "run-close",
    ]
    assert [event["service"] for event in lifecycle if event["record"] == "service-started"] == [
        "verified-replay",
        "storage-admission",
        "strict-isolation",
        "credential-custody",
        "run-controller",
    ]
    assert lifecycle[-3]["reaped_children"] == 3
    assert lifecycle[-2]["disposition"] == NORMAL
    assert lifecycle[-1]["disposition"] == NORMAL

    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["receipt_type"] == "supervisor-lifecycle"
    assert receipt["boot_ids"] == ["boot-000001"]
    assert receipt["service_order"] == [
        "verified-replay",
        "storage-admission",
        "strict-isolation",
        "credential-custody",
        "run-controller",
    ]
    assert receipt["terminal_disposition"] == NORMAL
    assert receipt["child_reap_result"] == {"reaped_children": 3}
    assert receipt["manifest_link"] == {
        "row_id": "core.supervisor",
        "receipt_ref": "receipt:supervisor-lifecycle",
    }
    assert verify_receipt(result.receipt_path) == result.receipt_path


def test_restart_closes_the_unfinished_boot_before_opening_one_new_identity(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    store.append(_lifecycle("run:open", "run-open"), body=b"")
    store.append(_lifecycle("boot-000001:open", "boot-open", boot_id="boot-000001"), body=b"")

    result = Supervisor(
        state=tmp_path,
        run_id="run-1",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: ExitedBoot(0)),
    ).run()

    lifecycle = [
        event.payload
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.event_type == "lifecycle.recorded"
    ]
    assert result.boot_id == "boot-000002"
    assert len([event for event in lifecycle if event["record"] == "run-open"]) == 1
    assert [event["boot_id"] for event in lifecycle if event["record"] == "boot-open"] == [
        "boot-000001",
        "boot-000002",
    ]
    prior_close = next(
        event for event in lifecycle if event["record"] == "boot-close" and event["boot_id"] == "boot-000001"
    )
    assert prior_close["disposition"] == "crashed"
    assert prior_close["detail"] == "reconciled-after-supervisor-loss"


def test_a_second_supervisor_cannot_overlap_the_same_run(tmp_path):
    with SupervisorLock(tmp_path, "run-1"):
        with pytest.raises(RunAlreadySupervised):
            Supervisor(
                state=tmp_path,
                run_id="run-1",
                redactor=Redactor({}),
                services=services(launch=lambda _boot_id: ExitedBoot(0)),
            ).run()

    assert EventStore(tmp_path, run_id="run-1").events() == []


@pytest.mark.parametrize(
    ("exit_code", "disposition"),
    [(0, NORMAL), (2, REFUSED), (1, CRASHED)],
)
def test_boot_exit_is_classified_into_one_terminal_run_disposition(tmp_path, exit_code, disposition):
    result = Supervisor(
        state=tmp_path,
        run_id=f"run-{exit_code}",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: ExitedBoot(exit_code)),
    ).run()

    lifecycle = [
        event.payload
        for event in EventStore(tmp_path, run_id=f"run-{exit_code}").events()
        if event.event_type == "lifecycle.recorded"
    ]
    assert result.disposition == disposition
    assert [event["disposition"] for event in lifecycle if event["record"] == "run-close"] == [disposition]


class StopsOnSignal:
    def __init__(self) -> None:
        self.supervisor = None
        self.forwarded = []

    def wait(self, _timeout: float | None = None) -> int:
        self.supervisor.request_stop(signal.SIGTERM)
        return 0

    def forward(self, signal_number: int) -> None:
        self.forwarded.append(signal_number)

    def kill(self) -> None:
        raise AssertionError("the graceful Boot does not need killing")

    def group_alive(self) -> bool:
        return False


def test_term_is_forwarded_and_a_graceful_boot_is_classified_interrupted(tmp_path):
    boot = StopsOnSignal()
    supervisor = Supervisor(
        state=tmp_path,
        run_id="run-signal",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: boot),
    )
    boot.supervisor = supervisor

    result = supervisor.run()

    assert boot.forwarded == [signal.SIGTERM]
    assert result.disposition == INTERRUPTED
    assert json.loads(result.receipt_path.read_text())["signal_trace"] == ["SIGTERM"]


class IgnoresSignal:
    def __init__(self) -> None:
        self.supervisor = None
        self.forwarded = []
        self.killed = False

    def wait(self, _timeout: float | None = None) -> int:
        if self.killed:
            return -signal.SIGKILL
        self.supervisor.request_stop(signal.SIGINT)
        raise TimeoutError

    def forward(self, signal_number: int) -> None:
        self.forwarded.append(signal_number)

    def kill(self) -> None:
        self.killed = True

    def group_alive(self) -> bool:
        return not self.killed


def test_signal_grace_is_bounded_then_the_boot_is_killed_and_reaped(tmp_path):
    times = iter([0.0, 2.0])
    boot = IgnoresSignal()
    supervisor = Supervisor(
        state=tmp_path,
        run_id="run-killed",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: boot, reap=lambda: 1),
        monotonic=lambda: next(times),
        grace_seconds=1.0,
    )
    boot.supervisor = supervisor

    result = supervisor.run()

    assert boot.forwarded == [signal.SIGINT]
    assert boot.killed is True
    assert result.disposition == INTERRUPTED
    assert json.loads(result.receipt_path.read_text())["child_reap_result"] == {"reaped_children": 2}


class FailsWhileDescendantLives:
    def __init__(self) -> None:
        self.killed = False
        self.waited_after_kill = False

    def wait(self, _timeout: float | None = None) -> int:
        if not self.killed:
            raise RuntimeError("lost controller wait")
        self.waited_after_kill = True
        return -signal.SIGKILL

    def forward(self, _signal_number: int) -> None:
        pass

    def kill(self) -> None:
        self.killed = True

    def group_alive(self) -> bool:
        return not self.killed


def test_wait_failure_extinguishes_and_reaps_the_boot_group_before_terminal_state(tmp_path):
    boot = FailsWhileDescendantLives()

    result = Supervisor(
        state=tmp_path,
        run_id="run-wait-failure",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: boot),
        grace_seconds=0,
    ).run()

    assert boot.killed is True
    assert boot.waited_after_kill is True
    assert result.disposition == CRASHED


@pytest.mark.skipif(
    sys.platform == "darwin" or not hasattr(__import__("os"), "fork"),
    reason="requires Linux container process-group semantics",
)
def test_a_real_lingering_boot_descendant_is_gone_before_terminal_state(tmp_path):
    spawned = None

    def launch(_boot_id: str):
        nonlocal spawned
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import os,time; pid=os.fork(); time.sleep(30) if pid == 0 else os._exit(0)",
            ],
            start_new_session=True,
        )
        spawned = SpawnedBoot(process)
        return spawned

    result = Supervisor(
        state=tmp_path,
        run_id="run-real-descendant",
        redactor=Redactor({}),
        services=services(launch=launch),
        grace_seconds=0.05,
    ).run()

    assert result.disposition == NORMAL
    assert spawned is not None
    assert spawned.group_alive() is False


class StopBeforeLaunch(ExitedBoot):
    def __init__(self) -> None:
        super().__init__(0)
        self.forwarded = []

    def forward(self, signal_number: int) -> None:
        self.forwarded.append(signal_number)


def test_a_signal_latched_during_launch_is_recorded_and_forwarded(tmp_path):
    boot = StopBeforeLaunch()
    supervisor = Supervisor(
        state=tmp_path,
        run_id="run-launch-signal",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: supervisor.request_stop(signal.SIGTERM) or boot),
    )

    result = supervisor.run()

    assert boot.forwarded == [signal.SIGTERM]
    assert result.disposition == INTERRUPTED
    assert json.loads(result.receipt_path.read_text())["signal_trace"] == ["SIGTERM"]


def test_pid_one_main_composes_the_run_controller_behind_pre_authority_checks(tmp_path, monkeypatch, capsys):
    trace = []

    class FakeCustody:
        def __init__(self, **_options) -> None:
            pass

        def open(self, boot_id: str) -> EmptyCustody:
            trace.append(f"credential-custody:{boot_id}")
            return EmptyCustody()

    monkeypatch.setattr(
        supervisor_module,
        "verify_and_materialize_run_state",
        lambda *_args: trace.append("verified-replay"),
    )
    monkeypatch.setattr(
        supervisor_module,
        "admit_storage",
        lambda *_args: trace.append("storage-admission"),
    )
    monkeypatch.setattr(
        supervisor_module,
        "preflight_isolation",
        lambda *_args: trace.append("strict-isolation"),
    )
    monkeypatch.setattr(
        supervisor_module,
        "launch_boot",
        lambda boot_id, _environ: trace.append(f"run-controller:{boot_id}") or ExitedBoot(0),
    )
    monkeypatch.setattr(supervisor_module, "reap_children", lambda: 0)
    monkeypatch.setattr(supervisor_module, "SupervisorCustody", FakeCustody)

    exit_code = supervisor_module.main({"RUN_ID": "run-main"}, state=tmp_path, stay_quiescent=False)

    assert exit_code == 0
    assert trace == [
        "verified-replay",
        "storage-admission",
        "strict-isolation",
        "credential-custody:boot-000001",
        "run-controller:boot-000001",
    ]
    assert "normal: Run run-main, Boot boot-000001" in capsys.readouterr().out


def test_pid_one_stays_quiescent_after_a_pre_authority_refusal(tmp_path, monkeypatch):
    quiesced = []
    monkeypatch.setattr(supervisor_module, "quiesce_refusal", lambda: quiesced.append(True))

    exit_code = supervisor_module.main({}, state=tmp_path)

    assert exit_code == 2
    assert quiesced == [True]


def test_a_terminal_run_quiesces_without_opening_another_boot_or_close(tmp_path):
    first = Supervisor(
        state=tmp_path,
        run_id="run-terminal",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: ExitedBoot(0)),
    ).run()
    before = EventStore(tmp_path, run_id="run-terminal").events()

    second = Supervisor(
        state=tmp_path,
        run_id="run-terminal",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: pytest.fail("a terminal Run cannot open another Boot")),
    ).run()

    after = EventStore(tmp_path, run_id="run-terminal").events()
    assert second.boot_id == first.boot_id
    assert second.disposition == NORMAL
    assert [event.event_digest for event in after] == [event.event_digest for event in before]
    assert len([event for event in after if event.payload["record"] == "run-close"]) == 1


def test_an_unclassified_terminal_record_is_rejected_before_it_can_enter_the_chain(tmp_path):
    store = EventStore(tmp_path, run_id="run-invalid")

    with pytest.raises(ValueError):
        TerminalDisposition("mystery")

    assert store.events() == []


def test_lifecycle_receipt_attaches_to_the_provisional_supervisor_manifest_row(tmp_path):
    result = Supervisor(
        state=tmp_path,
        run_id="run-manifest",
        redactor=Redactor({}),
        services=services(launch=lambda _boot_id: ExitedBoot(0)),
    ).run()
    descriptor = manifest_receipt(result.receipt_path)
    draft = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    requirements = copy.deepcopy(draft["requirements"])
    supervisor_row = next(row for row in requirements if row["row_id"] == "core.supervisor")
    supervisor_row.update(status="implemented", receipt_ref=descriptor["ref"])

    linked = generate_manifest(
        image_digest=draft["candidate"]["image_digest"],
        release_candidate_profile=draft["selected_profile"],
        requirements=requirements,
        receipts=[descriptor],
    )

    linked_row = next(row for row in linked["requirements"] if row["row_id"] == "core.supervisor")
    assert linked_row["status"] == "implemented"
    assert linked_row["receipt_ref"] == "receipt:supervisor-lifecycle"
    assert linked["lifecycle"] == "provisional"


def _lifecycle(event_id: str, record: str, *, boot_id: str = "") -> LifecycleRecorded:
    fact = RunOpened() if record == "run-open" else BootOpened(boot_id)
    return LifecycleRecorded(event_id=event_id, fact=fact, ts="2026-09-11T00:00:00+00:00")
