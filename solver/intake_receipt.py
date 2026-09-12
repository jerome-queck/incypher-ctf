"""Self-contained receipt and independent verification for one Intake attempt."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from solver.event_store import EventStore
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.intake_evidence import IntakeEvidenceReader
from solver.intake_contracts import INTAKE_DECISION_RECORDED, INTAKE_OBSERVATION_RECORDED, IntakeRecord
from solver.intake_qualification import (
    IntakeDecision,
    IntakeProbe,
    PriorFence,
    decision_document,
    probe_document,
    probe_from_document,
    qualify,
)

SCHEMA_VERSION = 1
RECEIPT_TYPE = "intake-snapshot"
RECEIPT_REF = "receipt:intake-snapshot"
MANIFEST_ROW_ID = "core.intake"
RECEIPTS_DIRECTORY = "intake-receipts"


def write_receipt(
    state: Path,
    run_id: str,
    probe: IntakeProbe,
    decision: IntakeDecision,
    *,
    publication_record: str | None = None,
    observed_fence: PriorFence | None = None,
) -> Path:
    recomputed = qualify(probe)
    if decision_document(recomputed) != decision_document(decision):
        raise ValueError("Intake receipt decision was not derived from its probe")
    canonical_events, publication = _canonical_attempt(Path(state), run_id, probe, decision)
    if publication_record is not None and publication["record"] != publication_record:
        raise ValueError("Intake receipt publication record disagrees")
    if observed_fence is not None and publication["observed_fence"] != observed_fence.document():
        raise ValueError("Intake receipt observed fence disagrees")
    document = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "qualifier_version": probe.contract.qualifier_version,
        "run_id": run_id,
        "attempt_id": probe.attempt_id,
        "probe": _receipt_probe(probe),
        "canonical_events": canonical_events,
        "assessment": _assessment(decision),
        "decision": decision_document(decision),
        "publication": publication,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF},
    }
    return _write_document(Path(state), run_id, probe.attempt_id, document)


def write_interrupted_receipt(state: Path, run_id: str, attempt_id: str) -> Path:
    """Seal the independently checkable recovery of one incomplete attempt."""

    store = EventStore(state, run_id=run_id)
    selected = _selected_events(store, attempt_id)
    start, terminal, _observations = _event_parts(selected)
    if terminal.payload["record"] != IntakeRecord.UNSETTLED.value or not str(terminal.payload["reason"]).startswith(
        "interrupted-"
    ):
        raise ValueError("Intake recovery receipt needs an interrupted terminal")
    contract = json.loads(start.body).get("contract")
    if not isinstance(contract, Mapping) or not isinstance(contract.get("qualifier_version"), int):
        raise ValueError("Intake recovery start has no versioned contract")
    reason = str(terminal.payload["reason"])
    publication = _publication_from_terminal(terminal)
    document = {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "qualifier_version": contract["qualifier_version"],
        "run_id": run_id,
        "attempt_id": attempt_id,
        "probe": None,
        "canonical_events": _event_summaries(selected),
        "assessment": {
            "settlement": "unsettled",
            "reason": reason,
            "snapshot_digest": "",
            "empty_diagnosis": "unsettled",
            "challenge_revisions": [],
        },
        "decision": None,
        "publication": publication,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF},
    }
    path = _write_document(Path(state), run_id, attempt_id, document)
    from solver.intake_evidence import IntakeEvidenceWriter

    IntakeEvidenceWriter(Path(state), run_id).retire_interrupted_attempt(attempt_id)
    return path


def _write_document(state: Path, run_id: str, attempt_id: str, document: Mapping[str, object]) -> Path:
    body = canonical_bytes(document) + b"\n"
    name = hashlib.sha256(attempt_id.encode()).hexdigest() + ".json"
    path = state / "runs" / run_id / "canonical" / RECEIPTS_DIRECTORY / name
    if path.exists() and path.read_bytes() != body:
        raise ValueError("immutable Intake receipt already exists with different content")
    atomic_write(path, body)
    return path


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Intake receipt cannot be read as JSON") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise ValueError("Intake receipt is not canonical JSON")
    expected = {
        "schema_version",
        "receipt_type",
        "qualifier_version",
        "run_id",
        "attempt_id",
        "probe",
        "canonical_events",
        "assessment",
        "decision",
        "publication",
        "manifest_link",
    }
    if set(supplied) != expected:
        raise ValueError("Intake receipt shape is unsupported")
    if supplied["schema_version"] != SCHEMA_VERSION or supplied["receipt_type"] != RECEIPT_TYPE:
        raise ValueError("Intake receipt schema is unsupported")
    if supplied["manifest_link"] != {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF}:
        raise ValueError("Intake receipt manifest link is invalid")
    if receipt_path.parent.name != RECEIPTS_DIRECTORY or receipt_path.parent.parent.name != "canonical":
        raise ValueError("Intake receipt path is invalid")
    run_id = str(supplied["run_id"])
    if not run_id or receipt_path.parents[2].name != run_id or receipt_path.parents[3].name != "runs":
        raise ValueError("Intake receipt path does not match its Run")
    state = receipt_path.parents[4]
    if supplied["probe"] is None:
        _verify_interrupted(supplied, state, run_id)
        return _verify_name(receipt_path, str(supplied["attempt_id"]))
    probe = probe_from_document(_hydrate_probe(supplied["probe"], state, run_id, str(supplied["attempt_id"])))
    if probe.attempt_id != supplied["attempt_id"] or probe.contract.qualifier_version != supplied["qualifier_version"]:
        raise ValueError("Intake receipt probe identity disagrees")
    decision = qualify(probe)
    if supplied["decision"] != decision_document(decision):
        raise ValueError("Intake receipt decision does not match its evidence")
    if supplied["assessment"] != _assessment(decision):
        raise ValueError("Intake receipt assessment does not match its evidence")
    canonical_events, publication = _canonical_attempt(state, run_id, probe, decision)
    if supplied["canonical_events"] != canonical_events:
        raise ValueError("Intake receipt differs from canonical events")
    if supplied["publication"] != publication:
        raise ValueError("Intake receipt publication differs from canonical terminal")
    return _verify_name(receipt_path, probe.attempt_id)


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    receipt = json.loads(verified.read_text())
    if receipt["assessment"]["settlement"] != "settled" or receipt["publication"]["record"] != "settled":
        raise ValueError("only a settled Intake publication can satisfy the candidate manifest")
    state = verified.parents[4]
    run_id = str(receipt["run_id"])
    latest = [
        event
        for event in EventStore(state, run_id=run_id).events()
        if event.event_type == INTAKE_DECISION_RECORDED and event.payload.get("record") == IntakeRecord.SETTLED.value
    ]
    terminal = latest[-1] if latest else None
    receipt_terminal = receipt["canonical_events"][-1] if receipt["canonical_events"] else {}
    if (
        terminal is None
        or receipt_terminal.get("event_id") != terminal.payload["event_id"]
        or receipt_terminal.get("event_digest") != terminal.event_digest
        or receipt["decision"]["snapshot_digest"] != terminal.payload["snapshot_digest"]
    ):
        raise ValueError("Intake receipt is not the latest canonical settled publication")
    return {"ref": RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


def _canonical_attempt(
    state: Path,
    run_id: str,
    probe: IntakeProbe,
    decision: IntakeDecision,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    store = EventStore(state, run_id=run_id)
    evidence_reader = IntakeEvidenceReader(state, run_id)
    events = store.events()
    selected = _selected_events(store, probe.attempt_id)
    start, terminal, observations = _event_parts(selected)
    if (
        start.payload["contract_digest"] != probe.contract.digest
        or start.payload["profile_digest"] != probe.contract.profile_digest
        or start.payload["expected_fence"] != probe.prior_fence.document()
    ):
        raise ValueError("Intake canonical start differs from probe")
    start_document = json.loads(start.body)
    if not isinstance(start_document, Mapping) or set(start_document) != {"contract", "authority"}:
        raise ValueError("Intake canonical start body is unsupported")
    authority = start_document["authority"]
    if (
        start_document["contract"] != probe.contract.document()
        or not isinstance(authority, Mapping)
        or digest_bytes(canonical_bytes(authority)) != start.payload["authority_digest"]
        or authority.get("profile_digest") != probe.contract.profile_digest
        or authority.get("profile_decision_event_digest") != (probe.contract.profile_decision_event_digest or "f" * 64)
    ):
        raise ValueError("Intake canonical start authority differs from probe")
    documents = [document for observed in probe.passes for document in observed.documents]
    if documents and (
        {document.capability_digest for document in documents} != {authority.get("binding_digest")}
        or {document.peer_digest for document in documents} != {authority.get("controller_peer_digest")}
    ):
        raise ValueError("Intake observations differ from started authority")
    if len(observations) != len(documents):
        raise ValueError("Intake canonical observations are incomplete")
    for event, document in zip(observations, documents, strict=True):
        evidence = document.evidence()
        expected_fields = {
            "classified_event_id": evidence["classified_event_id"],
            "request_id": evidence["request_id"],
            "kind": evidence["kind"],
            "pass": evidence["pass"],
            "raw_digest": evidence["raw_digest"],
            "profile_digest": evidence["profile_digest"],
            "subject_digest": evidence["subject_digest"],
            "capability_digest": evidence["capability_digest"],
            "peer_digest": evidence["peer_digest"],
            "endpoint": evidence["endpoint"],
            "status": evidence["status"],
            "content_type": evidence["content_type"],
            "original_bytes": evidence["original_bytes"],
            "complete": evidence["complete"],
            "page": evidence["page"],
            "challenge_id": evidence["challenge_id"],
            "outcome": evidence["outcome"],
            "location": evidence["location"],
            "raw_blob_digest": evidence["raw_blob_digest"],
            "sanitized_blob_digest": evidence["sanitized_blob_digest"],
            "request_digest": evidence["request_digest"],
            "hop": evidence["hop"],
            "auth_forwarded": evidence["auth_forwarded"],
            "resource_identity": evidence["resource_identity"],
        }
        if any(event.payload[name] != value for name, value in expected_fields.items()):
            raise ValueError("Intake canonical observation differs from probe")
        if document.raw_blob_digest:
            if document.raw_blob_digest != document.raw_digest:
                raise ValueError("Intake private raw response differs from observation")
            broker = next(
                (
                    candidate
                    for candidate in events
                    if candidate.event_type == "board-broker.recorded"
                    and candidate.payload.get("event_id") == document.classified_event_id
                    and candidate.payload.get("record") == "classified"
                ),
                None,
            )
            if broker is None:
                raise ValueError("Intake observation has no canonical broker classification")
            if (
                evidence_reader.read(
                    document.raw_blob_digest,
                    classified_event_id=document.classified_event_id,
                    sanitized=broker.body,
                )
                != document.raw
            ):
                raise ValueError("Intake private raw response differs from observation")
            reserved = next(
                (
                    candidate
                    for candidate in events
                    if candidate.event_type == "board-broker.recorded"
                    and candidate.payload.get("event_id") == document.request_id
                    and candidate.payload.get("record") == "reserved"
                ),
                None,
            )
            if reserved is None or reserved.sequence >= broker.sequence:
                raise ValueError("Intake observation has no prior broker reservation")
            broker_expected = {
                "operation": "intake-read",
                "scope": "board.intake",
                "request_id": document.request_id,
                "response_digest": document.raw_digest,
                "response_original_bytes": document.original_bytes,
                "response_sanitized_bytes": broker.blob_bytes,
                "response_truncated": False,
                "response_lost_bytes": 0,
                "raw_blob_digest": document.raw_blob_digest,
                "raw_blob_bytes": document.original_bytes,
                "raw_blob_class": "canonical-private-board-response",
                "binding_digest": document.capability_digest,
                "peer_identity_digest": document.peer_digest,
                "request_digest": document.request_digest,
                "profile_digest": document.profile_digest,
                "http_status": document.status,
                "endpoint": _wire_endpoint(document.endpoint),
                "response_content_type": document.content_type,
                "response_location": document.location,
                "redaction_policy_digest": evidence_reader.redaction_policy_digest(document.classified_event_id),
            }
            if any(broker.payload.get(name) != value for name, value in broker_expected.items()):
                raise ValueError("Intake observation differs from broker raw evidence")
            if broker.blob_digest != document.sanitized_blob_digest:
                raise ValueError("Intake observation differs from broker sanitized evidence")
            reserved_expected = {
                "request_id": document.request_id,
                "operation": "intake-read",
                "scope": "board.intake",
                "binding_digest": document.capability_digest,
                "peer_identity_digest": document.peer_digest,
                "request_digest": document.request_digest,
            }
            if any(reserved.payload.get(name) != value for name, value in reserved_expected.items()):
                raise ValueError("Intake observation differs from broker reservation")
        elif digest_bytes(event.body) != document.raw_digest:
            raise ValueError("Intake canonical observation body differs from raw evidence")
    record = terminal.payload["record"]
    expected_records = (
        {IntakeRecord.SETTLED.value, IntakeRecord.FENCE_CONFLICT.value}
        if decision.settled
        else {IntakeRecord.UNSETTLED.value}
    )
    if record not in expected_records:
        raise ValueError("Intake canonical terminal differs from decision")
    expected_reason = "prior-fence-changed" if record == IntakeRecord.FENCE_CONFLICT.value else decision.reason
    expected_snapshot = decision.snapshot.digest if decision.snapshot and record == IntakeRecord.SETTLED.value else ""
    if (
        terminal.payload["reason"] != expected_reason
        or terminal.payload["expected_fence"] != decision.prior_fence.document()
    ):
        raise ValueError("Intake canonical terminal payload differs from decision")
    if (
        terminal.payload["observation_ids"] != list(decision.observation_ids)
        or terminal.payload["snapshot_digest"] != expected_snapshot
    ):
        raise ValueError("Intake canonical terminal payload differs from decision")
    if record == IntakeRecord.FENCE_CONFLICT.value:
        if decision.snapshot is None or terminal.payload["proposed_snapshot_digest"] != decision.snapshot.digest:
            raise ValueError("Intake fence conflict does not bind its proposal")
    elif terminal.payload["proposed_snapshot_digest"] or terminal.payload["observed_fence"]:
        raise ValueError("Intake terminal carries unexpected conflict state")
    if (
        decision.snapshot is not None
        and record == IntakeRecord.SETTLED.value
        and terminal.body != decision.snapshot.canonical_bytes()
    ):
        raise ValueError("Intake canonical snapshot body differs from decision")
    return _event_summaries(selected), _publication_from_terminal(terminal)


def verify_canonical_attempt(
    state: Path,
    run_id: str,
    probe: IntakeProbe,
    decision: IntakeDecision,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Independently bind one reconstructed probe to its canonical attempt."""

    return _canonical_attempt(Path(state), run_id, probe, decision)


