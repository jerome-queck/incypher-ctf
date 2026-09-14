"""Boot reconstruction of fixed Instance authority reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from solver.instance_reconciliation_contracts import AdmissionVerdict, ReconciliationResult
from solver.recovery.contracts import ProbationOutcome
from solver.recovery.runtime import AuthoritativeChange, DomainRecovery

INSTANCE_ADAPTER = "instance-reconciliation-v1"


@dataclass(frozen=True)
class InstanceObservation:
    join_digest: str
    reconcile: Callable[[], ReconciliationResult]
    evidence: bytes
    probation: Callable[[], ProbationOutcome] | None = None


def instance_recovery(
    failed_join_digest: str,
    observe: Callable[[], InstanceObservation],
) -> DomainRecovery:
    """Reread authorities, then reconcile only when their joined state changed."""
    pending: list[InstanceObservation] = []
    results: list[ReconciliationResult] = []

    def project():
        pending[:] = [observe()]
        current = pending[0]
        if current.join_digest == failed_join_digest:
            return None
        return AuthoritativeChange(
            failed_join_digest,
            current.join_digest,
            "authenticated-ledger-lease-generation-join",
        )

    def apply():
        if not pending:
            return False
        results[:] = [pending[0].reconcile()]
        return True

    def prove_probation():
        if not pending:
            pending[:] = [observe()]
        current = pending[0]
        if current is not None and current.probation is not None:
            return current.probation()
        return (
            ProbationOutcome.PASSED
            if results and results[0].verdict is AdmissionVerdict.OPEN
            else ProbationOutcome.UNSETTLED
        )

    return DomainRecovery(
        "instance-authority-join",
        project,
        apply,
        prove_probation,
        capture=lambda: pending[0].evidence if pending else b"",
        adapter_id=INSTANCE_ADAPTER,
        adapter_config={"failed_join_digest": failed_join_digest},
    )
