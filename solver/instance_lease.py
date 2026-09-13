"""One durable coordinator fences every Instance Lease and Board effect."""

from __future__ import annotations

import datetime as dt
import json
import threading
from collections.abc import Callable
from pathlib import Path

from solver.board import ABSENT, ANSWERED, Reply
from solver.instance_lease_contracts import (
    LeaseCloseCause,
    LeaseGrant,
    LeaseIdentity,
    LeasePhase,
    LeaseVerdict,
    RowCorroboration,
    grant_document,
    grant_from_document,
)
from solver.instance_lease_projection import replay_leases
from solver.instance_lease_receipt import link_manifest, write_receipt
from solver.manifest import canonical_manifest_bytes, parse_manifest
from solver.event_store_storage import atomic_write
from solver.work_generation import GenerationIdentity
from solver.write_reservation import (
    Capacity,
    EffectIdentity,
    ReservationConflict,
    ReservedEffect,
    RetentionPolicy,
    WriteAuthority,
)

LEASE_EFFECT_CAPACITY = Capacity(bytes=2048, objects=1, operations=3)


class StaleLease(RuntimeError):
    """An executor presented superseded Lease or Work-generation authority."""


class LeaseContended(RuntimeError):
    """Another Attempt already holds the Challenge's active Lease."""


