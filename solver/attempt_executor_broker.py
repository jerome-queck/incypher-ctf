"""Fixed cgroup write broker retained after the controller drops setup capabilities."""

from __future__ import annotations

import json
import ctypes
import socket
import struct
import sys
from pathlib import Path


MAX_FRAME = 4096
CAP_SYS_ADMIN = 21
LINUX_CAPABILITY_VERSION_3 = 0x20080522
PR_CAPBSET_DROP = 24


class _CapabilityHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapabilityData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def _retain_cgroup_capability() -> None:
    library = ctypes.CDLL(None, use_errno=True)
    header = _CapabilityHeader(LINUX_CAPABILITY_VERSION_3, 0)
    data = (_CapabilityData * 2)()
    final = int(Path("/proc/sys/kernel/cap_last_cap").read_text(encoding="ascii"))
    for capability in range(final + 1):
        if capability != CAP_SYS_ADMIN and library.prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "cannot narrow cgroup broker bounding set")
    data[0].effective = 1 << CAP_SYS_ADMIN
    data[0].permitted = 1 << CAP_SYS_ADMIN
    if library.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        raise OSError(ctypes.get_errno(), "cannot narrow cgroup broker capability sets")


def _write(path: Path, name: str, value: str) -> None:
    (path / name).write_text(value, encoding="ascii")


def serve(connection: socket.socket, slots: tuple[Path, ...]) -> int:
    _retain_cgroup_capability()
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    connection.send(b'{"type":"ready"}')
    while True:
        data, ancillary, _flags, _address = connection.recvmsg(MAX_FRAME, socket.CMSG_SPACE(struct.calcsize("3i")))
        credentials = next(
            (
                struct.unpack("3i", value[: struct.calcsize("3i")])
                for level, kind, value in ancillary
                if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS
            ),
            None,
        )
        try:
            request = json.loads(data)
            if credentials is None or credentials[1] != 0:
                raise PermissionError("cgroup broker peer is not the controller")
            if request.get("type") == "close":
                return 0
            slot_number = int(request["slot"])
            if slot_number < 0:
                raise IndexError("invalid cgroup slot")
            slot = slots[slot_number]
            operation = request.get("type")
            if operation == "configure":
                quota = int(request["cpu_quota_us"])
                memory = int(request["memory_bytes"])
                pids = int(request["pids"])
                if not 1 <= quota <= 100_000 or memory <= 0 or pids <= 0:
                    raise ValueError("invalid cgroup limits")
                _write(slot, "cpu.max", f"{quota} 100000\n")
                _write(slot, "memory.max", f"{memory}\n")
                _write(slot, "memory.swap.max", "0\n")
                _write(slot, "pids.max", f"{pids}\n")
            elif operation == "attach":
                pid = int(request["pid"])
                if pid <= 0:
                    raise ValueError("invalid pid")
                _write(slot, "cgroup.procs", f"{pid}\n")
            elif operation == "kill":
                _write(slot, "cgroup.kill", "1\n")
            else:
                raise ValueError("unsupported cgroup operation")
            response = {"type": "ok"}
        except (KeyError, IndexError, TypeError, ValueError, OSError, PermissionError) as error:
            response = {"type": "error", "error": f"{type(error).__name__}: {error}"[:512]}
        connection.send(json.dumps(response, sort_keys=True, separators=(",", ":")).encode())


def main() -> int:
    if len(sys.argv) < 3:
        return 2
    return serve(socket.socket(fileno=int(sys.argv[1])), tuple(Path(value) for value in sys.argv[2:]))


if __name__ == "__main__":
    raise SystemExit(main())
