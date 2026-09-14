"""Recovery composition for governed storage retirement."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.recovery.contracts import FaultKind, ProbationOutcome
from solver.recovery.runtime import AuthoritativeChange, DomainRecovery, RecoveryRegistry
from solver.storage_governor import StorageGovernor
from solver.storage_governor_contracts import ReachabilityRoots, RetirementCandidate

STORAGE_ADAPTER = "storage-governor-v1"


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

    def domain(replay_candidates=candidates, replay_roots=roots, replay_reason=reason, before=failed_revision):
        retired = []

        def apply():
            retired.append(governor.retire(replay_candidates, replay_roots, reason=replay_reason))
            return True

        revision = digest_bytes(
            canonical_bytes(
                {"candidates": [item.identity_dict() for item in replay_candidates], "reason": replay_reason}
            )
        )
        return DomainRecovery(
            "storage-revision",
            lambda: AuthoritativeChange(before, revision, "canonical-storage-classification"),
            apply,
            lambda: ProbationOutcome.PASSED if retired else ProbationOutcome.FAILED,
            capture=lambda: canonical_bytes([item.identity_dict() for item in replay_candidates]),
            adapter_id=STORAGE_ADAPTER,
            adapter_config={
                "candidates": json.dumps([item.identity_dict() for item in replay_candidates], sort_keys=True),
                "roots": json.dumps(replay_roots.as_dict(), sort_keys=True),
                "reason": replay_reason,
                "failed_revision": before,
            },
        )

    registry = RecoveryRegistry()
    registry.register(
        STORAGE_ADAPTER,
        lambda config: domain(
            tuple(RetirementCandidate(**item) for item in json.loads(config["candidates"])),
            ReachabilityRoots.from_dict(json.loads(config["roots"])),
            config["reason"],
            config["failed_revision"],
        ),
    )
    recovery.replay(registry)

    return recovery.handle(
        kind=FaultKind.STORAGE,
        fault_id=f"storage:{failed_revision}",
        scope="run-shared:storage",
        generation_id="storage-governor",
        evidence=reason,
        failed_action_value=failed_revision,
        original_deadline=now() + dt.timedelta(seconds=180),
        recovery=domain(),
    )
