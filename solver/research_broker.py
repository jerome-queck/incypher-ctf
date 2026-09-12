"""Bounded, generation-scoped access to public Internet Research."""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import threading
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from solver.capability import CapabilityAuthority, CapabilityBinding, CapabilityRefused, local_peer_identity
from solver.event_store import EventStore
from solver.event_store_contracts import ATTEMPT_ENVELOPE_RECORDED
from solver.redaction import Redactor
from solver.research_broker_contracts import (
    ResearchBrokerRecorded,
    ResearchLimits,
    ResearchOutcome,
    ResearchProvenance,
    ResearchResult,
    ResearchTransportResult,
)
from solver.research_broker_transport import fetch as transport_fetch

RESEARCH_SCOPE = "research.fetch"


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
        transport: Callable[[str, str, ResearchLimits], ResearchTransportResult] | None = None,
        denied_hosts: tuple[str, ...] = (),
    ) -> None:
        self.run_id = run_id
        self.boot_id = boot_id
        self.limits = limits
        self._timestamp = timestamp
        self._resolve = resolve or self._system_resolve
        self._transport = transport or transport_fetch
        self._denied_hosts = tuple(host.lower().rstrip(".") for host in denied_hosts)
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
        self._serial = 0
        self._lock = threading.Lock()
        self._cache: dict[str, ResearchResult] = {}

    def prepare_attempt(self, binding: CapabilityBinding) -> None:
        if binding.run_id != self.run_id or binding.boot_id != self.boot_id:
            raise ValueError("Research Attempt binding belongs to another Run or Boot")
        self._expected[binding.generation_id] = binding

    def claim(self, connection: socket.socket, generation_id: str) -> str:
        binding = self._expected.pop(generation_id, None)
        if binding is None:
            raise CapabilityRefused()
        handle = self._authority.issue(binding, RESEARCH_SCOPE, self._authority.peer_identity(connection))
        self._handles.setdefault(generation_id, set()).add(handle)
        return handle

    def revoke_generation(self, generation_id: str) -> None:
        self._expected.pop(generation_id, None)
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

    def fetch(self, connection: socket.socket, handle: str, url: str) -> ResearchResult:
        try:
            peer = self._authority.peer_identity(connection)
            grant = self._authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as error:
            outcome = ResearchOutcome.REVOKED if error.reason == "revoked" else ResearchOutcome.CAPABILITY_REFUSED
            return ResearchResult(outcome)
        if grant.scope != RESEARCH_SCOPE:
            return ResearchResult(ResearchOutcome.CAPABILITY_REFUSED)
        with self._lock:
            self._serial += 1
            request_id = f"research-broker:{self._serial:06d}"
        now = self._timestamp()
        cached = self._cache.get(url)
        if cached is not None and _before(now, cached.provenance.expires_at):
            result = replace(cached, request_id=request_id, cached=True)
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
        provenance = ResearchProvenance(
            tuple(dns_chain),
            tuple(redirects),
            "sha256:" + hashlib.sha256(body).hexdigest(),
            observed_at,
            _expires(observed_at, self.limits.cache_seconds),
            elapsed,
        )
        result = ResearchResult(outcome, body, status, content_type, provenance, request_id)
        if outcome is ResearchOutcome.ANSWERED:
            self._cache[url] = result
        self._append(grant.binding, url, result)
        return result

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


__all__ = ["ResearchBrokerRuntime"]
