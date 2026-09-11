"""One generation-fenced owner for Solve Lead Engagement transitions."""

from __future__ import annotations

from pathlib import Path

from solver.event_store import EventStore, GenerationAuthority
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_codec import (
    PROPOSAL_TYPES,
    model_output_document,
    proposal_disposition,
    proposal_document,
    proposal_kind,
    request_digest,
    request_document,
    text,
    turn_document,
    valid_proposal,
)
from solver.lead_contracts import (
    MAX_APPROACH_BYTES,
    MAX_CONTEXT_BYTES,
    MAX_TURN_BYTES,
    LeadClassification,
    LeadEngagementRecorded,
    LeadModel,
    LeadOutcome,
    LeadRequest,
    LeadState,
    MeasuredLeadTurn,
    TurnMeasure,
)
from solver.lead_projection import empty_state, primary_events, project_lead, transition_digest
from solver.redaction import Redactor
from solver.work_generation import GenerationFence


class LeadController:
    """Validate one measured model result, fence it, then append one proposal fact."""

    def __init__(
        self,
        state: Path,
        run_id: str,
        redactor: Redactor,
        timestamp,
        model: LeadModel,
        *,
        fence: GenerationFence | None = None,
    ) -> None:
        self._state = Path(state)
        self.store = EventStore(state, run_id=run_id, redactor=redactor)
        self.run_id = run_id
        self._timestamp = timestamp
        self._model = model
        self._fence = fence or GenerationFence(state, run_id, redactor, timestamp)

    def handle(self, request: LeadRequest) -> LeadOutcome:
        self._validate_request(request)
        request_fingerprint = request_digest(request)
        events = self.store.events()
        existing = next(
            (
                event
                for event in primary_events(events, request.engagement_id)
                if event.payload["turn_index"] == request.turn_index
            ),
            None,
        )
        if existing is not None:
            state = project_lead(events, self.run_id, request.engagement_id)
            if existing.payload["request_digest"] == request_fingerprint:
                return LeadOutcome(LeadClassification.DUPLICATE, state, detail="turn already recorded")
            return self._conflict(request, request_fingerprint, state)

        prior = primary_events(events, request.engagement_id)
        if prior:
            state = project_lead(events, self.run_id, request.engagement_id)
            if state.generation_id != request.generation_id:
                return self._conflict(request, request_fingerprint, state)
            if state.disposition is not None:
                return LeadOutcome(LeadClassification.LATE, state, detail="Engagement is terminal")
            if request.turn_index != state.turn_count + 1:
                return LeadOutcome(LeadClassification.MALFORMED, state, detail="turn sequence gap")
        elif request.turn_index != 1:
            return LeadOutcome(
                LeadClassification.MALFORMED,
                empty_state(self.run_id, request),
                detail="first turn is not one",
            )

        if len(request.context.encode()) > MAX_CONTEXT_BYTES:
            return self._record_rejection(
                request, request_fingerprint, LeadClassification.OVERSIZED, "context exceeds bound"
            )
        try:
            turn = self._model(request)
        except Exception:
            return self._record_rejection(request, request_fingerprint, LeadClassification.MALFORMED, "model failed")
        classification, detail = self._validate_turn(turn)
        if classification is not LeadClassification.ACCEPTED:
            return self._record_rejection(request, request_fingerprint, classification, detail)
        assert isinstance(turn, MeasuredLeadTurn)
        model_output = canonical_bytes(model_output_document(turn))
        body = canonical_bytes(turn_document(request, turn))
        if len(body) > MAX_TURN_BYTES:
            return self._record_rejection(
                request, request_fingerprint, LeadClassification.OVERSIZED, "turn exceeds bound"
            )
        proposal_body = proposal_document(turn.proposal)
        proposal_fingerprint = digest_bytes(canonical_bytes(proposal_body))
        disposition = proposal_disposition(turn.proposal)
        previous = prior[-1].payload["transition_digest"] if prior else ""
        transition_fingerprint = transition_digest(
            previous,
            request_fingerprint,
            LeadClassification.ACCEPTED,
            proposal_kind(turn.proposal),
            proposal_fingerprint,
            disposition,
        )
        measure = turn.measure
        assert measure is not None
        event = LeadEngagementRecorded(
            event_id=f"{request.engagement_id}:turn-{request.turn_index:06d}",
            engagement_id=request.engagement_id,
            generation_id=request.generation_id,
            turn_index=request.turn_index,
            classification=LeadClassification.ACCEPTED,
            request_digest=request_fingerprint,
            transition_digest=transition_fingerprint,
            proposal_kind=proposal_kind(turn.proposal),
            proposal_digest=proposal_fingerprint,
            context_bytes=len(request.context.encode()),
            output_bytes=len(model_output),
            model=measure.model,
            duration_ms=measure.duration_ms,
            tokens_in=measure.tokens_in,
            tokens_out=measure.tokens_out,
            usage_known=measure.usage_known,
            disposition=disposition,
            ts=self._timestamp(),
        )
        decision, _ = self._fence.authorize_and_commit(
            request.generation_id,
            GenerationAuthority.AUTHORITY,
            lambda: self.store.append(event, body=body),
            evidence=canonical_bytes(
                {
                    "request_digest": request_fingerprint,
                    "output_digest": digest_bytes(model_output),
                    "output_bytes": len(model_output),
                }
            ),
        )
        if not decision.accepted:
            return LeadOutcome(
                LeadClassification.LATE, self._state_or_empty(request), detail=decision.classification.value
            )
        self._write_receipt()
        state = project_lead(self.store.events(), self.run_id, request.engagement_id)
        return LeadOutcome(LeadClassification.ACCEPTED, state, turn.proposal)

    def _record_rejection(
        self,
        request: LeadRequest,
        request_digest: str,
        classification: LeadClassification,
        detail: str,
    ) -> LeadOutcome:
        prior = primary_events(self.store.events(), request.engagement_id)
        previous = prior[-1].payload["transition_digest"] if prior else ""
        transition_fingerprint = transition_digest(previous, request_digest, classification)
        event = LeadEngagementRecorded(
            event_id=f"{request.engagement_id}:turn-{request.turn_index:06d}",
            engagement_id=request.engagement_id,
            generation_id=request.generation_id,
            turn_index=request.turn_index,
            classification=classification,
            request_digest=request_digest,
            transition_digest=transition_fingerprint,
            context_bytes=len(request.context.encode()),
            detail=detail,
            ts=self._timestamp(),
        )
        decision, _ = self._fence.authorize_and_commit(
            request.generation_id,
            GenerationAuthority.AUTHORITY,
            lambda: self.store.append(event, body=b""),
            evidence=canonical_bytes({"request_digest": request_digest, "classification": classification.value}),
        )
        if not decision.accepted:
            return LeadOutcome(
                LeadClassification.LATE, self._state_or_empty(request), detail=decision.classification.value
            )
        self._write_receipt()
        return LeadOutcome(
            classification,
            project_lead(self.store.events(), self.run_id, request.engagement_id),
            detail=detail,
        )

    def _conflict(self, request: LeadRequest, request_digest: str, state: LeadState) -> LeadOutcome:
        body = canonical_bytes({"request": request_document(request)})
        if len(body) > MAX_TURN_BYTES:
            body = canonical_bytes({"request_digest": request_digest, "context_bytes": len(request.context.encode())})
        event = LeadEngagementRecorded(
            event_id=f"{request.engagement_id}:turn-{request.turn_index:06d}:conflict-{request_digest[:12]}",
            engagement_id=request.engagement_id,
            generation_id=request.generation_id,
            turn_index=request.turn_index,
            classification=LeadClassification.CONFLICT,
            request_digest=request_digest,
            transition_digest=state.transition_digest or "0" * 64,
            context_bytes=len(request.context.encode()),
            output_bytes=len(body),
            detail="turn identity reused with different input",
            ts=self._timestamp(),
        )
        decision, _ = self._fence.authorize_and_commit(
            request.generation_id,
            GenerationAuthority.AUTHORITY,
            lambda: self.store.append(event, body=body),
            evidence=canonical_bytes({"request_digest": request_digest}),
        )
        classification = LeadClassification.CONFLICT if decision.accepted else LeadClassification.LATE
        if decision.accepted:
            self._write_receipt()
        return LeadOutcome(classification, state, detail=event.detail)

    def _write_receipt(self) -> None:
        from solver.lead_receipt import write_receipt

        write_receipt(self._state, self.run_id)

    def _state_or_empty(self, request: LeadRequest) -> LeadState:
        events = primary_events(self.store.events(), request.engagement_id)
        return (
            project_lead(self.store.events(), self.run_id, request.engagement_id)
            if events
            else empty_state(self.run_id, request)
        )

    @staticmethod
    def _validate_request(request: LeadRequest) -> None:
        if not isinstance(request, LeadRequest):
            raise TypeError("Lead request has the wrong type")
        if (
            not text(request.engagement_id)
            or not text(request.generation_id)
            or not isinstance(request.turn_index, int)
            or isinstance(request.turn_index, bool)
            or request.turn_index < 1
        ):
            raise ValueError("Lead request identity is incomplete")
        if not isinstance(request.context, str):
            raise TypeError("Lead context must be text")

    @staticmethod
    def _validate_turn(turn: object) -> tuple[LeadClassification, str]:
        if not isinstance(turn, MeasuredLeadTurn) or not isinstance(turn.proposal, PROPOSAL_TYPES):
            return LeadClassification.MALFORMED, "model output does not match the Lead contract"
        if turn.measure is None or not isinstance(turn.measure, TurnMeasure):
            return LeadClassification.UNMEASURED, "model output has no trusted turn measure"
        measure = turn.measure
        if (
            not isinstance(measure.model, str)
            or not measure.model
            or any(
                not isinstance(value, int) or isinstance(value, bool)
                for value in (measure.duration_ms, measure.tokens_in, measure.tokens_out)
            )
            or not isinstance(measure.usage_known, bool)
        ):
            return LeadClassification.UNMEASURED, "turn measure is invalid"
        if min(measure.duration_ms, measure.tokens_in, measure.tokens_out) < 0:
            return LeadClassification.UNMEASURED, "turn measure is invalid"
        if not isinstance(turn.approach, str):
            return LeadClassification.MALFORMED, "approach is not text"
        if not turn.approach or len(turn.approach.encode()) > MAX_APPROACH_BYTES:
            return LeadClassification.OVERSIZED, "approach is empty or exceeds bound"
        if not valid_proposal(turn.proposal):
            return LeadClassification.MALFORMED, "proposal contains invalid values"
        document = proposal_document(turn.proposal)
        if len(canonical_bytes(document)) > MAX_TURN_BYTES:
            return LeadClassification.OVERSIZED, "proposal exceeds bound"
        return LeadClassification.ACCEPTED, ""


__all__ = ["LeadController", "project_lead"]
