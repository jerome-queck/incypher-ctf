"""Independent verification of canonical Incident receipts."""

from __future__ import annotations

import json
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.recovery.catalogue import CATALOGUE_VERSION, entry_for
from solver.recovery.contracts import FAULT_REASONS, FaultKind, IncidentDisposition, IncidentStep, ProbationOutcome
from solver.write_reservation_contracts import ReservationState


def write_receipt(path: Path, run_id: str, row) -> None:
    receipt = {
        "schema_version": 1,
        "receipt_type": "incident-containment",
        "run_id": run_id,
        "incident_id": row["incident_id"],
        "fault_id": row["fault"]["fault_id"],
        "fault_identity": row["fault_identity"],
        "fingerprint": row.get("fingerprint", ""),
        "successor_of": row.get("successor_of", ""),
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
        "remedy_authority": "fixed-core:no-inference:deterministic-catalogue"
        if row.get("catalogue_version")
        else "fixed-core:no-inference:replace-once",
        "manifest_link": {"row_id": "core.deterministic-recovery", "receipt_ref": "receipt:incident-containment"},
        "catalogue_version": row.get("catalogue_version", ""),
        "probe": {"id": row.get("probe_id", ""), "outcome": row.get("probe_outcome", "")},
        "remedy": {"id": row.get("remedy_id", ""), "version": row.get("remedy_version", "")},
        "changed_action": row.get("changed_action", {}),
        "original_deadline": row.get("original_deadline", ""),
        "allowance": row.get("allowance", 0),
        "consumed_allowance": row.get("consumed_allowance", 0),
        "probation_outcome": row.get("probation_outcome", ""),
        "final_outcome": row.get("final_outcome", ""),
        "adapter": {"id": row.get("adapter_id", ""), "config": json.loads(row.get("adapter_config", "{}"))},
    }
    receipt["receipt_digest"] = digest_bytes(canonical_bytes(receipt))
    from solver.event_store_storage import atomic_write

    atomic_write(path, canonical_bytes(receipt) + b"\n")


def verify_receipt(path: Path) -> Path:
    value = json.loads(Path(path).read_text())
    digest = value.pop("receipt_digest", None)
    if value.get("schema_version") != 1 or value.get("receipt_type") != "incident-containment":
        raise ValueError("incident-containment receipt version or kind is invalid")
    if digest != digest_bytes(canonical_bytes(value)):
        raise ValueError("incident-containment receipt digest is invalid")
    authority = value.get("remedy_authority")
    if authority not in {
        "fixed-core:no-inference:replace-once",
        "fixed-core:no-inference:deterministic-catalogue",
    }:
        raise ValueError("incident-containment remedy authority is invalid")
    refused = value.get("fault_kind") in {FaultKind.AMBIGUOUS.value, FaultKind.UNCLASSIFIED.value}
    if authority.endswith("replace-once"):
        trace = ["generation-fence", "evidence-capture", "full-teardown", "bounded-replacement"]
        if value.get("trace") != ([] if refused else trace):
            raise ValueError("incident-containment order is invalid")
        if value.get("disposition") not in {"replacement-admitted", "replacement-refused"}:
            raise ValueError("incident-containment disposition is invalid")
    else:
        _verify_deterministic(value)
    if not isinstance(value.get("replay_count"), int) or value["replay_count"] < 0:
        raise ValueError("incident-containment replay count is invalid")
    if value.get("authority_state") not in {
        item.value for item in (ReservationState.COMMITTED, ReservationState.ABORTED, ReservationState.TERMINAL)
    }:
        raise ValueError("incident-containment authority is not closed")
    expected = {kind.value: reason for kind, reason in FAULT_REASONS.items()}
    if value.get("reason") != expected.get(value.get("fault_kind")):
        raise ValueError("incident-containment fault classification is invalid")
    evidence = value.get("evidence")
    if not isinstance(evidence, dict) or digest_bytes(str(evidence.get("projection", "")).encode()) != evidence.get(
        "digest"
    ):
        raise ValueError("incident-containment evidence projection is invalid")
    if refused and not evidence.get("projection"):
        raise ValueError("incident-containment refusal evidence is absent")
    return Path(path)


def _verify_deterministic(value) -> None:
    if value.get("catalogue_version") != CATALOGUE_VERSION:
        raise ValueError("deterministic Recovery catalogue version is invalid")
    if value.get("disposition") not in {
        item.value
        for item in (IncidentDisposition.RESOLVED, IncidentDisposition.CONTAINED, IncidentDisposition.PROBATION)
    }:
        raise ValueError("deterministic Recovery outcome is invalid")
    allowance, consumed = value.get("allowance"), value.get("consumed_allowance")
    if (
        type(allowance) is not int
        or allowance < 1
        or type(consumed) is not int
        or consumed not in {0, 1}
        or consumed > allowance
        or not value.get("original_deadline")
    ):
        raise ValueError("deterministic Recovery bound is invalid")
    probe, changed = value.get("probe", {}), value.get("changed_action", {})
    if probe.get("outcome") == "inapplicable":
        if value.get("trace") or consumed or changed:
            raise ValueError("inapplicable deterministic Recovery gained authority")
        return
    try:
        entry = entry_for(FaultKind(value["fault_kind"]), value["scope"])
    except (KeyError, ValueError):
        raise ValueError("deterministic Recovery applicability is invalid") from None
    if probe.get("id") != entry.probe_id or value.get("remedy") != {
        "id": entry.remedy_id,
        "version": entry.remedy_version,
    }:
        raise ValueError("deterministic Recovery catalogue entry is invalid")
    prefix = [
        IncidentStep.GENERATION_FENCE.value,
        IncidentStep.EVIDENCE_CAPTURE.value,
        IncidentStep.FULL_TEARDOWN.value,
        IncidentStep.FIXED_PROBE.value,
    ]
    trace = value.get("trace")
    if (
        not consumed
        and not probe.get("outcome")
        and value.get("authority_state") == ReservationState.ABORTED.value
        and not changed
        and trace in (prefix[:3], prefix)
    ):
        return
    if not isinstance(trace, list) or trace[:4] != prefix:
        raise ValueError("deterministic Recovery order is invalid")
    if consumed:
        completed = [*prefix, IncidentStep.CHANGED_REMEDY.value, IncidentStep.SEMANTIC_PROBATION.value]
        stopped_before_probation = (
            value.get("final_outcome") == IncidentDisposition.CONTAINED.value
            and value.get("probation_outcome") != ProbationOutcome.PASSED.value
            and trace in (prefix, completed[:-1])
        )
        if trace != completed and not stopped_before_probation:
            raise ValueError("deterministic Recovery probation order is invalid")
        if (
            changed.get("dimension") != entry.changed_dimension
            or not all(isinstance(changed.get(name), str) and changed[name] for name in ("before", "after", "source"))
            or not changed.get("accepted")
            or changed["before"] == changed["after"]
        ):
            raise ValueError("deterministic Recovery changed action is invalid")
    elif changed.get("accepted"):
        raise ValueError("deterministic Recovery accepted an unconsumed action")
    if (
        value.get("final_outcome") == IncidentDisposition.RESOLVED.value
        and value.get("probation_outcome") != ProbationOutcome.PASSED.value
    ):
        raise ValueError("deterministic Recovery resolved without probation")
