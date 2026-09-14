"""Recovery composition for governed storage retirement."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable

from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.recovery.contracts import FaultKind, ProbationOutcome
from solver.recovery.runtime import AuthoritativeChange, DomainRecovery, RecoveryRegistry
from solver.storage_governor import StorageGovernor
from solver.storage_governor_contracts import (
    STORAGE_GOVERNOR_RECORDED,
    ReachabilityRoots,
    RetirementCandidate,
    RetirementResult,
)

STORAGE_ADAPTER = "storage-governor-v1"


def _completed_retirement_digests(
    governor: StorageGovernor,
    candidates: tuple[RetirementCandidate, ...],
) -> frozenset[str]:
    """Project durable retirement completions for this recovery's candidates."""

    governor.replay_pending_retirements()
    expected = {
        (
            candidate.path,
            candidate.storage_class,
            candidate.digest,
            candidate.length,
            tuple(sorted(set(candidate.event_sequences))),
        ): candidate.digest
        for candidate in candidates
    }
    store = EventStore(governor.run_dir.parent.parent, run_id=governor.run_id)
    completed = set()
    for event in store.events():
        payload = event.payload
        if event.event_type != STORAGE_GOVERNOR_RECORDED or payload.get("record") != "retirement-complete":
            continue
        identity = (
            payload.get("path"),
            payload.get("storage_class"),
            payload.get("target_digest"),
            payload.get("target_length"),
            tuple(payload.get("event_sequences", ())),
        )
        if identity in expected:
            completed.add(expected[identity])
    return frozenset(completed)


def _retirement_domain(governor, replay_candidates, replay_roots, replay_reason, before):
    retirement_result: RetirementResult | None = None

    def apply():
        nonlocal retirement_result
        retirement_result = governor.retire(replay_candidates, replay_roots, reason=replay_reason)
        return bool(retirement_result.retired_digests)

    def probation():
        completed = _completed_retirement_digests(governor, replay_candidates)
        if retirement_result is not None and not retirement_result.retired_digests:
            return ProbationOutcome.FAILED
        return ProbationOutcome.PASSED if completed else ProbationOutcome.FAILED

    revision = digest_bytes(
        canonical_bytes({"candidates": [item.identity_dict() for item in replay_candidates], "reason": replay_reason})
    )
    return DomainRecovery(
        "storage-revision",
        lambda: AuthoritativeChange(before, revision, "canonical-storage-classification"),
        apply,
        probation,
        capture=lambda: canonical_bytes([item.identity_dict() for item in replay_candidates]),
        adapter_id=STORAGE_ADAPTER,
        adapter_config={
            "candidates": json.dumps([item.identity_dict() for item in replay_candidates], sort_keys=True),
            "roots": json.dumps(replay_roots.as_dict(), sort_keys=True),
            "reason": replay_reason,
            "failed_revision": before,
        },
    )


def _domain_from_config(governor, config):
    if "pressure_decision" in config:
        return DomainRecovery(
            "storage-revision",
            lambda: None,
            lambda: False,
            lambda: ProbationOutcome.FAILED,
            capture=lambda: config["pressure_decision"].encode(),
            adapter_id=STORAGE_ADAPTER,
            adapter_config=config,
        )
    return _retirement_domain(
        governor,
        tuple(RetirementCandidate(**item) for item in json.loads(config["candidates"])),
        ReachabilityRoots.from_dict(json.loads(config["roots"])),
        config["reason"],
        config["failed_revision"],
    )


def replay_storage_recovery(governor, recovery):
    registry = RecoveryRegistry()
    registry.register(STORAGE_ADAPTER, lambda config: _domain_from_config(governor, config))
    return recovery.replay(registry)


def contain_storage_pressure(recovery, decision, *, now):
    """Keep denied admission fenced until a classified retirement is supplied."""
    body = canonical_bytes(decision.as_dict())
    revision = digest_bytes(body)
    return recovery.handle(
        kind=FaultKind.STORAGE,
        fault_id=f"storage:{revision}",
        scope="run-shared:storage",
        generation_id="storage-governor",
        evidence="storage admission refused; no proved retirement inventory",
        failed_action_value=revision,
        original_deadline=now() + dt.timedelta(seconds=180),
        recovery=_domain_from_config(None, {"pressure_decision": body.decode()}),
    )


def recover_storage_pressure(
    governor: StorageGovernor,
    recovery,
    candidates: tuple[RetirementCandidate, ...],
    roots: ReachabilityRoots,
    *,
    failed_revision: str,
    reason: str,
    now: Callable[[], dt.datetime],
):
    """Authorize one changed storage revision, then perform actual governed retirement."""
    replay_storage_recovery(governor, recovery)
    return recovery.handle(
        kind=FaultKind.STORAGE,
        fault_id=f"storage:{failed_revision}",
        scope="run-shared:storage",
        generation_id="storage-governor",
        evidence=reason,
        failed_action_value=failed_revision,
        original_deadline=now() + dt.timedelta(seconds=180),
        recovery=_retirement_domain(governor, candidates, roots, reason, failed_revision),
    )
