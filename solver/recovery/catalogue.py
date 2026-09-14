"""Closed, versioned Core Recovery probe and Remedy catalogue."""

from dataclasses import dataclass

from solver.recovery.contracts import FaultKind

CATALOGUE_VERSION = "deterministic-recovery-v1"


@dataclass(frozen=True)
class CatalogueEntry:
    probe_id: str
    remedy_id: str
    remedy_version: str
    changed_dimension: str
    scope_prefix: str
    maximum_uses: int = 1


_ENTRIES = {
    FaultKind.WORKER_CRASH: CatalogueEntry(
        "process-tree-v1",
        "replace-owned-process",
        "1",
        "process-generation",
        "owner-local:",
    ),
    FaultKind.ROUTE_LOCAL_INFERENCE: CatalogueEntry(
        "inference-route-health-v1",
        "restart-local-inference-harness",
        "1",
        "inference-route",
        "owner-local:",
    ),
    FaultKind.TARGET_RESEARCH: CatalogueEntry(
        "target-research-transport-v1",
        "retry-safe-read",
        "1",
        "request-attempt",
        "external:",
    ),
    FaultKind.INSTANCE: CatalogueEntry(
        "instance-authority-v1",
        "reconcile-instance-authority",
        "1",
        "instance-authority-join",
        "external:",
    ),
    FaultKind.SUBMISSION_AMBIGUITY: CatalogueEntry(
        "submission-authority-v1",
        "advance-submission-epoch",
        "1",
        "submission-epoch",
        "run-shared:",
    ),
    FaultKind.STORAGE: CatalogueEntry(
        "storage-authority-v1",
        "govern-storage",
        "1",
        "storage-revision",
        "run-shared:",
    ),
    FaultKind.FINAL_INTERVAL: CatalogueEntry(
        "final-interval-authority-v1",
        "reconcile-final-interval",
        "1",
        "final-interval-phase",
        "run-shared:",
    ),
}


def entry_for(kind: FaultKind, scope: str = "") -> CatalogueEntry:
    try:
        entry = _ENTRIES[kind]
    except KeyError:
        raise ValueError(f"{kind.value} has no deterministic Core Recovery entry") from None
    if scope and not scope.startswith(entry.scope_prefix):
        raise ValueError(f"{kind.value} is not applicable to {scope}")
    return entry
