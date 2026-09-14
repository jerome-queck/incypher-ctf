"""Shared no-inference containment for unsettled Target and Research reads."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

from solver.recovery.contracts import FaultKind, ProbationOutcome
from solver.recovery.runtime import DeterministicRecovery, DomainRecovery, RecoveryRegistry

SAFE_READ_ADAPTER = "safe-read-containment-v1"


@dataclass(frozen=True)
class SafeReadFault:
    fault_id: str
    scope: str
    generation_id: str
    evidence: str
    failed_request: str
    observed_at: dt.datetime


class SafeReadRecovery:
    """Keep identical reads fenced; brokers do not manufacture a changed retry."""

    def __init__(self, recovery: DeterministicRecovery, *, fence: Callable[[], None] = lambda: None) -> None:
        self._recovery = recovery
        self._fence = fence

    def contain(self, fault: SafeReadFault) -> None:
        self._recovery.handle(
            kind=FaultKind.TARGET_RESEARCH,
            fault_id=fault.fault_id,
            scope=fault.scope,
            generation_id=fault.generation_id,
            evidence=fault.evidence,
            failed_action_value=fault.failed_request,
            original_deadline=fault.observed_at + dt.timedelta(seconds=180),
            recovery=DomainRecovery(
                "request-attempt",
                lambda: None,
                lambda: False,
                lambda: ProbationOutcome.UNSETTLED,
                fence=self._fence,
                capture=lambda: fault.evidence.encode(),
                adapter_id=SAFE_READ_ADAPTER,
                adapter_config={"scope": fault.scope},
            ),
        )

    def replay(self) -> None:
        registry = RecoveryRegistry()
        registry.register(
            SAFE_READ_ADAPTER,
            lambda config: DomainRecovery(
                "request-attempt",
                lambda: None,
                lambda: False,
                lambda: ProbationOutcome.UNSETTLED,
                adapter_id=SAFE_READ_ADAPTER,
                adapter_config=config,
            ),
        )
        self._recovery.replay(registry)
