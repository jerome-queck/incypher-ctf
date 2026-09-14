"""Wire-bounded network transport for one pre-resolved declared Target."""

from __future__ import annotations

import socket
import ssl
import threading
import time
from dataclasses import dataclass

from solver.target_broker_contracts import (
    TargetEndpoint,
    TargetExchangeRequest,
    TargetLimits,
    TargetOutcome,
    TargetProtocol,
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


__all__ = ["TransportResult", "exchange", "request_size"]
