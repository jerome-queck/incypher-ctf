"""Publish current Lease-derived Target authority through the opaque Target broker."""

from __future__ import annotations

import shlex
import socket
import threading
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from solver.capability import CapabilityAuthority, CapabilityBinding, CapabilityRefused, local_peer_identity
from solver.lease_target import LeaseTargetGrant
from solver.redaction import Redactor
from solver.target_broker import TARGET_SCOPE, TargetBrokerRuntime
from solver.target_broker_contracts import (
    TargetCandidateBinding,
    TargetEndpoint,
    TargetLimits,
    TargetOutcome,
    TargetProtocol,
    TargetResult,
)

DEFAULT_TARGET_LIMITS = TargetLimits(
    32,
    1024 * 1024,
    8 * 1024 * 1024,
    20,
    max_exchanges=128,
    max_total_request_bytes=16 * 1024 * 1024,
    max_total_response_bytes=64 * 1024 * 1024,
    max_redirects=8,
    max_total_seconds=300,
)


@dataclass(frozen=True)
class _PublishedTarget:
    runtime: TargetBrokerRuntime
    grant: LeaseTargetGrant


def target_endpoint(connection: str) -> TargetEndpoint:
    """Parse only the explicit TCP and HTTP forms chall-manager publishes."""

    value = connection.strip()
    try:
        words = shlex.split(value)
    except ValueError as error:
        raise ValueError("Target connection is malformed") from error
    if len(words) == 3 and words[0] in {"nc", "ncat"}:
        return _endpoint(TargetProtocol.TCP, words[1], words[2])
    if "://" in value:
        parsed = urllib.parse.urlsplit(value)
        protocols = {"http": TargetProtocol.HTTP, "https": TargetProtocol.HTTPS}
        if (
            parsed.scheme not in protocols
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
        ):
            raise ValueError("Target connection is unsupported or ambiguous")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as error:
            raise ValueError("Target connection is malformed") from error
        return _endpoint(protocols[parsed.scheme], parsed.hostname, port)
    parsed = urllib.parse.urlsplit(f"//{value}")
    if not parsed.hostname or parsed.path or parsed.username is not None or parsed.password is not None:
        raise ValueError("Target connection is unsupported or ambiguous")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Target connection is malformed") from error
    if port is None:
        raise ValueError("Target connection is unsupported or ambiguous")
    return _endpoint(TargetProtocol.TCP, parsed.hostname, port)


def _endpoint(protocol: TargetProtocol, host: str, port: int | str) -> TargetEndpoint:
    try:
        return TargetEndpoint(protocol, host, int(port))
    except (TypeError, ValueError) as error:
        raise ValueError("Target connection is malformed") from error


class LeaseTargetBroker:
    """Route each current Work generation to its one Lease-derived Target runtime."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        boot_id: str,
        candidate: TargetCandidateBinding,
        target_authority,
        timestamp: Callable[[], str],
        limits: TargetLimits = DEFAULT_TARGET_LIMITS,
        runtime_factory=TargetBrokerRuntime,
        browser_launcher=None,
        recovery=None,
    ) -> None:
        if not run_id or not boot_id:
            raise ValueError("Lease Target broker identity is incomplete")
        self.state = Path(state)
        self.run_id = run_id
        self.boot_id = boot_id
        self.candidate = candidate
        self._target_authority = target_authority
        self._timestamp = timestamp
        self._limits = limits
        self._runtime_factory = runtime_factory
        self._browser_launcher = browser_launcher
        self._recovery = recovery
        self._capability = CapabilityAuthority(
            state=self.state,
            run_id=run_id,
            boot_id=boot_id,
            redactor=Redactor({}),
            peer_identity=local_peer_identity,
            timestamp=timestamp,
        )
        self._capability.reconcile_restart(TARGET_SCOPE)
        self._generations: dict[str, _PublishedTarget] = {}
        self._handles: dict[str, _PublishedTarget] = {}
        self._lock = threading.RLock()

    def register(self, grant) -> None:
        """Publish one exact current Lease grant without exposing its connection string."""

        if not self._target_authority.reauthorize(grant):
            raise ValueError("Target connection has no current Lease authority")
        endpoint = target_endpoint(grant.exact_connection)
        with self._lock:
            current = self._generations.get(grant.generation_id)
            if current is not None and current.grant.connection_digest == grant.connection_digest:
                self._generations[grant.generation_id] = _PublishedTarget(current.runtime, grant)
                return
            if current is not None:
                self.revoke_generation(grant.generation_id)
            runtime = self._runtime_factory(
                state=self.state,
                run_id=self.run_id,
                boot_id=self.boot_id,
                challenge_id=grant.work_id,
                candidate=self.candidate,
                endpoint=endpoint,
                limits=self._limits,
                timestamp=self._timestamp,
                request_namespace=grant.generation_id,
                recovery=self._recovery,
                capability_authority=self._capability,
                reconcile_authority=False,
                browser_launcher=self._browser_launcher,
            )
            self._generations[grant.generation_id] = _PublishedTarget(runtime, grant)

    def available(self, generation_id: str) -> bool:
        with self._lock:
            current = self._generations.get(generation_id)
            if current is None:
                return False
            if self._target_authority.reauthorize(current.grant):
                return True
            self.revoke_generation(generation_id)
            return False

    def prepare_attempt(self, binding: CapabilityBinding) -> None:
        current = self._current(binding.generation_id)
        current.runtime.prepare_attempt(binding)

    def claim(self, connection: socket.socket, generation_id: str) -> str:
        current = self._current(generation_id)
        handle = current.runtime.claim(connection, generation_id)
        with self._lock:
            self._handles[handle] = current
        return handle

    def exchange(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        return self._operation("exchange", connection, handle, request)

    def http_session(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        return self._operation("http_session", connection, handle, request)

    def http_fuzz(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        return self._operation("http_fuzz", connection, handle, request)

    def tcp_session(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        return self._operation("tcp_session", connection, handle, request)

    def browser(self, connection: socket.socket, handle: str, request: Mapping[str, object]) -> TargetResult:
        return self._operation("browser", connection, handle, request)

    def _operation(
        self,
        operation: str,
        connection: socket.socket,
        handle: str,
        request: Mapping[str, object],
    ) -> TargetResult:
        with self._lock:
            current = self._handles.get(handle)
            if current is None:
                return TargetResult(TargetOutcome.CAPABILITY_REFUSED)
            if not self._target_authority.reauthorize(current.grant):
                self.revoke_generation(current.grant.generation_id)
                return TargetResult(TargetOutcome.REVOKED)
        return getattr(current.runtime, operation)(connection, handle, request)

    def revoke(self, handle: str) -> None:
        with self._lock:
            current = self._handles.pop(handle, None)
        if current is not None:
            current.runtime.revoke(handle)

    def revoke_generation(self, generation_id: str) -> None:
        with self._lock:
            current = self._generations.pop(generation_id, None)
            handles = [
                handle
                for handle, owner in self._handles.items()
                if current is not None and owner.runtime is current.runtime
            ]
            for handle in handles:
                self._handles.pop(handle, None)
        if current is not None:
            current.runtime.revoke_generation(generation_id)

    def bind_attempt_executor(self, executor) -> None:
        executor.add_generation_revocation(self.revoke_generation)

    def _current(self, generation_id: str):
        with self._lock:
            current = self._generations.get(generation_id)
            if current is None or not self._target_authority.reauthorize(current.grant):
                if current is not None:
                    self.revoke_generation(generation_id)
                raise CapabilityRefused("revoked")
            return current


__all__ = ["LeaseTargetBroker", "target_endpoint"]
