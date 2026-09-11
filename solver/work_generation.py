"""Durable ownership fences for Challenge Work generations."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from types import MappingProxyType
from typing import Any, TypeVar

from solver.event_store import (
    WORK_GENERATION_RECORDED,
    EventStore,
    GenerationAuthority,
    GenerationClassification,
    GenerationDisposition,
    GenerationRecord,
    InvalidEventError,
    WorkGenerationRecorded,
)
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.redaction import Redactor


PROJECTION_SCHEMA_VERSION = 1
_GENERATION_ID = re.compile(r"^generation-(\d+)$")
_T = TypeVar("_T")


class GenerationConflict(InvalidEventError):
    """A requested ownership operation conflicts with a durable active generation."""


class UnknownGeneration(InvalidEventError):
    """No durable generation has the requested identity."""


class IllegalGenerationTransition(InvalidEventError):
    """Verified canonical events contain an impossible generation transition."""


@dataclass(frozen=True)
class GenerationIdentity:
    generation_id: str
    work_id: str
    attempt_id: str


@dataclass(frozen=True)
class GenerationState:
    generation_id: str
    work_id: str
    attempt_id: str
    disposition: GenerationDisposition | None
    acquired_sequence: int
    closed_sequence: int | None

    @property
    def active(self) -> bool:
        return self.disposition is None

    def document(self) -> dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "work_id": self.work_id,
            "attempt_id": self.attempt_id,
            "disposition": self.disposition.value if self.disposition is not None else None,
            "acquired_sequence": self.acquired_sequence,
            "closed_sequence": self.closed_sequence,
        }


@dataclass(frozen=True)
class GenerationProjection:
    generations: tuple[GenerationState, ...]
    active_by_work: Mapping[str, GenerationState]
    digest: str
    chain_head: str


@dataclass(frozen=True)
class AuthorityDecision:
    accepted: bool
    classification: GenerationClassification


def _illegal(message: str, sequence: int | None = None) -> IllegalGenerationTransition:
    return IllegalGenerationTransition(message, sequence=sequence)


def _projection_document(
    run_id: str,
    states: tuple[GenerationState, ...],
    active_by_work: Mapping[str, GenerationState],
) -> dict[str, Any]:
    return {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "run_id": run_id,
        "generations": [state.document() for state in states],
        "active_by_work": {work_id: state.generation_id for work_id, state in active_by_work.items()},
    }


def _project_events(events: list[Any], run_id: str) -> GenerationProjection:
    work_events = [event for event in events if event.event_type == WORK_GENERATION_RECORDED]
    states_by_id: dict[str, GenerationState] = {}
    order: list[str] = []
    active_by_work: dict[str, GenerationState] = {}
    generation_by_attempt: dict[str, str] = {}

    for event in work_events:
        payload = event.payload
        generation_id = payload["generation_id"]
        work_id = payload["work_id"]
        attempt_id = payload["attempt_id"]
        sequence = event.sequence
        try:
            record = GenerationRecord(payload["record"])
        except ValueError as error:
            raise _illegal(f"generation record {payload['record']!r} is unsupported", sequence) from error

        if record is GenerationRecord.ACQUIRE:
            if generation_id in states_by_id:
                raise _illegal(f"generation {generation_id!r} was acquired more than once", sequence)
            if work_id in active_by_work:
                raise _illegal(f"work {work_id!r} has more than one active generation", sequence)
            if attempt_id in generation_by_attempt:
                raise _illegal(f"attempt {attempt_id!r} belongs to more than one generation", sequence)
            state = GenerationState(
                generation_id=generation_id,
                work_id=work_id,
                attempt_id=attempt_id,
                disposition=None,
                acquired_sequence=sequence,
                closed_sequence=None,
            )
            states_by_id[generation_id] = state
            order.append(generation_id)
            active_by_work[work_id] = state
            generation_by_attempt[attempt_id] = generation_id
            continue

        state = states_by_id.get(generation_id)
        if state is None:
            raise _illegal(f"generation {generation_id!r} has no acquisition", sequence)
        if state.work_id != work_id or state.attempt_id != attempt_id:
            raise _illegal(f"generation {generation_id!r} changed ownership", sequence)

        if record is GenerationRecord.CLOSE:
            if not state.active:
                raise _illegal(f"generation {generation_id!r} was closed more than once", sequence)
            try:
                disposition = GenerationDisposition(payload["disposition"])
            except ValueError as error:
                raise _illegal(f"generation {generation_id!r} has an invalid disposition", sequence) from error
            closed = GenerationState(
                generation_id=state.generation_id,
                work_id=state.work_id,
                attempt_id=state.attempt_id,
                disposition=disposition,
                acquired_sequence=state.acquired_sequence,
                closed_sequence=sequence,
            )
            states_by_id[generation_id] = closed
            del active_by_work[work_id]
            continue

        if record is GenerationRecord.AUTHORITY:
            if not state.active:
                raise _illegal(f"closed generation {generation_id!r} reserved authority", sequence)
            if payload["classification"] != GenerationClassification.CURRENT.value:
                raise _illegal(f"generation {generation_id!r} has an invalid authority classification", sequence)
            continue

        if record is GenerationRecord.LATE_EVENT:
            if state.active:
                raise _illegal(f"active generation {generation_id!r} received a late event", sequence)
            expected = (
                GenerationClassification.SUPERSEDED.value
                if state.disposition == GenerationDisposition.SUPERSEDE
                else GenerationClassification.CLOSED.value
            )
            if payload["classification"] != expected:
                raise _illegal(f"generation {generation_id!r} has an invalid late-event classification", sequence)
            continue

    states = tuple(states_by_id[generation_id] for generation_id in order)
    active = MappingProxyType(dict(active_by_work))
    document = _projection_document(run_id, states, active)
    return GenerationProjection(
        generations=states,
        active_by_work=active,
        digest=digest_bytes(canonical_bytes(document)),
        chain_head=work_events[-1].event_digest if work_events else "",
    )


def _next_generation_id(projection: GenerationProjection) -> str:
    serials = []
    for state in projection.generations:
        match = _GENERATION_ID.fullmatch(state.generation_id)
        if match is not None:
            serials.append(int(match.group(1)))
    return f"generation-{max(serials, default=0) + 1:06d}"


class GenerationFence:
    """Own generation acquisition, closing, restart fencing and authority admission."""

    def __init__(
        self,
        state: Path,
        run_id: str,
        redactor: Redactor,
        timestamp: Callable[[], str],
    ) -> None:
        self.store = EventStore(state, run_id=run_id, redactor=redactor)
        self.run_id = run_id
        self._timestamp = timestamp
        self._authority_lock = RLock()

    def acquire(self, work_id: str, attempt_id: str) -> GenerationIdentity:
        if not work_id or not attempt_id:
            raise ValueError("work_id and attempt_id are required")
        projection = self.projection()
        if work_id in projection.active_by_work:
            raise GenerationConflict(f"work {work_id!r} already has an active generation")
        if any(state.attempt_id == attempt_id for state in projection.generations):
            raise GenerationConflict(f"attempt {attempt_id!r} already belongs to a generation")
        generation_id = _next_generation_id(projection)
        self._append(
            WorkGenerationRecorded(
                event_id=f"{generation_id}:acquire",
                generation_id=generation_id,
                work_id=work_id,
                attempt_id=attempt_id,
                record=GenerationRecord.ACQUIRE,
                ts=self._timestamp(),
            ),
            b"",
        )
        return GenerationIdentity(generation_id, work_id, attempt_id)

    def replace(self, work_id: str, attempt_id: str) -> GenerationIdentity:
        """Supersede current Work durably before acquiring its replacement."""

        active = self.projection().active_by_work.get(work_id)
        if active is not None:
            self.close(active.generation_id, GenerationDisposition.SUPERSEDE)
        return self.acquire(work_id, attempt_id)

    def close(self, generation_id: str, disposition: GenerationDisposition) -> None:
        with self._authority_lock:
            self._close(generation_id, disposition)

    def _close(self, generation_id: str, disposition: GenerationDisposition) -> None:
        disposition = GenerationDisposition(disposition)
        projection = self.projection()
        state = _state_for(projection, generation_id)
        if not state.active:
            if state.disposition == disposition:
                return
            raise GenerationConflict(f"generation {generation_id!r} already closed as {state.disposition.value}")
        self._append(
            WorkGenerationRecorded(
                event_id=f"{generation_id}:close",
                generation_id=generation_id,
                work_id=state.work_id,
                attempt_id=state.attempt_id,
                record=GenerationRecord.CLOSE,
                disposition=disposition,
                ts=self._timestamp(),
            ),
            b"",
        )

    def authorize(
        self,
        generation_id: str,
        authority: GenerationAuthority,
        evidence: bytes = b"",
    ) -> AuthorityDecision:
        with self._authority_lock:
            return self._authorize(generation_id, authority, evidence)

    def _authorize(
        self,
        generation_id: str,
        authority: GenerationAuthority,
        evidence: bytes = b"",
    ) -> AuthorityDecision:
        projection = self.projection()
        state = _state_for(projection, generation_id)
        authority = GenerationAuthority(authority)
        if state.active:
            event_id = self._next_authority_event_id(generation_id)
            self._append(
                WorkGenerationRecorded(
                    event_id=event_id,
                    generation_id=generation_id,
                    work_id=state.work_id,
                    attempt_id=state.attempt_id,
                    record=GenerationRecord.AUTHORITY,
                    authority=authority,
                    classification=GenerationClassification.CURRENT,
                    ts=self._timestamp(),
                ),
                b"",
            )
            return AuthorityDecision(True, GenerationClassification.CURRENT)
        classification = (
            GenerationClassification.SUPERSEDED
            if state.disposition == GenerationDisposition.SUPERSEDE
            else GenerationClassification.CLOSED
        )
        event_id = self._next_late_event_id(generation_id)
        self._append(
            WorkGenerationRecorded(
                event_id=event_id,
                generation_id=generation_id,
                work_id=state.work_id,
                attempt_id=state.attempt_id,
                record=GenerationRecord.LATE_EVENT,
                authority=authority,
                classification=classification,
                ts=self._timestamp(),
            ),
            evidence,
        )
        return AuthorityDecision(False, classification)

    def authorize_and_commit(
        self,
        generation_id: str,
        authority: GenerationAuthority,
        commit: Callable[[], _T],
        evidence: bytes = b"",
    ) -> tuple[AuthorityDecision, _T | None]:
        """Keep one admitted write atomic with respect to generation closing."""

        with self._authority_lock:
            decision = self._authorize(generation_id, authority, evidence)
            return decision, commit() if decision.accepted else None

    def reconcile_restart(self) -> tuple[str, ...]:
        active = tuple(state.generation_id for state in self.projection().generations if state.active)
        for generation_id in active:
            self.close(generation_id, GenerationDisposition.INTERRUPT)
        return active

    def projection(self) -> GenerationProjection:
        return _project_events(self.store.events(), self.run_id)

    def write_receipt(self) -> Path:
        from solver.work_generation_receipt import build_receipt

        events = self.store.events()
        projection = _project_events(events, self.run_id)
        return build_receipt(self.store, events, projection)

    def _append(self, event: WorkGenerationRecorded, body: bytes) -> None:
        self.store.append(event, body=body)
        self.write_receipt()

    def _next_late_event_id(self, generation_id: str) -> str:
        events = self.store.events()
        count = sum(
            1
            for event in events
            if event.event_type == WORK_GENERATION_RECORDED
            and event.payload["generation_id"] == generation_id
            and event.payload["record"] == GenerationRecord.LATE_EVENT.value
        )
        return f"{generation_id}:late-{count + 1:06d}"

    def _next_authority_event_id(self, generation_id: str) -> str:
        events = self.store.events()
        count = sum(
            1
            for event in events
            if event.event_type == WORK_GENERATION_RECORDED
            and event.payload["generation_id"] == generation_id
            and event.payload["record"] == GenerationRecord.AUTHORITY.value
        )
        return f"{generation_id}:authority-{count + 1:06d}"


def _state_for(projection: GenerationProjection, generation_id: str) -> GenerationState:
    for state in projection.generations:
        if state.generation_id == generation_id:
            return state
    raise UnknownGeneration(generation_id)


__all__ = [
    "AuthorityDecision",
    "GenerationConflict",
    "GenerationAuthority",
    "GenerationClassification",
    "GenerationDisposition",
    "GenerationFence",
    "GenerationIdentity",
    "GenerationProjection",
    "GenerationRecord",
    "GenerationState",
    "IllegalGenerationTransition",
    "UnknownGeneration",
]
