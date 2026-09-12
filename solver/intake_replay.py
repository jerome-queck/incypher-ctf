"""Deterministic reconstruction of published Intake authority from canonical evidence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from solver.event_store import CommittedEvent, EventStore
from solver.intake_contracts import INTAKE_DECISION_RECORDED, INTAKE_OBSERVATION_RECORDED, IntakeRecord
from solver.intake_evidence import IntakeEvidenceReader
from solver.intake_qualification import (
    IntakeDocument,
    IntakePass,
    IntakeProbe,
    IntakeSnapshot,
    PriorFence,
    contract_from_document,
    qualify,
    snapshot_from_document,
)
from solver.intake_receipt import verify_canonical_attempt


@dataclass(frozen=True)
class ReplayedIntake:
    snapshot: IntakeSnapshot | None
    fence: PriorFence
    history: tuple[IntakeSnapshot, ...] = ()


def replay_intake(state: Path, run_id: str, profile_digest: str) -> ReplayedIntake:
    """Requalify every accepted snapshot in order; refuse before returning authority."""

    store = EventStore(state, run_id=run_id)
    events = store.events()
    evidence = IntakeEvidenceReader(state, run_id)
    current = ReplayedIntake(None, PriorFence.genesis(profile_digest), ())
    terminals = [
        event
        for event in events
        if event.event_type == INTAKE_DECISION_RECORDED and event.payload.get("record") == IntakeRecord.SETTLED.value
    ]
    for terminal in terminals:
        attempt_id = str(terminal.payload["attempt_id"])
        selected = [
            event
            for event in events
            if event.event_type in {INTAKE_DECISION_RECORDED, INTAKE_OBSERVATION_RECORDED}
            and event.payload.get("attempt_id") == attempt_id
        ]
        starts = [event for event in selected if event.payload.get("record") == IntakeRecord.STARTED.value]
        attempt_terminals = [
            event
            for event in selected
            if event.event_type == INTAKE_DECISION_RECORDED
            and event.payload.get("record") != IntakeRecord.STARTED.value
        ]
        if len(starts) != 1 or attempt_terminals != [terminal]:
            raise ValueError("canonical Intake replay found an ambiguous attempt lifecycle")
        start = starts[0]
        if start.sequence >= terminal.sequence or start.payload["expected_fence"] != current.fence.document():
            raise ValueError("canonical Intake replay found discontinuous snapshot authority")
        start_body = json.loads(start.body)
        if not isinstance(start_body, dict):
            raise ValueError("canonical Intake replay start body is invalid")
        contract = contract_from_document(start_body.get("contract"))
        if contract.profile_digest != profile_digest or terminal.payload["profile_digest"] != profile_digest:
            raise ValueError("canonical Intake replay profile differs from current authority")
        observations = [event for event in selected if event.event_type == INTAKE_OBSERVATION_RECORDED]
        closure = [event.payload["classified_event_id"] for event in observations]
        if terminal.payload["observation_ids"] != closure:
            raise ValueError("canonical Intake replay observation closure is incomplete")
        published = snapshot_from_document(json.loads(terminal.body))
        probe = IntakeProbe(
            attempt_id,
            contract,
            current.fence,
            _passes(observations, evidence),
            published.observed_at,
            current.snapshot,
        )
        decision = qualify(probe)
        if not decision.settled or decision.snapshot is None:
            raise ValueError("canonical Intake replay evidence no longer qualifies")
        if decision.snapshot.document() != published.document():
            raise ValueError("canonical Intake replay projection differs from its publication")
        verify_canonical_attempt(state, run_id, probe, decision)
        current = ReplayedIntake(
            decision.snapshot,
            PriorFence(
                profile_digest,
                str(terminal.payload["event_id"]),
                terminal.event_digest,
                decision.snapshot.digest,
            ),
            (*current.history, decision.snapshot),
        )
    return current


def _passes(
    observations: list[CommittedEvent],
    evidence: IntakeEvidenceReader,
) -> tuple[IntakePass, IntakePass]:
    passes = []
    for pass_no in (1, 2):
        documents = [_document(event, evidence) for event in observations if event.payload["pass"] == pass_no]
        identities = [item for item in documents if item.kind == "identity"]
        if len(identities) != 2:
            raise ValueError("canonical Intake replay pass has no authentication bracket")
        by_kind = {
            kind: tuple(item for item in documents if item.kind == kind)
            for kind in ("list", "detail", "attachment", "absence")
        }
        landing = tuple(item for item in documents if item.kind == "landing")
        read_control = tuple(item for item in documents if item.kind == "read-control")
        scoreboard = tuple(item for item in documents if item.kind == "scoreboard")
        mana = tuple(item for item in documents if item.kind == "mana")
        if any(len(items) > 1 for items in (landing, read_control, scoreboard, mana)):
            raise ValueError("canonical Intake replay pass has duplicate controls")
        reconstructed = IntakePass(
            pass_no,
            identities[0],
            by_kind["list"],
            by_kind["detail"],
            by_kind["attachment"],
            identities[1],
            by_kind["absence"],
            landing[0] if landing else None,
            read_control[0] if read_control else None,
            scoreboard[0] if scoreboard else None,
            mana[0] if mana else None,
        )
        expected_ids = [item.classified_event_id for item in documents]
        if [item.classified_event_id for item in reconstructed.documents] != expected_ids:
            raise ValueError("canonical Intake replay document order is non-canonical")
        passes.append(reconstructed)
    return passes[0], passes[1]


def _document(event: CommittedEvent, evidence: IntakeEvidenceReader) -> IntakeDocument:
    payload = event.payload
    private_digest = str(payload["raw_blob_digest"])
    raw = (
        evidence.read(private_digest, classified_event_id=str(payload["classified_event_id"]))
        if private_digest
        else event.body
    )
    return IntakeDocument(
        request_id=str(payload["request_id"]),
        classified_event_id=str(payload["classified_event_id"]),
        pass_no=int(payload["pass"]),
        kind=str(payload["kind"]),
        endpoint=str(payload["endpoint"]),
        status=int(payload["status"]),
        content_type=str(payload["content_type"]),
        raw=raw,
        original_bytes=int(payload["original_bytes"]),
        complete=bool(payload["complete"]),
        profile_digest=str(payload["profile_digest"]),
        subject_digest=str(payload["subject_digest"]),
        capability_digest=str(payload["capability_digest"]),
        peer_digest=str(payload["peer_digest"]),
        page=int(payload["page"]),
        challenge_id=payload["challenge_id"],
        outcome=str(payload["outcome"]),
        location=str(payload["location"]),
        raw_blob_digest=private_digest,
        sanitized_blob_digest=str(payload["sanitized_blob_digest"]),
        request_digest=str(payload["request_digest"]),
        hop=int(payload["hop"]),
        auth_forwarded=bool(payload["auth_forwarded"]),
        resource_identity=str(payload["resource_identity"]),
    )


__all__ = ["ReplayedIntake", "replay_intake"]
