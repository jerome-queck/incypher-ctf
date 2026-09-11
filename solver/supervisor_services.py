"""The fixed typed startup graph for one Supervisor Boot."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from solver.event_store_contracts import ServiceName
from solver.supervisor_process import BootProcess


@dataclass(frozen=True)
class SupervisorServices:
    """Typed service ports in their one permitted dependency order."""

    verify_replay: Callable[[], object]
    admit_storage: Callable[[], None]
    launch_controller: Callable[[str], BootProcess]
    reap_children: Callable[[], int]

    def admit(self) -> tuple[ServiceName, ServiceName]:
        self.verify_replay()
        self.admit_storage()
        return ServiceName.VERIFIED_REPLAY, ServiceName.STORAGE_ADMISSION


__all__ = ["ServiceName", "SupervisorServices"]
