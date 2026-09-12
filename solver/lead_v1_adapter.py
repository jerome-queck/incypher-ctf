"""Narrow migration adapter from the v1 Run Turn into one Lead controller."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from solver.carry import Boundary
from solver.codex import Credential, Invocation
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_contracts import LeadBinding, LeadInitialContext, LeadOutcome, LeadRequest, LeadResume
from solver.lead_controller import LeadController


@dataclass(frozen=True)
class V1LeadTurn:
    prompt: str
    boundary: Boundary
    chain: tuple[Credential, ...]
    invocation: Invocation
    run_id: str
    boot_id: str
    generation_id: str
    lane_id: str
    attempt_id: str
    work_id: str
    budget_seconds: int
    started_at: dt.datetime
    deadline: dt.datetime


class V1LeadAdapter:
    """Bind v1's real prompt, carry and route facts to the pure Lead seam."""

    def __init__(self, controller: LeadController, *, harness: str = "native-codex", route: str | None = None) -> None:
        self._controller = controller
        self._harness = harness
        self._route = route
        self._bindings: dict[str, LeadBinding] = {}
        self._turns: dict[str, int] = {}

    def __call__(self, incoming: V1LeadTurn) -> LeadOutcome:
        if not incoming.chain:
            raise ValueError("Lead adapter requires a selected model route")
        binding = self._bindings.get(incoming.attempt_id)
        if binding is None:
            binding = self._binding(incoming)
            self._bindings[incoming.attempt_id] = binding
        turn_index = self._turns.get(incoming.attempt_id, 0) + 1
        request = LeadRequest(
            binding,
            turn_index,
            LeadInitialContext(incoming.prompt) if turn_index == 1 else LeadResume(incoming.prompt),
        )
        outcome = self._controller.handle(request)
        if outcome.classification.value in {"accepted", "already-accepted"}:
            self._turns[incoming.attempt_id] = turn_index
        return outcome

    def _binding(self, incoming: V1LeadTurn) -> LeadBinding:
        selected = incoming.chain[0]
        prompt_digest = digest_bytes(incoming.prompt.encode())
        carry = [line.render() for line in incoming.boundary.lines]
        empty_digest = digest_bytes(canonical_bytes([]))
        return LeadBinding(
            run_id=incoming.run_id,
            boot_id=incoming.boot_id,
            generation_id=incoming.generation_id,
            lane_id=incoming.lane_id,
            attempt_id=incoming.attempt_id,
            work_id=incoming.work_id,
            engagement_id=f"{incoming.attempt_id}:solve-lead",
            owner_id="run-controller",
            parent_engagement_id="",
            context_digest=prompt_digest,
            evidence_digest=digest_bytes(canonical_bytes({"carry": carry})),
            prompt_bundle_digest=prompt_digest,
            playbook_digest=empty_digest,
            tool_schema_digest=digest_bytes(
                canonical_bytes({"proposals": ["board", "target", "research", "tool", "candidate", "progress", "stop"]})
            ),
            capability_digest=digest_bytes(canonical_bytes({"authority": "proposal-only"})),
            harness=self._harness,
            requested_route=self._route or selected.slot,
            requested_model=selected.model,
            selected_model=selected.model,
            effective_model=selected.model,
            requested_effort=incoming.invocation.reasoning_effort,
            effective_effort=incoming.invocation.reasoning_effort,
            catalog_digest=digest_bytes(canonical_bytes([item.model for item in incoming.chain])),
            admitted_budget=incoming.budget_seconds,
            deadline=incoming.deadline.isoformat(),
            started_at=incoming.started_at.isoformat(),
        )


__all__ = ["V1LeadAdapter", "V1LeadTurn"]
