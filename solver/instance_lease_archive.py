"""Filesystem boundary for replaying and durably closing earlier-Run Leases."""

import json
from pathlib import Path

from solver.instance_lease_contracts import LeaseGrant, LeaseCloseCause, LeasePhase, grant_document
from solver.instance_lease_projection import replay_leases
from solver.write_reservation import Capacity, EffectIdentity, ReservedEffect, RetentionPolicy, WriteAuthority
from solver.write_reservation_contracts import profile_from
from solver.write_reservation_storage import AuthorityStorage


def replay_leases_across_runs(state: Path, board_id: str) -> tuple[LeaseGrant, ...]:
    leases = []
    for run_dir in sorted((Path(state) / "runs").glob("*")):
        profile_path = run_dir / "write-authority" / "profile.json"
        if not profile_path.is_file():
            continue
        profile = profile_from(json.loads(profile_path.read_bytes()))
        storage = AuthorityStorage(run_dir, profile, provision=False)
        with storage.locked():
            leases.extend(replay_leases(tuple(storage.latest_locked().values()), run_dir.name, board_id))
    return tuple(sorted(leases, key=lambda grant: (grant.identity.run_id, grant.identity.lease_seq)))


def persist_reconciled_close(state: Path, grant: LeaseGrant, snapshot_id: str, *, hook=None) -> LeaseGrant:
    run_dir = Path(state) / "runs" / grant.identity.run_id
    profile = profile_from(json.loads((run_dir / "write-authority" / "profile.json").read_bytes()))
    authority = WriteAuthority(run_dir, profile, hook=hook)
    try:
        closed = LeaseGrant(**{**grant.__dict__, "phase": LeasePhase.CLOSED, "close_cause": LeaseCloseCause.TERMINATED})
        replayed = replay_leases(authority.reservations(), grant.identity.run_id, grant.board_id)
        existing = next(
            (item for item in replayed if item.identity == grant.identity and item.phase is LeasePhase.CLOSED), None
        )
        if existing is not None:
            return existing
        if hook:
            hook("before_archive_write")
        subject = json.dumps(
            {**grant_document(grant), "snapshot_id": snapshot_id}, sort_keys=True, separators=(",", ":")
        )
        ReservedEffect(authority).execute(
            f"{grant.operation_key}:reconciled:{snapshot_id}:close",
            EffectIdentity("lease.release", subject),
            Capacity(2048, 1, 3),
            lambda: None,
            encode=lambda _result: grant_document(closed),
            decode=lambda _document: closed,
            retention=RetentionPolicy.RECORD,
        )
        if hook:
            hook("after_archive_write")
        return closed
    finally:
        authority.close()


__all__ = ["persist_reconciled_close", "replay_leases_across_runs"]
