"""Lease-derived Target authority stays exact, owned and generation-fenced."""

import datetime as dt
import json

import pytest

from solver.board import ANSWERED, Reply
from solver.event_store_contracts import GenerationAuthority
from solver.instance import Instances, Terms
from solver.instance_lease import LeaseCoordinator
from solver.instance_lease_contracts import LeasePhase, LeaseVerdict, RowCorroboration
from solver.lease_target import LeaseTargetAuthority, TargetAuthorityRefused
from solver.record import Recorder
from solver.redaction import Redactor

NOW = dt.datetime(2026, 9, 14, 8, tzinfo=dt.timezone.utc)


class BoardPort:
    def __init__(self) -> None:
        self.calls = []

    def deploy_instance(self, challenge_id):
        self.calls.append(("create", challenge_id))
        return Reply(ANSWERED, "target.example:31337", NOW + dt.timedelta(hours=1))

    def renew_instance(self, challenge_id):
        self.calls.append(("renew", challenge_id))
        return Reply(ANSWERED, until=NOW + dt.timedelta(hours=2))

    def terminate_instance(self, challenge_id):
        self.calls.append(("release", challenge_id))
        return Reply(ANSWERED)


def system(tmp_path, *, corroboration=None):
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    fence = recorder.generations
    generation = fence.acquire("integer:42", "attempt-1")
    write = recorder.write_authority
    board = BoardPort()
    leases = LeaseCoordinator(
        write,
        board,
        run_id="run-1",
        board_id="board-1",
        owner_id="team-7",
        corroborate=corroboration or (lambda _challenge_id: RowCorroboration(row_id="owned-row-42")),
        admit_generation=lambda generation_id, effect: fence.authorize_and_commit(
            generation_id,
            GenerationAuthority.AUTHORITY,
            lambda grant: effect(grant.sequence, grant.event_id),
        )[1],
        generation_events=fence.store.events,
    )
    targets = LeaseTargetAuthority(
        leases,
        fence,
        run_id="run-1",
        board_id="board-1",
        owner_id="team-7",
    )
    instances = Instances(
        board,
        recorder,
        now=lambda: NOW,
        coordinator=leases,
        target_authority=targets,
    )
    return board, write, fence, generation, leases, targets, instances


def test_owned_active_lease_issues_exact_opaque_target_authority_and_tracks_renewal(tmp_path):
    board, write, _fence, generation, leases, targets, instances = system(tmp_path)
    answer = instances.deploy(
        Terms(42, "dynamic_iac", timeout=3600),
        attempt_id=generation.attempt_id,
        generation_id=generation.generation_id,
    )
    held = leases.current(answer.lease.identity)

    target = instances.issue_target(
        answer.lease,
        attempt_id=generation.attempt_id,
        generation_id=generation.generation_id,
    )

    assert target.exact_connection == "target.example:31337"
    assert target.challenge_id == 42
    assert target.row_id == "owned-row-42"
    assert target.work_id == "integer:42"
    assert targets.reauthorize(target) is True
    assert "target.example:31337" not in json.dumps(target.document())

    renewed = leases.renew(
        held.identity,
        held.epoch,
        generation=generation,
        until=NOW + dt.timedelta(hours=2),
    )
    assert renewed.phase is LeasePhase.ATTEMPT_BOUND
    assert targets.reauthorize(target) is True

    leases.release(held.identity, held.epoch, generation=generation)
    assert targets.reauthorize(target) is False
    with pytest.raises(TargetAuthorityRefused):
        targets.issue(held.identity, held.epoch, generation=generation)
    assert board.calls == [("create", 42), ("renew", 42), ("release", 42)]
    write.close()


def test_foreign_or_unsettled_instance_never_mints_target_authority(tmp_path):
    _board, write, _fence, generation, leases, targets, _instances = system(
        tmp_path,
        corroboration=lambda _challenge_id: RowCorroboration(LeaseVerdict.FOREIGN_ROW),
    )
    unsettled = leases.acquire(42, generation=generation)

    assert unsettled.phase is LeasePhase.RECOVERABLE
    with pytest.raises(TargetAuthorityRefused):
        targets.issue(unsettled.identity, unsettled.epoch, generation=generation)
    write.close()


def test_generation_restart_revokes_target_without_mutating_the_instance(tmp_path):
    board, write, fence, generation, leases, targets, _instances = system(tmp_path)
    held = leases.acquire(42, generation=generation)
    target = targets.issue(held.identity, held.epoch, generation=generation)

    assert fence.reconcile_restart() == (generation.generation_id,)

    assert targets.reauthorize(target) is False
    assert leases.current(held.identity).phase is LeasePhase.ATTEMPT_BOUND
    assert board.calls == [("create", 42)]
    write.close()
