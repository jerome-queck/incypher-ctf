from __future__ import annotations

import base64
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from solver.attempt_executor_contracts import AttemptRequest, EnvelopeSpec, NetworkPolicy, ResourceOutcome
from solver.attempt_executor_pool import AttemptPool, AttemptSlot, _fixed_worker_command
from solver.attempt_executor_runtime import AttemptRuntime, _first_cause


class FakeCgroup:
    def write(self, path: Path, name: str, value: str) -> None:
        (path / name).write_text(value)

    def counter(self, path: Path, name: str, key: str) -> int:
        for line in (path / name).read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == key:
                return int(parts[1])
        return 0

    def kill(self, path: Path) -> None:
        (path / "cgroup.kill").write_text("1\n")
        (path / "cgroup.procs").write_text("")

    def count(self, path: Path) -> int:
        return len((path / "cgroup.procs").read_text().split())


def _request(tmp_path: Path) -> AttemptRequest:
    work = tmp_path / "work"
    work.mkdir()
    (work / "input.txt").write_text("ok")
    return AttemptRequest(
        "g",
        "a",
        "s",
        ("cat", "/work/input.txt"),
        work,
        EnvelopeSpec(1, 20_000, 1024, 4, 1024, NetworkPolicy.DENY, 1, 0.1),
    )


def _runtime(tmp_path: Path, peer: socket.socket) -> tuple[AttemptRuntime, Path]:
    slot_root = tmp_path / "container" / "attempt-executor" / "slot-0"
    (slot_root / "envelopes" / "active").mkdir(parents=True)
    slot_work = tmp_path / "slot" / "work"
    slot_work.mkdir(parents=True)
    slot = AttemptSlot(0, 20_000, slot_root, slot_work, peer)
    pool = AttemptPool(tmp_path / "container", tmp_path / "pool", (slot,))
    runtime = AttemptRuntime(pool, workspace_limit=1024, cgroup=FakeCgroup(), gate=lambda *_args: 4321)
    return runtime, slot_root / "envelopes" / "active"


def _initial_counters(cgroup: Path) -> None:
    (cgroup / "cpu.stat").write_text("usage_usec 0\nnr_throttled 0\n")
    (cgroup / "memory.events").write_text("oom 0\noom_kill 0\n")
    (cgroup / "pids.events").write_text("max 0\n")
    (cgroup / "cgroup.procs").write_text("")


