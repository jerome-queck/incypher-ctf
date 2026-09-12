"""Replay-validated authority gate from Board probe to ordinary operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from solver.board_profile_contracts import (
    BOARD_PROFILE_PHASE_RECORDED,
    BoardProfilePhaseRecorded,
    ProfilePhaseRecord,
)
from solver.event_store import EventStore, InvalidEventError
from solver.redaction import Redactor


@dataclass(frozen=True)
class ProfilePhase:
    probe_id: str = ""
    rules_digest: str = ""
    peer_identity_digest: str = ""
    decision: str = ""
    receipt_digest: str = ""
    operations_opened: bool = False
    starts: int = 0

    @property
    def authoritative(self) -> bool:
        return self.decision == "authoritative"


def project_profile_phase(events) -> ProfilePhase:
    state = ProfilePhase()
    for event in events:
        if event.event_type != BOARD_PROFILE_PHASE_RECORDED:
            continue
        payload = event.payload
        record = ProfilePhaseRecord(payload["record"])
        if record is ProfilePhaseRecord.PROBE_STARTED:
            if state.decision or state.operations_opened:
                raise InvalidEventError("Board-profile probe reopened after its decision", sequence=event.sequence)
            state = ProfilePhase(
                probe_id=payload["probe_id"],
                rules_digest=payload["rules_digest"],
                peer_identity_digest=payload["peer_identity_digest"],
                starts=state.starts + 1,
            )
            continue
        if not state.probe_id or payload["probe_id"] != state.probe_id or payload["rules_digest"] != state.rules_digest:
            raise InvalidEventError("Board-profile decision does not match its active probe", sequence=event.sequence)
        if record is ProfilePhaseRecord.PROFILE_DECIDED:
            if state.decision:
                raise InvalidEventError("Board profile was decided more than once", sequence=event.sequence)
            state = ProfilePhase(
                probe_id=state.probe_id,
                rules_digest=state.rules_digest,
                peer_identity_digest=state.peer_identity_digest,
                decision=payload["decision"],
                receipt_digest=payload["receipt_digest"],
                starts=state.starts,
            )
            continue
        if not state.authoritative or state.operations_opened:
            raise InvalidEventError(
                "Board operations opened outside one authoritative decision", sequence=event.sequence
            )
        if payload["receipt_digest"] != state.receipt_digest:
            raise InvalidEventError("Board operation gate names another receipt", sequence=event.sequence)
        state = ProfilePhase(
            probe_id=state.probe_id,
            rules_digest=state.rules_digest,
            peer_identity_digest=state.peer_identity_digest,
            decision=state.decision,
            receipt_digest=state.receipt_digest,
            operations_opened=True,
            starts=state.starts,
        )
    return state


class ProfilePhaseWriter:
    """Append idempotent phase transitions through the canonical writer."""

    def __init__(self, state: Path, run_id: str, redactor: Redactor, timestamp) -> None:
        self._store = EventStore(state, run_id=run_id, redactor=redactor)
        self._timestamp = timestamp

    def state(self) -> ProfilePhase:
        return project_profile_phase(self._store.events())

    def next_probe_id(self) -> str:
        return f"profile-probe-{self.state().starts + 1:06d}"

    def start(self, probe_id: str, rules_digest: str, peer_identity_digest: str) -> ProfilePhase:
        current = self.state()
        if current.decision:
            return current
        serial = current.starts + 1
        self._store.append(
            BoardProfilePhaseRecorded(
                event_id=f"board-profile-phase:{serial:06d}:start",
                probe_id=probe_id,
                record=ProfilePhaseRecord.PROBE_STARTED,
                rules_digest=rules_digest,
                peer_identity_digest=peer_identity_digest,
                ts=self._timestamp(),
            ),
            body=b"",
        )
        return self.state()

    def decide(self, probe_id: str, rules_digest: str, authoritative: bool, receipt_digest: str) -> ProfilePhase:
        current = self.state()
        decision = "authoritative" if authoritative else "refused"
        if current.decision:
            if (current.probe_id, current.rules_digest, current.decision, current.receipt_digest) != (
                probe_id,
                rules_digest,
                decision,
                receipt_digest,
            ):
                raise InvalidEventError("Board-profile decision conflicts with canonical state")
            return current
        self._store.append(
            BoardProfilePhaseRecorded(
                event_id=f"board-profile-phase:{current.starts:06d}:decision",
                probe_id=probe_id,
                record=ProfilePhaseRecord.PROFILE_DECIDED,
                rules_digest=rules_digest,
                decision=decision,
                receipt_digest=receipt_digest,
                ts=self._timestamp(),
            ),
            body=b"",
        )
        return self.state()

    def open_operations(self) -> ProfilePhase:
        current = self.state()
        if current.operations_opened:
            return current
        if not current.authoritative:
            raise PermissionError("Board operations require an authoritative profile")
        self._store.append(
            BoardProfilePhaseRecorded(
                event_id=f"board-profile-phase:{current.starts:06d}:operations",
                probe_id=current.probe_id,
                record=ProfilePhaseRecord.OPERATIONS_OPENED,
                rules_digest=current.rules_digest,
                decision=current.decision,
                receipt_digest=current.receipt_digest,
                ts=self._timestamp(),
            ),
            body=b"",
        )
        return self.state()


__all__ = ["ProfilePhase", "ProfilePhaseWriter", "project_profile_phase"]
