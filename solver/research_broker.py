"""Bounded, generation-scoped access to public Internet Research."""

from __future__ import annotations

import hashlib
import base64
import binascii
import json
import re
import ipaddress
import secrets
import socket
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from urllib.parse import quote

from solver.capability import CapabilityAuthority, CapabilityBinding, CapabilityRefused, local_peer_identity
from solver.event_store import EventStore
from solver.event_store_contracts import ATTEMPT_ENVELOPE_RECORDED
from solver.redaction import Redactor
from solver.research_broker_contracts import (
    RESEARCH_BROKER_RECORDED,
    ResearchBrokerRecorded,
    ResearchLimits,
    ResearchKind,
    ResearchOutcome,
    ResearchPolicyDecision,
    ResearchProvenance,
    ResearchResult,
    ResearchQuery,
    ResearchSource,
    ResearchTransportResult,
)
from solver.research_broker_transport import fetch as transport_fetch

RESEARCH_SCOPE = "research.fetch"


@dataclass(frozen=True)
class _QueryMetadata:
    kind: ResearchKind | None = None
    source_id: str = ""
    terms: str = ""
    robots: str = ""
    origin: str = ""
    query_digest: str = ""
    terms_decision: str = ResearchPolicyDecision.NOT_APPLICABLE.value
    robots_decision: str = ResearchPolicyDecision.NOT_APPLICABLE.value
    policy_decision: str = ResearchPolicyDecision.NOT_APPLICABLE.value

    @property
    def cache_fragment(self) -> str:
        return "\0".join(
            (
                self.kind.value if self.kind is not None else "",
                self.source_id,
                self.terms,
                self.robots,
                self.origin,
                self.query_digest,
                self.terms_decision,
                self.robots_decision,
                self.policy_decision,
            )
        )

    def provenance(self, **fields) -> ResearchProvenance:
        return ResearchProvenance(
            **fields,
            kind=self.kind,
            source_id=self.source_id,
            terms=self.terms,
            robots=self.robots,
            origin=self.origin,
            query_digest=self.query_digest,
            terms_decision=self.terms_decision,
            robots_decision=self.robots_decision,
            policy_decision=self.policy_decision,
        )


