"""Fixed privileged setup for the strict Isolation-profile startup probe.

This program accepts no arguments and runs only through ``isolation_runtime.STRICT_PROBE_COMMAND``.
It creates a private, disposable boundary, attacks it from UID 20000, tears down the whole cgroup,
and writes one sanitized JSON receipt to stdout. No hostile command or path crosses its interface.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from solver.attempt_executor_pool import discover_container_cgroup
from solver.isolation_seccomp import write_seccomp_policy

BASE = Path("/run/incypher-isolation-preflight")
CGROUP_MOUNT = Path("/run/cgroup-parent")
ATTEMPT_UID = 20000
BROKER_UID = 30000
MEMORY_MAX = 32 * 1024 * 1024
WORK_MAX = 8 * 1024 * 1024

HOSTILE_PROBE = r"""import json
import os
import socket
import subprocess
from pathlib import Path

checks = {}
checks["user"] = os.getuid() == 20000 and os.getgid() == 20000
status = Path("/proc/self/status").read_text()
checks["syscall"] = (
    "CapBnd:\t0000000000000000" in status
    and "NoNewPrivs:\t1" in status
    and "Seccomp:\t2" in status
    and subprocess.run(["unshare", "--user", "--map-root-user", "true"], capture_output=True).returncode != 0
)
checks["mount"] = subprocess.run(["touch", "/usr/escape"], capture_output=True).returncode != 0
checks["filesystem"] = Path("/work/own.txt").read_text() == "own work\n"
checks["control"] = all(not Path(path).exists() for path in ("/run/control", "/run/control.sock"))
checks["host-mount"] = all(not Path(path).exists() for path in ("/state", "/opt/solver"))
pid_one = Path("/proc/1/cmdline").read_bytes()
checks["sibling"] = (
    not Path("/run/sibling-work").exists()
    and b"solver.supervisor" not in pid_one
    and b"solver.isolation_preflight" not in pid_one
)
checks["board"] = all(name not in os.environ for name in ("CTFD_URL", "CTFD_API_TOKEN", "TEAM_KEY")) and not Path(
    "/run/board.sock"
).exists()
checks["codex"] = "CODEX_HOME" not in os.environ and all(
    not Path(path).exists() for path in ("/root/.codex/auth.json", "/run/codex.sock")
)
checks["cpa"] = all(name not in os.environ for name in ("CPA_URL", "CPA_TOKEN")) and not Path("/run/cpa.sock").exists()
try:
    outer_environ = Path("/proc/1/environ").read_bytes()
except PermissionError:
    outer_environ = b""
checks["credential"] = "PROBE_CREDENTIAL" not in os.environ and b"prototype-secret" not in outer_environ
checks["pid"] = len([path for path in Path("/proc").iterdir() if path.name.isdigit()]) < 5

blocked = []
for endpoint in (("1.1.1.1", 443), ("8.8.8.8", 53)):
    public = socket.socket()
    public.settimeout(0.2)
    blocked.append(public.connect_ex(endpoint) != 0)
    public.close()
checks["network"] = all(blocked)

broker = socket.socket(socket.AF_UNIX)
broker.settimeout(3)
broker.connect("/run/target.sock")
broker.sendall(b"ping")
checks["target-broker"] = broker.recv(4) == b"pong"
broker.close()

Path("/work/checks.json").write_text(json.dumps(checks, sort_keys=True, separators=(",", ":")))
Path("/work/ready").touch()
child = os.fork()
if child == 0:
    grandchild = os.fork()
    if grandchild == 0:
        time.sleep(30)
    time.sleep(30)
time.sleep(30)
"""

BROKER = r"""import os
import socket
import struct
import sys

sock = socket.socket(socket.AF_UNIX)
sock.bind(sys.argv[1])
os.chmod(sys.argv[1], 0o666)
sock.listen(1)
sock.settimeout(10)
connection, _ = sock.accept()
pid, uid, gid = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
with open(sys.argv[2], "w", encoding="ascii") as receipt:
    receipt.write(f"{pid} {uid} {gid}\n")
