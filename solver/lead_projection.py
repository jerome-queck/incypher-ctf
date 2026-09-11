"""Pure deterministic projection of generation-fenced Lead Engagements."""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

from solver.event_store import GenerationAuthority, InvalidEventError
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_codec import decode_proposal, proposal_kind
from solver.lead_contracts import (
    LEAD_ENGAGEMENT_RECORDED,
    LeadBinding,
    LeadClassification,
    LeadProposalResult,
    LeadRecord,
    LeadState,
    LeadTransition,
    ProposalResultStatus,
    TurnMeasure,
)


def lead_events(events: list[Any], engagement_id: str | None = None) -> list[Any]:
    selected = [event for event in events if event.event_type == LEAD_ENGAGEMENT_RECORDED]
    if engagement_id is not None:
        selected = [event for event in selected if event.payload["binding"]["engagement_id"] == engagement_id]
    return selected


def primary_events(events: list[Any], engagement_id: str | None = None) -> list[Any]:
    return [event for event in lead_events(events, engagement_id) if event.payload["record"] == LeadRecord.TURN.value]


def engagement_ids(events: list[Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(event.payload["binding"]["engagement_id"] for event in lead_events(events)))


def transition_digest(
    previous_digest: str,
    request_digest: str,
    classification: LeadClassification,
    proposal_id: str = "",
    proposal_kind: str = "",
    proposal_digest: str = "",
    result_digest: str = "",
    disposition: str = "",
) -> str:
    return digest_bytes(
        canonical_bytes(
            {
                "previous_transition_digest": previous_digest,
                "request_digest": request_digest,
                "classification": classification.value,
                "proposal_id": proposal_id,
                "proposal_kind": proposal_kind,
                "proposal_digest": proposal_digest,
                "result_digest": result_digest,
                "disposition": disposition,
            }
        )
    )


def project_lead(events: list[Any], run_id: str, engagement_id: str | None = None) -> LeadState:
    selected = lead_events(events, engagement_id)
    if not selected:
        raise InvalidEventError("Lead projection has no Engagement")
    identities = {event.payload["binding"]["engagement_id"] for event in selected}
    if len(identities) != 1:
        raise InvalidEventError("Lead projection requires one Engagement identity")
    binding_document = selected[0].payload["binding"]
    binding = LeadBinding(**binding_document)
    if binding.run_id != run_id:
        raise InvalidEventError("Lead Engagement belongs to another Run")

    _verify_authority(events, selected, run_id)
    admitted: dict[int, Any] = {}
    transitions: list[LeadTransition] = []
    disposition: str | None = None
    outstanding_id = ""
    outstanding_kind = ""
    previous_digest = ""
    for event in selected:
        payload = event.payload
        sequence = event.sequence
        if payload["binding"] != binding_document:
            if payload["record"] == LeadRecord.QUARANTINED.value:
                continue
            raise InvalidEventError("Lead Engagement binding drifted", sequence=sequence)
        record = LeadRecord(payload["record"])
        if record is LeadRecord.QUARANTINED:
            continue
        turn_index = payload["turn_index"]
        if record is LeadRecord.ADMITTED:
            if turn_index in admitted or turn_index != len(transitions) + 1:
                raise InvalidEventError("Lead admission sequence has a gap", sequence=sequence)
            if disposition is not None:
                raise InvalidEventError("Lead Engagement admitted after terminal", sequence=sequence)
            if turn_index == 1:
                request = json.loads(event.body)["request"]
                if request["input"]["kind"] != "initial":
                    raise InvalidEventError("Lead first Turn is not initial", sequence=sequence)
            else:
                request = json.loads(event.body)["request"]
                if request["input"]["kind"] != "resume":
                    raise InvalidEventError("Lead continuation is not a resume", sequence=sequence)
            if digest_bytes(canonical_bytes(request)) != payload["request_digest"]:
                raise InvalidEventError("Lead admission request digest disagrees", sequence=sequence)
            if request.get("binding") != binding_document or request.get("turn_index") != turn_index:
                raise InvalidEventError("Lead admission request binding disagrees", sequence=sequence)
            result = _decode_result(request["input"].get("result"))
            result_digest = digest_bytes(canonical_bytes(_result_document(result))) if result else ""
            if result_digest != payload["result_digest"]:
                raise InvalidEventError("Lead admission result digest disagrees", sequence=sequence)
            if outstanding_id:
                if result is None or result.proposal_id != outstanding_id:
                    raise InvalidEventError("Lead resume does not resolve its outstanding proposal", sequence=sequence)
                outstanding_id = ""
                outstanding_kind = ""
            elif result is not None:
                raise InvalidEventError("Lead resume supplies an unexpected result", sequence=sequence)
            admitted[turn_index] = (event, result)
            continue

        if turn_index not in admitted or turn_index != len(transitions) + 1:
            raise InvalidEventError("Lead Turn has no matching admission", sequence=sequence)
        admission, result = admitted.pop(turn_index)
        if admission.payload["request_digest"] != payload["request_digest"]:
            raise InvalidEventError("Lead admission and Turn disagree", sequence=sequence)
        classification = LeadClassification(payload["classification"])
        if classification is LeadClassification.ACCEPTED:
            _verify_turn_body(event)
        expected = transition_digest(
            previous_digest,
            payload["request_digest"],
            classification,
            payload["proposal_id"],
            payload["proposal_kind"],
            payload["proposal_digest"],
            payload["result_digest"],
            payload["disposition"],
        )
        if payload["transition_digest"] != expected:
            raise InvalidEventError("Lead transition digest disagrees with replay", sequence=sequence)
        if bool(result) != bool(payload["result_digest"]):
            raise InvalidEventError("Lead result digest presence disagrees", sequence=sequence)
        if result and digest_bytes(canonical_bytes(_result_document(result))) != payload["result_digest"]:
            raise InvalidEventError("Lead result digest disagrees", sequence=sequence)
        transitions.append(_transition(event, classification, result))
        if payload["proposal_kind"] in {"board", "target", "research", "tool"}:
            outstanding_id = payload["proposal_id"]
            outstanding_kind = payload["proposal_kind"]
        disposition = payload["disposition"] or None
        previous_digest = expected

    return LeadState(
        run_id,
        binding,
        len(transitions),
        disposition,
        outstanding_id,
        outstanding_kind,
        previous_digest,
        tuple(transitions),
    )


def _verify_authority(events: list[Any], selected: list[Any], run_id: str) -> None:
    by_sequence = {event.sequence: event for event in events}
    references = Counter(
        event.payload["authority_sequence"] for event in events if event.event_type == LEAD_ENGAGEMENT_RECORDED
    )
    consumed = set()
    for event in selected:
        authority_sequence = event.payload["authority_sequence"]
        authority = by_sequence.get(authority_sequence)
        if (
            authority_sequence in consumed
            or references[authority_sequence] != 1
            or authority is None
            or authority.sequence >= event.sequence
        ):
            raise InvalidEventError("Lead fact has no exact generation authority", sequence=event.sequence)
        binding = event.payload["binding"]
        if not (
            authority.event_type == "work-generation.recorded"
            and event.payload["authority_run_id"] == run_id
            and event.payload["authority_run_id"] == binding["run_id"]
            and authority.payload["record"] == "authority"
            and authority.payload["authority"] == GenerationAuthority.AUTHORITY.value
            and authority.payload["event_id"] == event.payload["authority_event_id"]
            and authority.payload["generation_id"] == binding["generation_id"]
            and authority.payload["work_id"] == binding["work_id"]
            and authority.payload["attempt_id"] == binding["attempt_id"]
        ):
            raise InvalidEventError("Lead fact authority binding disagrees", sequence=event.sequence)
        consumed.add(authority_sequence)


def _decode_result(document: object) -> LeadProposalResult | None:
    if document is None:
        return None
    if not isinstance(document, dict):
        raise InvalidEventError("Lead result is malformed")
    try:
        return LeadProposalResult(
            proposal_id=document["proposal_id"],
            status=ProposalResultStatus(document["status"]),
            evidence_refs=tuple(document["evidence_refs"]),
            detail=document["detail"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise InvalidEventError("Lead result is malformed") from error


def _verify_turn_body(event: Any) -> None:
    payload = event.payload
    try:
        document = json.loads(event.body)
        request = document["request"]
        turn = document["turn"]
        proposal_document = turn["proposal"]
        proposal = decode_proposal(proposal_document)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise InvalidEventError("Lead accepted Turn body is malformed", sequence=event.sequence) from error
    if digest_bytes(canonical_bytes(request)) != payload["request_digest"]:
        raise InvalidEventError("Lead accepted Turn request digest disagrees", sequence=event.sequence)
    if digest_bytes(canonical_bytes(proposal_document)) != payload["proposal_digest"]:
        raise InvalidEventError("Lead accepted proposal digest disagrees", sequence=event.sequence)
    if proposal_kind(proposal) != payload["proposal_kind"]:
        raise InvalidEventError("Lead accepted proposal kind disagrees", sequence=event.sequence)
    if payload["proposal_id"] != f"{payload['binding']['engagement_id']}:proposal-{payload['turn_index']:06d}":
        raise InvalidEventError("Lead proposal identity is not controller-derived", sequence=event.sequence)
    if len(canonical_bytes(turn)) != payload["output_bytes"]:
        raise InvalidEventError("Lead accepted output measure disagrees", sequence=event.sequence)
    measure = turn.get("measure")
    if not isinstance(measure, dict) or measure != {
        "model": payload["model"],
        "duration_ms": payload["duration_ms"],
        "tokens_in": payload["tokens_in"],
        "tokens_out": payload["tokens_out"],
        "usage_known": payload["usage_known"],
    }:
        raise InvalidEventError("Lead accepted model measure disagrees", sequence=event.sequence)


def _result_document(result: LeadProposalResult) -> dict[str, object]:
    return {
        "proposal_id": result.proposal_id,
        "status": result.status.value,
        "evidence_refs": list(result.evidence_refs),
        "detail": result.detail,
    }


def _transition(event: Any, classification: LeadClassification, result: LeadProposalResult | None) -> LeadTransition:
    payload = event.payload
    return LeadTransition(
        sequence=event.sequence,
        event_id=payload["event_id"],
        turn_index=payload["turn_index"],
        classification=classification,
        proposal_id=payload["proposal_id"],
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
        result=result,
        transition_digest=payload["transition_digest"],
    )


__all__ = ["engagement_ids", "lead_events", "primary_events", "project_lead", "transition_digest"]
