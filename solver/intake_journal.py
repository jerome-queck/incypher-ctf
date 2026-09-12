"""Durable Intake attempt lifecycle and short compare-and-append publication."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from solver.event_store import CommittedEvent, EventStore
from solver.event_store_storage import canonical_bytes
from solver.intake_contracts import (
    INTAKE_DECISION_RECORDED,
    INTAKE_OBSERVATION_RECORDED,
    IntakeDecisionRecorded,
    IntakeObservationRecorded,
    IntakeRecord,
)
from solver.intake_qualification import (
    IntakeContract,
    IntakeAuthority,
    IntakeDecision,
    IntakeDocument,
    IntakeSnapshot,
    PriorFence,
)
from solver.redaction import Redactor


@dataclass(frozen=True)
class PublishedDecision:
    record: str
    reason: str
    expected_fence: PriorFence
    observed_fence: PriorFence
    event: CommittedEvent


class IntakeJournal:
    """One canonical writer for starts, evidence closure and snapshot authority."""

    def __init__(self, state: Path, run_id: str, redactor: Redactor, *, timestamp) -> None:
        self._state = Path(state)
        self._run_id = run_id
        self.store = EventStore(state, run_id=run_id, redactor=redactor)
        self._timestamp = timestamp

    def start(
        self,
        attempt_id: str,
        contract: IntakeContract,
        prior: PriorFence,
        authority: IntakeAuthority | None = None,
    ) -> CommittedEvent:
        authority = authority or IntakeAuthority.testing(contract)
        event = IntakeDecisionRecorded(
            event_id=f"intake-decision:{attempt_id}:started",
            attempt_id=attempt_id,
            record=IntakeRecord.STARTED,
            profile_digest=contract.profile_digest,
            contract_digest=contract.digest,
            authority_digest=authority.digest,
            expected_fence=prior,
            ts=self._timestamp(),
        )
        return self.store.append(
            event,
            body=canonical_bytes({"contract": contract.document(), "authority": authority.document()}),
        )

    def observe(self, attempt_id: str, document: IntakeDocument) -> CommittedEvent:
        event = IntakeObservationRecorded(
            event_id=f"intake-observation:{attempt_id}:{document.classified_event_id}",
            attempt_id=attempt_id,
            classified_event_id=document.classified_event_id,
            request_id=document.request_id,
            kind=document.kind,
            pass_no=document.pass_no,
            raw_digest=document.raw_digest,
            profile_digest=document.profile_digest,
            subject_digest=document.subject_digest,
            capability_digest=document.capability_digest,
            peer_digest=document.peer_digest,
            endpoint=document.endpoint,
            status=document.status,
            content_type=document.content_type,
            original_bytes=document.original_bytes,
            complete=document.complete,
            page=document.page,
            challenge_id=document.challenge_id,
            outcome=document.outcome,
            location=document.location,
            raw_blob_digest=document.raw_blob_digest,
            sanitized_blob_digest=document.sanitized_blob_digest,
            request_digest=document.request_digest,
            hop=document.hop,
            auth_forwarded=document.auth_forwarded,
            resource_identity=document.resource_identity,
            ts=self._timestamp(),
        )
        return self.store.append(event, body=document.raw)

    def publish(self, decision: IntakeDecision) -> PublishedDecision:
        start = self._start(decision.attempt_id)
        observations = self._observations(decision.attempt_id)
        closed_ids = tuple(event.payload["classified_event_id"] for event in observations)
        if decision.observation_ids and closed_ids != decision.observation_ids:
            raise ValueError("Intake terminal observation closure differs from canonical observations")
        if not decision.settled:
            event = self._terminal_event(
                decision,
                start,
                IntakeRecord.UNSETTLED,
                observation_ids=closed_ids or decision.observation_ids,
            )
            committed = self.store.append(event, body=_decision_body(decision))
            return PublishedDecision(
                IntakeRecord.UNSETTLED.value,
                decision.reason,
                decision.prior_fence,
                decision.prior_fence,
                committed,
            )
        if decision.snapshot is None:
            raise ValueError("settled Intake decision has no snapshot")
        event = self._terminal_event(
            decision,
            start,
            IntakeRecord.SETTLED,
            observation_ids=closed_ids or decision.observation_ids,
        )
        compared = self.store.compare_and_append(
            event,
            body=decision.snapshot.canonical_bytes(),
            latest_event_type=INTAKE_DECISION_RECORDED,
            latest_payload={"record": IntakeRecord.SETTLED.value},
            expected_projection=decision.prior_fence.document(),
        )
        if compared.committed is not None:
            observed = _fence_from_event(compared.committed)
            return PublishedDecision(
                IntakeRecord.SETTLED.value,
                decision.reason,
                decision.prior_fence,
                observed,
                compared.committed,
            )
        if compared.observed is None:
            observed = PriorFence.genesis(str(start.payload["profile_digest"]))
        else:
            observed = _fence_from_event(compared.observed)
        conflict = IntakeDecisionRecorded(
            event_id=f"intake-decision:{decision.attempt_id}:fence-conflict",
            attempt_id=decision.attempt_id,
            record=IntakeRecord.FENCE_CONFLICT,
            profile_digest=str(start.payload["profile_digest"]),
            contract_digest=str(start.payload["contract_digest"]),
            authority_digest=str(start.payload["authority_digest"]),
            expected_fence=decision.prior_fence,
            observed_fence=observed,
            reason="prior-fence-changed",
            observation_ids=closed_ids or decision.observation_ids,
            proposed_snapshot_digest=decision.snapshot.digest,
            ts=self._timestamp(),
        )
        committed = self.store.append(conflict, body=_decision_body(decision))
        return PublishedDecision(
            IntakeRecord.FENCE_CONFLICT.value,
            "prior-fence-changed",
            decision.prior_fence,
            observed,
            committed,
        )

    def close_orphans(self) -> tuple[PublishedDecision, ...]:
        events = self.store.events()
        decisions = [event for event in events if event.event_type == INTAKE_DECISION_RECORDED]
        terminal = {
            str(event.payload["attempt_id"])
            for event in decisions
            if event.payload["record"] != IntakeRecord.STARTED.value
        }
        closed = []
        for start in decisions:
            attempt_id = str(start.payload["attempt_id"])
            if start.payload["record"] != IntakeRecord.STARTED.value or attempt_id in terminal:
                continue
            observations = self._observations(attempt_id)
            reason = "interrupted-after-read" if observations else "interrupted-before-read"
            prior = _fence(start.payload["expected_fence"])
            event = IntakeDecisionRecorded(
                event_id=f"intake-decision:{attempt_id}:unsettled",
                attempt_id=attempt_id,
                record=IntakeRecord.UNSETTLED,
                profile_digest=str(start.payload["profile_digest"]),
                contract_digest=str(start.payload["contract_digest"]),
                authority_digest=str(start.payload["authority_digest"]),
                expected_fence=prior,
                reason=reason,
                observation_ids=tuple(str(item.payload["classified_event_id"]) for item in observations),
                ts=self._timestamp(),
            )
            committed = self.store.append(event, body=canonical_bytes({"reason": reason}))
            closed.append(PublishedDecision("unsettled", reason, prior, prior, committed))
        return tuple(closed)

    def latest_snapshot_document(self) -> dict[str, object]:
        snapshot = self.latest_snapshot()
        if snapshot is None:
            raise LookupError("no coherent Intake snapshot has been published")
        return snapshot.document()

    def latest_snapshot(self) -> IntakeSnapshot | None:
        profile_digest = self._published_profile_digest()
        if profile_digest is None:
            return None
        return self._replay(profile_digest).snapshot

    def latest_fence(self, profile_digest: str) -> PriorFence:
        return self._replay(profile_digest).fence

    def _replay(self, profile_digest: str):
        from solver.intake_replay import replay_intake

        return replay_intake(self._state, self._run_id, profile_digest)

    def _published_profile_digest(self) -> str | None:
        settled = self._latest_settled()
        return str(settled.payload["profile_digest"]) if settled is not None else None

    def _latest_settled(self) -> CommittedEvent | None:
        return next(
            (
                event
                for event in reversed(self.store.events())
                if event.event_type == INTAKE_DECISION_RECORDED
                and event.payload["record"] == IntakeRecord.SETTLED.value
            ),
            None,
        )

    def _start(self, attempt_id: str) -> CommittedEvent:
        matches = [
            event
            for event in self.store.events()
            if event.event_type == INTAKE_DECISION_RECORDED
            and event.payload["attempt_id"] == attempt_id
            and event.payload["record"] == IntakeRecord.STARTED.value
        ]
        if len(matches) != 1:
            raise ValueError("Intake attempt has no unique durable start")
        return matches[0]

    def _observations(self, attempt_id: str) -> tuple[CommittedEvent, ...]:
        return tuple(
            event
            for event in self.store.events()
            if event.event_type == INTAKE_OBSERVATION_RECORDED and event.payload["attempt_id"] == attempt_id
        )

    def _terminal_event(
        self,
        decision: IntakeDecision,
        start: CommittedEvent,
        record: IntakeRecord,
        *,
        observation_ids: tuple[str, ...],
    ) -> IntakeDecisionRecorded:
        return IntakeDecisionRecorded(
            event_id=f"intake-decision:{decision.attempt_id}:{record.value}",
            attempt_id=decision.attempt_id,
            record=record,
            profile_digest=str(start.payload["profile_digest"]),
            contract_digest=str(start.payload["contract_digest"]),
            authority_digest=str(start.payload["authority_digest"]),
            expected_fence=decision.prior_fence,
            reason=decision.reason,
            observation_ids=observation_ids,
            snapshot_digest=decision.snapshot.digest if decision.snapshot and record is IntakeRecord.SETTLED else "",
            ts=self._timestamp(),
        )


def _decision_body(decision: IntakeDecision) -> bytes:
    return canonical_bytes(
        {
            "attempt_id": decision.attempt_id,
            "settled": decision.settled,
            "reason": decision.reason,
            "prior_fence": decision.prior_fence.document(),
            "snapshot_digest": decision.snapshot.digest if decision.snapshot else "",
            "observation_ids": list(decision.observation_ids),
        }
    )


def _fence(value: object) -> PriorFence:
    if not isinstance(value, dict):
        raise ValueError("Intake fence is not an object")
    return PriorFence(
        str(value["profile_digest"]),
        str(value["event_id"]),
        str(value["event_digest"]),
        str(value["snapshot_digest"]),
    )


def _fence_from_event(event: CommittedEvent) -> PriorFence:
    return PriorFence(
        str(event.payload["profile_digest"]),
        str(event.payload["event_id"]),
        event.event_digest,
        str(event.payload["snapshot_digest"]),
    )


__all__ = ["IntakeJournal", "PublishedDecision"]
