"""Generation-fenced confirmation and replay of bounded Attempt progress."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from solver.attempt_progress_contracts import (
    ATTEMPT_PROGRESS_RECORDED,
    AttemptProgressRecorded,
    CarryProjection,
    CarriedCheckpoint,
    EvidenceArtifact,
    EvidenceClassification,
    JudgeSource,
    ProgressOutcome,
    ProgressRequest,
    ProgressStatus,
)
from solver.event_store import EventStore, GenerationAuthority
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.redaction import Redactor
from solver.work_generation import GenerationFence


class ProgressController:
    """Accept each sealed, confirmed evidence identity once under current generation authority."""

    def __init__(self, state: Path, run_id: str, redactor: Redactor, timestamp, *, fence: GenerationFence):
        self.store = EventStore(state, run_id=run_id, redactor=redactor)
        self.run_id = run_id
        self._timestamp = timestamp
        self._fence = fence

    def confirm(self, request: ProgressRequest) -> ProgressOutcome:
        request_evidence = canonical_bytes({"request": asdict(request)})

        def decide(grant):
            events = self._events(request.generation_id)
            accepted = [event for event in events if event.payload["status"] == ProgressStatus.ACCEPTED.value]
            epoch_before = len(accepted)
            projected = tuple(self._carried(event) for event in accepted)
            if grant.attempt_id != request.attempt_id:
                status = ProgressStatus.OWNER_MISMATCH
            elif any(event.payload["evidence_id"] == request.evidence.evidence_id for event in accepted):
                return ProgressOutcome(
                    ProgressStatus.REPEATED,
                    epoch_before,
                    epoch_before,
                    self._carry_digest(request.generation_id, projected),
                )
            elif request.epoch != epoch_before:
                status = ProgressStatus.STALE_EPOCH
            elif request.evidence.classification is EvidenceClassification.NO_INFERENCE:
                status = ProgressStatus.NO_INFERENCE
            elif request.evidence.classification is EvidenceClassification.ACTIVITY:
                status = ProgressStatus.UNCONFIRMED
            elif not self._confirmed_artifact(request.evidence):
                status = ProgressStatus.UNCONFIRMED
            else:
                status = ProgressStatus.ACCEPTED
            epoch_after = epoch_before + (status is ProgressStatus.ACCEPTED)
            if status is ProgressStatus.ACCEPTED:
                projected += (
                    CarriedCheckpoint(
                        request.evidence.evidence_id,
                        request.evidence.sealed_digest,
                        request.evidence.moved,
                        request.evidence.replay,
                    ),
                )
            carry_digest = self._carry_digest(request.generation_id, projected)
            self.store.append(
                AttemptProgressRecorded(
                    event_id=f"{request.generation_id}:{request.checkpoint_id}:{request.evidence.evidence_id}",
                    generation_id=request.generation_id,
                    attempt_id=request.attempt_id,
                    checkpoint_id=request.checkpoint_id,
                    evidence_id=request.evidence.evidence_id,
                    evidence_digest=request.evidence.sealed_digest,
                    judge_source=(
                        request.evidence.judge_source.value
                        if isinstance(request.evidence.judge_source, JudgeSource)
                        else str(request.evidence.judge_source)
                    ),
                    moved=request.evidence.moved,
                    replay=request.evidence.replay,
                    status=status,
                    epoch_before=epoch_before,
                    epoch_after=epoch_after,
                    extension_bound=1,
                    carry_digest=carry_digest,
                    authority_event_id=grant.event_id,
                    authority_sequence=grant.sequence,
                    ts=self._timestamp(),
                ),
                body=request_evidence,
            )
            return ProgressOutcome(status, epoch_before, epoch_after, carry_digest)

        decision, outcome = self._fence.authorize_and_commit(
            request.generation_id, GenerationAuthority.CARRY, decide, request_evidence
        )
        if not decision.accepted or outcome is None:
            self._write_receipt()
            epoch = len(
                [
                    event
                    for event in self._events(request.generation_id)
                    if event.payload["status"] == ProgressStatus.ACCEPTED.value
                ]
            )
            return ProgressOutcome(
                ProgressStatus.LATE,
                epoch,
                epoch,
                self._projection(request.generation_id, ()).digest,
            )
        self._write_receipt()
        return outcome

    def carry(self, generation_id: str) -> CarryProjection:
        def inspect():
            selected = [
                event
                for event in self._events(generation_id)
                if event.payload["status"] == ProgressStatus.ACCEPTED.value
            ]
            return self._projection(generation_id, tuple(self._carried(event) for event in selected))

        return self._fence.inspect_current(generation_id, inspect) or self._projection(generation_id, ())

    def expired(self, generation_id: str, *, epoch: int, steps: int, cliff: int) -> bool:
        accepted = sum(
            event.payload["status"] == ProgressStatus.ACCEPTED.value for event in self._events(generation_id)
        )
        return steps >= cliff and accepted <= epoch

    def _events(self, generation_id: str):
        return [
            event
            for event in self.store.events()
            if event.event_type == ATTEMPT_PROGRESS_RECORDED and event.payload["generation_id"] == generation_id
        ]

    def _projection(self, generation_id, checkpoints):
        return CarryProjection(generation_id, checkpoints, self._carry_digest(generation_id, checkpoints))

    @staticmethod
    def _carry_digest(generation_id, checkpoints):
        return digest_bytes(
            canonical_bytes(
                {
                    "generation_id": generation_id,
                    "checkpoints": [asdict(item) for item in checkpoints],
                }
            )
        )

    @staticmethod
    def _carried(event):
        return CarriedCheckpoint(
            event.payload["evidence_id"],
            event.payload["evidence_digest"],
            event.payload["moved"],
            event.payload["replay"],
        )

    @staticmethod
    def _confirmed_artifact(artifact: EvidenceArtifact) -> bool:
        return (
            artifact.classification is EvidenceClassification.CONFIRMED
            and isinstance(artifact.judge_source, JudgeSource)
            and artifact.judge_source is not JudgeSource.NONE
            and bool(artifact.evidence_id and artifact.moved and artifact.replay)
            and len(artifact.sealed_digest) == 64
            and all(character in "0123456789abcdef" for character in artifact.sealed_digest)
        )

    def _write_receipt(self):
        from solver.attempt_progress_receipt import write_receipt

        write_receipt(self.store)


__all__ = ["ProgressController"]
