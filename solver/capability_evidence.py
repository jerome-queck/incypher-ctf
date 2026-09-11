"""Sanitized evidence recording at real custody-operation seams."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from solver.capability_event_contracts import EnvironmentCutover, ExecutorProbe, SocketProbe
from solver.event_store import CAPABILITY_CUSTODY_RECORDED, CapabilityCustodyRecorded, EventStore
from solver.event_store_storage import canonical_bytes
from solver.env_cutover import EnvCutoverResult
from solver.executor_secret_probe import ExecutorProbeResult
from solver.redaction import Redactor

PROBE_KINDS = ("memory", "environment", "argv", "file", "event")


class CapabilityEvidence:
    """Append sanitized results produced by completed operations to canonical state."""

    def __init__(self, state: Path, run_id: str, boot_id: str, redactor: Redactor | None, timestamp) -> None:
        self._run_id = run_id
        self._boot_id = boot_id
        self._redactor = redactor or Redactor({})
        self._timestamp = timestamp
        self._store = EventStore(state, run_id=run_id, redactor=self._redactor)

    def record_env_cutover(self, result: object) -> None:
        """Record only the typed result returned by the atomic replacement operation."""

        if not isinstance(result, EnvCutoverResult):
            raise TypeError("env cutover evidence must come from atomic_replace")
        evidence = result.evidence()
        self._store.append(
            CapabilityCustodyRecorded(
                event_id=self._event_id(),
                fact=EnvironmentCutover(
                    run_id=self._run_id,
                    boot_id=self._boot_id,
                    evidence_digest=_sanitized_evidence_digest(evidence, self._redactor),
                ),
                ts=self._timestamp(),
            ),
            body=b"",
        )

    def record_executor_probe(self, result: ExecutorProbeResult) -> None:
        checks = dict(result.checks)
        if set(checks) != set(PROBE_KINDS):
            raise ValueError("executor probe must supply every fixed surface exactly once")
        for kind in PROBE_KINDS:
            probe_result = "clear" if checks[kind] else "found"
            if kind == "memory" and not result.memory_complete:
                probe_result = "incomplete"
            self._store.append(
                CapabilityCustodyRecorded(
                    event_id=self._event_id(),
                    fact=ExecutorProbe(
                        run_id=self._run_id,
                        boot_id=self._boot_id,
                        probe_kind=kind,
                        probe_result=probe_result,
                        evidence_digest=result.evidence_digest,
                    ),
                    ts=self._timestamp(),
                ),
                body=b"",
            )

    def record_socket_probe(self, response: bytes, fixtures: tuple[bytes, ...]) -> bool:
        """Bind one actual denied broker response to the live bootstrap fixtures."""

        import json

        try:
            document = json.loads(response)
            outcome = document["result"]["outcome"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("socket probe did not return a typed Board refusal") from error
        leaked = any(fixture in response or fixture.hex().encode() in response for fixture in fixtures)
        refused = outcome == "capability-refused"
        basis = {
            "response_digest": hashlib.sha256(response).hexdigest(),
            "fixture_digests": sorted(hashlib.sha256(fixture).hexdigest() for fixture in fixtures),
        }
        self._store.append(
            CapabilityCustodyRecorded(
                event_id=self._event_id(),
                fact=SocketProbe(
                    run_id=self._run_id,
                    boot_id=self._boot_id,
                    evidence_digest=hashlib.sha256(canonical_bytes(basis)).hexdigest(),
                    probe_result="refused" if refused and not leaked else "found",
                ),
                ts=self._timestamp(),
            ),
            body=b"",
        )
        return refused and not leaked

    def _event_id(self) -> str:
        events = self._store.events()
        capability_events = [event for event in events if event.event_type == CAPABILITY_CUSTODY_RECORDED]
        serial = len(capability_events) + 1
        existing = {
            event.payload.get("event_id") for event in events if event.event_type == CAPABILITY_CUSTODY_RECORDED
        }
        while (candidate := f"capability:{self._boot_id}:{serial:06d}") in existing:
            serial += 1
        return candidate


def _sanitized_evidence_digest(evidence: object, redactor: Redactor) -> str:
    try:
        return hashlib.sha256(canonical_bytes(_sanitize(evidence, redactor))).hexdigest()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("env cutover evidence must be JSON-serializable") from None


def _sanitize(value: Any, redactor: Redactor) -> Any:
    if isinstance(value, (str, bytes)):
        return redactor.redact(value).decode("utf-8", "replace")
    if isinstance(value, dict):
        return {_sanitize(key, redactor): _sanitize(item, redactor) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, redactor) for item in value]
    return value


__all__ = ["CapabilityEvidence", "PROBE_KINDS"]
