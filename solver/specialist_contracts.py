"""Typed, effect-free contract for controller-owned Specialist Engagements."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from collections.abc import Iterable
from typing import Protocol

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes


class SpecialistVerdict(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    LATE = "late"
    DUPLICATE = "duplicate"
    UNSETTLED = "unsettled"


@dataclass(frozen=True)
class SpecialistProfile:
    maximum: int
    shared_quota_turns: int
    max_context_bytes: int = 8192
    max_result_bytes: int = 8192
    max_wall_seconds: float = 300
    tool_profiles: tuple[str, ...] = ("read-only",)

    def __post_init__(self):
        if not 0 <= self.maximum <= 2 or self.shared_quota_turns < 0:
            raise ValueError("Specialist profile exceeds supported pool or quota")

    def document(self):
        return asdict(self)


@dataclass(frozen=True)
class SpecialistTask:
    task_id: str
    engagement_id: str
    parent_engagement_id: str
    generation_id: str
    attempt_id: str
    goal: str
    question_or_hypothesis: str
    evidence_refs: tuple[str, ...]
    evidence_digest: str
    expected_critical_path_benefit: int
    requested_tool_profile: str
    success_evidence: str
    stop_conditions: tuple[str, ...]
    deadline: str
    turn_limit: int = 1

    def document(self):
        return asdict(self)


@dataclass(frozen=True)
class SpecialistView:
    task_id: str
    goal: str
    question_or_hypothesis: str
    evidence: tuple["SpecialistEvidence", ...]
    tools: "SpecialistTools"
    deadline: str


@dataclass(frozen=True)
class SpecialistProposal:
    summary: str
    evidence_refs: tuple[str, ...] = ()
    candidate: str = ""
    turns: int = 1
    context_bytes: int = 0


@dataclass(frozen=True)
class SpecialistInvocation:
    proposal: SpecialistProposal
    turns: int


@dataclass(frozen=True)
class SpecialistResult:
    task_id: str
    verdict: SpecialistVerdict
    proposal: SpecialistProposal | None = None
    reason: str = ""
    termination: SpecialistTermination | None = None
    replayed: bool = False


@dataclass(frozen=True)
class SpecialistBatch:
    results: tuple[SpecialistResult, ...]
    receipt_path: str
    evidence: tuple[SpecialistEvidence, ...] = ()


@dataclass(frozen=True)
class SpecialistEvidence:
    ref: str
    digest: str
    generation_id: str


class SpecialistTools(Protocol):
    profile: str

    def invoke(self, operation: str, arguments: tuple[str, ...]) -> str: ...

    def revoke(self) -> None: ...


class EvidenceResolver(Protocol):
    def __call__(self, ref: str, generation_id: str) -> SpecialistEvidence: ...


class SpecialistInvoke(Protocol):
    def __call__(self, view: SpecialistView) -> SpecialistInvocation: ...


@dataclass(frozen=True)
class SpecialistTermination:
    ended: bool
    evidence_digest: str
    survivors: int


class SpecialistTerminator(Protocol):
    def __call__(self, engagement_id: str) -> SpecialistTermination: ...


class SpecialistToolsFactory(Protocol):
    def __call__(self, profile: str) -> SpecialistTools: ...


class SpecialistCarry:
    """Only controller-selected, generation-bound sealed evidence crosses to the Lead."""

    def __init__(self, path: Path, generation_id: str, hook=lambda _point: None):
        self.path, self.generation_id = Path(path), generation_id
        self._hook = hook

    def _document(self):
        if not self.path.exists():
            return {"generation_id": self.generation_id, "evidence": [], "acceptances": []}
        document = json.loads(self.path.read_bytes())
        if document.get("generation_id") != self.generation_id:
            raise ValueError("Carry stored generation changed")
        return document

    def _write(self, document):
        atomic_write(self.path, canonical_bytes(document) + b"\n")

    @property
    def evidence(self) -> list[SpecialistEvidence]:
        return [SpecialistEvidence(**row) for row in self._document()["evidence"]]

    def accept_results(self, results, evidence, select, *, generation_id: str):
        if generation_id != self.generation_id:
            raise ValueError("Carry generation changed")
        document = self._document()
        evidence_by_ref = {item.ref: item for item in evidence}
        newly_imported = []
        for result in results:
            if result.proposal is None:
                continue
            proposal_digest = digest_bytes(
                canonical_bytes(
                    {
                        "summary": result.proposal.summary,
                        "evidence_refs": result.proposal.evidence_refs,
                        "candidate": result.proposal.candidate,
                    }
                )
            )
            decision = next((item for item in document["acceptances"] if item["task_id"] == result.task_id), None)
            if decision is None:
                decision = {
                    "acceptance_id": f"{generation_id}:{result.task_id}:{proposal_digest}",
                    "task_id": result.task_id,
                    "proposal_digest": proposal_digest,
                    "accepted": bool(select(result)),
                    "evidence_refs": list(result.proposal.evidence_refs),
                    "imported": False,
                }
                document["acceptances"].append(decision)
                document["acceptances"].sort(key=lambda item: item["task_id"])
                self._write(document)
                self._hook("after_acceptance_reservation")
            elif decision["proposal_digest"] != proposal_digest:
                raise ValueError("Carry proposal identity changed digest")
            if not decision["accepted"] or decision["imported"]:
                continue
            selected = tuple(evidence_by_ref[ref] for ref in decision["evidence_refs"])
            existing = {item["ref"]: item for item in document["evidence"]}
            if any(item.ref in existing and existing[item.ref]["digest"] != item.digest for item in selected):
                raise ValueError("Carry evidence reference changed digest")
            for item in selected:
                if item.generation_id != generation_id:
                    raise ValueError("Carry evidence crosses generation")
                if item.ref not in existing:
                    document["evidence"].append(item.__dict__)
                    newly_imported.append(item)
            document["evidence"].sort(key=lambda item: item["ref"])
            decision["imported"] = True
            self._write(document)
        return tuple(newly_imported)

    def import_selected(
        self, selected: Iterable[SpecialistEvidence], *, generation_id: str
    ) -> tuple[SpecialistEvidence, ...]:
        if generation_id != self.generation_id:
            raise ValueError("Carry generation changed")
        accepted = tuple(selected)
        if any(item.generation_id != generation_id for item in accepted):
            raise ValueError("Carry evidence crosses generation")
        document = self._document()
        existing = {item.ref: item for item in self.evidence}
        if any(item.ref in existing and existing[item.ref].digest != item.digest for item in accepted):
            raise ValueError("Carry evidence reference changed digest")
        merged = sorted({*self.evidence, *accepted}, key=lambda item: item.ref)
        document["evidence"] = [item.__dict__ for item in merged]
        self._write(document)
        return accepted


__all__ = [
    "EvidenceResolver",
    "SpecialistBatch",
    "SpecialistCarry",
    "SpecialistEvidence",
    "SpecialistInvoke",
    "SpecialistInvocation",
    "SpecialistProfile",
    "SpecialistProposal",
    "SpecialistResult",
    "SpecialistTask",
    "SpecialistTerminator",
    "SpecialistTermination",
    "SpecialistTools",
    "SpecialistToolsFactory",
    "SpecialistVerdict",
    "SpecialistView",
]
