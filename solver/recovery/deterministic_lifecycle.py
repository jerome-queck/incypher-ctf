"""Versioned fixed-probe, changed-Remedy, semantic-probation lifecycle."""

from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Protocol

from solver.event_store_storage import digest_bytes
from solver.recovery.catalogue import entry_for
from solver.recovery.contracts import (
    ChangedAction,
    IncidentDisposition,
    IncidentStep,
    ProbeObservation,
    ProbationOutcome,
)
from solver.write_reservation_contracts import ReservationState


class LifecyclePort(Protocol):
    """Public operations required by the deterministic lifecycle."""

    def redact_evidence(self, evidence: bytes) -> bytes: ...
    def write(self, document: dict[str, Any]) -> None: ...
    def step(self, document, row, step: IncidentStep, action: Callable[[], Any]) -> None: ...
    def step_result(self, document, row, step: IncidentStep, action: Callable[[], Any]) -> Any: ...
    def fence(self, fault) -> None: ...
    def capture(self, row, fault) -> None: ...
    def teardown(self, fault) -> None: ...
    def probe(self, fault, probe_id: str) -> ProbeObservation: ...
    def apply_remedy(self, fault, remedy_id: str, action: ChangedAction) -> bool: ...
    def probation(self, fault, remedy_id: str) -> ProbationOutcome: ...
    def now(self) -> dt.datetime: ...
    def finish(self, document, row): ...


def recover(port: LifecyclePort, document, row, fault, authority, reservation):
    """Advance one deterministic Incident while preserving its durable bound."""
    try:
        entry = entry_for(fault.kind, fault.scope)
    except ValueError as refusal:
        body = port.redact_evidence(fault.evidence.encode())[:4096]
        row["evidence"] = {
            "bytes": len(body),
            "digest": digest_bytes(body),
            "projection": body.decode("utf-8", "replace"),
        }
        row["authority_state"] = authority.abort(reservation, str(refusal)).state.value
        row.update(
            probe_outcome="inapplicable",
            disposition=IncidentDisposition.CONTAINED.value,
            final_outcome=IncidentDisposition.CONTAINED.value,
            terminal=True,
        )
        return port.finish(document, row)
    context = fault.recovery
    assert context is not None
    row.update(probe_id=entry.probe_id, remedy_id=entry.remedy_id, remedy_version=entry.remedy_version)
    port.write(document)
    port.step(document, row, IncidentStep.GENERATION_FENCE, lambda: port.fence(fault))
    port.step(document, row, IncidentStep.EVIDENCE_CAPTURE, lambda: port.capture(row, fault))
    port.step(document, row, IncidentStep.FULL_TEARDOWN, lambda: port.teardown(fault))
    try:
        deadline = dt.datetime.fromisoformat(context.original_deadline).astimezone(dt.timezone.utc)
    except ValueError:
        deadline = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    committed = reservation.state is ReservationState.COMMITTED
    expired = port.now().astimezone(dt.timezone.utc) >= deadline
    exhausted = row["consumed_allowance"] >= min(context.allowance, entry.maximum_uses)
    if expired or (not committed and exhausted):
        if not committed:
            row["authority_state"] = authority.abort(reservation, "recovery-bound-exhausted").state.value
        row.update(
            disposition=IncidentDisposition.CONTAINED.value,
            final_outcome=IncidentDisposition.CONTAINED.value,
            terminal=True,
        )
        return port.finish(document, row)
    if committed:
        admitted = True
    else:

        def observe():
            observation = port.probe(fault, entry.probe_id)
            if not isinstance(observation, ProbeObservation):
                raise TypeError("Recovery probe returned no typed observation")
            row["probe_outcome"] = observation.outcome
            row["changed_action"] = (
                observation.changed_action.document(accepted=False) if observation.changed_action is not None else {}
            )
            return observation

        if row["steps"].get(IncidentStep.FIXED_PROBE.value) == "complete" and row["probe_outcome"] == "settled":
            supplied = row["changed_action"]
            observation = ProbeObservation.settled(
                ChangedAction(supplied["dimension"], supplied["before"], supplied["after"], supplied["source"])
            )
        else:
            observation = port.step_result(document, row, IncidentStep.FIXED_PROBE, observe)
        action = observation.changed_action
        accepted = bool(
            observation.outcome == "settled"
            and action is not None
            and action.dimension == entry.changed_dimension
            and action.before == context.failed_action_value
            and action.changed
        )
        if action is not None:
            row["changed_action"] = action.document(accepted=accepted)
        if not accepted:
            row["authority_state"] = authority.abort(
                reservation, observation.reason or "unchanged-remedy-rejected"
            ).state.value
            unsettled = observation.outcome == "unsettled"
            row.update(
                disposition=IncidentDisposition.PROBATION.value if unsettled else IncidentDisposition.CONTAINED.value,
                final_outcome="" if unsettled else IncidentDisposition.CONTAINED.value,
                terminal=not unsettled,
            )
            return port.finish(document, row)
        row["consumed_allowance"] += 1
        port.write(document)
        admitted = port.step_result(
            document,
            row,
            IncidentStep.CHANGED_REMEDY,
            lambda: apply_remedy(port, row, fault, entry.remedy_id, action, authority, reservation),
        )
    if not admitted:
        row.update(
            disposition=IncidentDisposition.CONTAINED.value,
            final_outcome=IncidentDisposition.CONTAINED.value,
            terminal=True,
        )
        return port.finish(document, row)
    probation = port.step_result(
        document, row, IncidentStep.SEMANTIC_PROBATION, lambda: port.probation(fault, entry.remedy_id)
    )
    if not isinstance(probation, ProbationOutcome):
        raise TypeError("Recovery probation returned no typed outcome")
    row["probation_outcome"] = probation.value
    if probation is ProbationOutcome.UNSETTLED:
        row.update(disposition=IncidentDisposition.PROBATION.value, final_outcome="", terminal=False)
    else:
        final = IncidentDisposition.RESOLVED if probation is ProbationOutcome.PASSED else IncidentDisposition.CONTAINED
        row.update(disposition=final.value, final_outcome=final.value, terminal=True)
    return port.finish(document, row)


def apply_remedy(port: LifecyclePort, row, fault, remedy_id, action, authority, reservation):
    started = authority.start(reservation)
    row["authority_state"] = started.state.value
    admitted = port.apply_remedy(fault, remedy_id, action)
    if admitted:
        closed = authority.commit(
            started, {"outcome": "remedy-applied", "changed_action": action.document(accepted=True)}
        )
    else:
        closed = authority.refuse_indeterminate(
            authority.possibly_sent(started, "remedy-refused-after-admission"), "remedy-refused-after-admission"
        )
    row["authority_state"] = closed.state.value
    return admitted
