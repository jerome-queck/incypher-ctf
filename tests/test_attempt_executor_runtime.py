from __future__ import annotations

import base64
import json
import socket
import threading
import time
from pathlib import Path

import pytest

from solver.attempt_executor_contracts import (
    AttemptRequest,
    EnvelopeSpec,
    NetworkPolicy,
    ResourceOutcome,
    RuntimeBinding,
)
from solver.attempt_executor_pool import AttemptPool, AttemptSlot, _fixed_worker_command
from solver.attempt_executor_runtime import AttemptRuntime, RuntimeUnavailable, _first_cause, _workspace_usage
from solver.attempt_executor_worker import _network_breach
from solver.isolation import STRICT_PROFILE_DIGEST
from solver.target_broker_contracts import TargetCandidateBinding


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

    def pids(self, path: Path) -> tuple[int, ...]:
        return tuple(int(value) for value in (path / "cgroup.procs").read_text().split())

    def term(self, path: Path, pids: tuple[int, ...]) -> None:
        (path / "cgroup.procs").write_text("")


class EscalatingCgroup(FakeCgroup):
    def __init__(self) -> None:
        self.terms: list[tuple[int, ...]] = []
        self.kills = 0

    def term(self, path: Path, pids: tuple[int, ...]) -> None:
        self.terms.append(pids)
        (path / "cgroup.procs").write_text(f"{pids[-1]}\n")

    def kill(self, path: Path) -> None:
        self.kills += 1
        super().kill(path)


class UnreadableCgroup(FakeCgroup):
    def __init__(self) -> None:
        self.kills = 0

    def pids(self, path: Path) -> tuple[int, ...]:
        raise OSError("inventory unavailable")

    def kill(self, path: Path) -> None:
        self.kills += 1


