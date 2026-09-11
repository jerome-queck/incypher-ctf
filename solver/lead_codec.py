"""Canonical Lead request, turn, and proposal encoding."""

from __future__ import annotations

from dataclasses import asdict

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_contracts import (
    BoardProposal,
    CandidateProposal,
    LeadProposal,
    LeadRequest,
    MeasuredLeadTurn,
    ProgressProposal,
    PROPOSAL_CONTRACTS,
    ResearchProposal,
    StopProposal,
    TargetProposal,
    ToolProposal,
)


PROPOSAL_TYPES = tuple(PROPOSAL_CONTRACTS)


def request_document(request: LeadRequest) -> dict[str, object]:
    return {
        "engagement_id": request.engagement_id,
        "generation_id": request.generation_id,
        "turn_index": request.turn_index,
        "context": request.context,
    }


def request_digest(request: LeadRequest) -> str:
    return digest_bytes(canonical_bytes(request_document(request)))


def proposal_kind(proposal: LeadProposal) -> str:
    return PROPOSAL_CONTRACTS[type(proposal)][0]


def proposal_disposition(proposal: LeadProposal) -> str:
    return PROPOSAL_CONTRACTS[type(proposal)][1]


def proposal_document(proposal: LeadProposal) -> dict[str, object]:
    return {"kind": proposal_kind(proposal), **asdict(proposal)}


def model_output_document(turn: MeasuredLeadTurn) -> dict[str, object]:
    return {
        "approach": turn.approach,
        "proposal": proposal_document(turn.proposal),
        "measure": asdict(turn.measure) if turn.measure is not None else None,
    }


def turn_document(request: LeadRequest, turn: MeasuredLeadTurn) -> dict[str, object]:
    return {"request": request_document(request), "turn": model_output_document(turn)}


def valid_proposal(proposal: LeadProposal) -> bool:
    if isinstance(proposal, BoardProposal):
        return _text(proposal.operation) and _text(proposal.reference)
    if isinstance(proposal, TargetProposal):
        return _text(proposal.operation) and _text(proposal.target_ref) and isinstance(proposal.payload, str)
    if isinstance(proposal, ResearchProposal):
        return _text(proposal.query)
    if isinstance(proposal, ToolProposal):
        return _text(proposal.tool) and _text_tuple(proposal.arguments, empty=True)
    if isinstance(proposal, CandidateProposal):
        return _text(proposal.value) and _text_tuple(proposal.evidence_refs)
    if isinstance(proposal, ProgressProposal):
        return _text(proposal.detail)
    if isinstance(proposal, StopProposal):
        return _text(proposal.reason)
    return False


def text(value: object) -> bool:
    return _text(value)


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _text_tuple(value: object, *, empty: bool = False) -> bool:
    return (
        isinstance(value, tuple)
        and (empty or bool(value))
        and all(isinstance(item, str) and bool(item) for item in value)
    )


__all__ = [
    "PROPOSAL_TYPES",
    "model_output_document",
    "proposal_disposition",
    "proposal_document",
    "proposal_kind",
    "request_digest",
    "request_document",
    "text",
    "turn_document",
    "valid_proposal",
]
