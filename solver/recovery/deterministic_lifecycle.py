"""Versioned fixed-probe, changed-Remedy, semantic-probation lifecycle."""

from __future__ import annotations

import datetime as dt

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


def recover(engine, document, row, fault, authority, reservation):
    """Advance one deterministic Incident while preserving its durable bound."""
    try:
        entry = entry_for(fault.kind, fault.scope)
    except ValueError as refusal:
        body = engine._redactor.redact(fault.evidence.encode())[:4096]
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
        return _finish(engine, document, row)
    context = fault.recovery
    assert context is not None
    row.update(probe_id=entry.probe_id, remedy_id=entry.remedy_id, remedy_version=entry.remedy_version)
    engine._write(document)
    engine._step(document, row, IncidentStep.GENERATION_FENCE, lambda: engine._ports.fence(fault))
    engine._step(document, row, IncidentStep.EVIDENCE_CAPTURE, lambda: engine._capture(row, fault))
    engine._step(document, row, IncidentStep.FULL_TEARDOWN, lambda: engine._ports.teardown(fault))
    if reservation.state is ReservationState.COMMITTED:
        supplied = row["changed_action"]
        action = ChangedAction(supplied["dimension"], supplied["before"], supplied["after"], supplied["source"])
        admitted = True
    else:
        try:
            deadline = dt.datetime.fromisoformat(context.original_deadline).astimezone(dt.timezone.utc)
        except ValueError:
            deadline = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        if engine._now().astimezone(dt.timezone.utc) >= deadline or row["consumed_allowance"] >= min(
            context.allowance, entry.maximum_uses
        ):
            row["authority_state"] = authority.abort(reservation, "recovery-bound-exhausted").state.value
            row.update(
                disposition=IncidentDisposition.CONTAINED.value,
                final_outcome=IncidentDisposition.CONTAINED.value,
                terminal=True,
            )
            return _finish(engine, document, row)
        observation = engine._step_result(
            document, row, IncidentStep.FIXED_PROBE, lambda: engine._ports.probe(fault, entry.probe_id)
        )
        if not isinstance(observation, ProbeObservation):
            raise TypeError("Recovery probe returned no typed observation")
        row["probe_outcome"] = observation.outcome
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
            return _finish(engine, document, row)
        row["consumed_allowance"] += 1
        engine._write(document)
        admitted = engine._step_result(
            document,
            row,
            IncidentStep.CHANGED_REMEDY,
            lambda: apply_remedy(engine, row, fault, entry.remedy_id, action, authority, reservation),
        )
    if not admitted:
        row.update(
            disposition=IncidentDisposition.CONTAINED.value,
            final_outcome=IncidentDisposition.CONTAINED.value,
            terminal=True,
        )
        return _finish(engine, document, row)
    probation = engine._step_result(
        document, row, IncidentStep.SEMANTIC_PROBATION, lambda: engine._ports.probation(fault, entry.remedy_id)
    )
    if not isinstance(probation, ProbationOutcome):
        raise TypeError("Recovery probation returned no typed outcome")
    row["probation_outcome"] = probation.value
    if probation is ProbationOutcome.UNSETTLED:
        row.update(disposition=IncidentDisposition.PROBATION.value, final_outcome="", terminal=False)
    else:
        final = IncidentDisposition.RESOLVED if probation is ProbationOutcome.PASSED else IncidentDisposition.CONTAINED
        row.update(disposition=final.value, final_outcome=final.value, terminal=True)
    return _finish(engine, document, row)


def apply_remedy(engine, row, fault, remedy_id, action, authority, reservation):
    started = authority.start(reservation)
    row["authority_state"] = started.state.value
    admitted = engine._ports.apply_remedy(fault, remedy_id, action)
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


def _finish(engine, document, row):
    engine._write(document)
    engine._write_receipt(row)
    return engine._result(row)