def _request(tmp_path: Path, *, network_class: str = "deny") -> AttemptRequest:
    work = tmp_path / "work"
    work.mkdir()
    (work / "input.txt").write_text("ok")
    return AttemptRequest(
        "g",
        "a",
        "s",
        ("cat", "/work/input.txt"),
        work,
        EnvelopeSpec(1, 20_000, 1024, 4, 1024, NetworkPolicy.DENY, 1, 0.1, network_class),
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


def test_workspace_usage_ignores_an_entry_removed_after_listing(tmp_path: Path, monkeypatch) -> None:
    vanished = tmp_path / "temporary-output"
    vanished.write_bytes(b"held")
    vanished.unlink()
    monkeypatch.setattr(Path, "rglob", lambda _path, _pattern: iter((vanished,)))

    assert _workspace_usage(tmp_path) == (0, False)


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


def test_success_closes_research_socket_before_workspace_sync(tmp_path: Path, monkeypatch) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, cgroup = _runtime(tmp_path, parent)
    incoming = type("I", (), {"request": _request(tmp_path)})()
    runtime.prepare("envelope-1", incoming)
    _initial_counters(cgroup)
    slot_work = runtime.pool.slots[0].work_path
    broker_path = slot_work / ".research.sock"
    broker_path.write_bytes(b"")

    class Service:
        path = broker_path

        def close(self) -> None:
            broker_path.unlink(missing_ok=True)

    runtime._research_services["envelope-1"] = Service()  # type: ignore[assignment]
    runtime._broker_socket_inodes[slot_work] = {broker_path.stat().st_ino}
    sync_saw_closed_service: list[bool] = []

    def sync(_request, _slot) -> bool:
        sync_saw_closed_service.append("envelope-1" not in runtime._research_services)
        return True

    monkeypatch.setattr(runtime, "_sync_result_workspace", sync)

    def reply() -> None:
        worker.recv(1_000_000)
        worker.recv(1_000_000)
        worker.send(json.dumps({"type": "result", "exit_code": 0, "output": ""}).encode())
        worker.recv(1_000_000)
        worker.send(json.dumps({"type": "term-sent", "pids": [4321]}).encode())

    thread = threading.Thread(target=reply)
    thread.start()
    result = runtime.launch("envelope-1", incoming)
    thread.join()

    assert result.outcome is ResourceOutcome.EXITED
    assert result.cleanup_complete is True
    assert sync_saw_closed_service == [True]
    assert not broker_path.exists()
    parent.close()
    worker.close()


def test_runtime_does_not_expose_a_target_socket_without_published_generation_authority(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, _cgroup = _runtime(tmp_path, parent)

    class UnavailableTarget:
        def available(self, generation_id):
            assert generation_id == "g"
            return False

    runtime.target_broker = UnavailableTarget()
    incoming = type("I", (), {"request": _request(tmp_path)})()

    runtime.prepare("envelope-1", incoming)

    assert runtime._target_services == {}
    runtime.cancel("envelope-1")
    parent.close()
    worker.close()


@pytest.mark.parametrize(
    ("network_class", "target_expected", "research_expected"),
    (("deny", False, False), ("target-broker", True, False), ("research-broker", False, True)),
)
def test_runtime_exposes_only_the_declared_broker_authority(
    tmp_path: Path, network_class: str, target_expected: bool, research_expected: bool
) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, _cgroup = _runtime(tmp_path, parent)
    image_id = "sha256:" + "a" * 64
    candidate = TargetCandidateBinding(
        image_id,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
        "linux/arm64",
        STRICT_PROFILE_DIGEST,
    )

    class Broker:
        boot_id = "boot-1"

        def __init__(self):
            self.candidate = candidate
            self.prepared: list[str] = []

        def available(self, _generation_id):
            return True

        def prepare_attempt(self, binding):
            self.prepared.append(binding.generation_id)

        def revoke_generation(self, _generation_id):
            return None

    class Research:
        boot_id = "boot-1"

        def __init__(self):
            self.prepared: list[str] = []

        def prepare_attempt(self, binding):
            self.prepared.append(binding.generation_id)

        def revoke_generation(self, _generation_id):
            return None

    class Service:
        def __init__(self, path: Path, calls: list[Path]):
            self.path = path
            self.calls = calls

        def start(self):
            self.calls.append(self.path)
            self.path.write_bytes(b"")

        def close(self):
            self.path.unlink(missing_ok=True)

    target = Broker()
    research = Research()
    target_calls: list[Path] = []
    research_calls: list[Path] = []
    runtime.target_broker = target
    runtime.research_broker = research
    runtime._target_service_factory = lambda path, _broker: Service(path, target_calls)
    runtime._research_service_factory = lambda path, _broker: Service(path, research_calls)
    incoming = type(
        "I",
        (),
        {
            "run_id": "run-1",
            "request": _request(tmp_path, network_class=network_class),
            "binding": RuntimeBinding(image_id, candidate.manifest_digest, candidate.config_digest, candidate.platform),
        },
    )()

    runtime.prepare("envelope-1", incoming)
    assert bool(target_calls) is target_expected
    assert bool(research_calls) is research_expected
    assert bool(target.prepared) is target_expected
    assert bool(research.prepared) is research_expected
    runtime._close_target("envelope-1")
    runtime._close_research("envelope-1")
    runtime.pool.release(runtime.pool.slots[0])
    parent.close()
    worker.close()


def test_research_setup_failure_revokes_partially_prepared_target_authority(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, _cgroup = _runtime(tmp_path, parent)
    image_id = "sha256:" + "a" * 64
    candidate = TargetCandidateBinding(
        image_id,
        "sha256:" + "b" * 64,
        "sha256:" + "c" * 64,
        "linux/arm64",
        STRICT_PROFILE_DIGEST,
    )

    class Target:
        boot_id = "boot-1"
        revoked = []

        def __init__(self):
            self.candidate = candidate

        def available(self, _generation_id):
            return True

        def prepare_attempt(self, _binding):
            pass

        def revoke_generation(self, generation_id):
            self.revoked.append(generation_id)

    class Research:
        boot_id = "boot-1"

        def prepare_attempt(self, _binding):
            pass

        def revoke_generation(self, _generation_id):
            pass

    class Service:
        def __init__(self, path: Path, *, fail: bool):
            self.path = path
            self.fail = fail

        def start(self):
            if self.fail:
                raise OSError("research unavailable")
            self.path.write_bytes(b"")

        def close(self):
            self.path.unlink(missing_ok=True)

    target = Target()
    runtime.target_broker = target
    runtime.research_broker = Research()
    runtime._target_service_factory = lambda path, _broker: Service(path, fail=False)
    runtime._research_service_factory = lambda path, _broker: Service(path, fail=True)
    request = _request(tmp_path, network_class="research-broker")
    incoming = type(
        "I",
        (),
        {
            "run_id": "run-1",
            "request": request,
            "binding": RuntimeBinding(
                image_id,
                candidate.manifest_digest,
                candidate.config_digest,
                candidate.platform,
            ),
        },
    )()

    with pytest.raises(RuntimeUnavailable, match="Research broker preparation failed"):
        runtime.prepare("envelope-1", incoming)

    assert target.revoked == []
    assert runtime.pool.slots[0].busy is False
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


def test_only_the_fixed_target_unix_port_is_exempt_from_raw_egress_detection() -> None:
    allowed = "\n".join(
        (
            "1 socket(AF_UNIX, SOCK_STREAM|SOCK_CLOEXEC, 0) = 3",
            '1 connect(3, {sa_family=AF_UNIX, sun_path="/work/.target.sock"}, 110) = 0',
            '1 sendto(3, "request", 7, 0, NULL, 0) = 7',
        )
    )
    assert not _network_breach(allowed, "/work/.target.sock")
    assert _network_breach("1 socket(AF_INET, SOCK_STREAM, IPPROTO_TCP) = 3", "/work/.target.sock")
    assert _network_breach(
        '1 connect(3, {sa_family=AF_UNIX, sun_path="/run/control.sock"}, 110) = 0',
        "/work/.target.sock",
    )
    assert _network_breach(
        '1 sendto(3, "request", 7, 0, {sa_family=AF_INET, sin_port=htons(443)}, 16) = 7',
        "/work/.target.sock",
    )


def test_incomplete_live_trace_line_is_not_network_evidence() -> None:
    partial = '1 sendto(3, "request", 7, 0, NULL, 0'

    assert not _network_breach(partial, "/work/.target.sock")


def test_result_terms_the_adopted_tree_then_kills_and_reaps_the_survivor(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, cgroup = _runtime(tmp_path, parent)
    lifecycle = EscalatingCgroup()
    runtime.cgroup = lifecycle
    incoming = type("I", (), {"request": _request(tmp_path)})()
    runtime.prepare("envelope-1", incoming)
    _initial_counters(cgroup)

    def reply() -> None:
        launch = json.loads(worker.recv(1_000_000))
        go = json.loads(worker.recv(1_000_000))
        assert launch["type"] == "launch" and go["type"] == "go"
        (cgroup / "cgroup.procs").write_text("4321 4322 4323\n")
        worker.send(
            json.dumps(
                {
                    "type": "result",
                    "exit_code": 0,
                    "output": base64.b64encode(b"bounded").decode(),
                    "output_bytes_total": 7000,
                    "output_truncated": True,
                }
            ).encode()
        )
        terminate = json.loads(worker.recv(1_000_000))
        assert terminate == {"type": "terminate", "nonce": launch["nonce"]}
        worker.send(json.dumps({"type": "term-sent", "pids": [2, 3, 4]}).encode())

    thread = threading.Thread(target=reply)
    thread.start()
    result = runtime.launch("envelope-1", incoming)
    thread.join()

    process = result.process_lifecycle
    assert process is not None
    assert process.descendants == (4321, 4322, 4323)
    assert process.after_term == (4323,)
    assert process.after_kill == ()
    assert process.term_sent is True and process.kill_sent is True
    assert process.stream_captured_bytes == len(b"bounded")
    assert process.stream_total_bytes == 7000
    assert process.stream_truncated is True
    assert lifecycle.terms == [(4321, 4322, 4323)]
    assert lifecycle.kills == 1
    parent.close()
    worker.close()


def test_restart_reconciles_the_recorded_tree_before_a_replacement_reserves(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, cgroup = _runtime(tmp_path, parent)
    _initial_counters(cgroup)
    (cgroup / "cgroup.procs").write_text("4321 4322\n")

    def acknowledge() -> None:
        terminate = json.loads(worker.recv(1_000_000))
        assert terminate == {"type": "terminate", "nonce": "durable-nonce"}
        worker.send(json.dumps({"type": "term-sent", "pids": [4321, 4322]}).encode())

    thread = threading.Thread(target=acknowledge)
    thread.start()
    result = runtime.reconcile(
        (
            {
                "envelope_id": "envelope-1",
                "cgroup_path": str(cgroup),
                "executor_uid": 20_000,
                "declared": {"cleanup_seconds": 0.1},
                "observed": {"control_nonce": "durable-nonce"},
            },
        )
    )[0]
    thread.join()

    assert result.cleanup_complete is True
    assert result.process_lifecycle is not None
    assert result.process_lifecycle.descendants == (4321, 4322)
    assert result.process_lifecycle.after_kill == ()
    assert result.process_lifecycle.teardown_acknowledged is True
    replacement = runtime.prepare("envelope-2", type("I", (), {"request": _request(tmp_path)})())
    assert replacement.cgroup_path == str(cgroup)
    parent.close()
    worker.close()


def test_unreadable_process_inventory_cannot_be_recorded_as_clean(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, cgroup = _runtime(tmp_path, parent)
    unreadable = UnreadableCgroup()
    runtime.cgroup = unreadable
    result = runtime.reconcile(
        (
            {
                "envelope_id": "envelope-1",
                "cgroup_path": str(cgroup),
                "executor_uid": 20_000,
                "declared": {"cleanup_seconds": 0.1},
            },
        )
    )[0]

    assert result.cleanup_complete is False
    assert result.process_lifecycle is not None
    assert result.process_lifecycle.inventory_complete is False
    assert unreadable.kills == 1
    parent.close()
    worker.close()


def test_control_eof_during_teardown_is_explicit_incomplete_evidence(tmp_path: Path) -> None:
    parent, worker = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    runtime, cgroup = _runtime(tmp_path, parent)
    incoming = type("I", (), {"request": _request(tmp_path)})()
    runtime.prepare("envelope-1", incoming)
    _initial_counters(cgroup)

    def reply_then_close() -> None:
        worker.recv(1_000_000)
        worker.recv(1_000_000)
        (cgroup / "cgroup.procs").write_text("4321\n")
        worker.send(json.dumps({"type": "result", "exit_code": 0, "output": ""}).encode())
        worker.close()

    thread = threading.Thread(target=reply_then_close)
    thread.start()
    result = runtime.launch("envelope-1", incoming)
    thread.join()

    assert result.cleanup_complete is False
    assert result.process_lifecycle is not None
    assert result.process_lifecycle.control_eof is True
    assert result.process_lifecycle.teardown_acknowledged is False
    parent.close()


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
    assert ["--ro-bind", "/opt/solver/tool-supply", "/opt/solver/tool-supply"] in [
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
