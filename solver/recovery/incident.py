"""Durable containment of one classified fault without inference authority."""

from __future__ import annotations

import fcntl
import json
import os
import datetime as dt
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.event_store import EventStore
from solver.recovery.contracts import (
    FAULT_REASONS,
    FaultKind,
    IncidentDisposition,
    IncidentStep,
    ProbeObservation,
    ProbationOutcome,
    RecoveryContext,
)
from solver.recovery.catalogue import CATALOGUE_VERSION
from solver.recovery.receipt import verify_receipt as verify_receipt
from solver.redaction import Redactor
from solver.write_reservation import WriteAuthority
from solver.write_reservation_contracts import (
    Capacity,
    DEFAULT_WRITE_PROFILE,
    EffectIdentity,
    ReservationState,
    RetentionPolicy,
    profile_from,
)

SCHEMA_VERSION = 1
RECEIPT = "incident-containment.receipt.json"


class Remedy(str, Enum):
    REPLACE_ONCE = "replace-once"


class ModelRemedyRejected(ValueError):
    """Core Recovery cannot accept a model-selected remedy."""


@dataclass(frozen=True)
class Fault:
    fault_id: str
    scope: str
    generation_id: str
    evidence: str
    kind: FaultKind = FaultKind.WORKER_CRASH
    recovery: RecoveryContext | None = None

    @property
    def identity(self) -> str:
        return digest_bytes(canonical_bytes({"fault_id": self.fault_id, "scope": self.scope}))

    @property
    def fingerprint(self) -> str:
        return digest_bytes(
            canonical_bytes(
                {
                    "scope": self.scope,
                    "kind": self.kind.value,
                    "adapter": self.recovery.adapter_id if self.recovery else "legacy",
                    "failed_action": self.recovery.failed_action_value if self.recovery else "",
                }
            )
        )


class ContainmentPorts(Protocol):
    def fence(self, fault: Fault) -> None: ...
    def evidence(self, fault: Fault) -> bytes: ...
    def teardown(self, fault: Fault) -> None: ...
    def replace(self, fault: Fault) -> bool: ...
    def probe(self, fault: Fault, probe_id: str) -> ProbeObservation: ...
    def apply_remedy(self, fault: Fault, remedy_id: str, changed_action) -> bool: ...
    def probation(self, fault: Fault, remedy_id: str) -> ProbationOutcome: ...


@dataclass(frozen=True)
class IncidentResult:
    incident_id: str
    disposition: str
    receipt_path: Path