class ResearchBrokerRuntime:
    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        boot_id: str,
        limits: ResearchLimits,
        timestamp: Callable[[], str],
        resolve: Callable[[str], tuple[str, ...]] | None = None,
        dns_resolve: Callable[[str], tuple[str, ...]] | None = None,
        transport: Callable[[str, str, ResearchLimits], ResearchTransportResult] | None = None,
        denied_hosts: tuple[str, ...] = (),
        sources: dict[str, ResearchSource] | None = None,
        recovery=None,
    ) -> None:
        self.run_id = run_id
        self.boot_id = boot_id
        self.limits = limits
        self._timestamp = timestamp
        self._resolve = resolve or self._system_resolve
        self._dns_resolve = dns_resolve
        self._transport = transport
        self._denied_hosts = tuple(host.lower().rstrip(".") for host in denied_hosts)
        self._sources = dict(sources or {})
        self._store = EventStore(state, run_id=run_id, redactor=Redactor({}))
        self._authority = CapabilityAuthority(
            state=state,
            run_id=run_id,
            boot_id=boot_id,
            redactor=Redactor({}),
            peer_identity=local_peer_identity,
            timestamp=timestamp,
        )
        self._authority.reconcile_restart(RESEARCH_SCOPE)
        self._expected: dict[str, CapabilityBinding] = {}
        self._handles: dict[str, set[str]] = {}
        self._serial = max(
            (
                int(str(event.payload["request_id"]).rsplit(":", 1)[-1])
                for event in self._store.events()
                if event.event_type == RESEARCH_BROKER_RECORDED
                and str(event.payload.get("request_id", "")).startswith("research-broker:")
            ),
            default=0,
        )
        self._lock = threading.Lock()
        self._cache: dict[str, ResearchResult] = {}
        self._requests: dict[str, int] = {}
        self._bytes: dict[str, int] = {}
        self._elapsed: dict[str, int] = {}
        self._last_request: dict[str, datetime] = {}
        self._cancelled: dict[str, threading.Event] = {}
        self._active_connections: dict[str, object] = {}
        self._recovery = recovery
        if recovery is not None:
            from solver.recovery.safe_read import SafeReadRecovery

            SafeReadRecovery(recovery).replay()

    def prepare_attempt(self, binding: CapabilityBinding) -> None:
        if binding.run_id != self.run_id or binding.boot_id != self.boot_id:
            raise ValueError("Research Attempt binding belongs to another Run or Boot")
        self._expected[binding.generation_id] = binding
        self._cancelled[binding.generation_id] = threading.Event()

    def bind_attempt_executor(self, executor) -> None:
        """Remove Research authority and interrupt transport at the generation fence."""

        executor.add_generation_revocation(self.revoke_generation)

    def claim(self, connection: socket.socket, generation_id: str) -> str:
        binding = self._expected.pop(generation_id, None)
        if binding is None:
            raise CapabilityRefused()
        handle = self._authority.issue(binding, RESEARCH_SCOPE, self._authority.peer_identity(connection))
        self._handles.setdefault(generation_id, set()).add(handle)
        return handle

    def revoke_generation(self, generation_id: str) -> None:
        self._expected.pop(generation_id, None)
        cancelled = self._cancelled.setdefault(generation_id, threading.Event())
        cancelled.set()
        with self._lock:
            connection = self._active_connections.pop(generation_id, None)
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
        for handle in tuple(self._handles.pop(generation_id, ())):
            self._authority.revoke(handle, "generation-closing")

    def record_raw_egress_denial(self, binding: CapabilityBinding, attempted_endpoint_digest: str) -> ResearchResult:
        """Bind a trusted strict-executor network denial to Research evidence."""

        observed = next(
            (
                event
                for event in reversed(self._store.events())
                if event.event_type == ATTEMPT_ENVELOPE_RECORDED
                and event.payload["record"] == "result"
                and event.payload["outcome"] == "network-limit"
                and event.payload["generation_id"] == binding.generation_id
                and event.payload["attempt_id"] == binding.attempt_id
                and event.payload["step_id"] == binding.step_id
                and event.payload["network_probe"]
                == {"kind": "research", "attempted_endpoint_digest": attempted_endpoint_digest}
            ),
            None,
        )
        if observed is None:
            raise ValueError("Research raw-egress denial was not observed by the strict executor")
        with self._lock:
            self._serial += 1
            request_id = f"research-broker:{self._serial:06d}"
        now = self._timestamp()
        result = ResearchResult(
            ResearchOutcome.DENIED,
            provenance=ResearchProvenance(observed_at=now, expires_at=now),
            request_id=request_id,
        )
        self._append(binding, "raw-egress:" + attempted_endpoint_digest, result)
        return result

    def query(
        self,
        connection: socket.socket,
        handle: str,
        supplied: ResearchQuery | dict[str, object],
    ) -> ResearchResult:
        """Resolve one typed OSINT query against an admitted source or recording."""

        try:
            peer = self._authority.peer_identity(connection)
            grant = self._authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = ResearchOutcome.REVOKED if error.reason == "revoked" else ResearchOutcome.CAPABILITY_REFUSED
            return ResearchResult(outcome)
        if grant.scope != RESEARCH_SCOPE:
            return ResearchResult(ResearchOutcome.CAPABILITY_REFUSED)
        query = _query(supplied)
        if query is None:
            return ResearchResult(ResearchOutcome.CAPABILITY_REFUSED)
        source = self._sources.get(query.source_id)
        if query.kind is ResearchKind.DNS and query.source_id == "dns":
            source = ResearchSource(
                ResearchKind.DNS,
                "dns:{subject}",
                "public-dns",
                "not-applicable-protocol",
                ResearchPolicyDecision.NOT_APPLICABLE.value,
                ResearchPolicyDecision.NOT_APPLICABLE.value,
            )
        if source is None or source.kind is not query.kind:
            return ResearchResult(ResearchOutcome.CAPABILITY_REFUSED)
        query_digest = hashlib.sha256(query.subject.encode()).hexdigest()
        metadata = _QueryMetadata(
            kind=query.kind,
            source_id=query.source_id,
            terms=source.terms,
            robots=source.robots,
            origin="recorded" if query.body else "live",
            query_digest=query_digest,
            terms_decision=source.terms_decision,
            robots_decision=source.robots_decision,
            policy_decision=source.policy_decision,
        )
        if metadata.policy_decision == ResearchPolicyDecision.DENY.value:
            now = self._timestamp()
            result = ResearchResult(
                ResearchOutcome.DENIED,
                provenance=metadata.provenance(observed_at=now, expires_at=now),
                request_id=self._next_request_id(),
            )
            result = self._release_current(connection, handle, peer, result)
            self._append(grant.binding, f"policy:{query.source_id}:{query_digest}", result)
            return result
        if query.body:
            if len(query.body) > self.limits.max_body_bytes or not query.content_type:
                return ResearchResult(ResearchOutcome.TOO_LARGE)
            refused = self._reserve(grant.binding.generation_id)
            if refused is not None:
                return ResearchResult(refused)
            if not self._consume(grant.binding.generation_id, len(query.body), 0):
                return ResearchResult(ResearchOutcome.BUDGET_EXHAUSTED)
            with self._lock:
                self._serial += 1
                request_id = f"research-broker:{self._serial:06d}"
            now = self._timestamp()
            result = ResearchResult(
                ResearchOutcome.ANSWERED,
                query.body,
                200,
                query.content_type,
                metadata.provenance(
                    body_digest="sha256:" + hashlib.sha256(query.body).hexdigest(),
                    observed_at=now,
                    expires_at=now,
                ),
                request_id,
            )
            result = self._release_current(connection, handle, peer, result)
            self._append(grant.binding, f"recorded:{query.source_id}:{query_digest}", result)
            return result
        if query.kind is ResearchKind.DNS:
            refused = self._reserve(grant.binding.generation_id)
            if refused is not None:
                return ResearchResult(refused)
            try:
                supplied_address = ipaddress.ip_address(query.subject)
            except ValueError:
                supplied_address = None
            try:
                if self._dns_resolve is None:
                    addresses = _public_dns_resolve(
                        query.subject,
                        timeout_seconds=self.limits.timeout_seconds,
                        cancelled=self._cancelled.setdefault(grant.binding.generation_id, threading.Event()),
                        activate=lambda active: self._activate(grant.binding.generation_id, active),
                    )
                else:
                    addresses = self._dns_resolve(query.subject)
                addresses = tuple(dict.fromkeys(addresses))
            except OSError:
                addresses = ()
            if supplied_address is not None and not supplied_address.is_global:
                addresses = ()
            elif not addresses or not all(_public(address) for address in addresses):
                addresses = ()
            body = json.dumps({"addresses": addresses}, sort_keys=True, separators=(",", ":")).encode()
            if not self._consume(grant.binding.generation_id, len(body), 0):
                return ResearchResult(ResearchOutcome.BUDGET_EXHAUSTED)
            with self._lock:
                self._serial += 1
                request_id = f"research-broker:{self._serial:06d}"
            now = self._timestamp()
            outcome = (
                ResearchOutcome.ANSWERED
                if addresses and len(body) <= self.limits.max_body_bytes
                else ResearchOutcome.DENIED
            )
            result = ResearchResult(
                outcome,
                body if outcome is ResearchOutcome.ANSWERED else b"",
                0,
                "application/vnd.incypher.osint+json",
                metadata.provenance(
                    dns_chain=((query.subject, addresses),),
                    body_digest="sha256:" + hashlib.sha256(body).hexdigest(),
                    observed_at=now,
                    expires_at=now,
                ),
                request_id,
            )
            result = self._release_current(connection, handle, peer, result)
            self._append(grant.binding, f"dns:{query_digest}", result)
            return result
        url = source.url_template.replace("{subject}", quote(query.subject, safe=""))
        if "{md5_subject}" in url:
            normalized = query.subject.strip().lower().encode()
            url = url.replace("{md5_subject}", hashlib.md5(normalized, usedforsecurity=False).hexdigest())
        return self.fetch(connection, handle, url, _query_metadata=metadata)

    def fetch(
        self,
        connection: socket.socket,
        handle: str,
        url: str,
        *,
        _query_metadata: _QueryMetadata | None = None,
    ) -> ResearchResult:
        try:
            peer = self._authority.peer_identity(connection)
            grant = self._authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = ResearchOutcome.REVOKED if error.reason == "revoked" else ResearchOutcome.CAPABILITY_REFUSED
            return ResearchResult(outcome)
        if grant.scope != RESEARCH_SCOPE:
            return ResearchResult(ResearchOutcome.CAPABILITY_REFUSED)
        refused = self._reserve(grant.binding.generation_id)
        if refused is not None:
            return ResearchResult(refused)
        url_digest = hashlib.sha256(url.encode()).hexdigest()
        if self._recovery is not None and any(
            event.event_type == RESEARCH_BROKER_RECORDED
            and event.payload.get("url_digest") == url_digest
            and event.payload.get("outcome") in {ResearchOutcome.TIMEOUT.value, ResearchOutcome.UNREACHABLE.value}
            for event in self._store.events()
        ):
            return ResearchResult(ResearchOutcome.DENIED)
        with self._lock:
            self._serial += 1
            request_id = f"research-broker:{self._serial:06d}"
        now = self._timestamp()
        metadata = _query_metadata or _QueryMetadata()
        cache_key = url + ("\0" + metadata.cache_fragment if _query_metadata else "")
        cached = self._cache.get(cache_key)
        if cached is not None and _before(now, cached.provenance.expires_at):
            if not self._consume(grant.binding.generation_id, len(cached.body), 0):
                result = _without_body(
                    replace(cached, request_id=request_id, cached=True),
                    ResearchOutcome.BUDGET_EXHAUSTED,
                )
                self._append(grant.binding, url, result)
                return result
            result = replace(cached, request_id=request_id, cached=True)
            result = self._release_current(connection, handle, peer, result)
            self._append(grant.binding, url, result)
            return result
        outcome, body, status, content_type = ResearchOutcome.DENIED, b"", 0, ""
        dns_chain: list[tuple[str, tuple[str, ...]]] = []
        redirects: list[str] = []
        elapsed = 0
        current = url
        for _ in range(self.limits.max_redirects + 1):
            parsed = urlsplit(current)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                outcome = ResearchOutcome.DENIED
                break
            hostname = parsed.hostname.lower().rstrip(".")
            if any(hostname == denied or hostname.endswith("." + denied) for denied in self._denied_hosts):
                outcome = ResearchOutcome.DENIED
                break
            try:
                addresses = tuple(dict.fromkeys(self._resolve(hostname)))
            except OSError:
                outcome = ResearchOutcome.UNREACHABLE
                break
            dns_chain.append((hostname, addresses))
            if not addresses or not all(_public(address) for address in addresses):
                outcome = ResearchOutcome.DENIED
                break
            if self._transport is None:
                result = transport_fetch(
                    current,
                    addresses[0],
                    self.limits,
                    cancelled=self._cancelled.setdefault(grant.binding.generation_id, threading.Event()),
                    activate=lambda active: self._activate(grant.binding.generation_id, active),
                )
            else:
                result = self._transport(current, addresses[0], self.limits)
            elapsed += result.elapsed_ms
            outcome, status = result.outcome, result.status
            if result.redirect_url:
                redirects.append(current)
                current = result.redirect_url
                continue
            redirects.append(current)
            body = result.body
            content_type = next(
                (
                    value.split(";", 1)[0].strip().lower()
                    for key, value in result.headers.items()
                    if key.lower() == "content-type"
                ),
                "application/octet-stream",
            )
            if len(body) > self.limits.max_body_bytes:
                outcome, body = ResearchOutcome.TOO_LARGE, b""
            break
        else:
            outcome, body, status, content_type = ResearchOutcome.DENIED, b"", 0, ""
        observed_at = now
        provenance = metadata.provenance(
            dns_chain=tuple(dns_chain),
            redirect_chain=tuple(redirects),
            body_digest="sha256:" + hashlib.sha256(body).hexdigest(),
            observed_at=observed_at,
            expires_at=_expires(observed_at, self.limits.cache_seconds),
            elapsed_ms=elapsed,
        )
        result = ResearchResult(outcome, body, status, content_type, provenance, request_id)
        if not self._consume(grant.binding.generation_id, len(body), elapsed):
            result = _without_body(result, ResearchOutcome.BUDGET_EXHAUSTED)
        result = self._release_current(connection, handle, peer, result)
        if result.outcome is ResearchOutcome.ANSWERED:
            self._cache[cache_key] = result
        self._append(grant.binding, url, result)
        if self._recovery is not None and outcome in {ResearchOutcome.TIMEOUT, ResearchOutcome.UNREACHABLE}:
            from solver.recovery.safe_read import SafeReadFault, SafeReadRecovery

            now = datetime.fromisoformat(self._timestamp())
            SafeReadRecovery(self._recovery).contain(
                SafeReadFault(
                    fault_id=f"research:{request_id}:{outcome.value}",
                    scope="external:research",
                    generation_id=grant.binding.generation_id,
                    evidence=result.provenance.body_digest,
                    failed_request=request_id,
                    observed_at=now,
                )
            )
        return result

    def _activate(self, generation_id: str, connection: object | None) -> None:
        with self._lock:
            if connection is None:
                self._active_connections.pop(generation_id, None)
                return
            if self._cancelled.setdefault(generation_id, threading.Event()).is_set():
                close_now = True
            else:
                self._active_connections[generation_id] = connection
                close_now = False
        if close_now:
            connection.close()  # type: ignore[attr-defined]

    def _next_request_id(self) -> str:
        with self._lock:
            self._serial += 1
            return f"research-broker:{self._serial:06d}"

    def _release_current(
        self,
        connection: socket.socket,
        handle: str,
        peer,
        result: ResearchResult,
    ) -> ResearchResult:
        try:
            self._authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = ResearchOutcome.REVOKED if error.reason == "revoked" else ResearchOutcome.CAPABILITY_REFUSED
            return _without_body(result, outcome)
        return result

    def _reserve(self, generation_id: str) -> ResearchOutcome | None:
        now = _datetime(self._timestamp())
        with self._lock:
            used = self._requests.get(generation_id, 0)
            last = self._last_request.get(generation_id)
            if (
                used >= self.limits.max_requests
                or self._bytes.get(generation_id, 0) >= self.limits.max_total_bytes
                or self._elapsed.get(generation_id, 0) >= int(self.limits.max_total_seconds * 1000)
            ):
                return ResearchOutcome.BUDGET_EXHAUSTED
            if (
                last is not None
                and now is not None
                and (now - last).total_seconds() * 1000 < self.limits.min_interval_ms
            ):
                return ResearchOutcome.BUDGET_EXHAUSTED
            self._requests[generation_id] = used + 1
            if now is not None:
                self._last_request[generation_id] = now
        return None

    def _consume(self, generation_id: str, body_bytes: int, elapsed_ms: int) -> bool:
        with self._lock:
            total_bytes = self._bytes.get(generation_id, 0) + body_bytes
            total_elapsed = self._elapsed.get(generation_id, 0) + elapsed_ms
            if total_bytes > self.limits.max_total_bytes or total_elapsed > int(self.limits.max_total_seconds * 1000):
                return False
            self._bytes[generation_id] = total_bytes
            self._elapsed[generation_id] = total_elapsed
            return True

    def _append(self, binding: CapabilityBinding, url: str, result: ResearchResult) -> None:
        provenance = result.provenance
        self._store.append(
            ResearchBrokerRecorded(
                event_id=result.request_id,
                request_id=result.request_id,
                run_id=binding.run_id,
                boot_id=binding.boot_id,
                generation_id=binding.generation_id,
                attempt_id=binding.attempt_id,
                url_digest=hashlib.sha256(url.encode()).hexdigest(),
                outcome=result.outcome,
                content_type=result.content_type,
                dns_chain=provenance.dns_chain,
                redirect_chain=provenance.redirect_chain,
                observed_at=provenance.observed_at,
                expires_at=provenance.expires_at,
                elapsed_ms=provenance.elapsed_ms,
                cached=result.cached,
                max_body_bytes=self.limits.max_body_bytes,
                timeout_ms=int(self.limits.timeout_seconds * 1000),
                max_redirects=self.limits.max_redirects,
                cache_seconds=self.limits.cache_seconds,
                kind=provenance.kind,
                source_id=provenance.source_id,
                terms=provenance.terms,
                robots=provenance.robots,
                origin=provenance.origin,
                query_digest=provenance.query_digest,
                terms_decision=provenance.terms_decision,
                robots_decision=provenance.robots_decision,
                policy_decision=provenance.policy_decision,
                max_requests=self.limits.max_requests,
                max_total_bytes=self.limits.max_total_bytes,
                max_total_seconds_ms=int(self.limits.max_total_seconds * 1000),
                min_interval_ms=self.limits.min_interval_ms,
            ),
            body=result.body,
        )

    @staticmethod
    def _system_resolve(host: str) -> tuple[str, ...]:
        return tuple(item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))


