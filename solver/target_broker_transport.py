"""Wire-bounded network transport for one pre-resolved declared Target."""

from __future__ import annotations

import hashlib
import http.client
import socket
import ssl
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass

from solver.target_broker_contracts import (
    TargetEndpoint,
    TargetExchangeRequest,
    TargetLimits,
    TargetOutcome,
    TargetProtocol,
    TcpReceiveMode,
    TcpSessionRequest,
)

MAX_HTTP_HEADER_BYTES = 16 * 1024


@dataclass(frozen=True)
class TransportResult:
    outcome: TargetOutcome
    body: bytes = b""
    status: int = 0
    elapsed_ms: int = 0
    request_bytes: int = 0
    response_bytes: int = 0


@dataclass(frozen=True)
class HttpTransportRequest:
    method: str
    target: str
    body: bytes
    headers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class HttpTransportResult:
    outcome: TargetOutcome
    body: bytes = b""
    status: int = 0
    headers: tuple[tuple[str, str], ...] = ()
    elapsed_ms: int = 0
    request_bytes: int = 0
    response_bytes: int = 0
    certificate_sha256: str = ""


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(
        self,
        endpoint: TargetEndpoint,
        address: str,
        timeout: float,
        cancelled: threading.Event | None = None,
    ) -> None:
        super().__init__(
            endpoint.host,
            endpoint.port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._address = address
        self._cancelled = cancelled

    def connect(self) -> None:
        if self._cancelled is not None and self._cancelled.is_set():
            raise OSError("Target session was revoked")
        opened = socket.create_connection((self._address, self.port), self.timeout)
        if self._cancelled is not None and self._cancelled.is_set():
            opened.close()
            raise OSError("Target session was revoked")
        self.sock = self._context.wrap_socket(opened, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, endpoint: TargetEndpoint, address: str, timeout: float, cancelled: threading.Event) -> None:
        super().__init__(address, endpoint.port, timeout=timeout)
        self._address = address
        self._cancelled = cancelled

    def connect(self) -> None:
        if self._cancelled.is_set():
            raise OSError("Target session was revoked")
        opened = socket.create_connection((self._address, self.port), self.timeout)
        if self._cancelled.is_set():
            opened.close()
            raise OSError("Target session was revoked")
        self.sock = opened


class _IncompleteFrame(OSError):
    def __init__(self, body: bytes, wire_bytes: int | None = None) -> None:
        super().__init__("Target closed before the frame completed")
        self.body = body
        self.wire_bytes = len(body) if wire_bytes is None else wire_bytes


class _TimedOutFrame(_IncompleteFrame):
    pass


class TcpSessionTransport:
    """One persistent Target socket whose close interrupts an active exchange."""

    def __init__(self, endpoint: TargetEndpoint, address: str, limits: TargetLimits) -> None:
        self._endpoint = endpoint
        self._address = address
        self._limits = limits
        self._socket: socket.socket | None = None
        self._closed = False
        self._lock = threading.Lock()

    def exchange(self, request: TcpSessionRequest) -> TransportResult:
        if not self._lock.acquire(blocking=False):
            return TransportResult(TargetOutcome.DENIED)
        try:
            if self._closed:
                return TransportResult(TargetOutcome.REVOKED)
            started = time.monotonic()
            timed_out = threading.Event()

            def expire() -> None:
                timed_out.set()
                self.close()

            timer = threading.Timer(self._limits.timeout_seconds, expire)
            timer.daemon = True
            timer.start()
            try:
                target = self._connection()
                target.sendall(request.body)
                if request.half_close:
                    target.shutdown(socket.SHUT_WR)
                body, wire_bytes = _read_tcp_session(target, request)
                if body is None:
                    outcome, body, wire_bytes = TargetOutcome.TOO_LARGE, b"", 0
                else:
                    outcome = TargetOutcome.ANSWERED
            except (TimeoutError, socket.timeout):
                outcome, body, wire_bytes = TargetOutcome.TIMEOUT, b"", 0
            except _TimedOutFrame as error:
                outcome, body, wire_bytes = TargetOutcome.TIMEOUT, error.body, error.wire_bytes
            except _IncompleteFrame as error:
                outcome = TargetOutcome.TIMEOUT if timed_out.is_set() else TargetOutcome.AMBIGUOUS_CLOSE
                body, wire_bytes = error.body, error.wire_bytes
            except OSError:
                outcome, body, wire_bytes = (
                    (TargetOutcome.TIMEOUT, b"", 0) if timed_out.is_set() else (TargetOutcome.UNREACHABLE, b"", 0)
                )
            finally:
                timer.cancel()
            return TransportResult(
                outcome,
                body,
                elapsed_ms=max(0, int((time.monotonic() - started) * 1000)),
                request_bytes=len(request.body),
                response_bytes=wire_bytes,
            )
        finally:
            self._lock.release()

    def close(self) -> None:
        self._closed = True
        target, self._socket = self._socket, None
        if target is not None:
            try:
                target.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            target.close()

    def _connection(self) -> socket.socket:
        if self._socket is None:
            self._socket = socket.create_connection(
                (self._address, self._endpoint.port),
                self._limits.timeout_seconds,
            )
        return self._socket


class HttpSessionTransport:
    """Serial, cancellable requests for one HTTP capability handle."""

    def __init__(self, endpoint: TargetEndpoint, address: str, limits: TargetLimits) -> None:
        self._endpoint = endpoint
        self._address = address
        self._limits = limits
        self._cancelled = threading.Event()
        self._exchange_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active: http.client.HTTPConnection | None = None

    def exchange(self, request: HttpTransportRequest, *, max_response_bytes: int) -> HttpTransportResult:
        if not self._exchange_lock.acquire(blocking=False):
            return HttpTransportResult(TargetOutcome.DENIED)
        try:
            if self._cancelled.is_set():
                return HttpTransportResult(TargetOutcome.REVOKED)
            return _http_session_exchange(
                self._endpoint,
                self._address,
                self._limits,
                request,
                max_response_bytes=max_response_bytes,
                cancelled=self._cancelled,
                activate=self._activate,
            )
        finally:
            self._exchange_lock.release()

    def close(self) -> None:
        self._cancelled.set()
        with self._state_lock:
            connection = self._active
        if connection is not None and connection.sock is not None:
            try:
                connection.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def _activate(self, connection: http.client.HTTPConnection | None) -> None:
        with self._state_lock:
            self._active = connection


def _read_tcp_session(target: socket.socket, request: TcpSessionRequest) -> tuple[bytes | None, int]:
    receive = request.receive
    if receive.mode is TcpReceiveMode.RAW:
        if request.half_close:
            body = _read_bounded(target, receive.maximum_bytes)
        else:
            body = target.recv(receive.maximum_bytes + 1)
        return (body, len(body)) if len(body) <= receive.maximum_bytes else (None, 0)
    if receive.mode in {TcpReceiveMode.LINE, TcpReceiveMode.DELIMITER}:
        return _read_until(target, receive.delimiter, receive.maximum_bytes)
    if receive.mode is TcpReceiveMode.FIXED:
        body = _read_exact(target, receive.size)
        return body, len(body)
    try:
        prefix = _read_exact(target, receive.length_bytes)
    except _IncompleteFrame as error:
        raise _IncompleteFrame(b"", error.wire_bytes) from error
    size = int.from_bytes(prefix, receive.byteorder)
    if size > receive.maximum_bytes:
        return None, 0
    try:
        body = _read_exact(target, size)
    except _IncompleteFrame as error:
        raise _IncompleteFrame(error.body, len(prefix) + error.wire_bytes) from error
    return body, len(prefix) + len(body)


def _read_exact(target: socket.socket, size: int) -> bytes:
    held = bytearray()
    while len(held) < size:
        try:
            chunk = target.recv(size - len(held))
        except socket.timeout as error:
            raise _TimedOutFrame(bytes(held)) from error
        except OSError as error:
            raise _IncompleteFrame(bytes(held)) from error
        if not chunk:
            raise _IncompleteFrame(bytes(held))
        held.extend(chunk)
    return bytes(held)


def _read_until(target: socket.socket, delimiter: bytes, limit: int) -> tuple[bytes | None, int]:
    held = bytearray()
    while delimiter not in held:
        try:
            chunk = target.recv(1)
        except socket.timeout as error:
            raise _TimedOutFrame(bytes(held)) from error
        except OSError as error:
            raise _IncompleteFrame(bytes(held)) from error
        if not chunk:
            raise _IncompleteFrame(bytes(held))
        held.extend(chunk)
        if len(held) > limit:
            return None, 0
    return bytes(held), len(held)


def http_session_exchange(
    endpoint: TargetEndpoint,
    address: str,
    limits: TargetLimits,
    request: HttpTransportRequest,
) -> HttpTransportResult:
    """Execute one pinned-origin HTTP session request with bounded decoding."""

    return _http_session_exchange(
        endpoint,
        address,
        limits,
        request,
        max_response_bytes=limits.max_response_bytes,
    )


def _http_session_exchange(
    endpoint: TargetEndpoint,
    address: str,
    limits: TargetLimits,
    request: HttpTransportRequest,
    *,
    max_response_bytes: int,
    cancelled: threading.Event | None = None,
    activate: Callable[[http.client.HTTPConnection | None], None] | None = None,
) -> HttpTransportResult:

    request_bytes = http_request_size(endpoint, request)
    if request_bytes > limits.max_request_bytes:
        return HttpTransportResult(TargetOutcome.TOO_LARGE)
    connection: http.client.HTTPConnection
    if endpoint.protocol is TargetProtocol.HTTPS:
        connection = _PinnedHTTPSConnection(endpoint, address, limits.timeout_seconds, cancelled)
    elif cancelled is not None:
        connection = _PinnedHTTPConnection(endpoint, address, limits.timeout_seconds, cancelled)
    else:
        connection = http.client.HTTPConnection(address, endpoint.port, timeout=limits.timeout_seconds)
    if activate is not None:
        activate(connection)
    started = time.monotonic()
    try:
        connection.putrequest(request.method, request.target, skip_host=True, skip_accept_encoding=True)
        for name, value in _http_headers(endpoint, request):
            connection.putheader(name, value)
        connection.endheaders(request.body)
        response = connection.getresponse()
        response_headers = tuple((name.lower(), value) for name, value in response.getheaders())
        header_bytes = sum(len(name.encode()) + len(value.encode()) + 4 for name, value in response_headers) + 16
        if header_bytes > min(max_response_bytes, MAX_HTTP_HEADER_BYTES):
            return HttpTransportResult(TargetOutcome.TOO_LARGE, status=response.status)
        raw = response.read(max_response_bytes - header_bytes + 1)
        response_bytes = header_bytes + len(raw)
        if response_bytes > max_response_bytes:
            return HttpTransportResult(TargetOutcome.TOO_LARGE, status=response.status)
        encoding = next((value.lower() for name, value in response_headers if name == "content-encoding"), "")
        body = _decode_bounded(raw, encoding, max_response_bytes)
        if body is None:
            return HttpTransportResult(TargetOutcome.TOO_LARGE, status=response.status)
        certificate = ""
        if endpoint.protocol is TargetProtocol.HTTPS and connection.sock is not None:
            peer = connection.sock.getpeercert(binary_form=True)
            certificate = hashlib.sha256(peer).hexdigest() if peer else ""
        return HttpTransportResult(
            TargetOutcome.ANSWERED,
            body,
            response.status,
            response_headers,
            max(0, int((time.monotonic() - started) * 1000)),
            request_bytes,
            response_bytes,
            certificate,
        )
    except (TimeoutError, socket.timeout):
        return HttpTransportResult(TargetOutcome.TIMEOUT)
    except (OSError, ValueError, http.client.HTTPException, ssl.SSLError):
        outcome = TargetOutcome.REVOKED if cancelled is not None and cancelled.is_set() else TargetOutcome.UNREACHABLE
        return HttpTransportResult(outcome)
    finally:
        if activate is not None:
            activate(None)
        connection.close()


def _http_request_bytes(
    method: str,
    target: str,
    headers: tuple[tuple[str, str], ...],
    body: bytes,
) -> int:
    return (
        len(f"{method} {target} HTTP/1.1\r\n".encode())
        + sum(len(name.encode()) + len(value.encode()) + 4 for name, value in headers)
        + 2
        + len(body)
    )


def _http_headers(endpoint: TargetEndpoint, request: HttpTransportRequest) -> tuple[tuple[str, str], ...]:
    host = endpoint.host if endpoint.port in {80, 443} else f"{endpoint.host}:{endpoint.port}"
    return (("Host", host), *request.headers, ("Content-Length", str(len(request.body))))


def http_request_size(endpoint: TargetEndpoint, request: HttpTransportRequest) -> int:
    """Measure the exact request bytes before opening the pinned Target connection."""

    return _http_request_bytes(request.method, request.target, _http_headers(endpoint, request), request.body)


def _decode_bounded(body: bytes, encoding: str, limit: int) -> bytes | None:
    if encoding not in {"gzip", "deflate"}:
        return body
    window = 16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS
    try:
        decoder = zlib.decompressobj(window)
        decoded = decoder.decompress(body, limit + 1)
        if decoder.unconsumed_tail or len(decoded) > limit:
            return None
        decoded += decoder.flush(limit + 1 - len(decoded))
        return decoded if decoder.eof and not decoder.unused_data and len(decoded) <= limit else None
    except zlib.error:
        return None


def exchange(
    endpoint: TargetEndpoint,
    address: str,
    limits: TargetLimits,
    request: TargetExchangeRequest,
    request_body: bytes,
) -> TransportResult:
    prepared = _prepare_request(endpoint, request, request_body)
    if prepared is None:
        return TransportResult(TargetOutcome.DENIED)
    wire, request_bytes = prepared
    if request_bytes > limits.max_request_bytes:
        return TransportResult(TargetOutcome.TOO_LARGE)
    started = time.monotonic()
    deadline = started + limits.timeout_seconds
    timed_out = threading.Event()
    active_socket: socket.socket | None = None

    def expire() -> None:
        timed_out.set()
        if active_socket is not None:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active_socket.close()

    timer = threading.Timer(limits.timeout_seconds, expire)
    timer.daemon = True
    timer.start()
    try:
        with socket.create_connection((address, endpoint.port), limits.timeout_seconds) as opened:
            if endpoint.protocol is TargetProtocol.HTTPS:
                with ssl.create_default_context().wrap_socket(opened, server_hostname=endpoint.host) as secured:
                    active_socket = secured
                    secured.sendall(wire)
                    outcome, body, status, response_bytes = _read_http(secured, limits.max_response_bytes)
            else:
                active_socket = opened
                opened.sendall(wire)
                if endpoint.protocol is TargetProtocol.HTTP:
                    outcome, body, status, response_bytes = _read_http(opened, limits.max_response_bytes)
                else:
                    opened.shutdown(socket.SHUT_WR)
                    body = _read_bounded(opened, limits.max_response_bytes)
                    response_bytes = len(body)
                    status = 0
                    outcome = (
                        TargetOutcome.TOO_LARGE
                        if response_bytes > limits.max_response_bytes
                        else TargetOutcome.ANSWERED
                    )
                    if outcome is TargetOutcome.TOO_LARGE:
                        body = b""
    except (TimeoutError, socket.timeout):
        outcome, body, status, response_bytes = TargetOutcome.TIMEOUT, b"", 0, 0
    except (OSError, ValueError):
        outcome, body, status, response_bytes = (
            (TargetOutcome.TIMEOUT, b"", 0, 0)
            if timed_out.is_set() or time.monotonic() >= deadline
            else (TargetOutcome.UNREACHABLE, b"", 0, 0)
        )
    finally:
        timer.cancel()
    return TransportResult(
        outcome,
        body,
        status,
        max(0, int((time.monotonic() - started) * 1000)),
        request_bytes,
        response_bytes,
    )


def request_size(endpoint: TargetEndpoint, request: TargetExchangeRequest, body: bytes) -> int | None:
    prepared = _prepare_request(endpoint, request, body)
    return len(prepared[0]) if prepared is not None else None


def _prepare_request(endpoint: TargetEndpoint, request: TargetExchangeRequest, body: bytes) -> tuple[bytes, int] | None:
    if endpoint.protocol is TargetProtocol.TCP:
        return body, len(body)
    method = str(request.get("method", "GET"))
    path = str(request.get("path", "/"))
    if (
        method not in {"GET", "POST"}
        or not path.startswith("/")
        or path.startswith("//")
        or any(character in path for character in "\r\n")
    ):
        return None
    head = (
        f"{method} {path} HTTP/1.1\r\nHost: {endpoint.host}\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    ).encode("ascii", errors="strict")
    wire = head + body
    return wire, len(wire)


def _read_http(target: socket.socket, limit: int) -> tuple[TargetOutcome, bytes, int, int]:
    stream = target.makefile("rb")
    header_limit = min(limit, MAX_HTTP_HEADER_BYTES)
    held = bytearray()
    while b"\r\n\r\n" not in held:
        chunk = stream.read(1)
        if not chunk:
            raise ValueError("incomplete HTTP response headers")
        held.extend(chunk)
        if len(held) > header_limit:
            return TargetOutcome.TOO_LARGE, b"", 0, 0
    lines = bytes(held[:-4]).split(b"\r\n")
    fields = lines[0].split(b" ", 2)
    if len(fields) < 2 or not fields[1].isdigit():
        raise ValueError("invalid HTTP status")
    status = int(fields[1])
    remaining = limit - len(held)
    body = stream.read(remaining + 1)
    response_bytes = len(held) + len(body)
    if response_bytes > limit:
        return TargetOutcome.TOO_LARGE, b"", status, 0
    if 300 <= status < 400:
        return TargetOutcome.DENIED, b"", status, response_bytes
    return TargetOutcome.ANSWERED, body, status, response_bytes


def _read_bounded(target: socket.socket, limit: int) -> bytes:
    chunks = []
    held = 0
    while held <= limit:
        chunk = target.recv(limit + 1 - held)
        if not chunk:
            break
        chunks.append(chunk)
        held += len(chunk)
    return b"".join(chunks)


__all__ = [
    "HttpSessionTransport",
    "HttpTransportRequest",
    "HttpTransportResult",
    "TcpSessionTransport",
    "TransportResult",
    "exchange",
    "http_session_exchange",
    "request_size",
]
