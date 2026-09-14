"""Bounded, generation-scoped access to one declared Target."""

from __future__ import annotations

import base64
import binascii
import hashlib
import http.cookies
import json
import re
import socket
import threading
import datetime as dt
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import urlencode, urljoin, urlsplit

from solver.capability import CapabilityAuthority, CapabilityBinding, CapabilityRefused, local_peer_identity
from solver.event_store_storage import canonical_bytes
from solver.event_store import EventStore
from solver.event_store_contracts import ATTEMPT_ENVELOPE_RECORDED
from solver.redaction import Redactor
from solver.target_broker_contracts import (
    TARGET_EXCHANGE_RECORDED,
    BrowserObservations,
    BrowserSessionRequest,
    BrowserTransportResult,
    HttpBody,
    HttpBodyKind,
    HttpSessionRequest,
    HttpTargetExchangeRequest,
    TargetEndpoint,
    TargetCandidateBinding,
    TargetLimits,
    TargetOutcome,
    TargetProtocol,
    TargetProvenance,
    TargetRecord,
    TargetResult,
    TargetBrokerRecorded,
    TargetExchangeRequest,
    TcpReceive,
    TcpReceiveMode,
    TcpSessionRequest,
    TcpTargetExchangeRequest,
)
from solver.target_broker_transport import exchange as transport_exchange
from solver.target_broker_transport import HttpSessionTransport, HttpTransportRequest
from solver.target_broker_transport import TcpSessionTransport
from solver.target_broker_transport import request_size
from solver.work_generation import GenerationFence

TARGET_SCOPE = "target.exchange"
HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
FORBIDDEN_HTTP_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "cookie",
        "forwarded",
        "host",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
    }
)
HTTP_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}")


class GenerationRevocationSink(Protocol):
    def add_generation_revocation(self, callback: Callable[[str], None]) -> None: ...


class BrowserSession(Protocol):
    def browse(self, request: BrowserSessionRequest) -> BrowserTransportResult: ...

    def close(self) -> None: ...