def _event_summaries(events) -> list[dict[str, object]]:
    return [
        {
            "sequence": event.sequence,
            "event_type": event.event_type,
            "event_id": event.payload["event_id"],
            "event_digest": event.event_digest,
            "blob_digest": event.blob_digest,
            "blob_bytes": event.blob_bytes,
        }
        for event in events
    ]


def _selected_events(store: EventStore, attempt_id: str):
    return [
        event
        for event in store.events()
        if event.event_type in {INTAKE_DECISION_RECORDED, INTAKE_OBSERVATION_RECORDED}
        and event.payload.get("attempt_id") == attempt_id
    ]


def _event_parts(selected):
    starts = [event for event in selected if event.payload.get("record") == IntakeRecord.STARTED.value]
    terminals = [
        event
        for event in selected
        if event.event_type == INTAKE_DECISION_RECORDED and event.payload.get("record") != IntakeRecord.STARTED.value
    ]
    observations = [event for event in selected if event.event_type == INTAKE_OBSERVATION_RECORDED]
    if len(starts) != 1 or len(terminals) != 1:
        raise ValueError("Intake receipt needs one canonical start and terminal")
    return starts[0], terminals[0], observations


def _publication_from_terminal(terminal) -> dict[str, object]:
    record = str(terminal.payload["record"])
    return {
        "record": record,
        "reason": str(terminal.payload["reason"]),
        "expected_fence": terminal.payload["expected_fence"],
        "observed_fence": terminal.payload["observed_fence"],
        "proposed_snapshot_digest": str(terminal.payload["proposed_snapshot_digest"]),
    }


