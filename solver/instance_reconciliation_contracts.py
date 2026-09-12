"""Public typed contract for Boot Instance reconciliation."""

from dataclasses import dataclass
from enum import Enum


class AdmissionVerdict(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True)
class BootOwnership:
    predecessor_close_event_ids: tuple[str, ...]
    interrupted_attempt_close_event_ids: tuple[str, ...]
    interrupted_attempts_closed: bool
    predecessors_closed: bool = True

    @property
    def proved(self) -> bool:
        return self.predecessors_closed and self.interrupted_attempts_closed

    def document(self) -> dict[str, object]:
        return {
            "predecessor_close_event_ids": list(self.predecessor_close_event_ids),
            "interrupted_attempt_close_event_ids": list(self.interrupted_attempt_close_event_ids),
            "interrupted_attempts_closed": self.interrupted_attempts_closed,
            "predecessors_closed": self.predecessors_closed,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    boot_id: str
    snapshot_id: str
    join_digest: str
    actions: tuple[str, ...]
    unsettled: tuple[str, ...]
    verdict: AdmissionVerdict
    completion_key: str = ""
    ownership: BootOwnership = BootOwnership((), (), False)

    def document(self) -> dict[str, object]:
        return {
            "boot_id": self.boot_id,
            "snapshot_id": self.snapshot_id,
            "join_digest": self.join_digest,
            "actions": list(self.actions),
            "unsettled": list(self.unsettled),
            "admission": self.verdict.value,
            "completion_key": self.completion_key,
            "ownership": self.ownership.document(),
        }


__all__ = ["AdmissionVerdict", "BootOwnership", "ReconciliationResult"]
