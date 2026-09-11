"""Trusted measurement and cgroup admission for hostile Attempt workers."""

from __future__ import annotations

import base64
import binascii
import json
import os
import secrets
import shutil
import socket
import stat
import struct
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solver.attempt_executor_contracts import (
    AttemptRequest,
    ResourceOutcome,
    RuntimeObservation,
    RuntimeReservation,
)
from solver.attempt_executor_pool import AttemptPool, AttemptSlot
from solver.attempt_executor_worker import MAX_FRAME, MAX_OUTPUT, encode_frame


MAX_WORKSPACE_BYTES = 128 * 1024 * 1024
MAX_WORKSPACE_FILES = 4096
MAX_POLL_INTERVAL = 0.025


class RuntimeUnavailable(RuntimeError):
    """No child started because the fixed runtime could not reserve its boundary."""


@dataclass(frozen=True)
class ResourceSample:
    counters: Mapping[str, int]
    filesystem_bytes: int
    unsafe_workspace: bool
    cpu: bool
    memory: bool
    pids: bool
    filesystem: bool


class AttemptRuntime:
    def __init__(
        self,
        pool: AttemptPool,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        proc_root: Path = Path("/proc"),
        workspace_limit: int = MAX_WORKSPACE_BYTES,
        cgroup: Any | None = None,
        gate: Callable[[socket.socket, str, float], int] | None = None,
    ) -> None:
        self.pool = pool
        self.clock = clock
        self.sleep = sleep
        self.proc_root = Path(proc_root)
        self.workspace_limit = workspace_limit
        self.cgroup = cgroup
        self.gate = gate or self._await_gated
        self._cancelled: set[str] = set()
        self._cancel_lock = threading.Lock()
        self._reservations: dict[str, tuple[AttemptSlot, Path, int]] = {}

    def prepare(self, envelope_id: str, incoming: Any) -> RuntimeReservation:
        """Reserve and configure a childless envelope before canonical launch records."""

        if not self.pool.healthy:
            raise RuntimeUnavailable("Attempt worker pool is unavailable")
        try:
            files, baseline_bytes = _collect_workspace(incoming.request.workspace, self.workspace_limit)
        except (OSError, ValueError) as error:
            raise RuntimeUnavailable(f"Attempt workspace is invalid: {error}") from error
        slot = self.pool.acquire()
        if slot is None:
            raise RuntimeUnavailable("Attempt worker pool is busy")
        try:
            _materialize_workspace(slot.work_path, files, hostile_uid=slot.uid)
            cgroup = self._envelope_cgroup(slot, envelope_id)
            self._configure_envelope(cgroup, incoming.request)
        except (OSError, ValueError) as error:
            self.pool.release(slot)
            raise RuntimeUnavailable(f"Attempt envelope preparation failed: {error}") from error
        self._reservations[envelope_id] = slot, cgroup, baseline_bytes
        return RuntimeReservation(str(cgroup), slot.uid)

    def cancel(self, envelope_id: str) -> None:
        with self._cancel_lock:
            if envelope_id in self._cancelled:
                return
            self._cancelled.add(envelope_id)
        reserved = self._reservations.get(envelope_id)
        if reserved is not None:
            self._kill_cgroup(reserved[1])

    def launch(self, envelope_id: str, incoming: Any) -> RuntimeObservation:
        try:
            slot, cgroup, baseline_bytes = self._reservations[envelope_id]
        except KeyError as error:
            raise RuntimeUnavailable("Attempt envelope was not prepared") from error
        request = incoming.request
        nonce = secrets.token_hex(16)
        try:
            slot.connection.send(
                encode_frame(
                    {
                        "type": "launch",
                        "nonce": nonce,
                        "argv": list(request.argv),
                        "filesystem_bytes": request.envelope.filesystem_bytes,
                    }
                )
            )
            child_pid = self.gate(slot.connection, nonce, request.envelope.wall_seconds, slot.uid)
            if self.cgroup is None:
                self.pool.broker_request({"type": "attach", "slot": slot.number, "pid": child_pid})
            else:
                self._cgroup_write(cgroup, "cgroup.procs", f"{child_pid}\n")
            baseline = self._cgroup_counters(cgroup)
            slot.connection.send(encode_frame({"type": "go", "nonce": nonce}))
            return self._monitor(envelope_id, request, slot, cgroup, baseline, baseline_bytes)
        except (OSError, ValueError, ConnectionError, RuntimeUnavailable) as error:
            slot.healthy = False
            cleanup = self._cleanup(cgroup, request.envelope.cleanup_seconds)
            return RuntimeObservation(
                ResourceOutcome.LAUNCH_FAILED,
                None,
                b"",
                str(cgroup),
                slot.uid,
                {
                    "launch_failure": type(error).__name__,
                    "detail": str(error)[:512],
                    **self._cleanup_observed(cleanup),
                },
                bool(cleanup["complete"]),
            )
        finally:
            self._finish(envelope_id)

    def reconcile(self, unresolved: tuple[dict[str, object], ...]) -> tuple[RuntimeObservation, ...]:
        return tuple(self._reconcile_one(state) for state in unresolved)

    def _reconcile_one(self, state: Mapping[str, object]) -> RuntimeObservation:
        supplied = str(state.get("cgroup_path", ""))
        observed: dict[str, int | float | str | bool] = {"reconciled": True}
        if not supplied:
            observed.update(processes_before_kill=0, processes_after_kill=0, cleanup_seconds=0.0)
            return RuntimeObservation(ResourceOutcome.RECONCILED, None, b"", "", -1, observed, True)
        path = Path(supplied)
        allowed = {(slot.cgroup_path / "envelopes" / "active").resolve(strict=False) for slot in self.pool.slots}
        if path.resolve(strict=False) not in allowed:
            observed["cgroup_path_rejected"] = supplied
            return RuntimeObservation(ResourceOutcome.RECONCILED, None, b"", supplied, -1, observed, False)
        before = self._cgroup_count(path)
        started = self.clock()
        cleanup_seconds = float(dict(state.get("declared", {})).get("cleanup_seconds", 1.0))
        cleanup = self._cleanup(path, cleanup_seconds)
        observed.update(processes_before_kill=before, **self._cleanup_observed(cleanup))
        observed["cleanup_seconds"] = max(0.0, self.clock() - started)
        return RuntimeObservation(
            ResourceOutcome.RECONCILED,
            None,
            b"",
            supplied,
            int(state.get("executor_uid", -1)),
            observed,
            bool(cleanup["complete"]),
        )

    def _await_gated(self, connection: socket.socket, nonce: str, wall_seconds: float, expected_uid: int) -> int:
        connection.settimeout(min(3.0, wall_seconds))
        try:
            data, ancillary, _flags, _address = connection.recvmsg(MAX_FRAME, socket.CMSG_SPACE(struct.calcsize("3i")))
        finally:
            connection.settimeout(None)
        message = json.loads(data)
        credentials = None
        for level, kind, value in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                credentials = struct.unpack("3i", value[: struct.calcsize("3i")])
        if message != {"type": "gated", "nonce": nonce} or credentials is None:
            raise RuntimeUnavailable("Attempt child did not reach the kernel launch gate")
        pid, uid, gid = credentials
        if uid != expected_uid or gid != expected_uid or pid <= 0:
            raise RuntimeUnavailable("Attempt launch gate has the wrong kernel identity")
        return pid

    def _monitor(
        self,
        envelope_id: str,
        request: AttemptRequest,
        slot: AttemptSlot,
        cgroup: Path,
        baseline: Mapping[str, int],
        baseline_bytes: int,
    ) -> RuntimeObservation:
        envelope = request.envelope
        started = self.clock()
        response: dict[str, object] | None = None
        slot.connection.setblocking(False)
        while True:
            now = self.clock()
            sample = self._sample(request, slot, cgroup, baseline, baseline_bytes)
            with self._cancel_lock:
                cancelled = envelope_id in self._cancelled
            response = _recv_nonblocking(slot.connection)
            network = bool(response and response.get("network_breach") is True)
            if response is not None:
                sample = self._sample(request, slot, cgroup, baseline, baseline_bytes)
            cause = _first_cause(
                cancelled,
                sample.cpu,
                sample.memory,
                sample.pids,
                sample.filesystem,
                network,
                now - started >= envelope.wall_seconds,
            )
            if cause is not None or response is not None:
                return self._complete_monitor(
                    request,
                    slot,
                    cgroup,
                    baseline,
                    baseline_bytes,
                    started,
                    sample,
                    network,
                    cause,
                    response,
                )
            self.sleep(min(MAX_POLL_INTERVAL, max(0.0, envelope.wall_seconds - (now - started))))

    def _sample(
        self,
        request: AttemptRequest,
        slot: AttemptSlot,
        cgroup: Path,
        baseline: Mapping[str, int],
        baseline_bytes: int,
    ) -> ResourceSample:
        snapshot = self._cgroup_counters(cgroup)
        filesystem_bytes, unsafe = _workspace_usage(slot.work_path)
        return ResourceSample(
            counters=snapshot,
            filesystem_bytes=filesystem_bytes,
            unsafe_workspace=unsafe,
            cpu=snapshot["cpu_usage_usec"] - baseline["cpu_usage_usec"] >= request.envelope.cpu_seconds * 1_000_000,
            memory=snapshot["oom"] > baseline["oom"] or snapshot["oom_kill"] > baseline["oom_kill"],
            pids=snapshot["pids_max"] > baseline["pids_max"],
            filesystem=unsafe or filesystem_bytes >= baseline_bytes + request.envelope.filesystem_bytes,
        )

    def _complete_monitor(
        self,
        request: AttemptRequest,
        slot: AttemptSlot,
        cgroup: Path,
        baseline: Mapping[str, int],
        baseline_bytes: int,
        started: float,
        sample: ResourceSample,
        network: bool,
        cause: ResourceOutcome | None,
        response: dict[str, object] | None,
    ) -> RuntimeObservation:
        envelope = request.envelope
        cleanup = self._cleanup(cgroup, envelope.cleanup_seconds)
        worker_acknowledged = True
        if cause is not None and (response is None or response.get("type") not in {"result", "error"}):
            terminal = _await_worker_result(slot.connection, envelope.cleanup_seconds)
            worker_acknowledged = terminal is not None
            if terminal is not None:
                response = {**(response or {}), **terminal}
            else:
                slot.healthy = False
        observed = self._observed(
            envelope,
            baseline,
            sample.counters,
            cleanup,
            elapsed=max(0.0, self.clock() - started),
            filesystem_bytes=sample.filesystem_bytes,
            baseline_bytes=baseline_bytes,
            network=network,
            slot=slot,
            response=response,
        )
        observed["worker_acknowledged_cleanup"] = worker_acknowledged
        if cause is not None:
            return RuntimeObservation(
                cause,
                None,
                _output(response),
                str(cgroup),
                slot.uid,
                observed,
                bool(cleanup["complete"]) and worker_acknowledged,
            )
        assert response is not None
        if response.get("type") == "error":
            observed["launch_failure"] = str(response.get("error", "worker-error"))[:512]
            return RuntimeObservation(
                ResourceOutcome.LAUNCH_FAILED,
                None,
                b"",
                str(cgroup),
                slot.uid,
                observed,
                bool(cleanup["complete"]),
            )
        if cleanup["complete"] and not sample.unsafe_workspace:
            try:
                _sync_workspace(slot.work_path, request.workspace, self.workspace_limit)
            except (OSError, ValueError):
                observed["workspace_sync_failed"] = True
                return RuntimeObservation(
                    ResourceOutcome.FILESYSTEM,
                    None,
                    _output(response),
                    str(cgroup),
                    slot.uid,
                    observed,
                    bool(cleanup["complete"]),
                )
        return RuntimeObservation(
            ResourceOutcome.EXITED,
            _int_or_none(response.get("exit_code")),
            _output(response),
            str(cgroup),
            slot.uid,
            observed,
            bool(cleanup["complete"]),
        )

    def _observed(
        self,
        envelope: Any,
        baseline: Mapping[str, int],
        snapshot: Mapping[str, int],
        cleanup: Mapping[str, object],
        *,
        elapsed: float,
        filesystem_bytes: int,
        baseline_bytes: int,
        network: bool,
        slot: AttemptSlot,
        response: Mapping[str, object] | None,
    ) -> dict[str, int | float | str | bool]:
        return {
            "cpu_usage_usec": max(0, snapshot["cpu_usage_usec"] - baseline["cpu_usage_usec"]),
            "cpu_nr_throttled": max(0, snapshot["nr_throttled"] - baseline["nr_throttled"]),
            "memory_oom_events": max(0, snapshot["oom"] - baseline["oom"]),
            "memory_oom_kill_events": max(0, snapshot["oom_kill"] - baseline["oom_kill"]),
            "pids_max_events": max(0, snapshot["pids_max"] - baseline["pids_max"]),
            "filesystem_bytes": filesystem_bytes,
            "filesystem_baseline_bytes": baseline_bytes,
            "wall_seconds": elapsed,
            "network_syscalls": int(response.get("network_trace_bytes", 0)) if response else 0,
            "network_denied": network,
            "executor_uid": slot.uid,
            "deny_control": True,
            "deny_secrets": True,
            "deny_sibling": True,
            "deny_outside_workspace": True,
            "deny_public_network": True,
            **self._cleanup_observed(cleanup),
        }

    @staticmethod
    def _cleanup_observed(cleanup: Mapping[str, object]) -> dict[str, int | float | str | bool]:
        return {
            "processes_after_kill": int(cleanup["after"]),
            "cleanup_seconds": float(cleanup["seconds"]),
            "cleanup_result": str(cleanup["result"]),
        }

    def _finish(self, envelope_id: str) -> None:
        reserved = self._reservations.pop(envelope_id, None)
        with self._cancel_lock:
            self._cancelled.discard(envelope_id)
        if reserved is None:
            return
        slot, cgroup, _baseline = reserved
        self.pool.release(slot)

    def _configure_envelope(self, cgroup: Path, request: AttemptRequest) -> None:
        envelope = request.envelope
        if self.cgroup is None:
            slot = self._slot_for_cgroup(cgroup)
            self.pool.broker_request(
                {
                    "type": "configure",
                    "slot": slot.number,
                    "cpu_quota_us": envelope.cpu_quota_us,
                    "memory_bytes": envelope.memory_bytes,
                    "pids": envelope.pids,
                }
            )
            return
        self._cgroup_write(cgroup, "cpu.max", f"{envelope.cpu_quota_us} 100000\n")
        self._cgroup_write(cgroup, "memory.max", f"{envelope.memory_bytes}\n")
        self._cgroup_write(cgroup, "memory.swap.max", "0\n")
        self._cgroup_write(cgroup, "pids.max", f"{envelope.pids}\n")

    @staticmethod
    def _envelope_cgroup(slot: AttemptSlot, envelope_id: str) -> Path:
        if not envelope_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in envelope_id
        ):
            raise ValueError("envelope id is unsafe")
        return slot.cgroup_path / "envelopes" / "active"

    def _cgroup_counters(self, path: Path) -> dict[str, int]:
        return {
            "cpu_usage_usec": self._counter(path, "cpu.stat", "usage_usec"),
            "nr_throttled": self._counter(path, "cpu.stat", "nr_throttled"),
            "oom": self._counter(path, "memory.events", "oom"),
            "oom_kill": self._counter(path, "memory.events", "oom_kill"),
            "pids_max": self._counter(path, "pids.events", "max"),
        }

    def _cgroup_write(self, path: Path, name: str, value: str) -> None:
        if self.cgroup is not None and hasattr(self.cgroup, "write"):
            self.cgroup.write(path, name, value)
            return
        (path / name).write_text(value, encoding="ascii")

    def _counter(self, path: Path, name: str, key: str) -> int:
        if self.cgroup is not None and hasattr(self.cgroup, "counter"):
            return int(self.cgroup.counter(path, name, key))
        return _counter(path / name, key)

    def _kill_cgroup(self, path: Path) -> None:
        if self.cgroup is not None and hasattr(self.cgroup, "kill"):
            self.cgroup.kill(path)
            return
        try:
            slot = self._slot_for_cgroup(path)
            self.pool.broker_request({"type": "kill", "slot": slot.number})
        except OSError:
            pass

    def _slot_for_cgroup(self, path: Path) -> AttemptSlot:
        resolved = path.resolve(strict=False)
        for slot in self.pool.slots:
            if resolved == (slot.cgroup_path / "envelopes" / "active").resolve(strict=False):
                return slot
        raise OSError("Attempt cgroup path is outside the fixed pool")

    def _cgroup_count(self, path: Path) -> int:
        if self.cgroup is not None and hasattr(self.cgroup, "count"):
            return int(self.cgroup.count(path))
        try:
            return len((path / "cgroup.procs").read_text(encoding="ascii").split())
        except OSError:
            return 0

    def _cleanup(self, path: Path, seconds: float) -> dict[str, object]:
        started = self.clock()
        self._kill_cgroup(path)
        end = started + seconds
        while (after := self._cgroup_count(path)) and self.clock() < end:
            self.sleep(min(MAX_POLL_INTERVAL, max(0.0, end - self.clock())))
        elapsed = max(0.0, self.clock() - started)
        return {
            "after": after,
            "complete": after == 0,
            "seconds": elapsed,
            "result": "empty" if after == 0 else "deadline",
        }


