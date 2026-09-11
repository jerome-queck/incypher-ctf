"""Canonical Lead request, turn, and proposal encoding."""

from __future__ import annotations

from dataclasses import asdict

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_contracts import (
    BoardProposal,
    CandidateProposal,
    LeadInitialContext,
    LeadProposal,
    LeadProposalResult,
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
        "binding": request.binding.document(),
        "turn_index": request.turn_index,
        "input": input_document(request),
    }


def input_document(request: LeadRequest) -> dict[str, object]:
    if isinstance(request.input, LeadInitialContext):
        return {"kind": "initial", "context": request.input.context}
    result = request.input.result
    return {
        "kind": "resume",
        "delta": request.input.delta,
        "result": result_document(result) if result is not None else None,
    }


def result_document(result: LeadProposalResult) -> dict[str, object]:
    return {
        "proposal_id": result.proposal_id,
        "status": result.status.value,
        "evidence_refs": list(result.evidence_refs),
        "detail": result.detail,
    }


def request_digest(request: LeadRequest) -> str:
    return digest_bytes(canonical_bytes(request_document(request)))


def proposal_kind(proposal: LeadProposal) -> str:
    return PROPOSAL_CONTRACTS[type(proposal)][0]


def proposal_disposition(proposal: LeadProposal) -> str:
    return PROPOSAL_CONTRACTS[type(proposal)][1]


def proposal_requires_result(proposal: LeadProposal) -> bool:
    return PROPOSAL_CONTRACTS[type(proposal)][2]


def proposal_document(proposal: LeadProposal) -> dict[str, object]:
    return {"kind": proposal_kind(proposal), **asdict(proposal)}


def decode_proposal(document: object) -> LeadProposal:
    if not isinstance(document, dict):
        raise ValueError("proposal is not an object")
    values = dict(document)
    kind = values.pop("kind", None)
    contract = next((item for item, (name, _, _) in PROPOSAL_CONTRACTS.items() if name == kind), None)
    if contract is None:
        raise ValueError("proposal kind is unsupported")
    for field in ("arguments", "evidence_refs"):
        if field in values and isinstance(values[field], list):
            values[field] = tuple(values[field])
    proposal = contract(**values)
    if not valid_proposal(proposal):
        raise ValueError("proposal is invalid")
    return proposal


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
    "decode_proposal",
    "input_document",
    "model_output_document",
    "proposal_disposition",
    "proposal_document",
    "proposal_kind",
    "proposal_requires_result",
    "result_document",
    "request_digest",
    "request_document",
    "text",
    "turn_document",
    "valid_proposal",
]