def _public(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def _public_dns_resolve(
    host: str,
    *,
    timeout_seconds: float,
    cancelled: threading.Event,
    activate: Callable[[object | None], None],
) -> tuple[str, ...]:
    """Resolve through one fixed public recursive resolver, never the host resolver."""

    labels = host.rstrip(".").encode("idna").split(b".")
    if not labels or any(not label or len(label) > 63 for label in labels):
        raise OSError("DNS subject is invalid")
    qname = b"".join(bytes((len(label),)) + label for label in labels) + b"\0"
    if len(qname) > 255:
        raise OSError("DNS subject is invalid")
    addresses: list[str] = []
    for record_type, family in ((1, socket.AF_INET), (28, socket.AF_INET6)):
        if cancelled.is_set():
            raise OSError("Research generation was revoked")
        request_id = secrets.randbits(16)
        query = struct.pack("!HHHHHH", request_id, 0x0100, 1, 0, 0, 0) + qname + struct.pack("!HH", record_type, 1)
        opened = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        opened.settimeout(timeout_seconds)
        activate(opened)
        try:
            if cancelled.is_set():
                raise OSError("Research generation was revoked")
            opened.sendto(query, ("1.1.1.1", 53))
            response, peer = opened.recvfrom(4096)
        finally:
            activate(None)
            opened.close()
        if peer != ("1.1.1.1", 53):
            raise OSError("DNS response came from another resolver")
        addresses.extend(_dns_answers(response, request_id, record_type, family))
    return tuple(addresses)


def _dns_answers(payload: bytes, request_id: int, record_type: int, family: socket.AddressFamily) -> tuple[str, ...]:
    if len(payload) < 12:
        raise OSError("DNS response is truncated")
    response_id, flags, questions, answers, authorities, additional = struct.unpack("!HHHHHH", payload[:12])
    if response_id != request_id or flags & 0x8000 == 0 or flags & 0x000F or flags & 0x0200:
        raise OSError("DNS response is invalid")
    if questions != 1 or answers + authorities + additional > 128:
        raise OSError("DNS response is invalid")
    offset = _skip_dns_name(payload, 12)
    if offset + 4 > len(payload):
        raise OSError("DNS question is truncated")
    offset += 4
    resolved: list[str] = []
    expected_size = 4 if family == socket.AF_INET else 16
    for _ in range(answers):
        offset = _skip_dns_name(payload, offset)
        if offset + 10 > len(payload):
            raise OSError("DNS answer is truncated")
        answer_type, answer_class, _ttl, size = struct.unpack("!HHIH", payload[offset : offset + 10])
        offset += 10
        if offset + size > len(payload):
            raise OSError("DNS answer is truncated")
        if answer_type == record_type and answer_class == 1 and size == expected_size:
            resolved.append(socket.inet_ntop(family, payload[offset : offset + size]))
        offset += size
    return tuple(resolved)


def _skip_dns_name(payload: bytes, offset: int) -> int:
    labels = 0
    while offset < len(payload):
        size = payload[offset]
        if size & 0xC0 == 0xC0:
            if offset + 1 >= len(payload):
                break
            return offset + 2
        if size & 0xC0 or size > 63:
            break
        offset += 1
        if size == 0:
            return offset
        offset += size
        labels += 1
        if labels > 127 or offset > len(payload):
            break
    raise OSError("DNS name is invalid")


def _expires(observed_at: str, seconds: int) -> str:
    try:
        return (datetime.fromisoformat(observed_at) + timedelta(seconds=seconds)).isoformat()
    except ValueError:
        return observed_at


def _before(left: str, right: str) -> bool:
    try:
        return datetime.fromisoformat(left) < datetime.fromisoformat(right)
    except ValueError:
        return False


def _without_body(result: ResearchResult, outcome: ResearchOutcome) -> ResearchResult:
    provenance = replace(
        result.provenance,
        body_digest="sha256:" + hashlib.sha256(b"").hexdigest(),
    )
    return replace(
        result,
        outcome=outcome,
        body=b"",
        status=0,
        content_type="",
        provenance=provenance,
    )


def _datetime(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _query(supplied: ResearchQuery | dict[str, object]) -> ResearchQuery | None:
    if isinstance(supplied, ResearchQuery):
        query = supplied
    elif isinstance(supplied, dict) and set(supplied) == {"kind", "source_id", "subject", "body", "content_type"}:
        try:
            body = base64.b64decode(str(supplied["body"]), validate=True)
            query = ResearchQuery(
                ResearchKind(str(supplied["kind"])),
                str(supplied["source_id"]),
                str(supplied["subject"]),
                body,
                str(supplied["content_type"]),
            )
        except (ValueError, binascii.Error):
            return None
    else:
        return None
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,63}", query.source_id)
        or not query.subject
        or len(query.subject.encode()) > 1024
        or any(character in query.subject for character in "\r\n\0")
        or bool(query.body) != bool(query.content_type)
    ):
        return None
    return query


__all__ = ["ResearchBrokerRuntime"]
