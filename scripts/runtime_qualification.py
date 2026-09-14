"""Generate fresh controlled-runtime observations for #293 and #296.

The CLI runs inside the exact strict Candidate image. Its output root must be on `/state`, so every
receipt and process observation is born on the canonical external state bind before a host-side
Evaluator signs it.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import multiprocessing
import subprocess
import sys
import threading
import time
from pathlib import Path

from solver.board import CORRECT, Verdict
from solver.board_broker_contracts import BoardBrokerResult, BoardOperation, BoardOutcome
from solver.capability import CapabilityBinding
from solver.candidate_admission_contracts import (
    CandidateDisposition,
    CandidateProvenance,
    ReadyAdmission,
    ReadyCandidate,
    SubmissionContext,
)
from solver.event_store import GenerationDisposition
from solver.event_store_storage import atomic_write, canonical_bytes
from solver.record import Recorder
from solver.recovery.incident import Fault, IncidentEngine, verify_receipt as verify_incident
from solver.recovery.supervisor import WorkerSupervisor
from solver.redaction import Redactor
from solver.submission.authority import SerialSubmission
from solver.submission.receipt import verify_receipt as verify_submission
from solver.submission.receipt import write_receipt
from solver.supervisor_process import SpawnedBoot
from solver.work_generation import GenerationFence
from solver.write_reservation import EffectIndeterminate
from scripts.deterministic_recovery_qualification import qualify as qualify_deterministic_recovery

RUN_ID_293 = "runtime-qualification-293"
RUN_ID_296 = "runtime-qualification-296"
BINDING = CapabilityBinding(RUN_ID_293, "boot-1", "generation-1", "lane-1", "attempt-1", "step-1")


class _Clock:
    def __init__(self) -> None:
        self._last = dt.datetime.now(dt.UTC)
        self._lock = threading.Lock()

    def __call__(self) -> str:
        with self._lock:
            self._last += dt.timedelta(microseconds=1)
            return self._last.isoformat().replace("+00:00", "Z")

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self._last += dt.timedelta(seconds=seconds)


class _Wire:
    def __init__(self, *, fail_during: bool = False, block_first: bool = False) -> None:
        self.fail_during = fail_during
        self.block_first = block_first
        self.active = 0
        self.maximum = 0
        self.posts = 0
        self._lock = threading.Lock()
        self.entered = threading.Event()
        self.release = threading.Event()

    def submit(self, _challenge_id: int, _candidate: str, **_kwargs) -> BoardBrokerResult:
        with self._lock:
            self.posts += 1
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            post = self.posts
        if self.block_first and post == 1:
            self.entered.set()
            if not self.release.wait(timeout=10):
                raise TimeoutError("controlled Board POST was not released")
        if self.fail_during:
            self.fail_during = False
            raise SystemExit("injected partial POST loss")
        with self._lock:
            self.active -= 1
        return BoardBrokerResult(
            BoardOperation.SUBMIT,
            BoardOutcome.ANSWERED,
            Verdict(CORRECT, "controlled acceptance", 200),
        )

    def close(self) -> None:
        pass


class _Ports:
    def fence(self, _fault: Fault) -> None:
        pass

    def evidence(self, _fault: Fault) -> bytes:
        return b"controlled worker crash"

    def teardown(self, _fault: Fault) -> None:
        pass

    def replace(self, _fault: Fault) -> bool:
        return True


def _candidate(serial: int, ready_at: str, *, ready_order: int | None = None) -> ReadyAdmission:
    identity = str(serial) * 64
    ready = ReadyCandidate(
        identity=identity,
        challenge_id=serial,
        generation_id=BINDING.generation_id,
        candidate=f"qualification{{candidate-{serial}}}".encode(),
        candidate_digest="a" * 64,
        provenance=CandidateProvenance(CandidateDisposition.OBSERVED, ("b" * 64,), ()),
        admission_rule="candidate-admission-v1",
        submission_context=SubmissionContext.static("qualification-revision-1", "qualification-board"),
    )
    return ReadyAdmission(ready, ready_at, ready_order if ready_order is not None else serial, ())


@contextlib.contextmanager
def _submission(root: Path, wire: _Wire, clock: _Clock, *, hook=None):
    recorder = Recorder(root, RUN_ID_293, Redactor({}), write_authority_hook=hook)
    authority = SerialSubmission(
        recorder.run_dir / "canonical",
        recorder.write_authority,
        clock,
        root / "controlled-board.sock",
        open_client=lambda _path, _binding: wire,
        sleep=clock.sleep,
    )
    try:
        yield authority, recorder
    finally:
        recorder.write_authority.close()


def _wait_for_pending(authority: SerialSubmission, count: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if authority.pending_count() == count:
            return
        time.sleep(0.001)
    raise TimeoutError(f"serial qualification did not observe {count} pending Candidates")


def _dispatch_thread(
    authority: SerialSubmission,
    admission: ReadyAdmission,
    errors: list[BaseException],
) -> threading.Thread:
    def dispatch() -> None:
        try:
            authority.dispatch(admission, binding=BINDING)
        except BaseException as error:  # propagate controlled thread failures to the qualification
            errors.append(error)

    thread = threading.Thread(target=dispatch)
    thread.start()
    return thread


def _qualify_ready_order(output: Path, clock: _Clock) -> tuple[_Wire, list[str]]:
    wire = _Wire(block_first=True)
    errors: list[BaseException] = []
    with _submission(output, wire, clock) as (authority, recorder):
        threads = [_dispatch_thread(authority, _candidate(1, clock(), ready_order=0), errors)]
        if not wire.entered.wait(timeout=10):
            raise TimeoutError("first controlled Board POST did not start")
        threads.append(_dispatch_thread(authority, _candidate(3, clock(), ready_order=2), errors))
        _wait_for_pending(authority, 1)
        threads.append(_dispatch_thread(authority, _candidate(2, clock(), ready_order=1), errors))
        _wait_for_pending(authority, 2)
        wire.release.set()
        for thread in threads:
            thread.join(timeout=10)
            if thread.is_alive():
                raise TimeoutError("serial qualification dispatch did not finish")
        if errors:
            raise errors[0]
        receipt = write_receipt(recorder.run_dir / "canonical", RUN_ID_293, recorder.write_authority)
        dispatch_order = list(json.loads(receipt.read_text())["dispatch_order"])
    return wire, dispatch_order


def _qualify_possibly_sent(output: Path, clock: _Clock) -> tuple[str, int]:
    during = _Wire(fail_during=True)
    with _submission(output, during, clock) as (authority, _recorder):
        try:
            authority.dispatch(_candidate(4, clock()), binding=BINDING)
        except SystemExit as error:
            if str(error) != "injected partial POST loss":
                raise
    with _submission(output, during, clock) as (replay, _recorder):
        try:
            replay.dispatch(_candidate(4, clock()), binding=BINDING)
        except EffectIndeterminate as error:
            possibly_sent_replay = type(error).__name__
        else:
            raise AssertionError("possibly-sent submission became replayable")
    return possibly_sent_replay, during.posts


def _qualify_aborted(output: Path, clock: _Clock) -> None:
    armed = True

    def fail_after_reserve(point: str) -> None:
        nonlocal armed
        if armed and point == "after_reserve":
            armed = False
            raise RuntimeError("injected pre-POST loss")

    with _submission(output, _Wire(), clock, hook=fail_after_reserve) as (authority, _recorder):
        try:
            authority.dispatch(_candidate(5, clock()), binding=BINDING)
        except RuntimeError as error:
            if str(error) != "injected pre-POST loss":
                raise


def qualify_serial_submission(output: Path) -> tuple[Path, Path]:
    """Observe ordering, serialization and each exactly-once crash boundary."""

    output = Path(output)
    clock = _Clock()
    wire, dispatch_order = _qualify_ready_order(output, clock)
    with _submission(output, wire, clock) as (replay, _recorder):
        replay.dispatch(_candidate(1, clock(), ready_order=0), binding=BINDING)
    possibly_sent_replay, possibly_sent_posts = _qualify_possibly_sent(output, clock)
    _qualify_aborted(output, clock)
    with _submission(output, _Wire(), clock) as (_authority, recorder):
        receipt = write_receipt(recorder.run_dir / "canonical", RUN_ID_293, recorder.write_authority)
        verify_submission(receipt, recorder.write_authority)
    trace = output / "serial-submission.trace.json"
    atomic_write(
        trace,
        canonical_bytes(
            {
                "committed_replay_posts": wire.posts,
                "competing_dispatch_order": dispatch_order,
                "competing_ready_orders": [0, 2, 1],
                "first_ready_candidate": _candidate(2, clock(), ready_order=1).candidate.identity,
                "possibly_sent_replay": possibly_sent_replay,
                "possibly_sent_replay_posts": possibly_sent_posts,
                "wire_in_flight_maximum": wire.maximum,
            }
        )
        + b"\n",
    )
    return receipt, trace


def _report_from_process(state: str, ready, start, results) -> None:
    ready.put(True)
    start.wait()
    result = IncidentEngine(Path(state), "runtime-qualification-296-processes", _Ports(), Redactor({})).report(
        Fault("worker-exit:17", "worker", "generation-processes", "exit_code=17")
    )
    results.put((multiprocessing.current_process().pid, result.incident_id))


def _coalescing_observation(root: Path) -> dict[str, object]:
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [
        context.Process(target=_report_from_process, args=(str(root), ready, start, results)) for _ in range(6)
    ]
    for process in processes:
        process.start()
    for _process in processes:
        ready.get(timeout=20)
    start.set()
    observed = [results.get(timeout=30) for _process in processes]
    for process in processes:
        process.join(timeout=30)
        if process.exitcode != 0:
            raise RuntimeError(f"coalescing process exited {process.exitcode}")
    receipt = json.loads(
        (root / "runs/runtime-qualification-296-processes/canonical/incident-containment.receipt.json").read_text()
    )
    return {
        "process_count": len(processes),
        "process_ids": [pid for pid, _incident_id in observed],
        "incident_ids": [incident_id for _pid, incident_id in observed],
        "duplicate_reports": receipt["duplicate_reports"],
    }


class _CrashPorts(_Ports):
    def __init__(self, fence: GenerationFence, generation_id: str) -> None:
        self._fence = fence
        self._generation_id = generation_id

    def fence(self, _fault: Fault) -> None:
        self._fence.close(self._generation_id, GenerationDisposition.INTERRUPT)

    def evidence(self, _fault: Fault) -> bytes:
        raise RuntimeError("injected containment crash at evidence-capture")

    def teardown(self, _fault: Fault) -> None:
        raise AssertionError("teardown ran before replay")

    def replace(self, _fault: Fault) -> bool:
        raise AssertionError("replacement ran before replay")


class _ReplayPorts(_Ports):
    def __init__(self, crashed_boot: SpawnedBoot, crashed_pid: int) -> None:
        self._crashed_boot = crashed_boot
        self._crashed_pid = crashed_pid

    def fence(self, _fault: Fault) -> None:
        raise AssertionError("generation fence authority reopened")

    def evidence(self, _fault: Fault) -> bytes:
        return canonical_bytes(
            {
                "exit_code": 17,
                "group_extinguished": not self._crashed_boot.group_alive(),
                "pid": self._crashed_pid,
                "real_process": True,
            }
        )


def _launch_exit(
    exit_code: int,
    observed: dict[str, object],
    key: str,
    launched: list[SpawnedBoot] | None = None,
):
    def launch(_worker_id: str) -> SpawnedBoot:
        process = subprocess.Popen(
            [sys.executable, "-c", f"raise SystemExit({exit_code})"],
            start_new_session=True,
        )
        observed[key] = process.pid
        boot = SpawnedBoot(process)
        if launched is not None:
            launched.append(boot)
        return boot

    return launch


def _observe_containment_crash(
    output: Path,
    fence: GenerationFence,
    generation_id: str,
    observed: dict[str, object],
) -> SpawnedBoot:
    launched: list[SpawnedBoot] = []
    try:
        supervisor = WorkerSupervisor(
            IncidentEngine(output, RUN_ID_296, _CrashPorts(fence, generation_id), Redactor({})),
            _launch_exit(17, observed, "crashed_pid", launched),
            lambda: 0,
            lambda: generation_id,
            grace_seconds=0.05,
        )
        supervisor.run()
    except RuntimeError as error:
        if str(error) != "injected containment crash at evidence-capture":
            raise
    if not launched:
        raise AssertionError("controlled crashing worker was not launched")
    return launched[0]


def _replay_containment(
    output: Path,
    crashed_boot: SpawnedBoot,
    generation_id: str,
    observed: dict[str, object],
):
    return WorkerSupervisor(
        IncidentEngine(
            output,
            RUN_ID_296,
            _ReplayPorts(crashed_boot, int(observed["crashed_pid"])),
            Redactor({}),
        ),
        _launch_exit(0, observed, "replacement_pid"),
        lambda: 0,
        lambda: generation_id,
        grace_seconds=0.05,
    ).run()


def qualify_incident(output: Path) -> tuple[Path, Path]:
    """Observe a real worker crash, interrupted containment, replay and replacement."""

    output = Path(output)
    clock = _Clock()
    fence = GenerationFence(output, RUN_ID_296, Redactor({}), timestamp=clock)
    generation = fence.acquire("qualification-work", "qualification-attempt")
    observed: dict[str, object] = {}
    crashed_boot = _observe_containment_crash(output, fence, generation.generation_id, observed)
    result = _replay_containment(output, crashed_boot, generation.generation_id, observed)
    receipt = output / f"runs/{RUN_ID_296}/canonical/incident-containment.receipt.json"
    verify_incident(receipt)
    incident = json.loads(receipt.read_text())
    generation_state = next(
        state for state in fence.projection().generations if state.generation_id == generation.generation_id
    )
    observed.update(
        boots=result.boots,
        concurrency=_coalescing_observation(output),
        containment_crash={
            "injected": True,
            "message": "injected containment crash at evidence-capture",
            "step": "evidence-capture",
        },
        crashed_exit_code=17,
        generation_active_after=generation_state.active,
        generation_id=generation.generation_id,
        group_extinguished=not crashed_boot.group_alive(),
        real_process=True,
        replacement_exit_code=result.exit_code,
        replay_count=incident["replay_count"],
        replayed_incident_id=incident["incident_id"],
    )
    trace = output / "process-trace.json"
    atomic_write(trace, canonical_bytes(observed) + b"\n")
    return receipt, trace


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args(argv)
    arguments.output.mkdir(parents=True, exist_ok=False)
    serial, serial_trace = qualify_serial_submission(arguments.output)
    incident, trace = qualify_incident(arguments.output)
    deterministic = qualify_deterministic_recovery(arguments.output, arguments.output / "deterministic-recovery")
    print(serial)
    print(serial_trace)
    print(incident)
    print(trace)
    print(deterministic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