def _first_cause(
    cancelled: bool, cpu: bool, memory: bool, pids: bool, filesystem: bool, network: bool, wall: bool
) -> ResourceOutcome | None:
    for active, outcome in (
        (cancelled, ResourceOutcome.CANCELLED),
        (cpu, ResourceOutcome.CPU),
        (memory, ResourceOutcome.MEMORY),
        (pids, ResourceOutcome.PIDS),
        (filesystem, ResourceOutcome.FILESYSTEM),
        (network, ResourceOutcome.NETWORK),
        (wall, ResourceOutcome.DEADLINE),
    ):
        if active:
            return outcome
    return None


def _await_worker_result(connection: socket.socket, seconds: float) -> dict[str, object] | None:
    connection.settimeout(seconds)
    try:
        value = json.loads(connection.recv(MAX_FRAME))
        return value if isinstance(value, dict) and value.get("type") in {"result", "error"} else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    finally:
        connection.setblocking(False)


def _collect_workspace(workspace: Path, limit: int) -> tuple[list[tuple[Path, bytes, int]], int]:
    workspace = Path(workspace)
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("workspace must be a directory")
    records: list[tuple[Path, bytes, int]] = []
    total = 0
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if path.is_symlink():
            raise ValueError("workspace contains a link")
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            continue
        if not stat.S_ISREG(mode):
            raise ValueError("workspace contains a non-regular file")
        data = path.read_bytes()
        total += len(data)
        if len(records) >= MAX_WORKSPACE_FILES or total > limit:
            raise ValueError("workspace exceeds transfer bound")
        records.append((relative, data, stat.S_IMODE(mode)))
    return records, total


