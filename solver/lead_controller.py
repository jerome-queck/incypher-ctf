"""One generation-fenced owner for Solve Lead Engagement transitions."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from solver.event_store import EventStore, GenerationAuthority, InvalidEventError
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_codec import (
    PROPOSAL_TYPES,
    decode_proposal,
    model_output_document,
    proposal_disposition,
    proposal_document,
    proposal_kind,
    request_digest,
    request_document,
    result_document,
    valid_proposal,
)
from solver.lead_contracts import (
    CONTROLLER_OWNER,
    MAX_APPROACH_BYTES,
    LeadBinding,
    LeadClassification,
    LeadEngagementRecorded,
    LeadInitialContext,
    LeadModel,
    LeadOutcome,
    LeadProposalResult,
    LeadRecord,
    LeadRequest,
    LeadResume,
    LeadState,
    MeasuredLeadTurn,
    ProposalResultStatus,
    TurnMeasure,
    validate_binding,
)
from solver.lead_projection import lead_events, primary_events, project_lead, transition_digest
from solver.redaction import Redactor
from solver.work_generation import GenerationFence, UnknownGeneration
from solver.specialist_contracts import (
    SpecialistCarry,
    SpecialistEvidence,
    SpecialistInvoke,
    SpecialistResult,
    SpecialistTask,
)
from solver.specialist_pool import SpecialistPool


class LeadController:
    """Admit exact input, invoke one model Turn, then durably record its proposal."""

    def __init__(
        self,
        state: Path,
        run_id: str,
        redactor: Redactor,
        timestamp,
        model: LeadModel,
        *,
        fence: GenerationFence | None = None,
        specialists: SpecialistPool | None = None,
    ) -> None:
        self._state = Path(state)
        self.store = EventStore(state, run_id=run_id, redactor=redactor)
        self.run_id = run_id
        self._timestamp = timestamp
        self._model = model
        self._fence = fence or GenerationFence(state, run_id, redactor, timestamp)
        self._specialists = specialists
        if self._fence.run_id != run_id:
            raise ValueError("Lead controller and generation fence name different Runs")
        if specialists is not None and (
            specialists.run_id != run_id
            or specialists.state.resolve() != self._state.resolve()
            or specialists.generations is not self._fence
        ):
            raise ValueError("Lead controller and Specialist pool name different canonical authority")

    def dispatch_specialists(
        self,
        tasks: tuple[SpecialistTask, ...],
        invoke: SpecialistInvoke,
        *,
        cancelled: Callable[[], bool],
        select: Callable[[SpecialistResult], bool],
        carry: SpecialistCarry,
        generation_id: str,
        attempt_id: str,
        engagement_id: str,
    ) -> tuple[SpecialistEvidence, ...]:
        """Dispatch Lead-authored briefs through the controller-owned bounded pool."""
        if self._specialists is None:
            if tasks:
                raise ValueError("selected profile enables no Specialists")
            return ()
        if any(
            task.generation_id != generation_id
            or task.attempt_id != attempt_id
            or task.parent_engagement_id != engagement_id
            for task in tasks
        ):
            raise ValueError("Specialist batch crosses Lead Engagement ownership")
        if not any(
            state.generation_id == generation_id and state.attempt_id == attempt_id and state.active
            for state in self._fence.projection().generations
        ):
            raise ValueError("Specialist batch names no current Lead generation")
        batch = self._specialists.dispatch(tasks, invoke, cancelled=cancelled)
        return carry.accept_results(
            batch.results,
            batch.evidence,
            select,
            generation_id=generation_id,
        )

    @property
    def model_status(self) -> str | None:
        return getattr(self._model, "last_status", None)

    def handle(self, request: LeadRequest) -> LeadOutcome:
        self._validate_request(request)
        binding_rejection = self._binding_rejection(request.binding)
        if binding_rejection is not None:
            classification, detail = binding_rejection
            return LeadOutcome(classification, self._empty_state(request.binding), detail=detail)
        fingerprint = request_digest(request)
        events = self.store.events()
        engagement = lead_events(events, request.engagement_id)
        existing = next(
            (
                event
                for event in engagement
                if event.payload["record"] == LeadRecord.TURN.value
                and event.payload["turn_index"] == request.turn_index
            ),
            None,
        )
        if existing is not None:
            state = project_lead(events, self.run_id, request.engagement_id)
            if existing.payload["request_digest"] == fingerprint:
                proposal = self._decode_recorded_proposal(existing)
                self._write_receipt()
                return LeadOutcome(
                    LeadClassification.DUPLICATE,
                    state,
                    proposal,
                    existing.payload["proposal_id"],
                    "turn already recorded",
                )
            return self._quarantine(request, fingerprint, state, LeadClassification.CONFLICT, "turn identity reused")

        admitted = next(
            (
                event
                for event in engagement
                if event.payload["record"] == LeadRecord.ADMITTED.value
                and event.payload["turn_index"] == request.turn_index
            ),
            None,
        )
        if admitted is not None:
            state = self._state_or_empty(request.binding)
            classification = (
                LeadClassification.LATE
                if admitted.payload["request_digest"] == fingerprint
                else LeadClassification.CONFLICT
            )
            return LeadOutcome(classification, state, detail="turn was admitted without a durable result")

        primary = primary_events(events, request.engagement_id)
        state = (
            project_lead(events, self.run_id, request.engagement_id) if primary else self._empty_state(request.binding)
        )
        if primary and state.binding.document() != request.binding.document():
            return self._quarantine(request, fingerprint, state, LeadClassification.CONFLICT, "binding drift")
        if state.disposition is not None:
            return LeadOutcome(LeadClassification.LATE, state, detail="Engagement is terminal")
        if request.turn_index != state.turn_count + 1:
            return LeadOutcome(LeadClassification.MALFORMED, state, detail="turn sequence gap")

        invalid = self._validate_input(request, state)
        if invalid is not None:
            classification, detail = invalid
            return self._quarantine(request, fingerprint, state, classification, detail)
        admission_body = canonical_bytes({"request": request_document(request)})
        if len(admission_body) > request.binding.max_turn_bytes:
            return self._quarantine(
                request, fingerprint, state, LeadClassification.OVERSIZED, "admission exceeds bound"
            )
        admission = self._append_fenced(
            request,
            lambda grant: LeadEngagementRecorded(
                event_id=f"{request.engagement_id}:turn-{request.turn_index:06d}:admitted",
                record=LeadRecord.ADMITTED,
                binding=request.binding,
                authority_run_id=grant.run_id,
                authority_event_id=grant.event_id,
                authority_sequence=grant.sequence,
                turn_index=request.turn_index,
                classification=LeadClassification.ACCEPTED,
                request_digest=fingerprint,
                result_digest=self._result_digest(request),
                context_bytes=len(request.context.encode()),
                ts=self._timestamp(),
            ),
            admission_body,
            fingerprint,
        )
        if not admission:
            return LeadOutcome(LeadClassification.LATE, state, detail="generation is not current")

        try:
            turn = self._model(request)
        except Exception:
            return self._record_turn_rejection(
                request, fingerprint, state, LeadClassification.MALFORMED, "model failed"
            )
        classification, detail = self._validate_turn(turn, request.binding)
        if classification is not LeadClassification.ACCEPTED:
            return self._record_turn_rejection(request, fingerprint, state, classification, detail)
        assert isinstance(turn, MeasuredLeadTurn)
        model_output = canonical_bytes(model_output_document(turn))
        body = canonical_bytes({"request": request_document(request), "turn": model_output_document(turn)})
        if len(body) > request.binding.max_turn_bytes:
            return self._record_turn_rejection(
                request, fingerprint, state, LeadClassification.OVERSIZED, "turn exceeds bound"
            )
        proposal_body = proposal_document(turn.proposal)
        proposal_fingerprint = digest_bytes(canonical_bytes(proposal_body))
        proposal_id = f"{request.engagement_id}:proposal-{request.turn_index:06d}"
        disposition = proposal_disposition(turn.proposal)
        result_fingerprint = self._result_digest(request)
        transition_fingerprint = transition_digest(
            state.transition_digest,
            fingerprint,
            LeadClassification.ACCEPTED,
            proposal_id,
            proposal_kind(turn.proposal),
            proposal_fingerprint,
            result_fingerprint,
            disposition,
        )
        measure = turn.measure
        assert measure is not None
        committed = self._append_fenced(
            request,
            lambda grant: LeadEngagementRecorded(
                event_id=f"{request.engagement_id}:turn-{request.turn_index:06d}:result",
                record=LeadRecord.TURN,
                binding=request.binding,
                authority_run_id=grant.run_id,
                authority_event_id=grant.event_id,
                authority_sequence=grant.sequence,
                turn_index=request.turn_index,
                classification=LeadClassification.ACCEPTED,
                request_digest=fingerprint,
                transition_digest=transition_fingerprint,
                proposal_id=proposal_id,
                proposal_kind=proposal_kind(turn.proposal),
                proposal_digest=proposal_fingerprint,
                result_digest=result_fingerprint,
                context_bytes=len(request.context.encode()),
                output_bytes=len(model_output),
                model=measure.model,
                duration_ms=measure.duration_ms,
                tokens_in=measure.tokens_in,
                tokens_out=measure.tokens_out,
                usage_known=measure.usage_known,
                disposition=disposition,
                ts=self._timestamp(),
            ),
            body,
            digest_bytes(model_output),
        )
        if not committed:
            return LeadOutcome(LeadClassification.LATE, state, detail="generation closed after admission")
        self._write_receipt()
        projected = project_lead(self.store.events(), self.run_id, request.engagement_id)
        return LeadOutcome(LeadClassification.ACCEPTED, projected, turn.proposal, proposal_id)

    def _record_turn_rejection(
        self,
        request: LeadRequest,
        fingerprint: str,
        state: LeadState,
        classification: LeadClassification,
        detail: str,
    ) -> LeadOutcome:
        result_fingerprint = self._result_digest(request)
        transition_fingerprint = transition_digest(
            state.transition_digest,
            fingerprint,
            classification,
            result_digest=result_fingerprint,
        )
        committed = self._append_fenced(
            request,
            lambda grant: LeadEngagementRecorded(
                event_id=f"{request.engagement_id}:turn-{request.turn_index:06d}:result",
                record=LeadRecord.TURN,
                binding=request.binding,
                authority_run_id=grant.run_id,
                authority_event_id=grant.event_id,
                authority_sequence=grant.sequence,
                turn_index=request.turn_index,
                classification=classification,
                request_digest=fingerprint,
                transition_digest=transition_fingerprint,
                result_digest=result_fingerprint,
                context_bytes=len(request.context.encode()),
                detail=detail,
                ts=self._timestamp(),
            ),
            b"",
            fingerprint,
        )
        if not committed:
            return LeadOutcome(LeadClassification.LATE, state, detail="generation closed after admission")
        self._write_receipt()
        return LeadOutcome(
            classification,
            project_lead(self.store.events(), self.run_id, request.engagement_id),
            detail=detail,
        )

    def _quarantine(
        self,
        request: LeadRequest,
        fingerprint: str,
        state: LeadState,
        classification: LeadClassification,
        detail: str,
    ) -> LeadOutcome:
        body = canonical_bytes({"request": request_document(request)})
        if len(body) > request.binding.max_turn_bytes:
            body = canonical_bytes({"request_digest": fingerprint, "context_bytes": len(request.context.encode())})
        try:
            committed = self._append_fenced(
                request,
                lambda grant: LeadEngagementRecorded(
                    event_id=f"{request.engagement_id}:turn-{request.turn_index:06d}:quarantine-{fingerprint[:12]}",
                    record=LeadRecord.QUARANTINED,
                    binding=request.binding,
                    authority_run_id=grant.run_id,
                    authority_event_id=grant.event_id,
                    authority_sequence=grant.sequence,
                    turn_index=request.turn_index,
                    classification=classification,
                    request_digest=fingerprint,
                    context_bytes=len(request.context.encode()),
                    output_bytes=len(body),
                    detail=detail,
                    ts=self._timestamp(),
                ),
                body,
                fingerprint,
            )
        except UnknownGeneration:
            committed = False
        if committed and primary_events(self.store.events(), request.engagement_id):
            self._write_receipt()
        return LeadOutcome(classification if committed else LeadClassification.LATE, state, detail=detail)

    def _append_fenced(self, request, event_factory, body: bytes, evidence_digest: str) -> bool:
        def commit(grant):
            return self.store.append(event_factory(grant), body=body)

        try:
            decision, _ = self._fence.authorize_and_commit(
                request.generation_id,
                GenerationAuthority.AUTHORITY,
                commit,
                evidence=canonical_bytes({"lead_evidence_digest": evidence_digest}),
            )
        except UnknownGeneration:
            return False
        return decision.accepted

    def _write_receipt(self) -> None:
        from solver.lead_receipt import write_receipt

        write_receipt(self._state, self.run_id)

    def _state_or_empty(self, binding: LeadBinding) -> LeadState:
        return (
            project_lead(self.store.events(), self.run_id, binding.engagement_id)
            if primary_events(self.store.events(), binding.engagement_id)
            else self._empty_state(binding)
        )

    def _empty_state(self, binding: LeadBinding) -> LeadState:
        return LeadState(self.run_id, binding, 0, None, "", "", "", ())

    def _binding_rejection(self, binding: LeadBinding) -> tuple[LeadClassification, str] | None:
        if binding.run_id != self.run_id:
            return LeadClassification.CONFLICT, "binding Run disagrees with controller"
        if binding.owner_id != CONTROLLER_OWNER:
            return LeadClassification.CONFLICT, "binding owner disagrees with controller"
        generation = next(
            (state for state in self._fence.projection().generations if state.generation_id == binding.generation_id),
            None,
        )
        if generation is None:
            return LeadClassification.LATE, "binding generation is unknown"
        if generation.attempt_id != binding.attempt_id or generation.work_id != binding.work_id:
            return LeadClassification.CONFLICT, "binding ownership disagrees with generation"
        return None

    @staticmethod
    def _decode_recorded_proposal(event):
        if event.payload["classification"] != LeadClassification.ACCEPTED.value:
            return None
        try:
            return decode_proposal(json.loads(event.body)["turn"]["proposal"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise InvalidEventError("accepted Lead Turn body cannot be decoded", sequence=event.sequence) from error

    @staticmethod
    def _result_digest(request: LeadRequest) -> str:
        if not isinstance(request.input, LeadResume) or request.input.result is None:
            return ""
        return digest_bytes(canonical_bytes(result_document(request.input.result)))

    @staticmethod
    def _validate_request(request: LeadRequest) -> None:
        if not isinstance(request, LeadRequest):
            raise TypeError("Lead request has the wrong type")
        validate_binding(request.binding)
        if not isinstance(request.turn_index, int) or isinstance(request.turn_index, bool) or request.turn_index < 1:
            raise ValueError("Lead turn identity is incomplete")
        if not isinstance(request.input, (LeadInitialContext, LeadResume)) or not isinstance(request.context, str):
            raise TypeError("Lead input has the wrong type")

    @staticmethod
    def _validate_input(request: LeadRequest, state: LeadState):
        encoded = request.context.encode()
        if request.turn_index == 1 and not isinstance(request.input, LeadInitialContext):
            return LeadClassification.MALFORMED, "first turn requires initial context"
        if request.turn_index > 1 and not isinstance(request.input, LeadResume):
            return LeadClassification.MALFORMED, "continuation requires a resume delta"
        limit = (
            request.binding.max_context_bytes
            if isinstance(request.input, LeadInitialContext)
            else request.binding.max_resume_delta_bytes
        )
        if len(encoded) > limit:
            return LeadClassification.OVERSIZED, "Lead input exceeds its admitted bound"
        result = request.input.result if isinstance(request.input, LeadResume) else None
        if state.outstanding_proposal_id:
            if result is None:
                return LeadClassification.MALFORMED, "outstanding proposal has no result"
            if result.proposal_id != state.outstanding_proposal_id:
                return LeadClassification.CONFLICT, "proposal result identity mismatch"
        elif result is not None:
            return LeadClassification.CONFLICT, "unexpected proposal result"
        if result is not None and not LeadController._valid_result(result):
            return LeadClassification.MALFORMED, "proposal result is invalid"
        return None

    @staticmethod
    def _valid_result(result: LeadProposalResult) -> bool:
        return (
            isinstance(result.proposal_id, str)
            and bool(result.proposal_id)
            and isinstance(result.status, ProposalResultStatus)
            and isinstance(result.evidence_refs, tuple)
            and all(isinstance(item, str) and item for item in result.evidence_refs)
            and isinstance(result.detail, str)
        )

    @staticmethod
    def _validate_turn(turn: object, binding: LeadBinding) -> tuple[LeadClassification, str]:
        if not isinstance(turn, MeasuredLeadTurn) or not isinstance(turn.proposal, PROPOSAL_TYPES):
            return LeadClassification.MALFORMED, "model output does not match the Lead contract"
        if turn.measure is None or not isinstance(turn.measure, TurnMeasure):
            return LeadClassification.UNMEASURED, "model output has no trusted turn measure"
        measure = turn.measure
        if (
            not isinstance(measure.model, str)
            or measure.model != binding.effective_model
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in (measure.duration_ms, measure.tokens_in, measure.tokens_out)
            )
            or not isinstance(measure.usage_known, bool)
            or min(measure.duration_ms, measure.tokens_in, measure.tokens_out) < 0
        ):
            return LeadClassification.UNMEASURED, "turn measure is invalid or model drifted"
        if not isinstance(turn.approach, str):
            return LeadClassification.MALFORMED, "approach is not text"
        if not turn.approach or len(turn.approach.encode()) > MAX_APPROACH_BYTES:
            return LeadClassification.OVERSIZED, "approach is empty or exceeds bound"
        if not valid_proposal(turn.proposal):
            return LeadClassification.MALFORMED, "proposal contains invalid values"
        if len(canonical_bytes(proposal_document(turn.proposal))) > binding.max_turn_bytes:
            return LeadClassification.OVERSIZED, "proposal exceeds bound"
        return LeadClassification.ACCEPTED, ""


__all__ = ["LeadController", "project_lead"]