class IncidentEngine:
    """One writer for scope containment and its bounded fixed remedy."""

    remedy = Remedy.REPLACE_ONCE

    def __init__(
        self,
        state: Path,
        run_id: str,
        ports: ContainmentPorts,
        redactor: Redactor,
        *,
        now=None,
        authority: WriteAuthority | None = None,
    ) -> None:
        self._root = Path(state) / "runs" / run_id / "canonical"
        self._run_id = run_id
        self._ports = ports
        self._redactor = redactor
        self._store = EventStore(state, run_id=run_id, redactor=redactor)
        self._receipt_path = self._root / RECEIPT
        self._lock_path = self._root / "incident.lock"
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._authority = authority

    def accept_model_remedy(self, _claim: str) -> None:
        raise ModelRemedyRejected("model output has no Core Recovery authority")

    def report(self, fault: Fault) -> IncidentResult:
        with self._serialized():
            document = self._read()
            match = next((row for row in document["incidents"] if row["fault_identity"] == fault.identity), None)
            if match is not None:
                match["duplicate_reports"] += 1
                self._write(document)
                self._write_receipt(match)
                return self._result(match)
            active = next(
                (
                    row
                    for row in document["incidents"]
                    if row.get("fingerprint") == fault.fingerprint and not row["terminal"]
                ),
                None,
            )
            if active is not None:
                active["duplicate_reports"] += 1
                self._write(document)
                self._write_receipt(active)
                return self._result(active)
            authority = self._authority or self._open_authority()
            try:
                row, reservation = self._open(document, fault, authority)
                return self._advance(document, row, fault, authority, reservation)
            finally:
                if self._authority is None:
                    authority.close()

    def replay(self, *, adapter_ids: frozenset[str] | None = None) -> tuple[IncidentResult, ...]:
        with self._serialized():
            document = self._read()
            results = []
            for row in document["incidents"]:
                if row["terminal"]:
                    continue
                if adapter_ids is not None and row.get("adapter_id") not in adapter_ids:
                    continue
                row["replay_count"] += 1
                recovery = None
                if row.get("catalogue_version"):
                    recovery = RecoveryContext(
                        row["original_deadline"],
                        row["allowance"],
                        row["failed_action_value"],
                        row["adapter_id"],
                        row["adapter_config"],
                    )
                fault = Fault(
                    row["fault"]["fault_id"],
                    row["scope"],
                    row["fault"]["generation_id"],
                    "replay",
                    FaultKind(row["kind"]),
                    recovery,
                )
                self._write(document)
                authority = self._authority or self._open_authority()
                try:
                    reservation = authority.current(self._reservation_key(row["fault_identity"]))
                    if reservation is None:
                        raise RuntimeError("Incident lost its replacement reservation")
                    if reservation.state is ReservationState.POSSIBLY_SENT:
                        authority.refuse_indeterminate(reservation, "replacement-launch-indeterminate")
                        row["authority_state"] = ReservationState.TERMINAL.value
                        row["replacement_admitted"] = False
                        if row.get("catalogue_version"):
                            row["disposition"] = IncidentDisposition.CONTAINED.value
                            row["probation_outcome"] = ProbationOutcome.UNSETTLED.value
                            row["final_outcome"] = IncidentDisposition.CONTAINED.value
                        else:
                            row["disposition"] = IncidentDisposition.REPLACEMENT_REFUSED.value
                        row["terminal"] = True
                        self._write(document)
                        self._write_receipt(row)
                        results.append(self._result(row))
                        continue
                    if reservation.state is ReservationState.ABORTED:
                        reservation = self._reserve(authority, row["fault_identity"], retry_aborted=True)
                    results.append(self._advance(document, row, fault, authority, reservation))
                finally:
                    if self._authority is None:
                        authority.close()
            return tuple(results)

    def _advance(self, document, row, fault, authority, reservation):
        if fault.recovery is not None:
            return self._recover(document, row, fault, authority, reservation)
        if fault.kind is not FaultKind.WORKER_CRASH:
            return self._refuse(document, row, fault, authority, reservation)
        return self._contain(document, row, fault, authority, reservation)

    def _open_authority(self):
        directory = self._root.parent / "write-authority"
        profile = (
            profile_from(json.loads((directory / "profile.json").read_bytes()))
            if directory.exists()
            else DEFAULT_WRITE_PROFILE
        )
        return WriteAuthority(self._root.parent, profile, redactor=self._redactor)

    def _open(self, document, fault, authority):
        reservation = self._reserve(authority, fault.identity)
        row = {
            "incident_id": f"incident-{len(document['incidents']) + 1:06d}",
            "fault_identity": fault.identity,
            "fingerprint": fault.fingerprint,
            "successor_of": next(
                (
                    previous["incident_id"]
                    for previous in reversed(document["incidents"])
                    if previous["scope"] == fault.scope
                ),
                "",
            ),
            "fault": {"fault_id": fault.fault_id, "generation_id": fault.generation_id},
            "scope": fault.scope,
            "kind": fault.kind.value,
            "reason": FAULT_REASONS[fault.kind],
            "steps": {},
            "trace": [],
            "duplicate_reports": 0,
            "replay_count": 0,
            "terminal": False,
            "disposition": IncidentDisposition.CONTAINING.value,
            "authority_state": ReservationState.RESERVED.value,
            "catalogue_version": CATALOGUE_VERSION if fault.recovery else "",
            "probe_id": "",
            "probe_outcome": "",
            "remedy_id": "",
            "remedy_version": "",
            "changed_action": {},
            "original_deadline": fault.recovery.original_deadline if fault.recovery else "",
            "allowance": fault.recovery.allowance if fault.recovery else 0,
            "consumed_allowance": 0,
            "probation_outcome": "",
            "final_outcome": "",
            "failed_action_value": fault.recovery.failed_action_value if fault.recovery else "",
            "adapter_id": fault.recovery.adapter_id if fault.recovery else "",
            "adapter_config": fault.recovery.adapter_config if fault.recovery else "{}",
        }
        document["incidents"].append(row)
        self._write(document)
        return row, reservation

    def _recover(self, document, row, fault, authority, reservation):
        from solver.recovery.deterministic_lifecycle import recover

        return recover(_LifecyclePort(self), document, row, fault, authority, reservation)

    def _contain(self, document, row, fault, authority, reservation):
        self._step(document, row, IncidentStep.GENERATION_FENCE, lambda: self._ports.fence(fault))
        self._step(document, row, IncidentStep.EVIDENCE_CAPTURE, lambda: self._capture(row, fault))
        self._step(document, row, IncidentStep.FULL_TEARDOWN, lambda: self._ports.teardown(fault))
        replacement = IncidentStep.BOUNDED_REPLACEMENT
        self._step(document, row, replacement, lambda: self._replace(row, fault, authority, reservation))
        row["disposition"] = (
            IncidentDisposition.REPLACEMENT_ADMITTED.value
            if row.get("replacement_admitted", False)
            else IncidentDisposition.REPLACEMENT_REFUSED.value
        )
        row["terminal"] = True
        self._write(document)
        self._write_receipt(row)
        return self._result(row)

    def _refuse(self, document, row, fault, authority, reservation):
        body = self._redactor.redact(fault.evidence.encode())[:4096]
        row["evidence"] = {
            "bytes": len(body),
            "digest": digest_bytes(body),
            "projection": body.decode("utf-8", "replace"),
        }
        closed = authority.abort(reservation, row["reason"])
        row["authority_state"] = closed.state.value
        row["disposition"] = IncidentDisposition.REPLACEMENT_REFUSED.value
        row["terminal"] = True
        self._write(document)
        self._write_receipt(row)
        return self._result(row)

    def _step(self, document, row, step: IncidentStep, action):
        name = step.value
        state = row["steps"].get(name)
        if state == "complete":
            return
        row["steps"][name] = "reserved"
        if name not in row["trace"]:
            row["trace"].append(name)
        self._write(document)
        action()
        row["steps"][name] = "complete"
        self._write(document)

    def _step_result(self, document, row, step: IncidentStep, action):
        name = step.value
        row["steps"][name] = "reserved"
        if name not in row["trace"]:
            row["trace"].append(name)
        self._write(document)
        result = action()
        row["steps"][name] = "complete"
        self._write(document)
        return result

    def _capture(self, row, fault):
        body = self._redactor.redact(self._ports.evidence(fault))[:4096]
        row["evidence"] = {
            "bytes": len(body),
            "digest": digest_bytes(body),
            "projection": body.decode("utf-8", "replace"),
        }

    def _replace(self, row, fault, authority, reservation):
        started = authority.start(reservation)
        row["authority_state"] = started.state.value
        row["replacement_admitted"] = self._ports.replace(fault)
        if row["replacement_admitted"]:
            closed = authority.commit(started, {"outcome": "replacement-launched"})
        else:
            closed = authority.refuse_indeterminate(
                authority.possibly_sent(started, "replacement-refused-after-admission"),
                "replacement-refused-after-admission",
            )
        row["authority_state"] = closed.state.value

    def _reserve(self, authority, fault_identity, *, retry_aborted=False):
        return authority.reserve(
            self._reservation_key(fault_identity),
            EffectIdentity("replace-worker-once", fault_identity),
            Capacity(4096, 1, 8),
            retention=RetentionPolicy.RECORD,
            retry_aborted=retry_aborted,
        )

    @staticmethod
    def _reservation_key(fault_identity):
        return f"incident:{fault_identity}:replacement"

    def _read(self):
        from solver.recovery.projection import read

        return read(self._store, self._run_id, SCHEMA_VERSION)

    def _write(self, document):
        from solver.recovery.projection import write

        write(self._store, document)

    def _write_receipt(self, row):
        from solver.recovery.receipt import write_receipt

        write_receipt(self._receipt_path, self._run_id, row)

    def _result(self, row):
        return IncidentResult(row["incident_id"], row["disposition"], self._receipt_path)

    def _serialized(self):
        """Serialize projections and reservations across engines and processes."""

        class Lock:
            def __enter__(inner):
                self._root.mkdir(parents=True, exist_ok=True)
                descriptor = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o600)
                inner.file = os.fdopen(descriptor, "a+b")
                fcntl.flock(inner.file.fileno(), fcntl.LOCK_EX)

            def __exit__(inner, *_):
                fcntl.flock(inner.file.fileno(), fcntl.LOCK_UN)
                inner.file.close()

        return Lock()


class _LifecyclePort:
    """Typed adapter between Incident storage and deterministic lifecycle policy."""

    def __init__(self, engine: IncidentEngine) -> None:
        self._engine = engine

    def redact_evidence(self, evidence):
        return self._engine._redactor.redact(evidence)

    def write(self, document):
        self._engine._write(document)

    def step(self, document, row, step, action):
        self._engine._step(document, row, step, action)

    def step_result(self, document, row, step, action):
        return self._engine._step_result(document, row, step, action)

    def fence(self, fault):
        self._engine._ports.fence(fault)

    def capture(self, row, fault):
        self._engine._capture(row, fault)

    def teardown(self, fault):
        self._engine._ports.teardown(fault)

    def probe(self, fault, probe_id):
        return self._engine._ports.probe(fault, probe_id)

    def apply_remedy(self, fault, remedy_id, action):
        return self._engine._ports.apply_remedy(fault, remedy_id, action)

    def probation(self, fault, remedy_id):
        return self._engine._ports.probation(fault, remedy_id)

    def now(self):
        return self._engine._now()

    def finish(self, document, row):
        self._engine._write(document)
        self._engine._write_receipt(row)
        return self._engine._result(row)