def _verify_interrupted(receipt: Mapping[str, object], state: Path, run_id: str) -> None:
    attempt_id = str(receipt["attempt_id"])
    store = EventStore(state, run_id=run_id)
    selected = _selected_events(store, attempt_id)
    start, terminal, observations = _event_parts(selected)
    reason = str(terminal.payload["reason"])
    if terminal.payload["record"] != IntakeRecord.UNSETTLED.value or not reason.startswith("interrupted-"):
        raise ValueError("Intake recovery receipt terminal is invalid")
    if terminal.payload["observation_ids"] != [event.payload["classified_event_id"] for event in observations]:
        raise ValueError("Intake recovery receipt observation closure differs")
    contract = json.loads(start.body).get("contract")
    if not isinstance(contract, Mapping) or receipt["qualifier_version"] != contract.get("qualifier_version"):
        raise ValueError("Intake recovery receipt contract differs")
    expected_assessment = {
        "settlement": "unsettled",
        "reason": reason,
        "snapshot_digest": "",
        "empty_diagnosis": "unsettled",
        "challenge_revisions": [],
    }
    if receipt["assessment"] != expected_assessment or receipt["decision"] is not None:
        raise ValueError("Intake recovery receipt assessment differs")
    if receipt["canonical_events"] != _event_summaries(selected):
        raise ValueError("Intake recovery receipt differs from canonical events")
    if receipt["publication"] != _publication_from_terminal(terminal):
        raise ValueError("Intake recovery receipt publication differs")


