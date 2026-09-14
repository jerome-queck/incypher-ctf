"""Bounded, generation-scoped access to one declared Target."""

from __future__ import annotations

import base64
import binascii
import hashlib
import socket
import threading
import datetime as dt
from dataclasses import replace
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import Protocol

from solver.capability import CapabilityAuthority, CapabilityBinding, CapabilityRefused, local_peer_identity
from solver.event_store_storage import canonical_bytes
from solver.event_store import EventStore
from solver.event_store_contracts import ATTEMPT_ENVELOPE_RECORDED
from solver.redaction import Redactor
from solver.target_broker_contracts import (
    TARGET_EXCHANGE_RECORDED,
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
    TcpTargetExchangeRequest,
)
from solver.target_broker_transport import exchange as transport_exchange
from solver.target_broker_transport import request_size
from solver.work_generation import GenerationFence

TARGET_SCOPE = "target.exchange"


class GenerationRevocationSink(Protocol):
    def add_generation_revocation(self, callback: Callable[[str], None]) -> None: ...


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
        return handle

    def bind_attempt_executor(self, executor: GenerationRevocationSink) -> None:
        """Remove Target authority at the executor's generation fence."""

        executor.add_generation_revocation(self.revoke_generation)

    def revoke_generation(self, generation_id: str) -> None:
        self._expected.pop(generation_id, None)
        for handle in tuple(self._generation_handles.pop(generation_id, ())):
            self.authority.revoke(handle, "generation-closing")

    def revoke(self, handle: str) -> None:
        self.authority.revoke(handle, "target-client-closed")

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


__all__ = ["TargetBrokerRuntime"]
