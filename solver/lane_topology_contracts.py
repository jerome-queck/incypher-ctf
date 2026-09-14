"""Typed public contract for the one- or two-Lane scheduling topology."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from solver.attempt_executor_contracts import EnvelopeSpec
from solver.instance_lease_contracts import LeaseIdentity
from solver.work_generation import GenerationIdentity


class LaneVerdict(str, Enum):
    COMPLETE = "complete"
    FAILED = "failed"
    STALLED = "stalled"


class LaneJournalState(str, Enum):
    RESERVED = "reserved"
    ACTIVE = "active"
    CLOSING_COMPLETE = "closing-complete"
    CLOSING_FAILED = "closing-failed"
    CLOSING_STALLED = "closing-stalled"
    COMPLETE = "complete"
    FAILED = "failed"
    STALLED = "stalled"

    @property
    def unsettled(self) -> bool:
        return self in {self.RESERVED, self.ACTIVE}

    @property
    def closing(self) -> bool:
        return self in {self.CLOSING_COMPLETE, self.CLOSING_FAILED, self.CLOSING_STALLED}

    @classmethod
    def closing_for(cls, verdict: LaneVerdict) -> LaneJournalState:
        return cls(f"closing-{verdict.value}")

    @property
    def verdict(self) -> LaneVerdict:
        if not self.closing:
            raise ValueError("only a closing Lane journal state has a pending verdict")
        return LaneVerdict(self.value.removeprefix("closing-"))


@dataclass(frozen=True)
class OwnerTermination:
    ended: bool
    evidence_digest: str
    remaining_processes: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.evidence_digest or (self.ended and self.remaining_processes):
            raise ValueError("owner termination requires bounded evidence and no surviving process")


@dataclass(frozen=True)
class LaneProfile:
    lanes: int = 1
    global_resource_units: int = 1
    global_cpu_quota_us: int = 100_000
    global_memory_bytes: int = 2048
    global_pids: int = 8
    global_filesystem_bytes: int = 4096
    global_wall_seconds: int = 3600
    global_cleanup_seconds: float = 10

    def __post_init__(self) -> None:
        if self.lanes not in {1, 2}:
            raise ValueError("Lane profile supports exactly one or two Lanes")
        if self.global_resource_units < self.lanes:
            raise ValueError("enabled Lanes require independent global Resource capacity")
        if (
            min(
                self.global_cpu_quota_us,
                self.global_memory_bytes,
                self.global_pids,
                self.global_filesystem_bytes,
                self.global_wall_seconds,
                self.global_cleanup_seconds,
            )
            <= 0
        ):
            raise ValueError("Lane profile requires positive global Resource and clock budgets")

    def document(self) -> dict[str, int | float]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class WorkCandidate:
    work_id: str
    order_rank: int
    budget_seconds: float
    resource_units: int
    lease: LeaseIdentity | None
    envelope: EnvelopeSpec
    tier: int = 2
    order_version: str = "order-v1"

    def __post_init__(self) -> None:
        if not self.work_id or min(self.order_rank, self.budget_seconds, self.resource_units) <= 0:
            raise ValueError("Work candidate requires ranked Work, budget, and Resource identities")
        if self.tier <= 0 or not self.order_version:
            raise ValueError("Work candidate requires acquired Tier and Order version")
        if self.budget_seconds > self.envelope.wall_seconds:
            raise ValueError("Work budget exceeds its Attempt Resource envelope")


@dataclass(frozen=True)
class LaneBinding:
    lane_id: str
    work_id: str
    attempt_id: str
    generation: GenerationIdentity
    envelope_id: str
    envelope: EnvelopeSpec
    lease: LeaseIdentity | None
    order_rank: int
    budget_seconds: float
    resource_units: int
    tier: int
    order_version: str
    admitted_at: str
    hard_deadline: str


@dataclass(frozen=True)
class LaneOutcome:
    binding: LaneBinding
    outcome: LaneVerdict
    seconds: float
    reason: str = ""

    @classmethod
    def complete(cls, binding: LaneBinding, *, seconds: float) -> LaneOutcome:
        return cls(binding, LaneVerdict.COMPLETE, seconds)

    @classmethod
    def failed(cls, binding: LaneBinding, reason: str) -> LaneOutcome:
        return cls(binding, LaneVerdict.FAILED, 0.0, reason)

    @classmethod
    def stalled(cls, binding: LaneBinding, termination: OwnerTermination) -> LaneOutcome:
        return cls(
            binding,
            LaneVerdict.STALLED,
            binding.budget_seconds,
            f"budget-expired:{termination.evidence_digest}",
        )


@dataclass(frozen=True)
class LaneTimeline:
    lane_id: str
    work_id: str
    attempt_id: str
    generation_id: str
    envelope_id: str
    lease_id: str
    envelope: dict[str, object]
    order_rank: int
    budget_seconds: int
    resource_units: int
    tier: int
    order_version: str
    admitted_at: str
    hard_deadline: str
    outcome: str
    seconds: float
    reason: str

    def document(self) -> dict[str, object]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class LaneCycleResult:
    timelines: tuple[LaneTimeline, ...]
    total_resource_units: int
    parked: tuple[str, ...]
    receipt_path: str


__all__ = [
    "LaneBinding",
    "LaneCycleResult",
    "LaneJournalState",
    "LaneOutcome",
    "LaneProfile",
    "LaneTimeline",
    "LaneVerdict",
    "OwnerTermination",
    "WorkCandidate",
]