if connection.recv(16) == b"ping":
    connection.sendall(b"pong")
connection.close()
sock.close()
"""


def main() -> int:
    """Exercise the fixed boundary and print its receipt."""

    try:
        receipt = _probe()
        control_baseline = int(receipt.pop("_control_baseline"))
        receipt["owned_residue"] = _owned_residue(control_baseline)
    except Exception as error:
        print(f"strict isolation probe failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


def _probe() -> dict[str, object]:
    container_cgroup = _container_cgroup()
    control_cgroup = container_cgroup / "control"
    attempt_cgroup = container_cgroup / "attempt"
    _prepare_paths()
    if not control_cgroup.is_dir() or not attempt_cgroup.is_dir():
        raise RuntimeError("prepared control cgroups are absent")
    control_baseline = _cgroup_count(control_cgroup)
    broker: subprocess.Popen[bytes] | None = None
    try:
        _set_limits(attempt_cgroup)
        _mount_work()
        broker = _start_broker()
        process = _start_hostile(attempt_cgroup)
        _wait_for(BASE / "work" / "ready")
        checks = json.loads((BASE / "work" / "checks.json").read_text())
        broker.wait(timeout=5)
        _require_success("broker", broker.returncode)
        _pid, broker_peer_uid, _gid = (int(value) for value in (BASE / "broker" / "peer").read_text().split())
        checks["credential"] = checks.get("credential") is True and broker_peer_uid == ATTEMPT_UID
        processes_before = _cgroup_count(attempt_cgroup)
        (attempt_cgroup / "cgroup.kill").write_text("1\n")
        process.wait(timeout=5)
        _wait_for_empty(attempt_cgroup)
        processes_after = _cgroup_count(attempt_cgroup)
        checks["process-tree"] = processes_before >= 3 and processes_after == 0
        checks["resource"] = _probe_resources(attempt_cgroup)
        return {
            "checks": checks,
            "broker_peer_uid": broker_peer_uid,
            "processes_before_kill": processes_before,
            "processes_after_kill": processes_after,
            "owned_residue": [],
            "_control_baseline": control_baseline,
        }
    finally:
        _kill_cgroup(attempt_cgroup)
        _wait_for_empty(attempt_cgroup)
        if broker is not None:
            _stop_process(broker)
        try:
            attempt_cgroup.rmdir()
        except FileNotFoundError:
            pass
        _unmount_work()
        shutil.rmtree(BASE, ignore_errors=True)


def _container_cgroup() -> Path:
    if not CGROUP_MOUNT.is_dir() or not os.access(CGROUP_MOUNT, os.W_OK):
        raise RuntimeError("dedicated cgroup parent is absent or read-only")
    try:
        return discover_container_cgroup(CGROUP_MOUNT)
    except OSError as error:
        raise RuntimeError(str(error)) from error


def _owned_residue(control_baseline: int) -> list[str]:
    container = _container_cgroup()
    owned = (BASE, container / "attempt")
    residue = [str(path) for path in owned if path.exists()]
    if _cgroup_count(container / "control") != control_baseline:
        residue.append("control-cgroup-process-count")
    if str(BASE) in Path("/proc/self/mountinfo").read_text():
        residue.append("attempt-work-mount")
    return residue


def _prepare_paths() -> None:
    if BASE.exists():
        raise RuntimeError(f"owned runtime path already exists: {BASE}")
    for name in ("control", "broker", "work", "sibling-work"):
        (BASE / name).mkdir(parents=True, exist_ok=True)
    (BASE / "work" / "own.txt").write_text("own work\n")
    (BASE / "sibling-work" / "secret").write_text("sibling secret\n")
    (BASE / "work" / "hostile_probe.py").write_text(HOSTILE_PROBE)


def _set_limits(cgroup: Path) -> None:
    (cgroup / "cpu.max").write_text("20000 100000\n")
    (cgroup / "memory.max").write_text(f"{MEMORY_MAX}\n")
    (cgroup / "memory.swap.max").write_text("0\n")
    (cgroup / "pids.max").write_text("16\n")


def _mount_work() -> None:
    subprocess.run(
        ["/usr/bin/mount", "-t", "tmpfs", "-o", f"size={WORK_MAX},nosuid,nodev,noexec", "tmpfs", str(BASE / "work")],
        check=True,
    )
    (BASE / "work" / "own.txt").write_text("own work\n")
    (BASE / "work" / "hostile_probe.py").write_text(HOSTILE_PROBE)


def _start_broker() -> subprocess.Popen[bytes]:
    broker_dir = BASE / "broker"
    broker_dir.chmod(0o777)
    process = subprocess.Popen(
        [
            "/usr/bin/setpriv",
            "--reuid",
            str(BROKER_UID),
            "--regid",
            str(BROKER_UID),
            "--clear-groups",
            "/usr/bin/python3",
            "-I",
            "-c",
            BROKER,
            str(broker_dir / "target.sock"),
            str(broker_dir / "peer"),
        ],
        env={"PATH": "/usr/bin:/bin"},
    )
    _wait_for(broker_dir / "target.sock")
    return process


def _start_hostile(cgroup: Path) -> subprocess.Popen[bytes]:
    seccomp = BASE / "control" / "attempt-seccomp.bpf"
    write_seccomp_policy(seccomp)
    descriptor = os.open(seccomp, os.O_RDONLY)
    command = [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-uts",
        "--unshare-ipc",
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/sbin",
        "/sbin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--dir",
        "/etc",
        "--dir",
        "/etc/ssl",
        "--ro-bind",
        "/etc/ssl/certs",
        "/etc/ssl/certs",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--bind",
        str(BASE / "work"),
        "/work",
        "--dir",
        "/run",
        "--ro-bind",
        str(BASE / "broker" / "target.sock"),
        "/run/target.sock",
        "--tmpfs",
        "/tmp",
        "--chmod",
        "1777",
        "/tmp",
        "--dir",
        "/home",
        "--dir",
        "/home/attempt",
        "--chdir",
        "/work",
        "--clearenv",
        "--setenv",
        "HOME",
        "/home/attempt",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "--seccomp",
        str(descriptor),
        "/usr/bin/setpriv",
        "--reuid",
        str(ATTEMPT_UID),
        "--regid",
        str(ATTEMPT_UID),
        "--clear-groups",
        "--bounding-set=-all",
        "--no-new-privs",
        "/usr/bin/python3",
        "-I",
        "/work/hostile_probe.py",
    ]
    process = subprocess.Popen(
        ["/bin/sh", "-c", 'read -r gate; exec "$@"', "strict-attempt", *command],
        stdin=subprocess.PIPE,
        pass_fds=(descriptor,),
        env={"PATH": "/usr/bin:/bin", "PROBE_CREDENTIAL": "prototype-secret-never-visible"},
    )
    os.close(descriptor)
    (cgroup / "cgroup.procs").write_text(f"{process.pid}\n")
    assert process.stdin is not None
    process.stdin.write(b"go\n")
    process.stdin.close()
    return process


def _probe_resources(cgroup: Path) -> bool:
    memory_before = _event(cgroup / "memory.events", "oom_kill")
    memory = _gated_process(cgroup, ["/usr/bin/python3", "-c", "x=bytearray(80*1024*1024); print(len(x))"])
    memory.wait(timeout=5)
    memory_enforced = memory.returncode != 0 and _event(cgroup / "memory.events", "oom_kill") > memory_before

    (cgroup / "pids.max").write_text("6\n")
    pids_before = _event(cgroup / "pids.events", "max")
    pids = _gated_process(cgroup, ["/bin/sh", "-c", "for i in 1 2 3 4 5 6 7 8; do sleep 1 & done; wait"])
    pids.wait(timeout=5)
    pids_enforced = _event(cgroup / "pids.events", "max") > pids_before
    _kill_cgroup(cgroup)
    _wait_for_empty(cgroup)
    (cgroup / "pids.max").write_text("16\n")

    throttled_before = _event(cgroup / "cpu.stat", "nr_throttled")
    cpu = _gated_process(cgroup, ["/usr/bin/timeout", "1", "/usr/bin/yes"])
    cpu.wait(timeout=3)
    throttled = _event(cgroup / "cpu.stat", "nr_throttled") > throttled_before

    io_probe = _gated_process(
        cgroup,
        ["/usr/bin/dd", "if=/dev/zero", "of=/tmp/strict-io.bin", "bs=1M", "count=1", "oflag=direct", "status=none"],
    )
    io_probe.wait(timeout=3)
    devices = (cgroup / "io.stat").read_text().splitlines()
    io_enforced = io_probe.returncode == 0 and bool(devices)
    if devices:
        device = devices[0].split()[0]
        (cgroup / "io.max").write_text(f"{device} wbps=1048576\n")
        started = time.monotonic()
        limited = _gated_process(
            cgroup,
            [
                "/usr/bin/dd",
                "if=/dev/zero",
                "of=/tmp/strict-io-limited.bin",
                "bs=1M",
                "count=4",
                "oflag=direct",
                "status=none",
            ],
        )
        limited.wait(timeout=8)
        elapsed = time.monotonic() - started
        io_enforced = "wbps=1048576" in (cgroup / "io.max").read_text() and limited.returncode == 0 and elapsed >= 2.0
    Path("/tmp/strict-io.bin").unlink(missing_ok=True)
    Path("/tmp/strict-io-limited.bin").unlink(missing_ok=True)

    overfill = BASE / "work" / "overfill"
    try:
        with overfill.open("wb") as target:
            target.write(b"0" * (WORK_MAX + 1024 * 1024))
        tmpfs_enforced = False
    except OSError:
        tmpfs_enforced = overfill.stat().st_size <= WORK_MAX
    overfill.unlink(missing_ok=True)
    return memory_enforced and pids_enforced and throttled and io_enforced and tmpfs_enforced


def _gated_process(cgroup: Path, command: list[str]) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        ["/bin/sh", "-c", 'read -r gate; exec "$@"', "strict-resource", *command],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (cgroup / "cgroup.procs").write_text(f"{process.pid}\n")
    assert process.stdin is not None
    process.stdin.write(b"go\n")
    process.stdin.close()
    return process


def _event(path: Path, name: str) -> int:
    values = {line.split()[0]: int(line.split()[1]) for line in path.read_text().splitlines()}
    return values.get(name, 0)


def _cgroup_count(cgroup: Path) -> int:
    return len(cgroup.joinpath("cgroup.procs").read_text().splitlines())


def _kill_cgroup(cgroup: Path) -> None:
    if cgroup.exists() and _cgroup_count(cgroup):
        (cgroup / "cgroup.kill").write_text("1\n")


def _wait_for_empty(cgroup: Path, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while cgroup.exists() and _cgroup_count(cgroup):
        if time.monotonic() >= deadline:
            raise RuntimeError("hostile cgroup did not empty")
        time.sleep(0.01)


def _wait_for(path: Path, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() >= deadline:
            raise RuntimeError(f"timed out waiting for {path.name}")
        time.sleep(0.01)


def _require_success(what: str, returncode: int | None) -> None:
    if returncode != 0:
        raise RuntimeError(f"{what} exited {returncode}")


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def _unmount_work() -> None:
    work = BASE / "work"
    if work.exists():
        subprocess.run(
            ["/usr/bin/umount", str(work)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )


if __name__ == "__main__":
    raise SystemExit(main())
