"""Controller-only authority derived from one owned active Instance Lease."""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass, field

from solver.event_store import InvalidEventError
from solver.instance_lease import LeaseCoordinator
from solver.instance_lease_contracts import LeaseGrant, LeaseIdentity, LeasePhase, LeaseVerdict
from solver.work_generation import GenerationFence, GenerationIdentity


class TargetAuthorityRefused(PermissionError):
    """No current owned Lease can authorize the declared Target."""


@dataclass(frozen=True)
class LeaseTargetGrant:
    """Exact connection authority retained by the Controller, never the worker."""

    lease_identity: LeaseIdentity
    challenge_id: int | str
    board_id: str
    owner_id: str
    epoch: int
    generation_id: str
    attempt_id: str
    row_id: str
    exact_connection: str = field(repr=False)
    until: dt.datetime | None = None
    lease_operation_key: str = ""
    generation_authority_sequence: int = 0
    generation_authority_event_id: str = ""

    def __post_init__(self) -> None:
        if (
            not self.lease_identity.run_id
            or self.lease_identity.lease_seq <= 0
            or not self.board_id
            or not self.owner_id
            or self.epoch <= 0
            or not self.generation_id
            or not self.attempt_id
            or not self.row_id
            or not self.exact_connection
            or not self.lease_operation_key
            or self.generation_authority_sequence <= 0
            or not self.generation_authority_event_id
        ):
            raise ValueError("Lease-derived Target authority is incomplete")

    @property
    def work_id(self) -> str:
        kind = "integer" if isinstance(self.challenge_id, int) and not isinstance(self.challenge_id, bool) else "string"
        return f"{kind}:{self.challenge_id}"

    @property
    def connection_digest(self) -> str:
        return hashlib.sha256(self.exact_connection.encode()).hexdigest()

    def document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "authority": "lease-target",
            "run_id": self.lease_identity.run_id,
            "lease_seq": self.lease_identity.lease_seq,
            "challenge_id": self.challenge_id,
            "board_id": self.board_id,
            "owner_id": self.owner_id,
            "epoch": self.epoch,
            "generation_id": self.generation_id,
            "attempt_id": self.attempt_id,
            "row_id": self.row_id,
            "connection_digest": self.connection_digest,
            "until": self.until.isoformat() if self.until else "",
            "lease_operation_key": self.lease_operation_key,
            "generation_authority_sequence": self.generation_authority_sequence,
            "generation_authority_event_id": self.generation_authority_event_id,
        }


class LeaseTargetAuthority:
    """Mint and reauthorize Target grants against Lease and Work-generation truth."""

    def __init__(
        self,
        leases: LeaseCoordinator,
        generations: GenerationFence,
        *,
        run_id: str,
        board_id: str,
        owner_id: str,
    ) -> None:
        if not run_id or not board_id or not owner_id or generations.run_id != run_id:
            raise ValueError("Lease-derived Target authority identity is incomplete")
        self._leases = leases
        self._generations = generations
        self._run_id = run_id
        self._board_id = board_id
        self._owner_id = owner_id

    def issue(
        self,
        identity: LeaseIdentity,
        epoch: int,
        *,
        generation: GenerationIdentity,
    ) -> LeaseTargetGrant:
        if generation.work_id == "" or generation.attempt_id == "":
            raise TargetAuthorityRefused("Target authority requires one Work generation")
        try:
            grant = self._generations.inspect_current(
                generation.generation_id,
                lambda: self._issue_current(identity, epoch, generation),
            )
        except (InvalidEventError, KeyError, ValueError) as error:
            raise TargetAuthorityRefused("Target authority has no current owned Lease") from error
        if grant is None:
            raise TargetAuthorityRefused("Target authority has no current owned Lease")
        return grant

    def reauthorize(self, grant: LeaseTargetGrant) -> bool:
        try:
            current = self._generations.inspect_current(
                grant.generation_id,
                lambda: self._matches_current(grant),
            )
        except (InvalidEventError, KeyError, ValueError):
            return False
        return current is True

    def _issue_current(
        self,
        identity: LeaseIdentity,
        epoch: int,
        generation: GenerationIdentity,
    ) -> LeaseTargetGrant:
        lease = self._leases.current(identity)
        self._validate_lease(lease, epoch, generation)
        return LeaseTargetGrant(
            lease.identity,
            lease.challenge_id,
            lease.board_id,
            lease.owner_id,
            lease.epoch,
            lease.generation_id,
            lease.attempt_id,
            lease.row_id,
            lease.target,
            lease.until,
            lease.operation_key,
            lease.generation_authority_sequence,
            lease.generation_authority_event_id,
        )

    def _matches_current(self, grant: LeaseTargetGrant) -> bool:
        lease = self._leases.current(grant.lease_identity)
        try:
            self._validate_lease(
                lease,
                grant.epoch,
                GenerationIdentity(grant.generation_id, grant.work_id, grant.attempt_id),
            )
        except TargetAuthorityRefused:
            return False
        return lease.row_id == grant.row_id and lease.target == grant.exact_connection

    def _validate_lease(self, lease: LeaseGrant, epoch: int, generation: GenerationIdentity) -> None:
        kind = (
            "integer" if isinstance(lease.challenge_id, int) and not isinstance(lease.challenge_id, bool) else "string"
        )
        if (
            lease.identity.run_id != self._run_id
            or lease.board_id != self._board_id
            or lease.owner_id != self._owner_id
            or lease.epoch != epoch
            or lease.generation_id != generation.generation_id
            or lease.attempt_id != generation.attempt_id
            or generation.work_id != f"{kind}:{lease.challenge_id}"
            or lease.phase is not LeasePhase.ATTEMPT_BOUND
            or lease.verdict is not LeaseVerdict.NONE
            or not lease.row_id
            or not lease.target
            or lease.generation_authority_sequence <= 0
            or not lease.generation_authority_event_id
        ):
            raise TargetAuthorityRefused("Target authority has no current owned Lease")


__all__ = ["LeaseTargetAuthority", "LeaseTargetGrant", "TargetAuthorityRefused"]
