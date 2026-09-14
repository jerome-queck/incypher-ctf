"""Self-contained Board-profile receipt and independent semantic verification."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from solver.board_profile import (
    PROFILE_ENDPOINTS,
    ProfileCycle,
    ProfileDecision,
    ProfileDocument,
    ProfileProbe,
    decision_document,
    profile_cycle_documents,
    profile_document_endpoint,
    profile_document_sort_key,
    qualify,
    rules_document,
    rules_from_document,
)
from solver.board_profile_contracts import BOARD_PROFILE_OBSERVATION_RECORDED
from solver.event_store import EventStore
from solver.board_profile_phase import project_profile_phase
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.profile import Rules

RECEIPT_TYPE = "board-profile"
RECEIPT_FILENAME = "board-profile.receipt.json"
RECEIPT_REF = "receipt:board-profile"
MANIFEST_ROW_ID = "core.board-target-lease"
SCHEMA_VERSION = 2


def write_receipt(
    state: Path,
    run_id: str,
    probe: ProfileProbe,
    rules: Rules,
    decision: ProfileDecision,
) -> Path:
    """Write the complete evidence needed to recompute one profile decision."""

    if not run_id:
        raise ValueError("Board-profile receipt needs a Run identity")
    canonical_probe, canonical_events = _canonical_probe(Path(state), run_id, probe.probe_id, probe.rules_source)
    if canonical_probe != probe:
        raise ValueError("Board-profile receipt input differs from canonical observations")
    phase = project_profile_phase(EventStore(state, run_id=run_id).events())
    expected_rules_digest = digest_bytes(canonical_bytes(rules_document(rules)))
    if phase.probe_id != probe.probe_id or phase.rules_digest != expected_rules_digest or phase.decision:
        raise ValueError("Board-profile receipt has no matching active canonical probe")
    document = _receipt_document(run_id, canonical_probe, canonical_events, rules, decision)
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    """Recompute rules, response classifications and the combined profile."""

    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Board-profile receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise ValueError("Board-profile receipt is not canonical JSON")
    if set(supplied) != {
        "schema_version",
        "receipt_type",
        "qualifier_version",
        "acceptance_scope",
        "external_evidence",
        "assessment",
        "rules",
        "probe",
        "decision",
        "manifest_link",
        "run_id",
        "canonical_observations",
    }:
        raise ValueError("Board-profile receipt shape is unsupported")
    if supplied["schema_version"] != SCHEMA_VERSION or supplied["qualifier_version"] != 2:
        raise ValueError("Board-profile receipt schema is unsupported")
    if (
        supplied["acceptance_scope"] != "public-contract-double"
        or supplied["external_evidence"] != "non-acceptance-boundary"
    ):
        raise ValueError("Board-profile evidence scope is unsupported")
    rules_block = _mapping(supplied["rules"], "rules")
    rules_document = _mapping(rules_block.get("document"), "rules document")
    if rules_block.get("digest") != digest_bytes(canonical_bytes(rules_document)):
        raise ValueError("Board-profile rules digest disagrees")
    rules = rules_from_document(rules_document)
    probe = _probe_from(_mapping(supplied["probe"], "probe"), str(rules_block.get("source", "")))
    run_id = str(supplied["run_id"])
    if (
        not run_id
        or receipt_path.name != RECEIPT_FILENAME
        or receipt_path.parent.name != "canonical"
        or receipt_path.parent.parent.name != run_id
        or receipt_path.parent.parent.parent.name != "runs"
    ):
        raise ValueError("Board-profile receipt path does not match its Run identity")
    state = receipt_path.parents[3]
    canonical_probe, canonical_events = _canonical_probe(state, run_id, probe.probe_id, probe.rules_source)
    if probe != canonical_probe or supplied["canonical_observations"] != canonical_events:
        raise ValueError("Board-profile receipt differs from canonical observations")
    phase = project_profile_phase(EventStore(state, run_id=run_id).events())
    decision_block = _mapping(supplied["decision"], "decision")
    expected_decision = "authoritative" if decision_block.get("authoritative") is True else "refused"
    if (
        phase.probe_id != probe.probe_id
        or phase.rules_digest != rules_block["digest"]
        or phase.decision != expected_decision
        or phase.receipt_digest != digest_bytes(raw)
    ):
        raise ValueError("Board-profile receipt disagrees with its canonical phase")
    recomputed = qualify(probe, rules)
    if supplied["assessment"] != _assessment_document(probe, recomputed):
        raise ValueError("Board-profile assessment does not match its evidence")
    if supplied["decision"] != decision_document(recomputed):
        raise ValueError("Board-profile decision does not match its evidence")
    if supplied["manifest_link"] != {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF}:
        raise ValueError("Board-profile manifest link is invalid")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


def _receipt_document(
    run_id: str,
    probe: ProfileProbe,
    canonical_events: list[dict[str, object]],
    rules: Rules,
    decision: ProfileDecision,
) -> dict[str, object]:
    recomputed = qualify(probe, rules)
    if decision != recomputed:
        raise ValueError("Board-profile decision was not derived from this probe")
    normalized_rules = rules_document(rules)
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "qualifier_version": 2,
        "acceptance_scope": "public-contract-double",
        "external_evidence": "non-acceptance-boundary",
        "rules": {
            "source": probe.rules_source,
            "digest": digest_bytes(canonical_bytes(normalized_rules)),
            "document": normalized_rules,
        },
        "probe": _probe_document(probe),
        "canonical_observations": canonical_events,
        "assessment": _assessment_document(probe, decision),
        "decision": decision_document(decision),
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF},
    }


def _assessment_document(probe: ProfileProbe, decision: ProfileDecision) -> dict[str, object]:
    cycle_names = ("first", "second")
    comparisons = {}
    for cycle_name, cycle in zip(cycle_names, probe.cycles, strict=True):
        for name, document in profile_cycle_documents(cycle):
            if name == "landing":
                continue
            comparisons[f"{cycle_name}:{name}"] = "same" if document.digest == cycle.landing.digest else "distinct"
    field_documents = {
        "authenticated_identity": ("identity", "landing"),
        "read_contract": ("read_contract",),
        "instanced_challenges": ("challenges", "challenge_details"),
        "chall_manager": ("ledger",),
        "mana": ("mana",),
        "submissions_per_minute": ("configs",),
        "configs_outcome": ("configs",),
        "board_window": ("configs", "landing"),
        "unauthenticated_read": ("anonymous_challenges",),
    }
    provenance = {}
    for field, names in field_documents.items():
        provenance[field] = []
        for cycle in probe.cycles:
            documents = dict(profile_cycle_documents(cycle))
            for name in names:
                if name == "challenge_details":
                    provenance[field].extend(
                        document.request_id
                        for document_name, document in documents.items()
                        if document_name.startswith("challenge_detail_")
                    )
                else:
                    provenance[field].append(documents[name].request_id)
    unsettled = list(decision.unsettled_fields)
    if decision.profile is not None:
        if decision.profile.chall_manager == "unreadable":
            unsettled.append("chall_manager")
        if decision.profile.unauthenticated_read == "unreadable":
            unsettled.append("unauthenticated_read")
        if decision.profile.configs_outcome != "answered":
            unsettled.append("configs_outcome")
        if decision.profile.mana_outcome == "unreadable":
            unsettled.append("mana")
        window_sources = decision.profile.board_window_observations
        if not any(
            window_sources.get(source, {}).get("outcome") in {"answered", "published", "not-published"}
            for source in ("configs", "landing")
        ):
            unsettled.append("board_window")
    unsettled = list(dict.fromkeys(unsettled))
    return {
        "snapshot_identity": probe.probe_id,
        "compatibility_verdict": decision.reason,
        "catch_all_comparisons": comparisons,
        "field_provenance": provenance,
        "unsettled_fields": unsettled,
    }


def _probe_document(probe: ProfileProbe) -> dict[str, object]:
    return {
        "schema_version": probe.schema_version,
        "probe_id": probe.probe_id,
        "cycles": [
            {"documents": {name: _document_document(document) for name, document in profile_cycle_documents(cycle)}}
            for cycle in probe.cycles
        ],
    }


def _document_document(document: ProfileDocument) -> dict[str, object]:
    return {
        "request_id": document.request_id,
        "endpoint": document.endpoint,
        "status": document.status,
        "content_type": document.content_type,
        "body": base64.b64encode(document.body).decode("ascii"),
        "body_digest": hashlib.sha256(document.body).hexdigest(),
        "body_bytes": len(document.body),
        "original_bytes": document.original_bytes,
        "complete": document.complete,
    }


def _probe_from(document: Mapping[str, object], rules_source: str) -> ProfileProbe:
    if set(document) != {"schema_version", "probe_id", "cycles"} or not isinstance(document.get("cycles"), list):
        raise ValueError("Board-profile probe shape is unsupported")
    cycles = []
    request_ids = set()
    for raw_cycle in document["cycles"]:
        cycle = _mapping(raw_cycle, "profile cycle")
        if set(cycle) != {"documents"}:
            raise ValueError("Board-profile cycle shape is unsupported")
        documents = _mapping(cycle["documents"], "profile documents")
        if not set(PROFILE_ENDPOINTS).issubset(documents):
            raise ValueError("Board-profile document set is incomplete or duplicated")
        parsed = {name: _document_from(_mapping(documents[name], name)) for name in documents}
        for name, profile_document in parsed.items():
            if (
                profile_document.endpoint != profile_document_endpoint(name)
                or profile_document.request_id in request_ids
            ):
                raise ValueError("Board-profile response identity or endpoint is invalid")
            request_ids.add(profile_document.request_id)
        fixed = {name: parsed.pop(name) for name in PROFILE_ENDPOINTS}
        details = tuple(parsed[name] for name in sorted(parsed, key=profile_document_sort_key))
        cycles.append(ProfileCycle(**fixed, challenge_details=details))
    return ProfileProbe(str(document["probe_id"]), rules_source, tuple(cycles), int(document["schema_version"]))


def _document_from(document: Mapping[str, object]) -> ProfileDocument:
    expected = {
        "request_id",
        "endpoint",
        "status",
        "content_type",
        "body",
        "body_digest",
        "body_bytes",
        "original_bytes",
        "complete",
    }
    if set(document) != expected or not isinstance(document.get("complete"), bool):
        raise ValueError("Board-profile response is unsupported")
    try:
        body = base64.b64decode(str(document["body"]), validate=True)
    except ValueError as error:
        raise ValueError("Board-profile response body is invalid") from error
    if len(body) != document["body_bytes"] or hashlib.sha256(body).hexdigest() != document["body_digest"]:
        raise ValueError("Board-profile response body digest disagrees")
    original_bytes = document["original_bytes"]
    if (
        not isinstance(original_bytes, int)
        or isinstance(original_bytes, bool)
        or original_bytes < len(body)
        or bool(document["complete"]) != (original_bytes == len(body))
    ):
        raise ValueError("Board-profile response completeness disagrees")
    return ProfileDocument(
        str(document["request_id"]),
        str(document["endpoint"]),
        int(document["status"]),
        str(document["content_type"]),
        body,
        original_bytes,
        bool(document["complete"]),
    )


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"Board-profile {name} is not an object")
    return value


def _canonical_probe(
    state: Path,
    run_id: str,
    probe_id: str,
    rules_source: str,
) -> tuple[ProfileProbe, list[dict[str, object]]]:
    events = [
        event
        for event in EventStore(state, run_id=run_id).events()
        if event.event_type == BOARD_PROFILE_OBSERVATION_RECORDED and event.payload["probe_id"] == probe_id
    ]
    indexed = {(event.payload["cycle"], event.payload["document_name"]): event for event in events}
    if len(events) != len(indexed):
        raise ValueError("canonical Board-profile observations are incomplete or duplicated")
    cycles = []
    for cycle in (1, 2):
        names = {name for event_cycle, name in indexed if event_cycle == cycle}
        if not set(PROFILE_ENDPOINTS).issubset(names):
            raise ValueError("canonical Board-profile observations are incomplete or duplicated")
        documents = {}
        for name in names:
            event = indexed[(cycle, name)]
            payload = event.payload
            if payload["endpoint"] != profile_document_endpoint(name):
                raise ValueError("canonical Board-profile endpoint is invalid")
            documents[name] = ProfileDocument(
                payload["request_id"],
                payload["endpoint"],
                payload["http_status"],
                payload["content_type"],
                event.body,
                payload["original_bytes"],
                payload["complete"],
            )
        fixed = {name: documents.pop(name) for name in PROFILE_ENDPOINTS}
        details = tuple(documents[name] for name in sorted(documents, key=profile_document_sort_key))
        cycles.append(ProfileCycle(**fixed, challenge_details=details))
    descriptors = [
        {
            "event_id": event.payload["event_id"],
            "sequence": event.sequence,
            "event_digest": event.event_digest,
            "body_digest": event.blob_digest,
        }
        for event in sorted(events, key=lambda item: item.sequence)
    ]
    return ProfileProbe(probe_id, rules_source, tuple(cycles)), descriptors


__all__ = ["link_manifest", "manifest_receipt", "verify_receipt", "write_receipt"]
