"""Pure deterministic projection of generation-fenced Lead transitions."""

from __future__ import annotations

from typing import Any

from solver.event_store import GenerationAuthority, InvalidEventError
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_contracts import (
    LEAD_ENGAGEMENT_RECORDED,
    ROLE,
    LeadClassification,
    LeadRequest,
    LeadState,
    LeadTransition,
    TurnMeasure,
)


def primary_events(events: list[Any], engagement_id: str | None = None) -> list[Any]:
    selected = [
        event
        for event in events
        if event.event_type == LEAD_ENGAGEMENT_RECORDED
        and event.payload["classification"] != LeadClassification.CONFLICT.value
    ]
    if engagement_id is not None:
        selected = [event for event in selected if event.payload["engagement_id"] == engagement_id]
    return selected


def empty_state(run_id: str, request: LeadRequest) -> LeadState:
    return LeadState(run_id, request.engagement_id, request.generation_id, ROLE, 0, None, "", ())


def transition_digest(
    previous_digest: str,
    request_digest: str,
    classification: LeadClassification,
    proposal_kind: str = "",
    proposal_digest: str = "",
    disposition: str = "",
) -> str:
    basis = {
        "previous_transition_digest": previous_digest,
        "request_digest": request_digest,
        "classification": classification.value,
        "proposal_kind": proposal_kind,
        "proposal_digest": proposal_digest,
        "disposition": disposition,
    }
    return digest_bytes(canonical_bytes(basis))


def project_lead(events: list[Any], run_id: str, engagement_id: str | None = None) -> LeadState:
    """Rebuild one Engagement using only verified canonical events."""

    selected = primary_events(events, engagement_id)
    identities = {event.payload["engagement_id"] for event in selected}
    if not selected:
        raise InvalidEventError("Lead projection has no Engagement")
    if len(identities) != 1:
        raise InvalidEventError("Lead projection requires one Engagement identity")
    identity = next(iter(identities))
    generation_id = selected[0].payload["generation_id"]
    fences = [
        event
        for event in events
        if event.event_type == "work-generation.recorded"
        and event.payload["record"] == "authority"
        and event.payload["authority"] == GenerationAuthority.AUTHORITY.value
        and event.payload["generation_id"] == generation_id
    ]
    _consume_fences(selected, fences)
    transitions: list[LeadTransition] = []
    disposition: str | None = None
    previous_digest = ""
    for expected_turn, event in enumerate(selected, start=1):
        payload = event.payload
        sequence = event.sequence
        if payload["engagement_id"] != identity or payload["generation_id"] != generation_id:
            raise InvalidEventError("Lead Engagement changed identity", sequence=sequence)
        if payload["turn_index"] != expected_turn:
            raise InvalidEventError("Lead Engagement turn sequence has a gap", sequence=sequence)
        if disposition is not None:
            raise InvalidEventError("Lead Engagement transitioned after a terminal proposal", sequence=sequence)
        classification = LeadClassification(payload["classification"])
        expected_digest = transition_digest(
            previous_digest,
            payload["request_digest"],
            classification,
            payload["proposal_kind"],
            payload["proposal_digest"],
            payload["disposition"],
        )
        if payload["transition_digest"] != expected_digest:
            raise InvalidEventError("Lead transition digest disagrees with replay", sequence=sequence)
        transitions.append(_transition(event, classification, expected_digest))
        disposition = payload["disposition"] or None
        previous_digest = expected_digest
    return LeadState(
        run_id,
        identity,
        generation_id,
        ROLE,
        len(transitions),
        disposition,
        previous_digest,
        tuple(transitions),
    )


def _consume_fences(selected: list[Any], fences: list[Any]) -> None:
    for event in selected:
        fence = next((candidate for candidate in fences if candidate.sequence < event.sequence), None)
        if fence is None:
            raise InvalidEventError("Lead transition has no generation fence", sequence=event.sequence)
        fences.remove(fence)


def _transition(event: Any, classification: LeadClassification, transition_digest: str) -> LeadTransition:
    payload = event.payload
    return LeadTransition(
        sequence=event.sequence,
        event_id=payload["event_id"],
        turn_index=payload["turn_index"],
        classification=classification,
        proposal_kind=payload["proposal_kind"],
        proposal_digest=payload["proposal_digest"],
        context_bytes=payload["context_bytes"],
        output_bytes=payload["output_bytes"],
        measure=TurnMeasure(
            model=payload["model"],
            duration_ms=payload["duration_ms"],
            tokens_in=payload["tokens_in"],
            tokens_out=payload["tokens_out"],
            usage_known=payload["usage_known"],
        ),
        transition_digest=transition_digest,
    )


__all__ = ["empty_state", "primary_events", "project_lead", "transition_digest"]
