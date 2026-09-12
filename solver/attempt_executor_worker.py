"""Capability-free PID-namespace init for one fixed hostile-execution slot."""

from __future__ import annotations

import base64
import ctypes
import json
import os
import resource
import selectors
import signal
import socket
import subprocess
import sys


MAX_FRAME = 4 * 1024 * 1024
MAX_OUTPUT = 1 * 1024 * 1024
MAX_ARGV = 1024
PR_SET_DUMPABLE = 4
STRACE = "/usr/bin/strace"
NETWORK_MARKERS = ("socket(", "socketpair(", "connect(", "bind(", "sendto(", "sendmsg(")


def encode_frame(message: dict[str, object]) -> bytes:
    frame = json.dumps(message, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(frame) > MAX_FRAME:
        raise ValueError("worker protocol frame exceeds bound")
    return frame


def read_frame(connection: socket.socket) -> dict[str, object] | None:
    data = connection.recv(MAX_FRAME + 1)
    if not data:
        return None
    if len(data) > MAX_FRAME:
        raise ValueError("worker protocol frame exceeds bound")
    value = json.loads(data.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("worker frame must be a JSON object")
    return value


def _hardening() -> None:
    try:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (AttributeError, OSError):
        pass
    for caught in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(caught, signal.SIG_IGN)


def _validated(request: dict[str, object]) -> tuple[str, tuple[str, ...], int, str, str]:
    nonce = request.get("nonce")
    argv = request.get("argv")
    filesystem_bytes = request.get("filesystem_bytes")
    if (
        not isinstance(nonce, str)
        or len(nonce) != 32
        or any(character not in "0123456789abcdef" for character in nonce)
    ):
        raise ValueError("launch nonce is invalid")
    if (
        not isinstance(argv, list)
        or not argv
        or len(argv) > MAX_ARGV
        or any(not isinstance(item, str) or not item or "\x00" in item for item in argv)
    ):
        raise ValueError("argv is invalid")
    if isinstance(filesystem_bytes, bool) or not isinstance(filesystem_bytes, int) or filesystem_bytes <= 0:
        raise ValueError("filesystem limit is invalid")
    target_socket = request.get("target_socket", "")
    target_generation = request.get("target_generation", "")
    if target_socket not in {"", "/work/.target.sock"} or not isinstance(target_generation, str):
        raise ValueError("Target port binding is invalid")
    if bool(target_socket) != bool(target_generation):
        raise ValueError("Target port binding is incomplete")
    return nonce, tuple(argv), filesystem_bytes, target_socket, target_generation


def _gate(connection: socket.socket, nonce: str) -> None:
    connection.send(encode_frame({"type": "gated", "nonce": nonce}))
    answer = read_frame(connection)
    if answer != {"type": "go", "nonce": nonce}:
        os._exit(125)


def _trace_bytes(trace: int) -> bytes:
    return os.pread(trace, MAX_FRAME, 0)


def _capture(
    process: subprocess.Popen[bytes], trace: int, connection: socket.socket, nonce: str, target_socket: str = ""
) -> tuple[bytes, bool, bytes, int]:
    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    selector = selectors.DefaultSelector()
    selector.register(descriptor, selectors.EVENT_READ)
    selector.register(connection, selectors.EVENT_READ)
    output = bytearray()
    total = 0
    truncated = False
    network_reported = False
    while process.poll() is None:
        for key, _events in selector.select(0.05):
            if key.fileobj is connection:
                message = read_frame(connection)
                if message != {"type": "terminate", "nonce": nonce}:
                    raise ValueError("Attempt termination request is invalid")
                connection.send(encode_frame({"type": "term-sent", "pids": list(_terminate_descendants())}))
                continue
            chunk = os.read(descriptor, 65536)
            total += len(chunk)
            if len(output) < MAX_OUTPUT:
                room = MAX_OUTPUT - len(output)
                output.extend(chunk[:room])
                truncated = truncated or len(chunk) > room
            elif chunk:
                truncated = True
        traced = _trace_bytes(trace)
        if not network_reported and _network_breach(traced.decode("utf-8", errors="replace"), target_socket):
            connection.send(
                encode_frame(
                    {
                        "type": "breach",
                        "network_breach": True,
                        "network_trace_bytes": len(traced),
                    }
                )
            )
            network_reported = True
    while True:
        try:
            chunk = os.read(descriptor, 65536)
        except BlockingIOError:
            break
        if not chunk:
            break
        total += len(chunk)
        if len(output) < MAX_OUTPUT:
            room = MAX_OUTPUT - len(output)
            output.extend(chunk[:room])
            truncated = truncated or len(chunk) > room
        else:
            truncated = True
    selector.close()
    process.stdout.close()
    return bytes(output), truncated, _trace_bytes(trace), total


def _terminate_descendants(proc_root: str = "/proc") -> tuple[int, ...]:
    descendants = tuple(sorted(int(name) for name in os.listdir(proc_root) if name.isdigit() and int(name) != 1))
    for pid in descendants:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return descendants


def _run(connection: socket.socket, request: dict[str, object]) -> dict[str, object]:
    nonce, argv, filesystem_bytes, target_socket, target_generation = _validated(request)
    trace = os.memfd_create("attempt-network-trace", flags=getattr(os, "MFD_CLOEXEC", 0))

    def before_exec() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (filesystem_bytes, filesystem_bytes))
        _gate(connection, nonce)
        connection.close()

    command = [STRACE, "-f", "-qq", "-e", "trace=network", "-o", f"/proc/self/fd/{trace}", "--", *argv]
    try:
        environment = {"HOME": "/home/attempt", "PATH": "/usr/local/bin:/usr/bin:/bin", "TMPDIR": "/work"}
        if target_socket:
            environment.update(
                INCYPHER_TARGET_SOCKET=target_socket,
                INCYPHER_TARGET_GENERATION=target_generation,
            )
        process = subprocess.Popen(
            command,
            cwd="/work",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            close_fds=True,
            pass_fds=(connection.fileno(), trace),
            preexec_fn=before_exec,
        )
        output, truncated, traced_bytes, total = _capture(process, trace, connection, nonce, target_socket)
        process.wait()
        traced = traced_bytes.decode("utf-8", errors="replace")
        network_breach = _network_breach(traced, target_socket)
        return {
            "type": "result",
            "exit_code": process.returncode,
            "output": base64.b64encode(output).decode("ascii"),
            "output_truncated": truncated,
            "output_bytes_total": total,
            "network_breach": network_breach,
            "network_trace_bytes": len(traced.encode("utf-8")),
        }
    finally:
        os.close(trace)


def _network_breach(trace: str, target_socket: str) -> bool:
    network = [line for line in trace.splitlines() if any(marker in line for marker in NETWORK_MARKERS)]
    if not network:
        return False
    return any(not _allowed_target_socket_call(line, target_socket) for line in network)


def _allowed_target_socket_call(line: str, target_socket: str) -> bool:
    if any(
        marker in line for marker in ("socket(AF_UNIX", "socket(AF_LOCAL", "socketpair(AF_UNIX", "socketpair(AF_LOCAL")
    ):
        return True
    if "connect(" in line:
        return (bool(target_socket) and target_socket in line) or (
            ("sa_family=AF_UNIX" in line or "sa_family=AF_LOCAL" in line) and " = -1 " in line
        )
    if "bind(" in line:
        return "sa_family=AF_UNIX" in line or "sa_family=AF_LOCAL" in line
    if "sendto(" in line:
        return (
            line.rstrip().endswith("NULL, 0) = 0")
            or ", NULL, 0) = " in line
            or (("AF_UNIX" in line or "AF_LOCAL" in line) and " = -1 " in line)
        )
    if "sendmsg(" in line:
        return "msg_name=NULL" in line
    return False


def serve(connection: socket.socket) -> int:
    _hardening()
    connection.send(encode_frame({"type": "ready", "uid": os.getuid()}))
    while True:
        try:
            request = read_frame(connection)
            if request is None or request.get("type") == "close":
                return 0
            if request.get("type") == "terminate":
                response = {"type": "term-sent", "pids": list(_terminate_descendants())}
            elif request.get("type") != "launch":
                raise ValueError("unsupported request")
            else:
                response = _run(connection, request)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            response = {"type": "error", "error": f"{type(error).__name__}: {error}"[:512]}
        connection.send(encode_frame(response))


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    descriptor = int(sys.argv[1])
    return serve(socket.socket(fileno=descriptor))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["MAX_FRAME", "MAX_OUTPUT", "encode_frame", "read_frame", "serve"]
