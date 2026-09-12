"""One DNS-pinned HTTP exchange for the Research broker."""

from __future__ import annotations

import http.client
import socket
import ssl
from urllib.parse import urljoin, urlsplit

from solver.research_broker_contracts import ResearchLimits, ResearchOutcome, ResearchTransportResult


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, address: str, port: int, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


def fetch(url: str, address: str, limits: ResearchLimits) -> ResearchTransportResult:
    parsed = urlsplit(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection: http.client.HTTPConnection
    if parsed.scheme == "https":
        connection = _PinnedHTTPSConnection(parsed.hostname or "", address, port, limits.timeout_seconds)
    else:
        connection = http.client.HTTPConnection(address, port, timeout=limits.timeout_seconds)
    target = parsed.path or "/"
    if parsed.query:
        target += "?" + parsed.query
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    try:
        connection.request("GET", target, headers={"Host": host, "User-Agent": "incypher-research/1"})
        response = connection.getresponse()
        headers = {key.lower(): value for key, value in response.getheaders()}
        location = headers.get("location", "")
        if 300 <= response.status < 400 and location:
            return ResearchTransportResult(
                status=response.status,
                headers=headers,
                redirect_url=urljoin(url, location),
            )
        stated = headers.get("content-length", "")
        if stated.isdigit() and int(stated) > limits.max_body_bytes:
            return ResearchTransportResult(status=response.status, headers=headers, outcome=ResearchOutcome.TOO_LARGE)
        body = response.read(limits.max_body_bytes + 1)
        if len(body) > limits.max_body_bytes:
            return ResearchTransportResult(status=response.status, headers=headers, outcome=ResearchOutcome.TOO_LARGE)
        return ResearchTransportResult(status=response.status, headers=headers, body=body)
    except (TimeoutError, socket.timeout):
        return ResearchTransportResult(outcome=ResearchOutcome.TIMEOUT)
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return ResearchTransportResult(outcome=ResearchOutcome.UNREACHABLE)
    finally:
        connection.close()


__all__ = ["fetch"]
