"""Capability-free browser child launcher prepared before controller privilege drop."""

from __future__ import annotations

import base64
import json
import os
import queue
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

BROWSER_DRIVER = Path("/browser-driver.py")
MAX_FRAME_BYTES = 2 * 1024 * 1024
MAX_DRIVER_STDOUT_BYTES = 1024 * 1024
MAX_DRIVER_STDERR_BYTES = 64 * 1024
READ_BLOCK_BYTES = 64 * 1024


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    held = bytearray()
    while len(held) < size:
        chunk = connection.recv(size - len(held))
        if not chunk:
            raise EOFError("browser launcher controller closed")
        held.extend(chunk)
    return bytes(held)


def _receive(connection: socket.socket) -> dict[str, object]:
    size = int.from_bytes(_receive_exact(connection, 4), "big")
    if size < 2 or size > MAX_FRAME_BYTES:
        raise ValueError("browser launcher frame is invalid")
    value = json.loads(_receive_exact(connection, size))
    if not isinstance(value, dict):
        raise ValueError("browser launcher document is invalid")
    return value


def _send(connection: socket.socket, document: Mapping[str, object]) -> None:
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_FRAME_BYTES:
        raise ValueError("browser launcher response exceeds its frame bound")
    connection.sendall(len(encoded).to_bytes(4, "big") + encoded)


def _kill(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        process.kill()
    process.wait(timeout=1)


def _read_bounded(stream, limit: int, held: list[bytes], limited: threading.Event) -> None:
    size = 0
    while True:
        chunk = stream.read(min(READ_BLOCK_BYTES, limit - size + 1))
        if not chunk:
            return
        remaining = limit - size
        if len(chunk) > remaining:
            if remaining:
                held.append(chunk[:remaining])
            limited.set()
            return
        held.append(chunk)
        size += len(chunk)


def _bounded_communicate(
    process: subprocess.Popen[bytes],
    document: bytes,
    *,
    stdout_limit: int = MAX_DRIVER_STDOUT_BYTES,
    stderr_limit: int = MAX_DRIVER_STDERR_BYTES,
) -> tuple[bytes, bytes, bool]:
    """Feed one driver and drain both pipes without retaining over-limit output."""

    if stdout_limit < 0 or stderr_limit < 0:
        raise ValueError("browser driver output bound is invalid")
    if process.stdin is None or process.stdout is None or process.stderr is None:
        raise OSError("browser driver pipes are unavailable")
    limited = threading.Event()
    stdout: list[bytes] = []
    stderr: list[bytes] = []
    readers = (
        threading.Thread(target=_read_bounded, args=(process.stdout, stdout_limit, stdout, limited), daemon=True),
        threading.Thread(target=_read_bounded, args=(process.stderr, stderr_limit, stderr, limited), daemon=True),
    )
    for reader in readers:
        reader.start()
    try:
        process.stdin.write(document)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        process.stdin.close()
    while any(reader.is_alive() for reader in readers):
        if limited.is_set():
            _kill(process)
            break
        time.sleep(0.01)
    if limited.is_set():
        _kill(process)
    process.wait()
    for reader in readers:
        reader.join(1)
    if any(reader.is_alive() for reader in readers):
        _kill(process)
        for reader in readers:
            reader.join(1)
    if any(reader.is_alive() for reader in readers):
        raise OSError("browser driver output collector did not stop")
    return b"".join(stdout), b"".join(stderr), limited.is_set()


def _collect(
    process: subprocess.Popen[bytes],
    document: bytes,
    result: queue.Queue[tuple[bytes, bytes, bool]],
) -> None:
    result.put(_bounded_communicate(process, document))


def _serve_browse(
    connection: socket.socket,
    inbox: queue.Queue[dict[str, object]],
    command: Mapping[str, object],
    transport_descriptor: int,
) -> tuple[bool, str]:
    request_id = command.get("request_id")
    document = command.get("document")
    timeout_seconds = command.get("timeout_seconds")
    if (
        not isinstance(request_id, str)
        or not isinstance(document, dict)
        or not isinstance(timeout_seconds, (int, float))
    ):
        raise ValueError("browser launcher request is invalid")
    process = subprocess.Popen(
        ["/usr/bin/python3", str(BROWSER_DRIVER), str(transport_descriptor)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        pass_fds=(transport_descriptor,),
        env={"HOME": "/tmp", "PATH": "/usr/local/bin:/usr/bin:/bin", "TMPDIR": "/tmp"},
        start_new_session=True,
    )
    collected: queue.Queue[tuple[bytes, bytes, bool]] = queue.Queue(maxsize=1)
    collector = threading.Thread(
        target=_collect,
        args=(process, json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n", collected),
        daemon=True,
    )
    collector.start()
    deadline = time.monotonic() + float(timeout_seconds)
    outcome = "completed"
    stop = False
    reset_id = ""
    while collector.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            outcome = "timeout"
            _kill(process)
            break
        try:
            control = inbox.get(timeout=min(0.05, remaining))
        except queue.Empty:
            continue
        operation = control.get("command")
        if operation == "cancel" and control.get("request_id") == request_id:
            outcome = "cancelled"
            _kill(process)
            break
        if operation == "shutdown":
            outcome = "cancelled"
            stop = True
            _kill(process)
            break
        if operation == "reset" and isinstance(control.get("reset_id"), str):
            outcome = "cancelled"
            reset_id = str(control["reset_id"])
            _kill(process)
            break
        raise ValueError("browser launcher received an overlapping request")
    collector.join(1)
    if collector.is_alive():
        raise OSError("browser launcher collector did not stop")
    stdout, stderr, output_limited = collected.get_nowait()
    if output_limited and outcome == "completed":
        outcome = "output-limit"
    _send(
        connection,
        {
            "outcome": outcome,
            "request_id": request_id,
            "returncode": int(process.returncode or 0),
            "stdout": base64.b64encode(stdout).decode(),
            "stderr": base64.b64encode(stderr[:65536]).decode(),
        },
    )
    return stop, reset_id


def serve(connection: socket.socket, transport_descriptor: int) -> None:
    inbox: queue.Queue[dict[str, object]] = queue.Queue()

    def receive() -> None:
        try:
            while True:
                inbox.put(_receive(connection))
        except (EOFError, OSError, ValueError, json.JSONDecodeError):
            inbox.put({"command": "shutdown"})

    receiver = threading.Thread(target=receive, daemon=True)
    receiver.start()
    _send(connection, {"status": "ready"})
    while True:
        command = inbox.get()
        operation = command.get("command")
        if operation == "shutdown":
            return
        if operation == "reset" and isinstance(command.get("reset_id"), str):
            _send(connection, {"status": "ready", "reset_id": command["reset_id"]})
            continue
        if operation != "browse":
            raise ValueError("browser launcher command is invalid")
        stop, reset_id = _serve_browse(connection, inbox, command, transport_descriptor)
        if reset_id:
            _send(connection, {"status": "ready", "reset_id": reset_id})
        if stop:
            return


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2:
        return 2
    connection = socket.socket(fileno=int(arguments[0]))
    try:
        serve(connection, int(arguments[1]))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
