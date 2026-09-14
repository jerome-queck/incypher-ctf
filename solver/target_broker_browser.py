"""Controller-owned browser process for one generation-scoped Target handle."""

from __future__ import annotations

import base64
import json
import secrets
import socket
import subprocess
import threading
import time
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import canonical_bytes
from solver.target_broker_contracts import (
    BrowserObservations,
    BrowserSessionRequest,
    BrowserTransportResult,
    TargetEndpoint,
    TargetLimits,
    TargetOutcome,
    TargetProtocol,
)

BROWSER_WORKER = Path("/opt/solver/solver/target_broker_browser_worker.py")
BROWSER_DRIVER = Path("/opt/solver/tool-supply/web/browser_driver.py")
BROWSER_UID = 30_001
BROWSER_LAUNCHER_FD_ENV = "INCYPHER_BROWSER_LAUNCHER_FD"
MAX_DRIVER_OUTPUT_BYTES = 1024 * 1024
MAX_DRIVER_OUTPUT_BASE64_BYTES = 4 * ((MAX_DRIVER_OUTPUT_BYTES + 2) // 3)
MAX_FRAME_BYTES = 2 * 1024 * 1024


class BrowserLauncher:
    """One pre-dropped UID worker that launches fresh browser processes."""

    def __init__(
        self,
        connection: socket.socket,
        process: subprocess.Popen[bytes] | None,
        *,
        await_ready: bool = True,
    ) -> None:
        self._connection = connection
        self._process = process
        self._request_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._closed = False
        if await_ready and _receive(connection) != {"status": "ready"}:
            raise OSError("browser launcher did not attest readiness")

    def controller_environment(self) -> tuple[str, tuple[int, ...]]:
        descriptor = self._connection.fileno()
        if descriptor < 0 or self._closed:
            raise OSError("browser launcher is closed")
        return str(descriptor), (descriptor,)

    def reset(self) -> None:
        reset_id = secrets.token_hex(16)
        _send(self._connection, {"command": "reset", "reset_id": reset_id}, self._send_lock)
        while True:
            response = _receive(self._connection)
            if response.get("status") == "ready" and response.get("reset_id") == reset_id:
                return

    def execute(
        self,
        request_id: str,
        document: Mapping[str, object],
        *,
        timeout_seconds: float,
    ) -> tuple[str, int, bytes] | None:
        if not self._request_lock.acquire(blocking=False):
            return None
        try:
            if self._closed:
                return None
            _send(
                self._connection,
                {
                    "command": "browse",
                    "document": document,
                    "request_id": request_id,
                    "timeout_seconds": timeout_seconds,
                },
                self._send_lock,
            )
            response = _receive(self._connection)
            if response.get("request_id") != request_id:
                raise OSError("browser launcher response changed request identity")
            outcome = response.get("outcome")
            returncode = response.get("returncode")
            stdout = response.get("stdout")
            if not isinstance(outcome, str) or not isinstance(returncode, int) or not isinstance(stdout, str):
                raise OSError("browser launcher response is invalid")
            if len(stdout) > MAX_DRIVER_OUTPUT_BASE64_BYTES:
                raise OSError("browser launcher output exceeds its byte bound")
            return outcome, returncode, base64.b64decode(stdout, validate=True)
        finally:
            self._request_lock.release()

    def cancel(self, request_id: str) -> None:
        if not self._closed:
            _send(
                self._connection,
                {"command": "cancel", "request_id": request_id},
                self._send_lock,
            )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._process is not None:
            try:
                _send(self._connection, {"command": "shutdown"}, self._send_lock)
            except OSError:
                pass
        self._connection.close()
        if self._process is None:
            return
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=1)


_prepared_launcher: BrowserLauncher | None = None


def prepare_browser_launcher() -> BrowserLauncher:
    """Create the fixed-UID launcher while startup still owns SETUID/SETGID."""

    global _prepared_launcher
    if _prepared_launcher is not None:
        raise OSError("browser launcher is already prepared")
    controller, worker = socket.socketpair()
    try:
        process = subprocess.Popen(
            _browser_worker_command(worker.fileno()),
            close_fds=True,
            pass_fds=(worker.fileno(),),
            env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=None,
            start_new_session=True,
        )
    finally:
        worker.close()
    try:
        controller.settimeout(3)
        _prepared_launcher = BrowserLauncher(controller, process)
        controller.settimeout(None)
        return _prepared_launcher
    except Exception:
        controller.close()
        process.kill()
        process.wait(timeout=1)
        raise


