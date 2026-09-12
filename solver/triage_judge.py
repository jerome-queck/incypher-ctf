"""Acceptance boundary for one measured Triage Judge arrival batch."""

from __future__ import annotations
from dataclasses import asdict
from pathlib import Path
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.triage_judge_contracts import (
    MAX_EVIDENCE_ITEMS,
    MAX_SUMMARY_BYTES,
    MeasuredTriageJudgement,
    TriageEvidence,
    TriageJudgeClassification,
    TriageJudgeMeasure,
    TriageJudgeOutcome,
    TriageJudgeRecorded,
    TriageJudgeRequest,
    TriageProposal,
)


def evidence_digest(evidence):
    return digest_bytes(canonical_bytes({"evidence": [asdict(item) for item in evidence]}))


class TriageJudgeController:
    def __init__(self, state: Path, run_id: str, redactor, model, *, confidence_threshold=0.6):
        self.store, self.run_id, self._model, self._threshold = (
            EventStore(state, run_id=run_id, redactor=redactor),
            run_id,
            model,
            confidence_threshold,
        )

    def evaluate(self, request: TriageJudgeRequest):
        self._validate(request)
        fingerprint = digest_bytes(canonical_bytes({"request": self._request_document(request)}))
        existing = next(
            (
                e
                for e in self.store.events()
                if e.event_type == "triage-judge.recorded" and e.payload["batch_id"] == request.batch_id
            ),
            None,
        )
        if existing:
            if existing.payload["request_digest"] != fingerprint:
                raise ValueError("Triage arrival-batch identity conflict")
            return self._outcome(existing.payload, TriageJudgeClassification.DUPLICATE)
        measured = TriageJudgeMeasure("", "", 0, 0, 0, False, "")
        classification, reason, chosen, source = (
            TriageJudgeClassification.UNAVAILABLE,
            "inference unavailable",
            request.deterministic,
            "deterministic",
        )
        try:
            answer = self._model(request.evidence)
        except Exception:
            answer = None
        if answer is not None:
            if isinstance(answer, MeasuredTriageJudgement) and (
                not isinstance(answer.measure, TriageJudgeMeasure) or not answer.measure.usage_known
            ):
                classification, reason = TriageJudgeClassification.UNMEASURED, "model output was not measured"
            elif not self._valid_turn(answer):
                classification, reason = TriageJudgeClassification.MALFORMED, "model output malformed"
            else:
                measured = answer.measure
                if measured.completed_at > request.deadline:
                    classification, reason = (
                        TriageJudgeClassification.LATE,
                        "model output arrived after Triage batch deadline",
                    )
                elif answer.proposal.confidence < self._threshold:
                    classification, reason = TriageJudgeClassification.REJECTED, "confidence below threshold"
                else:
                    classification, reason, chosen, source = (
                        TriageJudgeClassification.ACCEPTED,
                        "measured model Triage proposal accepted",
                        answer.proposal,
                        "model",
                    )
        verdict_id = f"{request.batch_id}:verdict"
        values = dict(
            event_id=verdict_id,
            batch_id=request.batch_id,
            request_digest=fingerprint,
            evidence_digest=request.evidence_digest,
            classification=classification,
            verdict_id=verdict_id,
            accepted_source=source,
            acceptance_reason=reason,
            proposal_kind=chosen.kind,
            proposal_value=chosen.value,
            proposal_confidence=chosen.confidence,
            proposal_provenance=chosen.provenance,
            measured_route=measured.route,
            measured_model=measured.model,
            duration_ms=measured.duration_ms,
            tokens_in=measured.tokens_in,
            tokens_out=measured.tokens_out,
            usage_known=measured.usage_known,
            completed_at=measured.completed_at,
            deterministic_same=chosen.kind == request.deterministic.kind
            and chosen.value == request.deterministic.value,
        )
        event = self.store.append(
            TriageJudgeRecorded(**values), body=canonical_bytes({"evidence": [asdict(x) for x in request.evidence]})
        )
        self.write_receipt()
        return self._outcome(event.payload, classification)

    def write_receipt(self):
        from solver.triage_judge_receipt import write_receipt

        return write_receipt(self.store.run_dir.parents[1], self.run_id)

    @staticmethod
    def _request_document(r):
        return {
            "batch_id": r.batch_id,
            "evidence_digest": r.evidence_digest,
            "evidence": [asdict(x) for x in r.evidence],
            "deterministic": asdict(r.deterministic),
            "deadline": r.deadline,
        }

    @staticmethod
    def _validate(r):
        if not isinstance(r, TriageJudgeRequest) or not r.batch_id or type(r.deadline) is not str:
            raise ValueError("invalid Triage Judge request")
        if type(r.evidence) is not tuple or len(r.evidence) > MAX_EVIDENCE_ITEMS:
            raise ValueError("Triage evidence item bound exceeded")
        for item in r.evidence:
            if not isinstance(item, TriageEvidence):
                raise TypeError("Triage evidence must be typed")
            if any(type(getattr(item, n)) is not str for n in ("kind", "reference", "digest", "summary")):
                raise TypeError("Triage evidence fields must be text")
            if (
                not item.kind
                or len(item.kind.encode()) > 32
                or not item.reference
                or len(item.reference.encode()) > 128
            ):
                raise ValueError("Triage evidence identity exceeds bound")
            if len(item.summary.encode()) > MAX_SUMMARY_BYTES:
                raise ValueError("Triage evidence summary exceeds bound")
            if not _digest(item.digest):
                raise ValueError("Triage evidence digest is invalid")
        if not _digest(r.evidence_digest) or r.evidence_digest != evidence_digest(r.evidence):
            raise ValueError("Triage evidence digest differs from typed evidence")
        if not isinstance(r.deterministic, TriageProposal):
            raise TypeError("deterministic Triage proposal must be typed")

    @staticmethod
    def _valid_turn(a):
        return (
            isinstance(a, MeasuredTriageJudgement)
            and isinstance(a.proposal, TriageProposal)
            and isinstance(a.measure, TriageJudgeMeasure)
            and a.proposal.kind == "triage"
            and type(a.proposal.confidence) is float
            and 0 <= a.proposal.confidence <= 1
            and a.measure.usage_known
            and bool(a.measure.route and a.measure.model and a.measure.completed_at)
        )

    @staticmethod
    def _outcome(p, c):
        return TriageJudgeOutcome(
            c,
            p["verdict_id"],
            TriageProposal(p["proposal_kind"], p["proposal_value"], p["proposal_confidence"], p["proposal_provenance"]),
            p["accepted_source"],
            p["acceptance_reason"],
        )


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