class LeaseCoordinator:
    """The only Run-scoped port which may create, renew or end a Lease."""

    def __init__(
        self,
        authority: WriteAuthority,
        board,
        *,
        run_id: str,
        board_id: str,
        owner_id: str,
        corroborate: Callable[[int | str], RowCorroboration],
        admit_generation: Callable[[str, Callable[[int, str], LeaseGrant]], LeaseGrant | None],
        generation_events: Callable[[], list] = list,
    ) -> None:
        if not run_id or not board_id or not owner_id:
            raise ValueError("Lease coordinator identity is incomplete")
        self._authority, self._effect, self._board = authority, ReservedEffect(authority), board
        self._run_id, self._board_id, self._owner_id = run_id, board_id, owner_id
        self._corroborate, self._lock = corroborate, threading.RLock()
        self._admit_generation, self._generation_events = admit_generation, generation_events
        replayed = replay_leases(authority.reservations(), run_id, board_id)
        self._leases = {grant.identity: grant for grant in replayed}
        self._active = {grant.challenge_id: grant for grant in replayed if grant.phase is not LeasePhase.CLOSED}
        self._next_seq = max((grant.identity.lease_seq for grant in replayed), default=0) + 1

    def acquire(self, challenge_id: int | str, *, generation: GenerationIdentity) -> LeaseGrant:
        self._validate_generation(challenge_id, generation)
        with self._lock:
            if existing := self._active.get(challenge_id):
                self._record_contender(existing, generation.attempt_id)
                raise LeaseContended(f"Challenge {challenge_id!r} is fenced by Lease {existing.identity.lease_seq}")
            identity = LeaseIdentity(self._run_id, self._next_seq)
            self._next_seq += 1
            reserved = LeaseGrant(
                identity,
                challenge_id,
                self._board_id,
                self._owner_id,
                1,
                generation.generation_id,
                generation.attempt_id,
                LeasePhase.RESERVED,
                self._key(identity, "create", 1),
            )
            grant = self._admit_effect(
                generation, reserved, "create", lambda: self._board.deploy_instance(challenge_id)
            )
            self._remember(grant)
            return grant

    def renew(
        self, identity: LeaseIdentity, epoch: int, *, generation: GenerationIdentity, until: dt.datetime
    ) -> LeaseGrant:
        with self._lock:
            current = self._current(identity, epoch, generation)
            renewal_sequence = (
                current.renewal_sequence
                if current.verdict is LeaseVerdict.RENEW_AMBIGUOUS
                else current.renewal_sequence + 1
            )
            proposed = LeaseGrant(
                **{
                    **current.__dict__,
                    "operation_key": self._key(identity, "renew", epoch, str(renewal_sequence)),
                    "renewal_sequence": renewal_sequence,
                    "until": until,
                }
            )
            result = self._admit_effect(
                generation, proposed, "renew", lambda: self._board.renew_instance(current.challenge_id)
            )
            self._remember(result)
            return result

    def release(self, identity: LeaseIdentity, epoch: int, *, generation: GenerationIdentity) -> LeaseGrant:
        with self._lock:
            current = self._current(identity, epoch, generation)
            proposed = LeaseGrant(
                **{
                    **current.__dict__,
                    "phase": LeasePhase.RESERVED,
                    "operation_key": self._key(identity, "release", epoch),
                }
            )
            result = self._admit_effect(
                generation, proposed, "release", lambda: self._board.terminate_instance(current.challenge_id)
            )
            self._remember(result)
            return result

    def expire(
        self, identity: LeaseIdentity, epoch: int, *, generation: GenerationIdentity, corroborated_absence: str
    ) -> LeaseGrant:
        if not corroborated_absence:
            raise ValueError("Lease expiry requires corroborated absence")
        with self._lock:
            current = self._current(identity, epoch, generation)
            proposed = LeaseGrant(
                **{
                    **current.__dict__,
                    "phase": LeasePhase.RESERVED,
                    "operation_key": self._key(identity, "expire", epoch, corroborated_absence),
                }
            )
            result = self._admit_effect(generation, proposed, "expire", lambda: Reply(ABSENT))
            self._remember(result)
            return result

    def current(self, identity: LeaseIdentity) -> LeaseGrant:
        return self._leases[identity]

    def active(self, challenge_id: int | str) -> LeaseGrant | None:
        return self._active.get(challenge_id)

    def leases(self) -> tuple[LeaseGrant, ...]:
        """Return the replay-derived Lease set for Boot reconciliation."""
        return tuple(sorted(self._leases.values(), key=lambda grant: grant.identity.lease_seq))

    def reconcile_release(self, identity: LeaseIdentity, epoch: int, *, snapshot_id: str) -> LeaseGrant:
        """Execute one fixed cleanup proved by an authenticated reconciliation snapshot."""
        if not snapshot_id:
            raise ValueError("Reconciliation cleanup requires snapshot evidence")
        with self._lock:
            current = self._leases.get(identity)
            if current is None or current.epoch != epoch or current.phase is LeasePhase.CLOSED:
                raise StaleLease("Lease identity or epoch no longer owns cleanup authority")
            proposed = LeaseGrant(
                **{
                    **current.__dict__,
                    "phase": LeasePhase.RESERVED,
                    "operation_key": self._key(identity, "release", epoch),
                }
            )
            result = self._run_effect(proposed, "release", lambda: self._board.terminate_instance(current.challenge_id))
            self._remember(result)
            return result

    def reconcile_grant_release(self, grant: LeaseGrant, *, snapshot_id: str) -> LeaseGrant:
        """Discharge one conclusively attributed Lease, including an earlier Run's."""
        if grant.board_id != self._board_id or grant.owner_id != self._owner_id:
            raise StaleLease("Reconciliation evidence belongs to another Board owner")
        if grant.identity.run_id == self._run_id:
            return self.reconcile_release(grant.identity, grant.epoch, snapshot_id=snapshot_id)
        proposed = LeaseGrant(
            **{
                **grant.__dict__,
                "phase": LeasePhase.RESERVED,
                "operation_key": self._key(grant.identity, "release", grant.epoch),
            }
        )
        return self._run_effect(proposed, "release", lambda: self._board.terminate_instance(grant.challenge_id))

    def write_receipt(self, destination: Path, *, manifest_path: Path | None = None) -> Path:
        path = write_receipt(self._authority, self._run_id, self._board_id, destination)
        if manifest_path is not None and manifest_path.exists():
            linked = link_manifest(
                parse_manifest(manifest_path.read_bytes()), path, self._authority, self._generation_events()
            )
            atomic_write(manifest_path, canonical_manifest_bytes(linked) + b"\n")
        return path

    def _admit_effect(self, generation, proposed, operation, call) -> LeaseGrant:
        def committed(sequence: int, event_id: str) -> LeaseGrant:
            admitted = LeaseGrant(
                **{
                    **proposed.__dict__,
                    "generation_authority_sequence": sequence,
                    "generation_authority_event_id": event_id,
                }
            )
            return self._run_effect(admitted, operation, call)

        result = self._admit_generation(generation.generation_id, committed)
        if result is None:
            raise StaleLease("Work generation is durably closed or superseded")
        return result

    def _run_effect(self, proposed: LeaseGrant, operation: str, call) -> LeaseGrant:
        try:
            return self._effect.execute(
                proposed.operation_key,
                EffectIdentity(
                    f"lease.{operation}",
                    json.dumps(self._intent_document(proposed), sort_keys=True, separators=(",", ":")),
                ),
                LEASE_EFFECT_CAPACITY,
                call,
                encode=lambda reply: self._classify(proposed, operation, reply),
                decode=grant_from_document,
                retention=RetentionPolicy.RECORD,
            )
        except ReservationConflict:
            raise
        except Exception:
            verdict = {
                "create": LeaseVerdict.CREATE_AMBIGUOUS,
                "renew": LeaseVerdict.RENEW_AMBIGUOUS,
                "release": LeaseVerdict.RELEASE_AMBIGUOUS,
                "expire": LeaseVerdict.EXPIRY_AMBIGUOUS,
            }.get(operation, LeaseVerdict.RELEASE_AMBIGUOUS)
            return LeaseGrant(**{**proposed.__dict__, "phase": LeasePhase.RECOVERABLE, "verdict": verdict})

    def _classify(self, proposed: LeaseGrant, operation: str, reply: Reply) -> dict[str, object]:
        answered = reply.outcome in {ANSWERED, ABSENT}
        row = RowCorroboration(LeaseVerdict.UNCORROBORATED_ROW)
        if operation == "create" and reply.outcome == ANSWERED:
            row = self._corroborate(proposed.challenge_id)
        if operation == "create":
            phase = LeasePhase.ATTEMPT_BOUND if reply.connection_info and row.row_id else LeasePhase.RECOVERABLE
            verdict = row.verdict if not row.row_id else LeaseVerdict.NONE
            if reply.outcome != ANSWERED:
                verdict = LeaseVerdict.CREATE_UNSETTLED
            cause = LeaseCloseCause.NONE
        elif operation in {"release", "expire"}:
            phase = LeasePhase.CLOSED if answered else LeasePhase.RECOVERABLE
            unsettled = LeaseVerdict.EXPIRY_UNSETTLED if operation == "expire" else LeaseVerdict.RELEASE_UNSETTLED
            verdict = LeaseVerdict.NONE if answered else unsettled
            cause = LeaseCloseCause.EXPIRED if operation == "expire" else LeaseCloseCause.TERMINATED
            if not answered:
                cause = LeaseCloseCause.NONE
        else:
            phase = proposed.phase if answered else LeasePhase.RECOVERABLE
            verdict = LeaseVerdict.NONE if answered else LeaseVerdict.RENEW_UNSETTLED
            cause = proposed.close_cause
        grant = LeaseGrant(
            **{
                **proposed.__dict__,
                "row_id": row.row_id or proposed.row_id,
                "phase": phase,
                "verdict": verdict,
                "close_cause": cause,
                "target": reply.connection_info or proposed.target,
                "until": reply.until or proposed.until,
            }
        )
        return grant_document(grant)

    def _record_contender(self, existing: LeaseGrant, attempt_id: str) -> None:
        subject = json.dumps(
            {
                "contender_attempt_id": attempt_id,
                "lease_seq": existing.identity.lease_seq,
                "run_id": existing.identity.run_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        key = f"lease:{self._run_id}:{existing.identity.lease_seq}:contender:{attempt_id}"
        reservation = self._authority.reserve(
            key,
            EffectIdentity("lease.contender", subject),
            LEASE_EFFECT_CAPACITY,
            retention=RetentionPolicy.RECORD,
        )
        self._authority.commit(self._authority.start(reservation), {"result": "fenced", "active_epoch": existing.epoch})

    def _remember(self, grant: LeaseGrant) -> None:
        self._leases[grant.identity] = grant
        if grant.phase is LeasePhase.CLOSED:
            self._active.pop(grant.challenge_id, None)
        else:
            self._active[grant.challenge_id] = grant

    @staticmethod
    def _intent_document(proposed: LeaseGrant) -> dict[str, object]:
        """Stable semantic identity before an outcome mutates the projection."""
        return grant_document(
            LeaseGrant(
                **{
                    **proposed.__dict__,
                    "phase": LeasePhase.RESERVED,
                    "verdict": LeaseVerdict.NONE,
                    "close_cause": LeaseCloseCause.NONE,
                }
            )
        )

    def _current(self, identity: LeaseIdentity, epoch: int, generation: GenerationIdentity) -> LeaseGrant:
        current = self._leases.get(identity)
        if (
            current is None
            or current.epoch != epoch
            or current.generation_id != generation.generation_id
            or current.attempt_id != generation.attempt_id
            or current.phase is LeasePhase.CLOSED
        ):
            raise StaleLease("Lease identity, epoch or Work generation no longer owns authority")
        return current

    @staticmethod
    def _validate_generation(challenge_id: int | str, generation: GenerationIdentity) -> None:
        kind = "integer" if isinstance(challenge_id, int) else "string"
        if generation.work_id != f"{kind}:{challenge_id}" or not generation.generation_id or not generation.attempt_id:
            raise StaleLease("Work generation does not belong to this Challenge")

    @staticmethod
    def _key(identity: LeaseIdentity, operation: str, epoch: int, discriminator: str = "") -> str:
        suffix = f":{discriminator}" if discriminator else ""
        return f"lease:{identity.run_id}:{identity.lease_seq}:{epoch}:{operation}{suffix}"


__all__ = ["LeaseContended", "LeaseCoordinator", "StaleLease"]