def _clear_workspace(path: Path) -> None:
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _materialize_workspace(
    path: Path, records: list[tuple[Path, bytes, int]], *, hostile_uid: int | None = None
) -> None:
    _clear_workspace(path)
    for relative, data, mode in records:
        target = path / relative
        missing = []
        parent = target.parent
        while parent != path and not parent.exists():
            missing.append(parent)
            parent = parent.parent
        for directory in reversed(missing):
            directory.mkdir()
            if hostile_uid is not None:
                if os.geteuid() == 0:
                    os.chown(directory, 0, hostile_uid)
                os.chmod(directory, 0o770)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            os.write(descriptor, data)
            effective = (mode & 0o700) | ((mode & 0o700) >> 3) if hostile_uid is not None else mode & 0o777
            os.fchmod(descriptor, effective)
            if hostile_uid is not None and os.geteuid() == 0:
                os.fchown(descriptor, hostile_uid, hostile_uid)
        finally:
            os.close(descriptor)


def _workspace_usage(path: Path) -> tuple[int, bool]:
    total = 0
    unsafe = False
    try:
        entries = list(path.rglob("*"))
    except OSError:
        return 0, True
    if len(entries) > MAX_WORKSPACE_FILES:
        unsafe = True
    for entry in entries[: MAX_WORKSPACE_FILES + 1]:
        try:
            mode = entry.lstat().st_mode
            if stat.S_ISREG(mode):
                total += entry.stat().st_size
            elif not stat.S_ISDIR(mode):
                unsafe = True
        except OSError:
            unsafe = True
    return total, unsafe


def _sync_workspace(source: Path, destination: Path, limit: int) -> None:
    records, _total = _collect_workspace(source, limit)
    _clear_workspace(destination)
    _materialize_workspace(destination, records)


def _recv_nonblocking(connection: socket.socket) -> dict[str, object] | None:
    try:
        data = connection.recv(MAX_FRAME + 1)
    except BlockingIOError:
        return None
    if not data:
        raise ConnectionError("Attempt worker closed its control descriptor")
    if len(data) > MAX_FRAME:
        raise ValueError("Attempt worker response exceeds bound")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Attempt worker response is not an object")
    return value


def _counter(path: Path, key: str) -> int:
    try:
        for line in path.read_text(encoding="ascii").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == key:
                return int(parts[1])
    except (OSError, ValueError):
        pass
    return 0


def _output(response: Mapping[str, object] | None) -> bytes:
    if not response or not isinstance(response.get("output"), str):
        return b""
    try:
        return base64.b64decode(response["output"], validate=True)[:MAX_OUTPUT]
    except (ValueError, binascii.Error):
        return b""


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = ["AttemptRuntime", "RuntimeUnavailable"]
