"""Prepare the fixed, capability-free hostile worker pool before Boot drops setup authority."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Callable, Mapping

from solver.isolation_seccomp import write_seccomp_policy


CGROUP_MOUNT = Path("/run/cgroup-parent")
POOL_ROOT = Path("/run/incypher-attempts")
POOL_NAME = "attempt-executor"
MAX_SLOTS = 2
ATTEMPT_UID = 20_000
WORKER_SOURCE = Path("/opt/solver/solver/attempt_executor_worker.py")
TARGET_CLIENT_SOURCE = Path("/opt/solver/solver/target_broker_worker_client.py")
RESEARCH_CLIENT_SOURCE = Path("/opt/solver/solver/research_broker_worker_client.py")
TOOL_SUPPLY_ROOT = Path("/opt/solver/tool-supply")
BROKER_SOURCE = Path("/opt/solver/solver/attempt_executor_broker.py")
REQUIRED_CONTROLLERS = ("cpu", "memory", "pids")
POOL_ENV = "INCYPHER_ATTEMPT_POOL"


@dataclass
class AttemptSlot:
    number: int
    uid: int
    cgroup_path: Path
    work_path: Path
    connection: socket.socket
    process: subprocess.Popen[bytes] | None = None
    busy: bool = False
    healthy: bool = True
    lock: Lock = field(default_factory=Lock, repr=False)

    @property
    def executor_uid(self) -> int:
        return self.uid

    def is_healthy(self) -> bool:
        return self.healthy and (self.process is None or self.process.poll() is None)


@dataclass
class AttemptPool:
    container_cgroup: Path
    root: Path
    slots: tuple[AttemptSlot, ...]
    broker: socket.socket | None = None
    broker_process: subprocess.Popen[bytes] | None = None
    broker_lock: Lock = field(default_factory=Lock, repr=False)
    closed: bool = False

    def broker_request(self, request: dict[str, object]) -> None:
        if self.broker is None:
            raise OSError("Attempt cgroup broker is unavailable")
        with self.broker_lock:
            self.broker.send(json.dumps(request, sort_keys=True, separators=(",", ":")).encode())
            response = json.loads(self.broker.recv(4096))
        if response != {"type": "ok"}:
            raise OSError(str(response.get("error", "Attempt cgroup broker failed")))

    def acquire(self) -> AttemptSlot | None:
        for slot in self.slots:
            with slot.lock:
                if not self.closed and not slot.busy and slot.is_healthy():
                    slot.busy = True
                    return slot
        return None

    def release(self, slot: AttemptSlot) -> None:
        with slot.lock:
            slot.busy = False

    @property
    def healthy(self) -> bool:
        return not self.closed and bool(self.slots) and all(slot.is_healthy() for slot in self.slots)

    def controller_environment(self) -> tuple[str, tuple[int, ...]]:
        """Describe only inherited anonymous descriptors and fixed prepared paths."""

        document = {
            "container_cgroup": str(self.container_cgroup),
            "root": str(self.root),
            "broker_fd": self.broker.fileno() if self.broker is not None else -1,
            "slots": [
                {
                    "number": slot.number,
                    "uid": slot.uid,
                    "cgroup_path": str(slot.cgroup_path),
                    "work_path": str(slot.work_path),
                    "fd": slot.connection.fileno(),
                }
                for slot in self.slots
            ],
        }
        descriptors = [slot.connection.fileno() for slot in self.slots]
        if self.broker is not None:
            descriptors.append(self.broker.fileno())
        return json.dumps(document, sort_keys=True, separators=(",", ":")), tuple(descriptors)


def discover_container_cgroup(mount: Path = CGROUP_MOUNT) -> Path:
    if not mount.is_dir():
        raise OSError(f"cgroup mount is absent: {mount}")
    domains = sorted(path for path in mount.iterdir() if path.is_dir() and not path.is_symlink())
    if len(domains) != 1:
        raise OSError(f"expected one container cgroup, found {len(domains)}")
    return domains[0].resolve()


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="ascii")


def _fixed_worker_command(work: Path, control_fd: int = 3, seccomp_fd: int = 4, *, uid: int = ATTEMPT_UID) -> list[str]:
    """Return the only privileged setup command; hostile bytes never enter it."""

    return [
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
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--dir",
        "/etc",
        "--ro-bind",
        "/etc/john",
        "/etc/john",
        "--ro-bind",
        "/etc/alternatives",
        "/etc/alternatives",
        "--dir",
        "/opt",
        "--dir",
        "/opt/solver",
        "--ro-bind",
        str(TOOL_SUPPLY_ROOT),
        str(TOOL_SUPPLY_ROOT),
        "--tmpfs",
        "/tmp",
        "--chmod",
        "1777",
        "/tmp",
        "--bind",
        str(work),
        "/work",
        "--ro-bind",
        str(WORKER_SOURCE),
        "/attempt-worker.py",
        "--ro-bind",
        str(TARGET_CLIENT_SOURCE),
        "/target-client.py",
        "--ro-bind",
        str(RESEARCH_CLIENT_SOURCE),
        "/research-client.py",
        "--dir",
        "/run",
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
        str(seccomp_fd),
        "/usr/bin/setpriv",
        "--reuid",
        str(uid),
        "--regid",
        str(uid),
        "--clear-groups",
        "--bounding-set=-all",
        "--no-new-privs",
        "/usr/bin/python3",
        "-I",
        "/attempt-worker.py",
        str(control_fd),
    ]


def _enable_controllers(path: Path) -> None:
    available = set((path / "cgroup.controllers").read_text(encoding="ascii").split())
    missing = set(REQUIRED_CONTROLLERS) - available
    if missing:
        raise OSError(f"Attempt cgroup controllers missing: {', '.join(sorted(missing))}")
    _write(path / "cgroup.subtree_control", " ".join(f"+{name}" for name in REQUIRED_CONTROLLERS) + "\n")


def _prepare_cgroup(container: Path, number: int) -> Path:
    slot = container / POOL_NAME / f"slot-{number}"
    slot.mkdir(exist_ok=False)
    _enable_controllers(slot)
    (slot / "worker").mkdir()
    envelopes = slot / "envelopes"
    envelopes.mkdir()
    _enable_controllers(envelopes)
    active = envelopes / "active"
    active.mkdir()
    for path in (active, *active.iterdir()):
        os.chown(path, os.getuid(), os.getgid())
    return slot


def _launch_worker(
    command: list[str],
    child_fd: int,
    seccomp_fd: int,
    *,
    launcher: Callable[..., subprocess.Popen[bytes]],
) -> subprocess.Popen[bytes]:
    return launcher(
        command,
        close_fds=True,
        pass_fds=(child_fd, seccomp_fd),
        env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=None,
    )


def _ready(connection: socket.socket, uid: int, timeout: float = 3.0) -> None:
    connection.settimeout(timeout)
    try:
        value = json.loads(connection.recv(65536))
    finally:
        connection.settimeout(None)
    if value != {"type": "ready", "uid": uid}:
        raise OSError("Attempt worker did not attest its fixed identity")


def _launch_broker(slots: list[AttemptSlot]) -> tuple[socket.socket, subprocess.Popen[bytes]]:
    controller, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
    command = [
        "/usr/bin/python3",
        "-I",
        str(BROKER_SOURCE),
        str(child.fileno()),
        *(str(slot.cgroup_path / "envelopes" / "active") for slot in slots),
    ]
    try:
        process = subprocess.Popen(
            command,
            close_fds=True,
            pass_fds=(child.fileno(),),
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
        )
    finally:
        child.close()
    controller.settimeout(3.0)
    try:
        if json.loads(controller.recv(4096)) != {"type": "ready"}:
            raise OSError("Attempt cgroup broker did not become ready")
    finally:
        controller.settimeout(None)
    controller.settimeout(3.0)
    return controller, process


def prepare_attempt_pool(
    *,
    cgroup_mount: Path = CGROUP_MOUNT,
    root: Path = POOL_ROOT,
    slots: int = MAX_SLOTS,
    launcher: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> AttemptPool:
    """Create every namespace, descriptor and cgroup before capabilities disappear."""

    if slots < 1 or slots > MAX_SLOTS:
        raise ValueError("Attempt pool supports one or two slots")
    container = discover_container_cgroup(Path(cgroup_mount))
    pool_root = Path(root)
    if pool_root.exists() or (container / POOL_NAME).exists():
        raise OSError("Attempt pool has unexplained startup residue")
    pool_root.mkdir(parents=True, mode=0o700)
    groups = set(os.getgroups())
    groups.update(ATTEMPT_UID + number for number in range(slots))
    os.setgroups(sorted(groups))
    created: list[AttemptSlot] = []
    try:
        pool_cgroup = container / POOL_NAME
        pool_cgroup.mkdir()
        _enable_controllers(pool_cgroup)
        seccomp = pool_root / "attempt-seccomp.bpf"
        write_seccomp_policy(seccomp)
        for number in range(slots):
            uid = ATTEMPT_UID + number
            work = pool_root / f"slot-{number}" / "work"
            work.mkdir(parents=True, mode=0o700)
            os.chown(work, 0, uid)
            os.chmod(work, 0o770)
            cgroup = _prepare_cgroup(container, number)
            controller, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
            controller.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            seccomp_fd = os.open(seccomp, os.O_RDONLY)
            try:
                process = _launch_worker(
                    _fixed_worker_command(work, child.fileno(), seccomp_fd, uid=uid),
                    child.fileno(),
                    seccomp_fd,
                    launcher=launcher,
                )
            finally:
                os.close(seccomp_fd)
                child.close()
            _write(cgroup / "worker" / "cgroup.procs", f"{process.pid}\n")
            slot = AttemptSlot(number, uid, cgroup, work, controller, process=process)
            created.append(slot)
            _ready(controller, uid)
        broker, broker_process = _launch_broker(created)
        return AttemptPool(container, pool_root, tuple(created), broker, broker_process)
    except Exception:
        close_attempt_pool(AttemptPool(container, pool_root, tuple(created)))
        raise


def attach_attempt_pool(environ: Mapping[str, str]) -> AttemptPool:
    """Reconstitute the controller side only from Supervisor-inherited descriptors."""

    try:
        document = json.loads(environ[POOL_ENV])
        root = Path(document["root"])
        container = Path(document["container_cgroup"])
        slots = tuple(
            AttemptSlot(
                int(row["number"]),
                int(row["uid"]),
                Path(row["cgroup_path"]),
                Path(row["work_path"]),
                socket.socket(fileno=int(row["fd"])),
            )
            for row in document["slots"]
        )
        broker_fd = int(document["broker_fd"])
        broker = socket.socket(fileno=broker_fd) if broker_fd >= 0 else None
        if broker is not None:
            broker.settimeout(3.0)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, OSError) as error:
        raise OSError("Attempt pool inheritance is invalid") from error
    if not slots or any(slot.uid != ATTEMPT_UID + slot.number for slot in slots):
        raise OSError("Attempt pool identity is invalid")
    return AttemptPool(container, root, slots, broker)


def close_attempt_pool(pool: AttemptPool | None) -> None:
    if pool is None or pool.closed:
        return
    pool.closed = True
    if pool.broker is not None:
        try:
            pool.broker.send(b'{"type":"close"}')
        except OSError:
            pass
        pool.broker.close()
    if pool.broker_process is not None and pool.broker_process.poll() is None:
        try:
            pool.broker_process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pool.broker_process.kill()
            pool.broker_process.wait(timeout=1)
    for slot in pool.slots:
        try:
            slot.connection.send(json.dumps({"type": "close"}).encode())
        except OSError:
            pass
        slot.connection.close()
        if slot.process is not None and slot.process.poll() is None:
            slot.process.terminate()
            try:
                slot.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                slot.process.kill()
                slot.process.wait(timeout=1)
        try:
            _write(slot.cgroup_path / "worker" / "cgroup.kill", "1\n")
        except OSError:
            pass
    shutil.rmtree(pool.root, ignore_errors=True)
    for slot in reversed(pool.slots):
        for path in (
            slot.cgroup_path / "worker",
            slot.cgroup_path / "envelopes" / "active",
            slot.cgroup_path / "envelopes",
            slot.cgroup_path,
        ):
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        (pool.container_cgroup / POOL_NAME).rmdir()
    except OSError:
        pass


__all__ = [
    "ATTEMPT_UID",
    "AttemptPool",
    "AttemptSlot",
    "CGROUP_MOUNT",
    "MAX_SLOTS",
    "POOL_ENV",
    "POOL_NAME",
    "attach_attempt_pool",
    "close_attempt_pool",
    "discover_container_cgroup",
    "prepare_attempt_pool",
]
