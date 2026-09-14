"""Narrow pathname-socket port for hostile Research clients."""

from __future__ import annotations

import base64
import json
import socket
import threading
from pathlib import Path

from solver.capability import CapabilityRefused
from solver.research_broker import ResearchBrokerRuntime
from solver.research_broker_contracts import (
    ResearchKind,
    ResearchOutcome,
    ResearchProvenance,
    ResearchQuery,
    ResearchResult,
)

MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_CLIENTS = 8
CLIENT_TIMEOUT_SECONDS = 1.0


class ResearchBrokerService:
    def __init__(self, path: Path, runtime: ResearchBrokerRuntime) -> None:
        self.path = Path(path)
        self._runtime = runtime
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._thread: threading.Thread | None = None
        self._closing = threading.Event()
        self._clients = threading.BoundedSemaphore(MAX_CLIENTS)
        self._client_threads: set[threading.Thread] = set()
        self._connections: set[socket.socket] = set()
        self._client_lock = threading.Lock()

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)
        self._socket.bind(str(self.path))
        self._socket.listen(MAX_CLIENTS)
        self._socket.settimeout(0.2)
        self._thread = threading.Thread(target=self._serve, daemon=True, name="research-broker")
        self._thread.start()

    def close(self) -> None:
        self._closing.set()
        self._socket.close()
        with self._client_lock:
            connections = tuple(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        if self._thread is not None:
            self._thread.join(timeout=1)
        with self._client_lock:
            clients = tuple(self._client_threads)
        for client in clients:
            client.join(CLIENT_TIMEOUT_SECONDS + 1)
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        while not self._closing.is_set():
            try:
                connection, _ = self._socket.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            if not self._clients.acquire(blocking=False):
                connection.close()
                continue
            connection.settimeout(CLIENT_TIMEOUT_SECONDS)
            thread = threading.Thread(target=self._bounded_client, args=(connection,), daemon=True)
            with self._client_lock:
                self._connections.add(connection)
                self._client_threads.add(thread)
            thread.start()

    def _bounded_client(self, connection: socket.socket) -> None:
        try:
            self._client(connection)
        finally:
            connection.close()
            self._clients.release()
            with self._client_lock:
                self._connections.discard(connection)
                self._client_threads.discard(threading.current_thread())

    def _client(self, connection: socket.socket) -> None:
        handle = ""
        while not self._closing.is_set():
            try:
                raw = _read_bounded_line(connection)
                if raw is None:
                    return
                try:
                    request = json.loads(raw)
                    if not isinstance(request, dict):
                        raise ValueError
                    if request.get("command") == "claim" and set(request) == {"command", "generation_id"}:
                        handle = self._runtime.claim(connection, str(request["generation_id"]))
                        answer = {"claimed": True}
                    elif request.get("command") == "fetch" and set(request) == {"command", "url"} and handle:
                        answer = _document(self._runtime.fetch(connection, handle, str(request["url"])))
                    elif request.get("command") == "query" and set(request) == {"command", "query"} and handle:
                        query = request["query"]
                        if not isinstance(query, dict):
                            raise ValueError
                        answer = _document(self._runtime.query(connection, handle, query))
                    else:
                        raise ValueError
                except (ValueError, TypeError, json.JSONDecodeError, CapabilityRefused):
                    answer = {"outcome": ResearchOutcome.CAPABILITY_REFUSED.value}
            except (OSError, socket.timeout):
                return
            try:
                connection.sendall(json.dumps(answer, separators=(",", ":"), sort_keys=True).encode() + b"\n")
            except OSError:
                return


class ResearchBrokerClient:
    def __init__(self, connection: socket.socket) -> None:
        self._connection = connection

    @classmethod
    def claim(cls, path: Path, generation_id: str) -> "ResearchBrokerClient":
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(str(path))
        connection.sendall(
            json.dumps({"command": "claim", "generation_id": generation_id}, separators=(",", ":")).encode() + b"\n"
        )
        answer = json.loads(_read_bounded_line(connection) or b"{}")
        if answer != {"claimed": True}:
            connection.close()
            raise CapabilityRefused()
        return cls(connection)

    def fetch(self, url: str) -> ResearchResult:
        self._connection.sendall(json.dumps({"command": "fetch", "url": url}, separators=(",", ":")).encode() + b"\n")
        answer = json.loads(_read_bounded_line(self._connection) or b"{}")
        outcome = ResearchOutcome(answer.get("outcome", ResearchOutcome.CAPABILITY_REFUSED.value))
        provenance = answer.get("provenance", {})
        return ResearchResult(
            outcome=outcome,
            body=base64.b64decode(answer.get("body", "")),
            status=int(answer.get("status", 0)),
            content_type=str(answer.get("content_type", "")),
            provenance=ResearchProvenance(
                tuple((str(host), tuple(addresses)) for host, addresses in provenance.get("dns_chain", [])),
                tuple(provenance.get("redirect_chain", [])),
                str(provenance.get("body_digest", "")),
                str(provenance.get("observed_at", "")),
                str(provenance.get("expires_at", "")),
                int(provenance.get("elapsed_ms", 0)),
                ResearchKind(str(provenance["kind"])) if provenance.get("kind") else None,
                str(provenance.get("source_id", "")),
                str(provenance.get("terms", "")),
                str(provenance.get("robots", "")),
                str(provenance.get("origin", "")),
                str(provenance.get("query_digest", "")),
            ),
            request_id=str(answer.get("request_id", "")),
            cached=bool(answer.get("cached", False)),
        )

    def query(self, query: ResearchQuery) -> ResearchResult:
        self._connection.sendall(
            json.dumps({"command": "query", "query": query.document()}, separators=(",", ":")).encode() + b"\n"
        )
        answer = json.loads(_read_bounded_line(self._connection) or b"{}")
        return _result(answer)

    def close(self) -> None:
        self._connection.close()


class ResearchCompatibilityAdapter:
    """Present the legacy recon probe shape over the typed Research port."""

    def __init__(self, client: ResearchBrokerClient) -> None:
        self._client = client

    def read(self, url: str) -> tuple[int | None, bytes]:
        result = self._client.fetch(url)
        return (0, result.body) if result.outcome is ResearchOutcome.ANSWERED else (None, result.outcome.value.encode())


def _read_bounded_line(connection: socket.socket) -> bytes | None:
    data = bytearray()
    while len(data) <= MAX_MESSAGE_BYTES:
        chunk = connection.recv(1)
        if not chunk:
            return bytes(data) or None
        if chunk == b"\n":
            return bytes(data)
        data.extend(chunk)
    return None


def _document(result: ResearchResult) -> dict[str, object]:
    return {
        "outcome": result.outcome.value,
        "body": base64.b64encode(result.body).decode(),
        "status": result.status,
        "content_type": result.content_type,
        "request_id": result.request_id,
        "cached": result.cached,
        "provenance": {
            "dns_chain": [[host, list(addresses)] for host, addresses in result.provenance.dns_chain],
            "redirect_chain": list(result.provenance.redirect_chain),
            "body_digest": result.provenance.body_digest,
            "observed_at": result.provenance.observed_at,
            "expires_at": result.provenance.expires_at,
            "elapsed_ms": result.provenance.elapsed_ms,
            "kind": result.provenance.kind.value if result.provenance.kind else "",
            "source_id": result.provenance.source_id,
            "terms": result.provenance.terms,
            "robots": result.provenance.robots,
            "origin": result.provenance.origin,
            "query_digest": result.provenance.query_digest,
        },
    }


def _result(answer: dict[str, object]) -> ResearchResult:
    outcome = ResearchOutcome(answer.get("outcome", ResearchOutcome.CAPABILITY_REFUSED.value))
    provenance = answer.get("provenance", {})
    if not isinstance(provenance, dict):
        provenance = {}
    return ResearchResult(
        outcome=outcome,
        body=base64.b64decode(answer.get("body", "")),
        status=int(answer.get("status", 0)),
        content_type=str(answer.get("content_type", "")),
        provenance=ResearchProvenance(
            tuple((str(host), tuple(addresses)) for host, addresses in provenance.get("dns_chain", [])),
            tuple(provenance.get("redirect_chain", [])),
            str(provenance.get("body_digest", "")),
            str(provenance.get("observed_at", "")),
            str(provenance.get("expires_at", "")),
            int(provenance.get("elapsed_ms", 0)),
            ResearchKind(str(provenance["kind"])) if provenance.get("kind") else None,
            str(provenance.get("source_id", "")),
            str(provenance.get("terms", "")),
            str(provenance.get("robots", "")),
            str(provenance.get("origin", "")),
            str(provenance.get("query_digest", "")),
        ),
        request_id=str(answer.get("request_id", "")),
        cached=bool(answer.get("cached", False)),
    )


__all__ = [
    "CLIENT_TIMEOUT_SECONDS",
    "MAX_CLIENTS",
    "ResearchBrokerClient",
    "ResearchBrokerService",
    "ResearchCompatibilityAdapter",
]