class TargetBrokerRuntime:
    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        boot_id: str,
        challenge_id: str,
        candidate: TargetCandidateBinding,
        endpoint: TargetEndpoint,
        limits: TargetLimits,
        timestamp: Callable[[], str],
        denied_endpoints: Mapping[str, TargetEndpoint] | None = None,
        request_namespace: str = "",
        recovery=None,
        capability_authority: CapabilityAuthority | None = None,
        reconcile_authority: bool = True,
        browser_factory: Callable[..., BrowserSession] | None = None,
        browser_subresources: Mapping[str, TargetEndpoint] | None = None,
    ) -> None:
        if not challenge_id or not boot_id:
            raise ValueError("Target binding needs Challenge and Boot identity")
        self.challenge_id = challenge_id
        self.candidate = candidate
        self.run_id = run_id
        self.boot_id = boot_id
        self.endpoint = endpoint
        self.limits = limits
        try:
            self._address = socket.getaddrinfo(endpoint.host, endpoint.port, type=socket.SOCK_STREAM)[0][4][0]
        except OSError as error:
            raise ValueError("declared Target cannot be resolved at capability creation") from error
        self.authority = capability_authority or CapabilityAuthority(
            state=state,
            run_id=run_id,
            boot_id=boot_id,
            redactor=Redactor({}),
            peer_identity=local_peer_identity,
            timestamp=timestamp,
        )
        if reconcile_authority:
            self.authority.reconcile_restart(TARGET_SCOPE)
        self._store = EventStore(state, run_id=run_id, redactor=Redactor({}))
        self._generations = GenerationFence(state, run_id, Redactor({}), timestamp)
        self._timestamp = timestamp
        self._connections: dict[str, int] = {}
        self._session_exchanges: dict[str, int] = {}
        self._session_request_bytes: dict[str, int] = {}
        self._session_response_bytes: dict[str, int] = {}
        self._session_elapsed_ms: dict[str, int] = {}
        self._http_cookies: dict[str, dict[str, str]] = {}
        self._http_sessions: dict[str, HttpSessionTransport] = {}
        self._tcp_sessions: dict[str, TcpSessionTransport] = {}
        self._browser_sessions: dict[str, BrowserSession] = {}
        if browser_factory is None:
            from solver.target_broker_browser import BrowserSessionTransport

            browser_factory = BrowserSessionTransport
        self._browser_factory = browser_factory
        self._browser_subresources = dict(browser_subresources or {})
        self._generation_handles: dict[str, set[str]] = {}
        if request_namespace and any(
            character not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for character in request_namespace
        ):
            raise ValueError("Target request namespace is invalid")
        self._request_prefix = f"target-broker:{request_namespace}:" if request_namespace else "target-broker:"
        self._serial = max(
            (
                int(str(event.payload["request_id"]).removeprefix(self._request_prefix))
                for event in self._store.events()
                if event.event_type == TARGET_EXCHANGE_RECORDED
                and str(event.payload.get("request_id", "")).startswith(self._request_prefix)
            ),
            default=0,
        )
        self._lock = threading.Lock()
        self._expected: dict[str, CapabilityBinding] = {}
        self._denied_endpoints = dict(denied_endpoints or {})
        self._denial_observations = {
            str(event.payload.get("observation_digest", ""))
            for event in self._store.events()
            if event.event_type == TARGET_EXCHANGE_RECORDED and event.payload.get("observation_digest")
        }
        self._request_operations: dict[str, str] = {}
        self._recovery = recovery
        if recovery is not None:
            from solver.recovery.safe_read import SafeReadRecovery

            SafeReadRecovery(recovery).replay()

    def available(self, generation_id: str) -> bool:
        """Answer whether this fixed Target belongs to one current Work generation."""

        return any(
            generation.generation_id == generation_id and generation.active and generation.work_id == self.challenge_id
            for generation in self._generations.projection().generations
        )

    def prepare_attempt(self, binding: CapabilityBinding) -> None:
        """Install the controller-selected identity claimed by the next hostile peer."""

        if binding.run_id != self.run_id or binding.boot_id != self.boot_id:
            raise ValueError("Target Attempt binding belongs to another Run or Boot")
        self._expected[binding.generation_id] = binding

    def claim(self, connection: socket.socket, generation_id: str) -> str:
        binding = self._expected.pop(generation_id, None)
        if binding is None:
            raise CapabilityRefused()
        return self._issue(connection, binding)

    def _issue(self, connection: socket.socket, binding: CapabilityBinding) -> str:
        generation = next(
            (
                item
                for item in self._generations.projection().generations
                if item.generation_id == binding.generation_id
            ),
            None,
        )
        if (
            generation is None
            or not generation.active
            or generation.work_id != self.challenge_id
            or generation.attempt_id != binding.attempt_id
        ):
            raise CapabilityRefused()
        handle = self.authority.issue(binding, TARGET_SCOPE, self.authority.peer_identity(connection))
        self._generation_handles.setdefault(binding.generation_id, set()).add(handle)
        self._http_cookies[handle] = {}
        if self.endpoint.protocol in {TargetProtocol.HTTP, TargetProtocol.HTTPS}:
            self._http_sessions[handle] = HttpSessionTransport(self.endpoint, self._address, self.limits)
            self._browser_sessions[handle] = self._browser_factory(
                self.endpoint,
                self._address,
                self.limits,
                self._browser_subresources,
            )
        return handle

    def bind_attempt_executor(self, executor: GenerationRevocationSink) -> None:
        """Remove Target authority at the executor's generation fence."""

        executor.add_generation_revocation(self.revoke_generation)

    def revoke_generation(self, generation_id: str) -> None:
        self._expected.pop(generation_id, None)
        for handle in tuple(self._generation_handles.pop(generation_id, ())):
            self.authority.revoke(handle, "generation-closing")
            self._http_cookies.pop(handle, None)
            self._close_http_session(handle)
            self._close_browser_session(handle)
            self._close_tcp_session(handle)

    def revoke(self, handle: str) -> None:
        self.authority.revoke(handle, "target-client-closed")
        self._http_cookies.pop(handle, None)
        self._close_http_session(handle)
        self._close_browser_session(handle)
        self._close_tcp_session(handle)

    def _close_http_session(self, handle: str) -> None:
        session = self._http_sessions.pop(handle, None)
        if session is not None:
            session.close()

    def _close_tcp_session(self, handle: str) -> None:
        session = self._tcp_sessions.pop(handle, None)
        if session is not None:
            session.close()

    def _close_browser_session(self, handle: str) -> None:
        session = self._browser_sessions.pop(handle, None)
        if session is not None:
            session.close()

    def browser(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        """Observe one fresh browser context confined to admitted Target origins."""

        try:
            peer = self.authority.peer_identity(connection)
            grant = self.authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = TargetOutcome.REVOKED if error.reason == "revoked" else TargetOutcome.CAPABILITY_REFUSED
            return TargetResult(outcome)
        if grant.scope != TARGET_SCOPE or self.endpoint.protocol not in {TargetProtocol.HTTP, TargetProtocol.HTTPS}:
            return TargetResult(TargetOutcome.CAPABILITY_REFUSED)
        request_digest = hashlib.sha256(canonical_bytes(request)).hexdigest()
        with self._lock:
            self._serial += 1
            request_id = f"{self._request_prefix}{self._serial:06d}"
            self._request_operations[request_id] = "browser"
        self._append_record(request_id, TargetRecord.RESERVED, grant.binding, request_digest)
        validated = _validate_browser_session(request, set(self._browser_subresources))
        if validated is None:
            return self._classify(request_id, grant.binding, request_digest, TargetOutcome.DENIED, b"", 0, 0, 0)
        generation_id = grant.binding.generation_id
        request_bytes = len(canonical_bytes(request))
        with self._lock:
            exchanges = self._session_exchanges.get(generation_id, 0)
            total_request = self._session_request_bytes.get(generation_id, 0) + request_bytes
            connections = self._connections.get(generation_id, 0)
            if (
                exchanges >= self.limits.max_exchanges
                or connections >= self.limits.max_connections
                or request_bytes > self.limits.max_request_bytes
                or total_request > self.limits.max_total_request_bytes
            ):
                return self._classify(
                    request_id,
                    grant.binding,
                    request_digest,
                    TargetOutcome.BUDGET_EXHAUSTED,
                    b"",
                    0,
                    0,
                    0,
                )
            self._session_exchanges[generation_id] = exchanges + 1
            self._session_request_bytes[generation_id] = total_request
            self._connections[generation_id] = connections + 1
        session = self._browser_sessions.get(handle)
        transported = (
            session.browse(validated) if session is not None else BrowserTransportResult(TargetOutcome.REVOKED)
        )
        observation = _browser_document(transported.observations)
        body = canonical_bytes(observation)
        with self._lock:
            total_response = self._session_response_bytes.get(generation_id, 0) + transported.response_bytes
            total_elapsed = self._session_elapsed_ms.get(generation_id, 0) + transported.elapsed_ms
            self._session_response_bytes[generation_id] = total_response
            self._session_elapsed_ms[generation_id] = total_elapsed
        outcome = transported.outcome
        if total_response > self.limits.max_total_response_bytes or total_elapsed > int(
            self.limits.max_total_seconds * 1000
        ):
            outcome = TargetOutcome.BUDGET_EXHAUSTED
            body = b""
        try:
            self.authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = TargetOutcome.REVOKED if error.reason == "revoked" else TargetOutcome.CAPABILITY_REFUSED
            body = b""
        provenance = TargetProvenance(
            challenge_id=self.challenge_id,
            generation_id=generation_id,
            endpoint="declared-target",
            protocol=self.endpoint.protocol,
            request_bytes=request_bytes,
            response_bytes=transported.response_bytes,
            transcript_digest=hashlib.sha256(self._transcript(request_digest, body, 0)).hexdigest(),
            elapsed_ms=transported.elapsed_ms,
            resolved_address=self._address,
            server_name=self.endpoint.host if self.endpoint.protocol is TargetProtocol.HTTPS else "",
        )
        result = TargetResult(
            outcome,
            provenance=provenance,
            request_id=request_id,
            browser=transported.observations if outcome is TargetOutcome.ANSWERED else BrowserObservations(),
        )
        self._append_record(
            request_id,
            TargetRecord.CLASSIFIED,
            grant.binding,
            request_digest,
            result=result,
            body=body,
        )
        return result

    def http_session(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        """Run one stateful bounded request against the declared HTTP origin."""

        try:
            peer = self.authority.peer_identity(connection)
            grant = self.authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = TargetOutcome.REVOKED if error.reason == "revoked" else TargetOutcome.CAPABILITY_REFUSED
            return TargetResult(outcome)
        if grant.scope != TARGET_SCOPE or self.endpoint.protocol not in {TargetProtocol.HTTP, TargetProtocol.HTTPS}:
            return TargetResult(TargetOutcome.CAPABILITY_REFUSED)
        request_digest = hashlib.sha256(canonical_bytes(request)).hexdigest()
        with self._lock:
            self._serial += 1
            request_id = f"{self._request_prefix}{self._serial:06d}"
            self._request_operations[request_id] = "http-session"
        self._append_record(request_id, TargetRecord.RESERVED, grant.binding, request_digest)
        validated = _validate_http_session(request)
        if validated is None:
            return self._classify(request_id, grant.binding, request_digest, TargetOutcome.DENIED, b"", 0, 0, 0)
        generation_id = grant.binding.generation_id
        with self._lock:
            exchanges = self._session_exchanges.get(generation_id, 0)
            if exchanges >= self.limits.max_exchanges:
                return self._classify(
                    request_id,
                    grant.binding,
                    request_digest,
                    TargetOutcome.BUDGET_EXHAUSTED,
                    b"",
                    0,
                    0,
                    0,
                )
            self._session_exchanges[generation_id] = exchanges + 1
        result = self._perform_http_session(handle, grant.binding, request_id, request_digest, validated)
        try:
            self.authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = TargetOutcome.REVOKED if error.reason == "revoked" else TargetOutcome.CAPABILITY_REFUSED
            provenance = replace(
                result.provenance,
                transcript_digest=hashlib.sha256(self._transcript(request_digest, b"", 0)).hexdigest(),
            )
            result = replace(
                result,
                outcome=outcome,
                body=b"",
                status=0,
                provenance=provenance,
                headers=(),
                redirect_chain=(),
                cookies=(),
            )
        self._append_record(
            request_id,
            TargetRecord.CLASSIFIED,
            grant.binding,
            request_digest,
            result=result,
            body=result.body,
        )
        return result

    def tcp_session(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        """Exchange one framed message on the generation-owned Target socket."""

        try:
            peer = self.authority.peer_identity(connection)
            grant = self.authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = TargetOutcome.REVOKED if error.reason == "revoked" else TargetOutcome.CAPABILITY_REFUSED
            return TargetResult(outcome)
        if grant.scope != TARGET_SCOPE or self.endpoint.protocol is not TargetProtocol.TCP:
            return TargetResult(TargetOutcome.CAPABILITY_REFUSED)
        request_digest = hashlib.sha256(canonical_bytes(request)).hexdigest()
        with self._lock:
            self._serial += 1
            request_id = f"{self._request_prefix}{self._serial:06d}"
            self._request_operations[request_id] = "tcp-session"
        self._append_record(request_id, TargetRecord.RESERVED, grant.binding, request_digest)
        validated = _validate_tcp_session(request)
        generation_id = grant.binding.generation_id
        if (
            validated is None
            or len(validated.body) > self.limits.max_request_bytes
            or validated.receive.maximum_bytes > self.limits.max_response_bytes
        ):
            return self._classify(request_id, grant.binding, request_digest, TargetOutcome.DENIED, b"", 0, 0, 0)
        with self._lock:
            exchanges = self._session_exchanges.get(generation_id, 0)
            total_request = self._session_request_bytes.get(generation_id, 0) + len(validated.body)
            if exchanges >= self.limits.max_exchanges or total_request > self.limits.max_total_request_bytes:
                return self._classify(
                    request_id,
                    grant.binding,
                    request_digest,
                    TargetOutcome.BUDGET_EXHAUSTED,
                    b"",
                    0,
                    0,
                    0,
                )
            session = self._tcp_sessions.get(handle)
            if session is None:
                used_connections = self._connections.get(generation_id, 0)
                if used_connections >= self.limits.max_connections:
                    return self._classify(
                        request_id,
                        grant.binding,
                        request_digest,
                        TargetOutcome.BUDGET_EXHAUSTED,
                        b"",
                        0,
                        0,
                        0,
                    )
                session = TcpSessionTransport(self.endpoint, self._address, self.limits)
                self._tcp_sessions[handle] = session
                self._connections[generation_id] = used_connections + 1
            self._session_exchanges[generation_id] = exchanges + 1
            self._session_request_bytes[generation_id] = total_request
        transported = session.exchange(validated)
        with self._lock:
            total_response = self._session_response_bytes.get(generation_id, 0) + transported.response_bytes
            total_elapsed = self._session_elapsed_ms.get(generation_id, 0) + transported.elapsed_ms
            self._session_response_bytes[generation_id] = total_response
            self._session_elapsed_ms[generation_id] = total_elapsed
        outcome, body = transported.outcome, transported.body
        if total_response > self.limits.max_total_response_bytes or total_elapsed > int(
            self.limits.max_total_seconds * 1000
        ):
            outcome, body = TargetOutcome.BUDGET_EXHAUSTED, b""
            self._close_tcp_session(handle)
        try:
            self.authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = TargetOutcome.REVOKED if error.reason == "revoked" else TargetOutcome.CAPABILITY_REFUSED
            body = b""
        provenance = TargetProvenance(
            challenge_id=self.challenge_id,
            generation_id=generation_id,
            endpoint="declared-target",
            protocol=self.endpoint.protocol,
            request_bytes=transported.request_bytes,
            response_bytes=transported.response_bytes,
            transcript_digest=hashlib.sha256(self._transcript(request_digest, body, 0)).hexdigest(),
            elapsed_ms=transported.elapsed_ms,
            resolved_address=self._address,
        )
        result = TargetResult(outcome, body, provenance=provenance, request_id=request_id)
        self._append_record(
            request_id,
            TargetRecord.CLASSIFIED,
            grant.binding,
            request_digest,
            result=result,
            body=body,
        )
        return result

    def _perform_http_session(
        self,
        handle: str,
        binding: CapabilityBinding,
        request_id: str,
        request_digest: str,
        request: HttpSessionRequest,
    ) -> TargetResult:
        target = request.path
        if request.query:
            target += "?" + urlencode(request.query)
        redirect_chain = [target]
        method = request.method
        body = request.body.content
        headers = list(request.headers)
        content_type = {
            HttpBodyKind.TEXT: "text/plain; charset=utf-8",
            HttpBodyKind.JSON: "application/json",
            HttpBodyKind.FORM: "application/x-www-form-urlencoded",
        }.get(request.body.kind)
        if content_type is not None and not any(name.lower() == "content-type" for name, _ in headers):
            headers.append(("Content-Type", content_type))
        cookies = self._http_cookies.setdefault(handle, {})
        session = self._http_sessions.get(handle)
        if session is None:
            return self._http_result(
                TargetOutcome.REVOKED,
                request_id,
                request_digest,
                binding,
                redirect_chain=redirect_chain,
                cookies=cookies,
            )
        elapsed_ms = 0
        request_bytes = 0
        response_bytes = 0
        certificate = ""
        transported = None
        for redirect_index in range(self.limits.max_redirects + 1):
            with self._lock:
                used_connections = self._connections.get(binding.generation_id, 0)
                if used_connections >= self.limits.max_connections:
                    return self._http_result(
                        TargetOutcome.BUDGET_EXHAUSTED,
                        request_id,
                        request_digest,
                        binding,
                        request_bytes=request_bytes,
                        response_bytes=response_bytes,
                        elapsed_ms=elapsed_ms,
                        redirect_chain=redirect_chain,
                        cookies=cookies,
                    )
                self._connections[binding.generation_id] = used_connections + 1
            request_headers = list(headers)
            if cookies:
                request_headers.append(
                    ("Cookie", "; ".join(f"{name}={value}" for name, value in sorted(cookies.items())))
                )
            remaining_response = self.limits.max_total_response_bytes - self._session_response_bytes.get(
                binding.generation_id, 0
            )
            if remaining_response <= 0:
                return self._http_result(
                    TargetOutcome.BUDGET_EXHAUSTED,
                    request_id,
                    request_digest,
                    binding,
                    request_bytes=request_bytes,
                    response_bytes=response_bytes,
                    elapsed_ms=elapsed_ms,
                    redirect_chain=redirect_chain,
                    cookies=cookies,
                )
            transported = session.exchange(
                HttpTransportRequest(method, target, body, tuple(request_headers)),
                max_response_bytes=min(self.limits.max_response_bytes, remaining_response),
            )
            elapsed_ms += transported.elapsed_ms
            request_bytes += transported.request_bytes
            response_bytes += transported.response_bytes
            certificate = transported.certificate_sha256 or certificate
            if transported.outcome is not TargetOutcome.ANSWERED:
                return self._http_result(
                    transported.outcome,
                    request_id,
                    request_digest,
                    binding,
                    status=transported.status,
                    request_bytes=request_bytes,
                    response_bytes=response_bytes,
                    elapsed_ms=elapsed_ms,
                    redirect_chain=redirect_chain,
                    cookies=cookies,
                    certificate=certificate,
                )
            if not self._retain_cookies(cookies, transported.headers):
                return self._http_result(
                    TargetOutcome.TOO_LARGE,
                    request_id,
                    request_digest,
                    binding,
                    request_bytes=request_bytes,
                    response_bytes=response_bytes,
                    elapsed_ms=elapsed_ms,
                    redirect_chain=redirect_chain,
                    cookies=cookies,
                    certificate=certificate,
                )
            location = next((value for name, value in transported.headers if name == "location"), "")
            if 300 <= transported.status < 400 and location:
                redirected = _same_origin_target(self.endpoint, target, location)
                if redirected is None or redirect_index >= self.limits.max_redirects:
                    return self._http_result(
                        TargetOutcome.DENIED,
                        request_id,
                        request_digest,
                        binding,
                        status=transported.status,
                        request_bytes=request_bytes,
                        response_bytes=response_bytes,
                        elapsed_ms=elapsed_ms,
                        redirect_chain=redirect_chain,
                        cookies=cookies,
                        certificate=certificate,
                    )
                target = redirected
                redirect_chain.append(target)
                if transported.status in {301, 302, 303} and method not in {"GET", "HEAD"}:
                    method, body = "GET", b""
                    headers = [(name, value) for name, value in headers if name.lower() != "content-type"]
                continue
            break
        assert transported is not None
        selected = {name.lower() for name in request.response_headers}
        visible_headers = tuple((name, value) for name, value in transported.headers if name in selected)
        return self._http_result(
            TargetOutcome.ANSWERED,
            request_id,
            request_digest,
            binding,
            body=transported.body,
            status=transported.status,
            headers=visible_headers,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            elapsed_ms=elapsed_ms,
            redirect_chain=redirect_chain,
            cookies=cookies,
            certificate=certificate,
        )

    def _http_result(
        self,
        outcome: TargetOutcome,
        request_id: str,
        request_digest: str,
        binding: CapabilityBinding,
        *,
        body: bytes = b"",
        status: int = 0,
        headers: tuple[tuple[str, str], ...] = (),
        request_bytes: int = 0,
        response_bytes: int = 0,
        elapsed_ms: int = 0,
        redirect_chain: list[str],
        cookies: Mapping[str, str],
        certificate: str = "",
    ) -> TargetResult:
        with self._lock:
            generation_id = binding.generation_id
            total_request = self._session_request_bytes.get(generation_id, 0) + request_bytes
            total_response = self._session_response_bytes.get(generation_id, 0) + response_bytes
            total_elapsed = self._session_elapsed_ms.get(generation_id, 0) + elapsed_ms
            if (
                total_request > self.limits.max_total_request_bytes
                or total_response > self.limits.max_total_response_bytes
                or total_elapsed > int(self.limits.max_total_seconds * 1000)
            ):
                outcome, body, status, headers = TargetOutcome.BUDGET_EXHAUSTED, b"", 0, ()
            else:
                self._session_request_bytes[generation_id] = total_request
                self._session_response_bytes[generation_id] = total_response
                self._session_elapsed_ms[generation_id] = total_elapsed
        provenance = TargetProvenance(
            challenge_id=self.challenge_id,
            generation_id=binding.generation_id,
            endpoint="declared-target",
            protocol=self.endpoint.protocol,
            request_bytes=request_bytes,
            response_bytes=response_bytes,
            transcript_digest=hashlib.sha256(self._transcript(request_digest, body, status)).hexdigest(),
            elapsed_ms=elapsed_ms,
            resolved_address=self._address,
            server_name=self.endpoint.host if self.endpoint.protocol is TargetProtocol.HTTPS else "",
            certificate_sha256=certificate,
        )
        return TargetResult(
            outcome,
            body,
            status,
            provenance,
            request_id,
            headers,
            tuple(redirect_chain),
            tuple(sorted(cookies.items())),
        )

    def _retain_cookies(self, retained: dict[str, str], headers: tuple[tuple[str, str], ...]) -> bool:
        for name, value in headers:
            if name != "set-cookie":
                continue
            parsed = http.cookies.SimpleCookie()
            try:
                parsed.load(value)
            except http.cookies.CookieError:
                return False
            for cookie_name, morsel in parsed.items():
                retained[cookie_name] = morsel.value
        return (
            len(retained) <= self.limits.max_cookies
            and sum(len(name.encode()) + len(value.encode()) for name, value in retained.items())
            <= self.limits.max_cookie_bytes
        )

    def exchange(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        try:
            peer = self.authority.peer_identity(connection)
            grant = self.authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = TargetOutcome.REVOKED if error.reason == "revoked" else TargetOutcome.CAPABILITY_REFUSED
            return TargetResult(outcome)
        if grant.scope != TARGET_SCOPE:
            return TargetResult(TargetOutcome.CAPABILITY_REFUSED)
        request_digest = hashlib.sha256(canonical_bytes(request)).hexdigest()
        if self._recovery is not None and any(
            event.event_type == TARGET_EXCHANGE_RECORDED
            and event.payload.get("record") == TargetRecord.CLASSIFIED.value
            and event.payload.get("challenge_id") == self.challenge_id
            and event.payload.get("endpoint") == f"{self.endpoint.host}:{self.endpoint.port}"
            and event.payload.get("resolved_address") == self._address
            and event.payload.get("protocol") == self.endpoint.protocol.value
            and event.payload.get("request_digest") == request_digest
            and event.payload.get("outcome") in {TargetOutcome.TIMEOUT.value, TargetOutcome.UNREACHABLE.value}
            for event in self._store.events()
        ):
            return TargetResult(TargetOutcome.DENIED)
        with self._lock:
            self._serial += 1
            request_id = f"{self._request_prefix}{self._serial:06d}"
            self._request_operations[request_id] = "exchange"
        self._append_record(request_id, TargetRecord.RESERVED, grant.binding, request_digest)
        validated = _validate_exchange(self.endpoint, request)
        if validated is None:
            return self._classify(request_id, grant.binding, request_digest, TargetOutcome.DENIED, b"", 0, 0, 0)
        typed_request, request_body = validated
        try:
            measured_request = request_size(self.endpoint, typed_request, request_body)
        except (UnicodeError, ValueError):
            measured_request = None
        if measured_request is None:
            return self._classify(request_id, grant.binding, request_digest, TargetOutcome.DENIED, b"", 0, 0, 0)
        if measured_request > self.limits.max_request_bytes:
            return self._classify(
                request_id,
                grant.binding,
                request_digest,
                TargetOutcome.TOO_LARGE,
                b"",
                0,
                0,
                0,
            )
        with self._lock:
            used = self._connections.get(grant.binding.generation_id, 0)
            if used >= self.limits.max_connections:
                return self._classify(
                    request_id,
                    grant.binding,
                    request_digest,
                    TargetOutcome.BUDGET_EXHAUSTED,
                    b"",
                    0,
                    len(request_body),
                    0,
                )
            self._connections[grant.binding.generation_id] = used + 1
        transported = transport_exchange(self.endpoint, self._address, self.limits, typed_request, request_body)
        outcome, body, status = transported.outcome, transported.body, transported.status
        elapsed_ms = transported.elapsed_ms
        transcript = self._transcript(request_digest, body, status)
        provenance = TargetProvenance(
            challenge_id=self.challenge_id,
            generation_id=grant.binding.generation_id,
            endpoint="declared-target",
            protocol=self.endpoint.protocol,
            request_bytes=transported.request_bytes,
            response_bytes=transported.response_bytes,
            transcript_digest=hashlib.sha256(transcript).hexdigest(),
            elapsed_ms=elapsed_ms,
        )
        try:
            self.authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome, body = (
                TargetOutcome.REVOKED if error.reason == "revoked" else TargetOutcome.CAPABILITY_REFUSED,
                b"",
            )
            status = 0
            transcript = self._transcript(request_digest, body, status)
            provenance = replace(
                provenance,
                response_bytes=0,
                transcript_digest=hashlib.sha256(transcript).hexdigest(),
            )
        result = TargetResult(outcome, body, status, provenance, request_id)
        self._append_record(
            request_id,
            TargetRecord.CLASSIFIED,
            grant.binding,
            request_digest,
            result=result,
            body=body,
        )
        if self._recovery is not None and outcome in {TargetOutcome.TIMEOUT, TargetOutcome.UNREACHABLE}:
            safe_read = self.endpoint.protocol is TargetProtocol.HTTP and typed_request.get("method") == "GET"
            from solver.recovery.safe_read import SafeReadFault, SafeReadRecovery

            try:
                now = dt.datetime.fromisoformat(self._timestamp())
            except ValueError:
                now = dt.datetime.now(dt.timezone.utc)
            SafeReadRecovery(
                self._recovery,
                fence=(lambda: None) if safe_read else lambda: self.revoke_generation(grant.binding.generation_id),
            ).contain(
                SafeReadFault(
                    fault_id=f"target:{request_id}:{outcome.value}",
                    scope="external:target",
                    generation_id=grant.binding.generation_id,
                    evidence=result.provenance.transcript_digest,
                    failed_request=request_id,
                    observed_at=now,
                )
            )
        return result

    def record_denial(self, binding: CapabilityBinding, kind: str) -> TargetResult:
        """Record a trusted controller's observation of one hostile raw connection denial."""

        endpoint = self._denied_endpoints.get(kind)
        attempted = (
            hashlib.sha256(f"{endpoint.host}:{endpoint.port}".encode()).hexdigest() if endpoint is not None else ""
        )
        canonical = self._store.events()
        declaration = {"kind": kind, "attempted_endpoint_digest": attempted}
        observed = next(
            (
                event
                for event in reversed(canonical)
                if event.event_type == ATTEMPT_ENVELOPE_RECORDED
                and event.payload["record"] == "result"
                and event.payload["outcome"] == "network-limit"
                and event.payload["generation_id"] == binding.generation_id
                and event.payload["attempt_id"] == binding.attempt_id
                and event.payload["step_id"] == binding.step_id
                and event.payload["network_probe"] == declaration
                and any(
                    reserved.event_type == ATTEMPT_ENVELOPE_RECORDED
                    and reserved.payload["record"] == "reserved"
                    and reserved.payload["envelope_id"] == event.payload["envelope_id"]
                    and reserved.payload["network_probe"] == declaration
                    for reserved in canonical
                )
                and event.event_digest not in self._denial_observations
            ),
            None,
        )
        if endpoint is None or observed is None:
            raise ValueError("Target deny proof was not observed at a trusted endpoint")
        self._denial_observations.add(observed.event_digest)
        with self._lock:
            self._serial += 1
            request_id = f"{self._request_prefix}{self._serial:06d}"
            self._request_operations[request_id] = "denial"
        digest = hashlib.sha256(
            canonical_bytes({"probe_kind": kind, "attempted_endpoint_digest": attempted})
        ).hexdigest()
        self._append_record(
            request_id,
            TargetRecord.RESERVED,
            binding,
            digest,
            probe_kind=kind,
            attempted=attempted,
            observation_sequence=observed.sequence,
            observation_digest=observed.event_digest,
        )
        result = TargetResult(
            TargetOutcome.DENIED,
            provenance=TargetProvenance(
                challenge_id=self.challenge_id,
                generation_id=binding.generation_id,
                endpoint="declared-target",
                protocol=self.endpoint.protocol,
                transcript_digest=hashlib.sha256(self._transcript(digest, b"", 0)).hexdigest(),
            ),
            request_id=request_id,
        )
        self._append_record(
            request_id,
            TargetRecord.CLASSIFIED,
            binding,
            digest,
            result=result,
            probe_kind=kind,
            attempted=attempted,
            observation_sequence=observed.sequence,
            observation_digest=observed.event_digest,
        )
        return result

    def _classify(
        self,
        request_id: str,
        binding: CapabilityBinding,
        request_digest: str,
        outcome: TargetOutcome,
        body: bytes,
        status: int,
        request_bytes: int,
        elapsed_ms: int,
    ) -> TargetResult:
        provenance = TargetProvenance(
            self.challenge_id,
            binding.generation_id,
            "declared-target",
            self.endpoint.protocol,
            request_bytes,
            len(body),
            hashlib.sha256(self._transcript(request_digest, body, status)).hexdigest(),
            elapsed_ms,
        )
        result = TargetResult(outcome, body, status, provenance, request_id)
        self._append_record(request_id, TargetRecord.CLASSIFIED, binding, request_digest, result=result, body=body)
        return result

    def _append_record(
        self,
        request_id: str,
        record: TargetRecord,
        binding: CapabilityBinding,
        request_digest: str,
        *,
        result: TargetResult | None = None,
        body: bytes = b"",
        probe_kind: str = "",
        attempted: str = "",
        observation_sequence: int = 0,
        observation_digest: str = "",
    ) -> None:
        provenance = result.provenance if result else TargetProvenance()
        response_headers_digest = hashlib.sha256(canonical_bytes(result.headers if result else ())).hexdigest()
        cookies_digest = hashlib.sha256(canonical_bytes(result.cookies if result else ())).hexdigest()
        browser_digest = hashlib.sha256(
            canonical_bytes(_browser_document(result.browser) if result else {})
        ).hexdigest()
        event_id = request_id + (":reserved" if record is TargetRecord.RESERVED else ":classified")
        sealed = self._transcript(request_digest, body, result.status) if result else b""
        self._store.append(
            TargetBrokerRecorded(
                event_id=event_id,
                request_id=request_id,
                record=record,
                binding_digest=binding.digest,
                run_id=binding.run_id,
                boot_id=binding.boot_id,
                generation_id=binding.generation_id,
                lane_id=binding.lane_id,
                attempt_id=binding.attempt_id,
                step_id=binding.step_id,
                challenge_id=self.challenge_id,
                image_id=self.candidate.image_id,
                image_manifest_digest=self.candidate.manifest_digest,
                image_config_digest=self.candidate.config_digest,
                platform=self.candidate.platform,
                profile_digest=self.candidate.profile_digest,
                endpoint=f"{self.endpoint.host}:{self.endpoint.port}",
                resolved_address=self._address,
                protocol=self.endpoint.protocol,
                max_connections=self.limits.max_connections,
                max_request_bytes=self.limits.max_request_bytes,
                max_response_bytes=self.limits.max_response_bytes,
                timeout_ms=int(self.limits.timeout_seconds * 1000),
                operation=self._request_operations.get(request_id, "exchange"),
                max_exchanges=self.limits.max_exchanges,
                max_total_request_bytes=self.limits.max_total_request_bytes,
                max_total_response_bytes=self.limits.max_total_response_bytes,
                max_total_seconds_ms=int(self.limits.max_total_seconds * 1000),
                max_redirects=self.limits.max_redirects,
                max_cookies=self.limits.max_cookies,
                request_digest=request_digest,
                observation_sequence=observation_sequence,
                observation_digest=observation_digest,
                outcome=result.outcome if result else None,
                request_bytes=provenance.request_bytes,
                response_bytes=provenance.response_bytes,
                status=result.status if result else 0,
                elapsed_ms=provenance.elapsed_ms,
                transcript_digest=provenance.transcript_digest,
                probe_kind=probe_kind,
                attempted_endpoint_digest=attempted,
                server_name=provenance.server_name,
                certificate_sha256=provenance.certificate_sha256,
                response_headers_digest=response_headers_digest,
                redirect_chain=result.redirect_chain if result else (),
                cookies_digest=cookies_digest,
                browser_observation_digest=browser_digest,
                ts=self._timestamp(),
            ),
            body=sealed,
        )

    def _transcript(self, request_digest: str, body: bytes, status: int) -> bytes:
        return canonical_bytes(
            {
                "endpoint": f"{self.endpoint.host}:{self.endpoint.port}",
                "protocol": self.endpoint.protocol.value,
                "request_digest": request_digest,
                "response_digest": hashlib.sha256(body).hexdigest(),
                "status": status,
            }
        )


def _validate_exchange(
    endpoint: TargetEndpoint, request: Mapping[str, object]
) -> tuple[TargetExchangeRequest, bytes] | None:
    expected = {"body"} if endpoint.protocol is TargetProtocol.TCP else {"method", "path", "body"}
    if set(request) != expected or any(not isinstance(request[name], str) for name in expected):
        return None
    try:
        body = base64.b64decode(request["body"], validate=True)
    except (ValueError, binascii.Error, UnicodeError):
        return None
    if endpoint.protocol is TargetProtocol.TCP:
        return TcpTargetExchangeRequest(body=request["body"]), body
    return HttpTargetExchangeRequest(
        method=request["method"],
        path=request["path"],
        body=request["body"],
    ), body


def _validate_http_session(request: Mapping[str, object]) -> HttpSessionRequest | None:
    if set(request) != {"method", "path", "query", "body", "headers", "response_headers"}:
        return None
    method, path = request.get("method"), request.get("path")
    if (
        not isinstance(method, str)
        or method not in HTTP_METHODS
        or not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or len(path.encode()) > 4096
        or any(character in path for character in "\r\n?#")
    ):
        return None
    query = _string_pairs(request.get("query"), maximum=64)
    headers = _string_pairs(request.get("headers"), maximum=64)
    response_headers = request.get("response_headers")
    body = request.get("body")
    if query is None or headers is None or not isinstance(response_headers, list) or not isinstance(body, Mapping):
        return None
    if (
        len(response_headers) > 64
        or any(not isinstance(name, str) or HTTP_HEADER_NAME.fullmatch(name) is None for name in response_headers)
        or len({name.lower() for name in response_headers}) != len(response_headers)
    ):
        return None
    normalized_headers = []
    seen = set()
    for name, value in headers:
        lowered = name.lower()
        if (
            HTTP_HEADER_NAME.fullmatch(name) is None
            or lowered in FORBIDDEN_HTTP_HEADERS
            or lowered.startswith("proxy-")
            or lowered in seen
            or any(character in value for character in "\r\n\0")
            or len(value.encode()) > 4096
        ):
            return None
        seen.add(lowered)
        normalized_headers.append((name, value))
    if (
        set(body) != {"kind", "content"}
        or not isinstance(body.get("kind"), str)
        or not isinstance(body.get("content"), str)
    ):
        return None
    try:
        kind = HttpBodyKind(body["kind"])
        content = base64.b64decode(body["content"], validate=True)
        if kind is HttpBodyKind.JSON:
            json.loads(content)
    except (ValueError, TypeError, binascii.Error, UnicodeError, json.JSONDecodeError):
        return None
    return HttpSessionRequest(
        method,
        path,
        tuple(query),
        HttpBody(kind, content),
        tuple(normalized_headers),
        tuple(name.lower() for name in response_headers),
    )


def _validate_tcp_session(request: Mapping[str, object]) -> TcpSessionRequest | None:
    if set(request) != {"body", "receive", "half_close"}:
        return None
    body, receive, half_close = request.get("body"), request.get("receive"), request.get("half_close")
    if not isinstance(body, str) or not isinstance(receive, Mapping) or not isinstance(half_close, bool):
        return None
    if set(receive) != {"mode", "maximum_bytes", "size", "delimiter", "length_bytes", "byteorder"}:
        return None
    try:
        payload = base64.b64decode(body, validate=True)
        delimiter = base64.b64decode(str(receive["delimiter"]), validate=True)
        mode = TcpReceiveMode(str(receive["mode"]))
        maximum = receive["maximum_bytes"]
        size = receive["size"]
        length_bytes = receive["length_bytes"]
        byteorder = receive["byteorder"]
    except (ValueError, TypeError, binascii.Error, UnicodeError):
        return None
    if any(not isinstance(value, int) or isinstance(value, bool) for value in (maximum, size, length_bytes)):
        return None
    if not 1 <= maximum <= 16 * 1024 * 1024 or byteorder not in {"big", "little"}:
        return None
    if mode is TcpReceiveMode.RAW and (size or delimiter or length_bytes):
        return None
    if mode is TcpReceiveMode.LINE and (size or delimiter != b"\n" or length_bytes):
        return None
    if mode is TcpReceiveMode.DELIMITER and (size or not 1 <= len(delimiter) <= 64 or length_bytes):
        return None
    if mode is TcpReceiveMode.FIXED and (not 1 <= size <= maximum or delimiter or length_bytes):
        return None
    if mode is TcpReceiveMode.LENGTH_PREFIXED and (size or delimiter or not 1 <= length_bytes <= 8):
        return None
    return TcpSessionRequest(
        payload,
        TcpReceive(mode, maximum, size, delimiter, length_bytes, str(byteorder)),
        half_close,
    )


def _validate_browser_session(
    request: Mapping[str, object], admitted_subresources: set[str]
) -> BrowserSessionRequest | None:
    if set(request) != {"path", "wait_selector", "download_selector", "admitted_subresources"}:
        return None
    path = request.get("path")
    wait_selector = request.get("wait_selector")
    download_selector = request.get("download_selector")
    subresources = request.get("admitted_subresources")
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or path.startswith("//")
        or any(character in path for character in "\r\n#")
        or len(path.encode()) > 4096
        or not isinstance(wait_selector, str)
        or not isinstance(download_selector, str)
        or len(wait_selector.encode()) > 1024
        or len(download_selector.encode()) > 1024
        or not isinstance(subresources, list)
        or len(subresources) > 16
        or any(not isinstance(value, str) or value not in admitted_subresources for value in subresources)
        or len(set(subresources)) != len(subresources)
    ):
        return None
    return BrowserSessionRequest(path, wait_selector, download_selector, tuple(subresources))


def _browser_document(observations: BrowserObservations) -> dict[str, object]:
    return {
        "dom": observations.dom,
        "network": [list(item) for item in observations.network],
        "local_storage": [list(item) for item in observations.local_storage],
        "session_storage": [list(item) for item in observations.session_storage],
        "downloads": [list(item) for item in observations.downloads],
    }


def _string_pairs(value: object, *, maximum: int) -> list[tuple[str, str]] | None:
    if not isinstance(value, list) or len(value) > maximum:
        return None
    pairs = []
    for pair in value:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not all(isinstance(item, str) and len(item.encode()) <= 4096 for item in pair)
        ):
            return None
        pairs.append((pair[0], pair[1]))
    return pairs


def _same_origin_target(endpoint: TargetEndpoint, current: str, location: str) -> str | None:
    scheme = endpoint.protocol.value
    origin = f"{scheme}://{endpoint.host}:{endpoint.port}"
    redirected = urlsplit(urljoin(origin + current, location))
    expected_port = endpoint.port
    actual_port = redirected.port or (443 if redirected.scheme == "https" else 80)
    if (
        redirected.scheme != scheme
        or redirected.hostname != endpoint.host
        or actual_port != expected_port
        or redirected.username
        or redirected.password
        or redirected.fragment
    ):
        return None
    target = redirected.path or "/"
    if redirected.query:
        target += "?" + redirected.query
    return target if len(target.encode()) <= 4096 else None


__all__ = ["TargetBrokerRuntime"]