def attach_browser_launcher(environ: Mapping[str, str]) -> BrowserLauncher:
    """Attach one controller Boot to the pre-admitted launcher and fence stale work."""

    global _prepared_launcher
    if _prepared_launcher is not None:
        raise OSError("browser launcher is already attached")
    try:
        descriptor = int(environ[BROWSER_LAUNCHER_FD_ENV])
    except (KeyError, TypeError, ValueError) as error:
        raise OSError("browser launcher descriptor is absent") from error
    connection = socket.socket(fileno=descriptor)
    connection.settimeout(3)
    launcher = BrowserLauncher(connection, None, await_ready=False)
    try:
        launcher.reset()
    except Exception:
        launcher.close()
        raise
    connection.settimeout(None)
    _prepared_launcher = launcher
    return launcher


def browser_launcher_environment() -> tuple[str, tuple[int, ...]]:
    if _prepared_launcher is None:
        raise OSError("browser launcher is not prepared")
    return _prepared_launcher.controller_environment()


def close_browser_launcher() -> None:
    global _prepared_launcher
    launcher, _prepared_launcher = _prepared_launcher, None
    if launcher is not None:
        launcher.close()


class BrowserSessionTransport:
    """Launch a fresh browser context and retain only its cancellable process."""

    def __init__(
        self,
        endpoint: TargetEndpoint,
        address: str,
        limits: TargetLimits,
        subresources: Mapping[str, TargetEndpoint],
        launcher: BrowserLauncher | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._address = address
        self._limits = limits
        self._subresources = {name: (origin, _resolve(origin)) for name, origin in subresources.items()}
        self._launcher = launcher or _prepared_launcher
        self._lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_request = ""
        self._closed = False

    def browse(self, request: BrowserSessionRequest) -> BrowserTransportResult:
        if not self._lock.acquire(blocking=False):
            return BrowserTransportResult(TargetOutcome.DENIED)
        started = time.monotonic()
        try:
            with self._state_lock:
                if self._closed:
                    return BrowserTransportResult(TargetOutcome.REVOKED)
            admitted = [self._origin_document(*self._subresources[name]) for name in request.admitted_subresources]
            document = {
                "origin": self._origin_document(self._endpoint, self._address),
                "path": request.path,
                "wait_selector": request.wait_selector,
                "download_selector": request.download_selector,
                "admitted_subresources": admitted,
                "limits": {
                    "response_bytes": self._limits.max_response_bytes,
                    "timeout_ms": int(self._limits.timeout_seconds * 1000),
                },
            }
            if self._launcher is None:
                return BrowserTransportResult(TargetOutcome.DENIED)
            request_id = secrets.token_hex(16)
            with self._state_lock:
                if self._closed:
                    return BrowserTransportResult(TargetOutcome.REVOKED)
                self._active_request = request_id
            try:
                launched = self._launcher.execute(
                    request_id,
                    document,
                    timeout_seconds=self._limits.timeout_seconds,
                )
            finally:
                with self._state_lock:
                    self._active_request = ""
            if self._closed:
                return BrowserTransportResult(TargetOutcome.REVOKED)
            if launched is None:
                return BrowserTransportResult(TargetOutcome.DENIED)
            outcome, returncode, stdout = launched
            if outcome == "cancelled":
                return BrowserTransportResult(TargetOutcome.REVOKED)
            if outcome == "timeout":
                return BrowserTransportResult(
                    TargetOutcome.TIMEOUT,
                    elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                )
            if (
                outcome != "completed"
                or returncode != 0
                or len(stdout) > min(MAX_DRIVER_OUTPUT_BYTES, self._limits.max_response_bytes)
            ):
                return BrowserTransportResult(TargetOutcome.DENIED)
            return _decode(stdout, elapsed_ms=max(0, int((time.monotonic() - started) * 1000)))
        except (KeyError, OSError, ValueError, json.JSONDecodeError):
            return BrowserTransportResult(TargetOutcome.UNREACHABLE)
        finally:
            self._lock.release()

    def close(self) -> None:
        with self._state_lock:
            self._closed = True
            request_id = self._active_request
        if request_id and self._launcher is not None:
            self._launcher.cancel(request_id)

    @staticmethod
    def _origin_document(endpoint: TargetEndpoint, address: str) -> dict[str, object]:
        if endpoint.protocol not in {TargetProtocol.HTTP, TargetProtocol.HTTPS}:
            raise ValueError("browser subresource origin must be HTTP")
        return {
            "scheme": endpoint.protocol.value,
            "host": endpoint.host,
            "port": endpoint.port,
            "address": address,
        }


def _resolve(endpoint: TargetEndpoint) -> str:
    try:
        return socket.getaddrinfo(endpoint.host, endpoint.port, type=socket.SOCK_STREAM)[0][4][0]
    except OSError as error:
        raise ValueError("browser subresource cannot be resolved at capability creation") from error


def _browser_worker_command(descriptor: int) -> list[str]:
    return [
        "/usr/bin/bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-pid",
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
        "--ro-bind",
        "/etc",
        "/etc",
        "--tmpfs",
        "/tmp",
        "--chmod",
        "1777",
        "/tmp",
        "--dir",
        "/home",
        "--dir",
        "/home/browser",
        "--ro-bind",
        str(BROWSER_WORKER),
        "/browser-worker.py",
        "--ro-bind",
        str(BROWSER_DRIVER),
        "/browser-driver.py",
        "--clearenv",
        "--setenv",
        "HOME",
        "/home/browser",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "/usr/bin/setpriv",
        "--reuid",
        str(BROWSER_UID),
        "--regid",
        str(BROWSER_UID),
        "--clear-groups",
        "--bounding-set=-all",
        "--no-new-privs",
        "/usr/bin/python3",
        "-I",
        "/browser-worker.py",
        str(descriptor),
    ]


def _receive_exact(connection: socket.socket, size: int) -> bytes:
    held = bytearray()
    while len(held) < size:
        chunk = connection.recv(size - len(held))
        if not chunk:
            raise OSError("browser launcher closed")
        held.extend(chunk)
    return bytes(held)


def _receive(connection: socket.socket) -> dict[str, object]:
    size = int.from_bytes(_receive_exact(connection, 4), "big")
    if size < 2 or size > MAX_FRAME_BYTES:
        raise OSError("browser launcher frame is invalid")
    value = json.loads(_receive_exact(connection, size))
    if not isinstance(value, dict):
        raise OSError("browser launcher response is invalid")
    return value


def _send(connection: socket.socket, document: Mapping[str, object], lock: threading.Lock) -> None:
    encoded = canonical_bytes(document)
    if len(encoded) > MAX_FRAME_BYTES:
        raise OSError("browser launcher request exceeds its frame bound")
    with lock:
        connection.sendall(len(encoded).to_bytes(4, "big") + encoded)


def _decode(raw: bytes, *, elapsed_ms: int) -> BrowserTransportResult:
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {"dom", "network", "local_storage", "session_storage", "downloads"}:
        raise ValueError("browser observation shape is invalid")
    dom = value["dom"]
    network = value["network"]
    local_storage = value["local_storage"]
    session_storage = value["session_storage"]
    downloads = value["downloads"]
    if (
        not isinstance(dom, str)
        or not isinstance(network, list)
        or not isinstance(local_storage, list)
        or not isinstance(session_storage, list)
        or not isinstance(downloads, list)
    ):
        raise ValueError("browser observation fields are invalid")
    observations = BrowserObservations(
        dom,
        tuple((str(method), str(url), int(status), int(size)) for method, url, status, size in network),
        tuple((str(name), str(content)) for name, content in local_storage),
        tuple((str(name), str(content)) for name, content in session_storage),
        tuple((str(name), str(digest), int(size)) for name, digest, size in downloads),
    )
    visible_bytes = (
        len(dom.encode())
        + sum(
            len(name.encode()) + len(content.encode())
            for name, content in (*observations.local_storage, *observations.session_storage)
        )
        + sum(size for _name, _digest, size in observations.downloads)
    )
    response_bytes = max(sum(item[3] for item in observations.network), visible_bytes)
    return BrowserTransportResult(TargetOutcome.ANSWERED, observations, elapsed_ms, response_bytes)


__all__ = [
    "BROWSER_LAUNCHER_FD_ENV",
    "BrowserLauncher",
    "BrowserSessionTransport",
    "attach_browser_launcher",
    "browser_launcher_environment",
    "close_browser_launcher",
    "prepare_browser_launcher",
]
