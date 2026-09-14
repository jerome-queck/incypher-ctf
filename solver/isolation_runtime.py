"""The fixed privileged setup call and irreversible capability drop for strict isolation."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from solver.isolation import (
    STRICT_CONTROLS,
    IsolationReason,
    IsolationRefusal,
    ProbeResult,
)
from solver.attempt_executor_pool import discover_container_cgroup

STRICT_PROBE_COMMAND = (
    "/usr/bin/unshare",
    "--mount",
    "--pid",
    "--net",
    "--uts",
    "--ipc",
    "--fork",
    "--kill-child=KILL",
    "--mount-proc",
    "/usr/bin/python3",
    "-I",
    "-c",
    (
        "import runpy,sys;sys.path.insert(0,'/opt/solver');"
        "runpy.run_module('solver.isolation_worker',run_name='__main__')"
    ),
)

CAP_NET_ADMIN = 12
CAP_SYS_ADMIN = 21
LINUX_CAPABILITY_VERSION_3 = 0x20080522
PR_CAPBSET_DROP = 24
PR_CAP_AMBIENT = 47
PR_CAP_AMBIENT_CLEAR_ALL = 4
CGROUP_MOUNT = Path("/run/cgroup-parent")
REQUIRED_CONTROLLERS = ("cpu", "io", "memory", "pids")


def run_strict_probe(
    *,
    execute: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    prepare: Callable[[], None] | None = None,
) -> ProbeResult:
    """Run the one fixed setup program and parse its sanitized final receipt."""

    (prepare or _prepare_control_cgroup)()
    try:
        result = execute(
            STRICT_PROBE_COMMAND,
            capture_output=True,
            check=False,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired as error:
        raise IsolationRefusal(IsolationReason.BOUNDARY_PROBE, "fixed probe timed out after 45 seconds") from error
    except OSError as error:
        raise IsolationRefusal(IsolationReason.BOUNDARY_PROBE, f"fixed probe could not start: {error}") from error
    if result.returncode != 0:
        detail = result.stderr.strip() if result.stderr.strip() else f"exit {result.returncode}"
        raise IsolationRefusal(IsolationReason.BOUNDARY_PROBE, detail)
    try:
        supplied = json.loads(result.stdout)
        checks = supplied["checks"]
        return ProbeResult(
            checks=tuple((control, checks[control] is True) for control in STRICT_CONTROLS),
            broker_peer_uid=int(supplied["broker_peer_uid"]),
            processes_before_kill=int(supplied["processes_before_kill"]),
            processes_after_kill=int(supplied["processes_after_kill"]),
            owned_residue=tuple(str(path) for path in supplied["owned_residue"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise IsolationRefusal(IsolationReason.BOUNDARY_PROBE, "the fixed probe returned no valid receipt") from error


def _prepare_control_cgroup() -> None:
    """Vacate the container domain before its worker enables child controllers."""

    if not CGROUP_MOUNT.is_dir() or not os.access(CGROUP_MOUNT, os.W_OK):
        raise IsolationRefusal(IsolationReason.BOUNDARY_PROBE, "dedicated cgroup parent is absent or read-only")
    try:
        domain = discover_container_cgroup(CGROUP_MOUNT)
    except OSError as error:
        raise IsolationRefusal(
            IsolationReason.BOUNDARY_PROBE,
            str(error),
        ) from error
    control = domain / "control"
    attempt = domain / "attempt"
    try:
        control.mkdir()
        attempt.mkdir()
        (control / "cgroup.procs").write_text(f"{os.getpid()}\n")
        available = set((domain / "cgroup.controllers").read_text().split())
        missing = set(REQUIRED_CONTROLLERS) - available
        if missing:
            raise RuntimeError(f"cgroup controllers missing: {', '.join(sorted(missing))}")
        (domain / "cgroup.subtree_control").write_text(
            " ".join(f"+{controller}" for controller in REQUIRED_CONTROLLERS) + "\n"
        )
    except (OSError, RuntimeError) as error:
        raise IsolationRefusal(IsolationReason.BOUNDARY_PROBE, f"cannot prepare control cgroup: {error}") from error


class _CapabilityHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapabilityData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def drop_outer_capabilities(
    *,
    libc: Any | None = None,
    cap_last: int | None = None,
    read_status: Callable[[], str] | None = None,
) -> None:
    """Empty PID 1's capability sets after the fixed launcher has finished setup."""

    if os.name != "posix" or not os.path.exists("/proc/self/status"):
        raise IsolationRefusal(IsolationReason.CAPABILITY_DROP, "Linux capability state is unavailable")
    library = libc or ctypes.CDLL(None, use_errno=True)
    header = _CapabilityHeader(LINUX_CAPABILITY_VERSION_3, 0)
    data = (_CapabilityData * 2)()
    if library.capget(ctypes.byref(header), ctypes.byref(data)) != 0:
        _raise_capability_error("capget", library)
    final_capability = int(Path("/proc/sys/kernel/cap_last_cap").read_text().strip()) if cap_last is None else cap_last
    for capability in range(final_capability + 1):
        if library.prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) != 0:
            _raise_capability_error(f"prctl drop {capability}", library)
    if library.prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0:
        _raise_capability_error("prctl clear ambient", library)
    for word in data:
        word.effective = 0
        word.permitted = 0
        word.inheritable = 0
    if library.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        _raise_capability_error("capset", library)
    status = (read_status or (lambda: Path("/proc/self/status").read_text()))()
    values = {line.split(":", 1)[0]: line.split(":", 1)[1].strip() for line in status.splitlines() if ":" in line}
    names = ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
    if any(name not in values or int(values[name], 16) != 0 for name in names):
        raise IsolationRefusal(IsolationReason.CAPABILITY_DROP, "capability sets are not empty after drop")


def _raise_capability_error(operation: str, library: Any) -> None:
    error_number = ctypes.get_errno()
    detail = os.strerror(error_number) if error_number else "failed"
    raise IsolationRefusal(IsolationReason.CAPABILITY_DROP, f"{operation}: {detail}")


__all__ = ["STRICT_PROBE_COMMAND", "drop_outer_capabilities", "run_strict_probe"]
