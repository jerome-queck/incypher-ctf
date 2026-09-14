"""Pathname IPC adapter for the Target broker runtime."""

from __future__ import annotations

import base64
import json
import socket
import threading
from collections.abc import Mapping
from typing import Protocol
from pathlib import Path

from solver.capability import CapabilityRefused
from solver.event_store_storage import canonical_bytes
from solver.local_ipc import receive_line
from solver.target_broker_contracts import (
    TARGET_EXCHANGE_COMMAND,
    TARGET_BROWSER_COMMAND,
    TARGET_HTTP_FUZZ_COMMAND,
    TARGET_HTTP_SESSION_COMMAND,
    TARGET_TCP_SESSION_COMMAND,
    BrowserObservations,
    BrowserSessionRequest,
    HttpSessionRequest,
    HttpFuzzRequest,
    TargetOutcome,
    TargetProtocol,
    TargetProvenance,
    TargetResult,
    TcpSessionRequest,
)

MAX_CLIENTS = 8
CLIENT_TIMEOUT_SECONDS = 1.0


class TargetBroker(Protocol):
    def claim(self, connection: socket.socket, generation_id: str) -> str: ...

    def exchange(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult: ...

    def http_session(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult: ...

    def http_fuzz(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult: ...

    def tcp_session(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult: ...

    def browser(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult: ...

    def revoke(self, handle: str) -> None: ...


class TargetBrokerClient:
    def __init__(self, path: Path, handle: str) -> None:
        self._path = Path(path)
        self._handle = handle
        self._closed = False

    @classmethod
    def claim(cls, path: Path, generation_id: str) -> "TargetBrokerClient":
        answer = _request(path, {"command": "claim", "generation_id": generation_id})
        if answer.get("status") != "issued":
            raise CapabilityRefused()
        return cls(path, str(answer["handle"]))

    def http(self, method: str, path: str, body: bytes = b"") -> TargetResult:
        return self._exchange({"method": method, "path": path, "body": base64.b64encode(body).decode()})

    def http_session(self, request: HttpSessionRequest) -> TargetResult:
        return self._request(TARGET_HTTP_SESSION_COMMAND, request.document())

    def http_fuzz(self, request: HttpFuzzRequest) -> TargetResult:
        return self._request(TARGET_HTTP_FUZZ_COMMAND, request.document())

    def tcp(self, payload: bytes) -> TargetResult:
        return self._exchange({"body": base64.b64encode(payload).decode()})

    def tcp_session(self, request: TcpSessionRequest) -> TargetResult:
        return self._request(TARGET_TCP_SESSION_COMMAND, request.document())

    def browser(self, request: BrowserSessionRequest) -> TargetResult:
        return self._request(TARGET_BROWSER_COMMAND, request.document())

    def _exchange(self, request: dict[str, object]) -> TargetResult:
        return self._request(TARGET_EXCHANGE_COMMAND, request)

    def _request(self, command: str, request: dict[str, object]) -> TargetResult:
        if self._closed:
            return TargetResult(TargetOutcome.REVOKED)
        answer = _request(
            self._path,
            {"command": command, "handle": self._handle, "request": request},
        )
        return self._decode(answer)

    def _decode(self, answer: dict[str, object]) -> TargetResult:
        result = answer.get("result")
        if not isinstance(result, dict):
            return TargetResult(TargetOutcome.UNREACHABLE)
        provenance = result.get("provenance", {})
        browser = result.get("browser", {})
        return TargetResult(
            TargetOutcome(str(result["outcome"])),
            base64.b64decode(str(result.get("body", ""))),
            int(result.get("status", 0)),
            TargetProvenance(
                challenge_id=str(provenance.get("challenge_id", "")),
                generation_id=str(provenance.get("generation_id", "")),
                endpoint=str(provenance.get("endpoint", "")),
                protocol=TargetProtocol(str(provenance["protocol"])) if provenance.get("protocol") else None,
                request_bytes=int(provenance.get("request_bytes", 0)),
                response_bytes=int(provenance.get("response_bytes", 0)),
                transcript_digest=str(provenance.get("transcript_digest", "")),
                elapsed_ms=int(provenance.get("elapsed_ms", 0)),
                resolved_address=str(provenance.get("resolved_address", "")),
                server_name=str(provenance.get("server_name", "")),
                certificate_sha256=str(provenance.get("certificate_sha256", "")),
            ),
            str(result.get("request_id", "")),
            tuple((str(name), str(value)) for name, value in result.get("headers", [])),
            tuple(str(value) for value in result.get("redirect_chain", [])),
            tuple((str(name), str(value)) for name, value in result.get("cookies", [])),
            BrowserObservations(
                str(browser.get("dom", "")),
                tuple(
                    (str(method), str(url), int(status), int(size))
                    for method, url, status, size in browser.get("network", [])
                ),
                tuple((str(name), str(value)) for name, value in browser.get("local_storage", [])),
                tuple((str(name), str(value)) for name, value in browser.get("session_storage", [])),
                tuple((str(name), str(digest), int(size)) for name, digest, size in browser.get("downloads", [])),
            ),
        )

    def close(self) -> None:
        if not self._closed:
            _request(self._path, {"command": "revoke", "handle": self._handle})
            self._closed = True


class TargetBrokerService:
    def __init__(self, path: Path, runtime: TargetBroker) -> None:
        self.path = Path(path)
        self._runtime = runtime
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._clients = threading.BoundedSemaphore(MAX_CLIENTS)
        self._client_threads: set[threading.Thread] = set()
        self._client_lock = threading.Lock()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True, name="target-broker")
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("Target broker listener did not start")

    def close(self) -> None:
        self._stop.set()
        if self._listener:
            self._listener.close()
        if self._thread:
            self._thread.join(5)
        with self._client_lock:
            clients = tuple(self._client_threads)
        for client in clients:
            client.join(CLIENT_TIMEOUT_SECONDS + 1)
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener = listener
        listener.bind(str(self.path))
        self.path.chmod(0o600)
        listener.listen(MAX_CLIENTS)
        listener.settimeout(0.2)
        self._ready.set()
        while not self._stop.is_set():
            try:
                connection, _ = listener.accept()
            except (OSError, socket.timeout):
                continue
            if not self._clients.acquire(blocking=False):
                connection.close()
                continue
            connection.settimeout(CLIENT_TIMEOUT_SECONDS)
            thread = threading.Thread(target=self._bounded_client, args=(connection,), daemon=True)
            with self._client_lock:
                self._client_threads.add(thread)
            thread.start()

    def _bounded_client(self, connection: socket.socket) -> None:
        try:
            self._one(connection)
        finally:
            self._clients.release()
            with self._client_lock:
                self._client_threads.discard(threading.current_thread())

    def _one(self, connection: socket.socket) -> None:
        try:
            request = json.loads(receive_line(connection, failure="incomplete Target broker request"))
            command = request.get("command")
            if command == "claim":
                answer = {
                    "status": "issued",
                    "handle": self._runtime.claim(connection, str(request.get("generation_id", ""))),
                }
            elif command == TARGET_EXCHANGE_COMMAND and isinstance(request.get("request"), dict):
                result = self._runtime.exchange(connection, str(request.get("handle", "")), request["request"])
                answer = {"status": "answered", "result": _document(result)}
            elif command == TARGET_HTTP_SESSION_COMMAND and isinstance(request.get("request"), dict):
                result = self._runtime.http_session(connection, str(request.get("handle", "")), request["request"])
                answer = {"status": "answered", "result": _document(result)}
            elif command == TARGET_HTTP_FUZZ_COMMAND and isinstance(request.get("request"), dict):
                result = self._runtime.http_fuzz(connection, str(request.get("handle", "")), request["request"])
                answer = {"status": "answered", "result": _document(result)}
            elif command == TARGET_TCP_SESSION_COMMAND and isinstance(request.get("request"), dict):
                result = self._runtime.tcp_session(connection, str(request.get("handle", "")), request["request"])
                answer = {"status": "answered", "result": _document(result)}
            elif command == TARGET_BROWSER_COMMAND and isinstance(request.get("request"), dict):
                result = self._runtime.browser(connection, str(request.get("handle", "")), request["request"])
                answer = {"status": "answered", "result": _document(result)}
            elif command == "revoke":
                self._runtime.revoke(str(request.get("handle", "")))
                answer = {"status": "revoked"}
            else:
                answer = {"status": "refused"}
        except Exception:
            answer = {"status": "refused"}
        try:
            connection.sendall(canonical_bytes(answer) + b"\n")
        finally:
            connection.close()


def _document(result: TargetResult) -> dict[str, object]:
    provenance = result.provenance
    return {
        "outcome": result.outcome.value,
        "body": base64.b64encode(result.body).decode(),
        "status": result.status,
        "request_id": result.request_id,
        "headers": [list(field) for field in result.headers],
        "redirect_chain": list(result.redirect_chain),
        "cookies": [list(field) for field in result.cookies],
        "browser": {
            "dom": result.browser.dom,
            "network": [list(item) for item in result.browser.network],
            "local_storage": [list(item) for item in result.browser.local_storage],
            "session_storage": [list(item) for item in result.browser.session_storage],
            "downloads": [list(item) for item in result.browser.downloads],
        },
        "provenance": {
            "challenge_id": provenance.challenge_id,
            "generation_id": provenance.generation_id,
            "endpoint": provenance.endpoint,
            "protocol": provenance.protocol.value if provenance.protocol else "",
            "request_bytes": provenance.request_bytes,
            "response_bytes": provenance.response_bytes,
            "transcript_digest": provenance.transcript_digest,
            "elapsed_ms": provenance.elapsed_ms,
            "resolved_address": provenance.resolved_address,
            "server_name": provenance.server_name,
            "certificate_sha256": provenance.certificate_sha256,
        },
    }


def _request(path: Path, request: dict[str, object]) -> dict[str, object]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(str(path))
        connection.sendall(canonical_bytes(request) + b"\n")
        answer = json.loads(receive_line(connection, failure="incomplete Target broker response"))
        if not isinstance(answer, dict):
            raise ValueError("Target broker response is invalid")
        return answer
    finally:
        connection.close()


__all__ = ["TargetBrokerClient", "TargetBrokerService"]
