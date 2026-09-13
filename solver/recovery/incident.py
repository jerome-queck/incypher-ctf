"""Durable containment of one classified fault without inference authority."""

from __future__ import annotations

import json
import fcntl
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.event_store import EventStore
from solver.recovery.contracts import (
    FAULT_REASONS,
    INCIDENT_RECORDED,
    FaultKind,
    IncidentDisposition,
    IncidentRecorded,
    IncidentStep,
)
from solver.redaction import Redactor
from solver.write_reservation import WriteAuthority
from solver.write_reservation_contracts import (
    Capacity,
    DEFAULT_WRITE_PROFILE,
    EffectIdentity,
    ReservationState,
    RetentionPolicy,
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

    @property
    def identity(self) -> str:
        return digest_bytes(canonical_bytes({"fault_id": self.fault_id, "scope": self.scope}))


class ContainmentPorts(Protocol):
    def fence(self, fault: Fault) -> None: ...
    def evidence(self, fault: Fault) -> bytes: ...
    def teardown(self, fault: Fault) -> None: ...
    def replace(self, fault: Fault) -> bool: ...


@dataclass(frozen=True)
class IncidentResult:
    incident_id: str
    disposition: str
    receipt_path: Path


class IncidentEngine:
    """One writer for scope containment and its bounded fixed remedy."""

    remedy = Remedy.REPLACE_ONCE

    def __init__(self, state: Path, run_id: str, ports: ContainmentPorts, redactor: Redactor) -> None:
        self._root = Path(state) / "runs" / run_id / "canonical"
        self._run_id = run_id
        self._ports = ports
        self._redactor = redactor
        self._store = EventStore(state, run_id=run_id, redactor=redactor)
        self._receipt_path = self._root / RECEIPT
        self._lock_path = self._root / "incident.lock"

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
                (row for row in document["incidents"] if row["scope"] == fault.scope and not row["terminal"]),
                None,
            )
            if active is not None:
                active["duplicate_reports"] += 1
                self._write(document)
                self._write_receipt(active)
                return self._result(active)
            authority = WriteAuthority(self._root.parent, DEFAULT_WRITE_PROFILE, redactor=self._redactor)
            try:
                row, reservation = self._open(document, fault, authority)
                if fault.kind is not FaultKind.WORKER_CRASH:
                    return self._refuse(document, row, fault, authority, reservation)
                return self._contain(document, row, fault, authority, reservation)
            finally:
                authority.close()

    def replay(self) -> tuple[IncidentResult, ...]:
        with self._serialized():
            document = self._read()
            results = []
            for row in document["incidents"]:
                if row["terminal"]:
                    continue
                row["replay_count"] += 1
                fault = Fault(row["fault"]["fault_id"], row["scope"], row["fault"]["generation_id"], "replay")
                self._write(document)
                authority = WriteAuthority(self._root.parent, DEFAULT_WRITE_PROFILE, redactor=self._redactor)
                try:
                    reservation = authority.current(self._reservation_key(row["fault_identity"]))
                    if reservation is None:
                        raise RuntimeError("Incident lost its replacement reservation")
                    if reservation.state is ReservationState.POSSIBLY_SENT:
                        authority.refuse_indeterminate(reservation, "replacement-launch-indeterminate")
                        row["authority_state"] = ReservationState.TERMINAL.value
                        row["replacement_admitted"] = False
                        row["disposition"] = IncidentDisposition.REPLACEMENT_REFUSED.value
                        row["terminal"] = True
                        self._write(document)
                        self._write_receipt(row)
                        results.append(self._result(row))
                        continue
                    if reservation.state is ReservationState.ABORTED:
                        reservation = self._reserve(authority, row["fault_identity"], retry_aborted=True)
                    results.append(self._contain(document, row, fault, authority, reservation))
                finally:
                    authority.close()
            return tuple(results)

    def _open(self, document, fault, authority):
        reservation = self._reserve(authority, fault.identity)
        row = {
            "incident_id": f"incident-{len(document['incidents']) + 1:06d}",
            "fault_identity": fault.identity,
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
        }
        document["incidents"].append(row)
        self._write(document)
        return row, reservation

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
        incidents = {}
        for event in self._store.events():
            if event.event_type != INCIDENT_RECORDED:
                continue
            value = event.payload
            incidents[value["incident_id"]] = {
                "incident_id": value["incident_id"],
                "fault_identity": value["fault_identity"],
                "fault": {"fault_id": value["fault_id"], "generation_id": value["generation_id"]},
                "scope": value["scope"],
                "kind": value["fault_kind"],
                "reason": value["reason"],
                "authority_state": value["authority_state"],
                "steps": {
                    name: "complete" if name in value["completed_steps"] else "reserved" for name in value["steps"]
                },
                "trace": value["steps"],
                "duplicate_reports": value["duplicate_reports"],
                "replay_count": value["replay_count"],
                "terminal": value["terminal"],
                "disposition": value["disposition"],
                "replacement_admitted": value["replacement_admitted"],
                "evidence": {
                    "digest": value["evidence_digest"],
                    "projection": value["evidence_projection"],
                    "bytes": len(value["evidence_projection"].encode()),
                },
            }
        return {"schema_version": SCHEMA_VERSION, "run_id": self._run_id, "incidents": list(incidents.values())}

    def _write(self, document):
        revision = sum(1 for event in self._store.events() if event.event_type == INCIDENT_RECORDED)
        for offset, row in enumerate(document["incidents"], 1):
            evidence = row.get("evidence", {})
            self._store.append(
                IncidentRecorded(
                    event_id=f"{row['incident_id']}:revision-{revision + offset:06d}",
                    incident_id=row["incident_id"],
                    fault_identity=row["fault_identity"],
                    fault_id=row["fault"]["fault_id"],
                    generation_id=row["fault"]["generation_id"],
                    scope=row["scope"],
                    fault_kind=row["kind"],
                    reason=row["reason"],
                    authority_state=row.get("authority_state", ""),
                    disposition=row["disposition"],
                    steps=tuple(row["trace"]),
                    completed_steps=tuple(name for name, state in row["steps"].items() if state == "complete"),
                    duplicate_reports=row["duplicate_reports"],
                    replay_count=row["replay_count"],
                    terminal=row["terminal"],
                    evidence_digest=evidence.get("digest", ""),
                    evidence_projection=evidence.get("projection", ""),
                    replacement_admitted=row.get("replacement_admitted", False),
                ),
                body=b"",
            )

    def _write_receipt(self, row):
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "receipt_type": "incident-containment",
            "run_id": self._run_id,
            "incident_id": row["incident_id"],
            "fault_id": row["fault"]["fault_id"],
            "fault_identity": row["fault_identity"],
            "generation_id": row["fault"]["generation_id"],
            "scope": row["scope"],
            "reason": row["reason"],
            "fault_kind": row["kind"],
            "authority_state": row.get("authority_state", ""),
            "trace": row["trace"],
            "replay_count": row["replay_count"],
            "duplicate_reports": row["duplicate_reports"],
            "disposition": row["disposition"],
            "evidence": row.get("evidence", {}),
            "remedy_authority": "fixed-core:no-inference:replace-once",
            "manifest_link": {
                "row_id": "core.deterministic-recovery",
                "receipt_ref": "receipt:incident-containment",
            },
        }
        receipt["receipt_digest"] = digest_bytes(canonical_bytes(receipt))
        atomic_write(self._receipt_path, canonical_bytes(receipt) + b"\n")

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