def test_prepare_records_real_limits_then_kernel_gate_precedes_exec(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, cgroup = _runtime(tmp_path, parent)
    incoming = type("I", (), {"request": _request(tmp_path)})()
    reservation = runtime.prepare("envelope-1", incoming)
    _initial_counters(cgroup)
    trace: list[str] = []

    def reply() -> None:
        request = json.loads(worker.recv(1_000_000))
        trace.append(request["type"])
        assert request["argv"] == ["cat", "/work/input.txt"]
        assert request["filesystem_bytes"] == 1024
        gate = json.loads(worker.recv(1_000_000))
        trace.append(gate["type"])
        worker.send(json.dumps({"type": "result", "exit_code": 0, "output": base64.b64encode(b"ok").decode()}).encode())

    thread = threading.Thread(target=reply)
    thread.start()
    result = runtime.launch("envelope-1", incoming)
    thread.join()

    assert reservation.cgroup_path == str(cgroup)
    assert trace == ["launch", "go"]
    assert result.outcome is ResourceOutcome.EXITED
    assert result.output == b"ok"
    assert (cgroup / "cpu.max").read_text() == "20000 100000\n"
    assert (cgroup / "memory.max").read_text() == "1024\n"
    assert (cgroup / "pids.max").read_text() == "4\n"
    assert (cgroup / "cgroup.procs").read_text() == ""
    parent.close()
    worker.close()


def test_cpu_usage_is_measured_not_inferred_from_throttling(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, cgroup = _runtime(tmp_path, parent)
    incoming = type("I", (), {"request": _request(tmp_path)})()
    runtime.prepare("envelope-1", incoming)
    _initial_counters(cgroup)

    def reply() -> None:
        worker.recv(1_000_000)
        worker.recv(1_000_000)
        (cgroup / "cpu.stat").write_text("usage_usec 1000001\nnr_throttled 0\n")
        worker.send(json.dumps({"type": "result", "exit_code": 0, "output": ""}).encode())

    thread = threading.Thread(target=reply)
    thread.start()
    result = runtime.launch("envelope-1", incoming)
    thread.join()

    assert result.outcome is ResourceOutcome.CPU
    assert result.observed["cpu_nr_throttled"] == 0
    parent.close()
    worker.close()


def test_live_network_breach_terminates_and_drains_the_worker_result(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, cgroup = _runtime(tmp_path, parent)
    incoming = type("I", (), {"request": _request(tmp_path)})()
    runtime.prepare("envelope-1", incoming)
    _initial_counters(cgroup)

    def reply() -> None:
        worker.recv(1_000_000)
        worker.recv(1_000_000)
        worker.send(json.dumps({"type": "breach", "network_breach": True, "network_trace_bytes": 24}).encode())
        worker.send(json.dumps({"type": "result", "exit_code": -9, "output": ""}).encode())

    thread = threading.Thread(target=reply)
    started = time.monotonic()
    thread.start()
    result = runtime.launch("envelope-1", incoming)
    thread.join()

    assert result.outcome is ResourceOutcome.NETWORK
    assert time.monotonic() - started < incoming.request.envelope.wall_seconds
    with pytest.raises(BlockingIOError):
        parent.recv(1_000_000)
    parent.close()
    worker.close()


def test_fixed_worker_has_private_namespaces_uid_root_and_no_control_path(tmp_path: Path) -> None:
    command = _fixed_worker_command(tmp_path / "work", control_fd=17, seccomp_fd=19)
    sibling = _fixed_worker_command(tmp_path / "sibling", control_fd=23, seccomp_fd=29, uid=20001)

    assert command[0] == "/usr/bin/bwrap"
    for boundary in ("--unshare-pid", "--unshare-net", "--unshare-uts", "--unshare-ipc"):
        assert boundary in command
    assert ["--seccomp", "19"] == command[command.index("--seccomp") :][:2]
    assert command[-1] == "17"
    assert ["--reuid", "20000"] == command[command.index("--reuid") :][:2]
    assert ["--reuid", "20001"] == sibling[sibling.index("--reuid") :][:2]
    assert "--bounding-set=-all" in command
    assert "/state" not in command
    assert ["--ro-bind", "/opt/solver", "/opt/solver"] not in [
        command[index : index + 3] for index in range(len(command) - 2)
    ]


def test_first_cause_precedence() -> None:
    assert _first_cause(True, True, True, True, True, True, True) is ResourceOutcome.CANCELLED
    assert _first_cause(False, True, True, True, True, True, True) is ResourceOutcome.CPU
    assert _first_cause(False, False, True, True, True, True, True) is ResourceOutcome.MEMORY
    assert _first_cause(False, False, False, True, True, True, True) is ResourceOutcome.PIDS
    assert _first_cause(False, False, False, False, True, True, True) is ResourceOutcome.FILESYSTEM
    assert _first_cause(False, False, False, False, False, True, True) is ResourceOutcome.NETWORK
    assert _first_cause(False, False, False, False, False, False, True) is ResourceOutcome.DEADLINE


def test_reconciliation_rejects_any_noncanonical_cgroup_path(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, _cgroup = _runtime(tmp_path, parent)
    outside = tmp_path / "container" / "control"
    outside.mkdir()
    result = runtime.reconcile(({"envelope_id": "envelope-1", "cgroup_path": str(outside)},))[0]

    assert result.outcome is ResourceOutcome.RECONCILED
    assert result.observed["cgroup_path_rejected"] == str(outside)
    assert result.cleanup_complete is False
    assert not (outside / "cgroup.kill").exists()
    parent.close()
    worker.close()
