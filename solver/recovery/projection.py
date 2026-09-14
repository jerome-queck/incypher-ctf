"""Canonical Incident event projection and append."""

from solver.recovery.contracts import INCIDENT_RECORDED, IncidentRecorded


def read(store, run_id, schema_version):
    incidents = {}
    for event in store.events():
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
            "steps": {name: "complete" if name in value["completed_steps"] else "reserved" for name in value["steps"]},
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
            "catalogue_version": value.get("catalogue_version", ""),
            "probe_id": value.get("probe_id", ""),
            "probe_outcome": value.get("probe_outcome", ""),
            "remedy_id": value.get("remedy_id", ""),
            "remedy_version": value.get("remedy_version", ""),
            "changed_action": {
                "dimension": value.get("changed_dimension", ""),
                "before": value.get("changed_before", ""),
                "after": value.get("changed_after", ""),
                "source": value.get("changed_source", ""),
                "accepted": value.get("changed_accepted", False),
            },
            "original_deadline": value.get("original_deadline", ""),
            "allowance": value.get("allowance", 0),
            "consumed_allowance": value.get("consumed_allowance", 0),
            "probation_outcome": value.get("probation_outcome", ""),
            "final_outcome": value.get("final_outcome", ""),
            "failed_action_value": value.get("failed_action_value", ""),
            "adapter_id": value.get("adapter_id", "legacy-caller-v1"),
            "adapter_config": value.get("adapter_config", "{}"),
        }
    return {"schema_version": schema_version, "run_id": run_id, "incidents": list(incidents.values())}


def write(store, document):
    revision = sum(1 for event in store.events() if event.event_type == INCIDENT_RECORDED)
    for offset, row in enumerate(document["incidents"], 1):
        evidence = row.get("evidence", {})
        store.append(
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
                catalogue_version=row.get("catalogue_version", ""),
                probe_id=row.get("probe_id", ""),
                probe_outcome=row.get("probe_outcome", ""),
                remedy_id=row.get("remedy_id", ""),
                remedy_version=row.get("remedy_version", ""),
                changed_dimension=row.get("changed_action", {}).get("dimension", ""),
                changed_before=row.get("changed_action", {}).get("before", ""),
                changed_after=row.get("changed_action", {}).get("after", ""),
                changed_source=row.get("changed_action", {}).get("source", ""),
                changed_accepted=row.get("changed_action", {}).get("accepted", False),
                original_deadline=row.get("original_deadline", ""),
                allowance=row.get("allowance", 0),
                consumed_allowance=row.get("consumed_allowance", 0),
                probation_outcome=row.get("probation_outcome", ""),
                final_outcome=row.get("final_outcome", ""),
                failed_action_value=row.get("failed_action_value", ""),
                adapter_id=row.get("adapter_id", ""),
                adapter_config=row.get("adapter_config", "{}"),
            ),
            body=b"",
        )