def verify_receipt(path: Path) -> Path:
    value = json.loads(Path(path).read_text())
    digest = value.pop("receipt_digest", None)
    if value.get("schema_version") != SCHEMA_VERSION or value.get("receipt_type") != "incident-containment":
        raise ValueError("incident-containment receipt version or kind is invalid")
    if digest != digest_bytes(canonical_bytes(value)):
        raise ValueError("incident-containment receipt digest is invalid")
    if value.get("remedy_authority") != "fixed-core:no-inference:replace-once":
        raise ValueError("incident-containment remedy authority is invalid")
    complete_trace = [
        "generation-fence",
        "evidence-capture",
        "full-teardown",
        "bounded-replacement",
    ]
    refused_kind = value.get("fault_kind") in {FaultKind.AMBIGUOUS.value, FaultKind.UNCLASSIFIED.value}
    if value.get("trace") != ([] if refused_kind else complete_trace):
        raise ValueError("incident-containment order is invalid")
    if value.get("disposition") not in {"replacement-admitted", "replacement-refused"}:
        raise ValueError("incident-containment disposition is invalid")
    if not isinstance(value.get("replay_count"), int) or value["replay_count"] < 0:
        raise ValueError("incident-containment replay count is invalid")
    if value.get("authority_state") not in {
        ReservationState.COMMITTED.value,
        ReservationState.ABORTED.value,
        ReservationState.TERMINAL.value,
    }:
        raise ValueError("incident-containment authority is not closed")
    expected_reason = {kind.value: reason for kind, reason in FAULT_REASONS.items()}
    if value.get("reason") != expected_reason.get(value.get("fault_kind")):
        raise ValueError("incident-containment fault classification is invalid")
    evidence = value.get("evidence")
    if not isinstance(evidence, dict) or digest_bytes(str(evidence.get("projection", "")).encode()) != evidence.get(
        "digest"
    ):
        raise ValueError("incident-containment evidence projection is invalid")
    if refused_kind and not evidence.get("projection"):
        raise ValueError("incident-containment refusal evidence is absent")
    return Path(path)