def _verify_name(path: Path, attempt_id: str) -> Path:
    expected_name = hashlib.sha256(attempt_id.encode()).hexdigest() + ".json"
    if path.name != expected_name:
        raise ValueError("Intake receipt filename does not match its attempt")
    return path


def _wire_endpoint(endpoint: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(endpoint)
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


def _assessment(decision: IntakeDecision) -> dict[str, object]:
    return {
        "settlement": "settled" if decision.settled else "unsettled",
        "reason": decision.reason,
        "snapshot_digest": decision.snapshot.digest if decision.snapshot else "",
        "empty_diagnosis": decision.snapshot.empty_diagnosis if decision.snapshot else "unsettled",
        "challenge_revisions": (
            [
                {
                    "challenge_id": item.challenge_id.document(),
                    "revision_digest": item.revision_digest,
                }
                for item in decision.snapshot.challenges
            ]
            if decision.snapshot
            else []
        ),
    }


def _receipt_probe(probe: IntakeProbe) -> dict[str, object]:
    document = json.loads(json.dumps(probe_document(probe)))
    for observed in _receipt_documents(document):
        observed["raw_ref"] = {
            "digest": observed["raw_digest"],
            "private_blob_digest": observed["raw_blob_digest"],
        }
        del observed["raw"]
    return document


def _hydrate_probe(value: object, state: Path, run_id: str, attempt_id: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("Intake receipt probe is not an object")
    document = json.loads(json.dumps(value))
    store = EventStore(state, run_id=run_id)
    evidence_reader = IntakeEvidenceReader(state, run_id)
    events = store.events()
    observations = {
        str(event.payload["classified_event_id"]): event
        for event in events
        if event.event_type == INTAKE_OBSERVATION_RECORDED and event.payload.get("attempt_id") == attempt_id
    }
    for observed in _receipt_documents(document):
        reference = observed.pop("raw_ref", None)
        if not isinstance(reference, dict) or reference.get("digest") != observed.get("raw_digest"):
            raise ValueError("Intake receipt raw reference is invalid")
        private_digest = str(reference.get("private_blob_digest", ""))
        if private_digest:
            raw = evidence_reader.read(
                private_digest,
                classified_event_id=str(observed.get("classified_event_id", "")),
            )
        else:
            canonical = observations.get(str(observed.get("classified_event_id", "")))
            if canonical is None:
                raise ValueError("Intake receipt raw observation is unavailable")
            raw = canonical.body
        if digest_bytes(raw) != observed["raw_digest"]:
            raise ValueError("Intake receipt raw observation digest disagrees")
        observed["raw"] = base64.b64encode(raw).decode("ascii")
    return document


def _receipt_documents(probe: dict[str, object]) -> list[dict[str, object]]:
    passes = probe.get("passes")
    if not isinstance(passes, list):
        raise ValueError("Intake receipt pass set is invalid")
    documents = []
    for observed in passes:
        if not isinstance(observed, dict):
            raise ValueError("Intake receipt pass is invalid")
        for name in ("identity_before",):
            if not isinstance(observed.get(name), dict):
                raise ValueError("Intake receipt identity is invalid")
            documents.append(observed[name])
        for name in ("landing", "read_control", "scoreboard", "mana"):
            if observed.get(name) is not None:
                if not isinstance(observed[name], dict):
                    raise ValueError("Intake receipt control is invalid")
                documents.append(observed[name])
        for name in ("pages", "details", "attachments", "absence_controls"):
            values = observed.get(name)
            if not isinstance(values, list) or any(not isinstance(item, dict) for item in values):
                raise ValueError("Intake receipt document collection is invalid")
            documents.extend(values)
        if not isinstance(observed.get("identity_after"), dict):
            raise ValueError("Intake receipt identity is invalid")
        documents.append(observed["identity_after"])
    return documents


__all__ = [
    "link_manifest",
    "manifest_receipt",
    "verify_canonical_attempt",
    "verify_receipt",
    "write_interrupted_receipt",
    "write_receipt",
]
