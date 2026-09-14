"""Crash-durable Lane admission facts written before any Attempt effect."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.lane_topology_contracts import LaneBinding, LaneJournalState, LaneOutcome, WorkCandidate


JOURNAL_FILENAME = "lane-topology.control.json"


class LaneJournal:
    def __init__(self, state: Path, run_id: str) -> None:
        self.path = Path(state) / run_id / JOURNAL_FILENAME
        self.run_id = run_id
        self._rows = self._load()

    def reserve(
        self,
        lane_id,
        candidate: WorkCandidate,
        attempt_id: str,
        envelope_id: str,
        admitted_at: str,
        hard_deadline: str,
        budget_seconds: float,
    ) -> None:
        if any(LaneJournalState(row["state"]).unsettled and row["work_id"] == candidate.work_id for row in self._rows):
            raise ValueError("Challenge already has an active Lane claim")
        self._rows.append(
            {
                "state": LaneJournalState.RESERVED.value,
                "lane_id": lane_id,
                "work_id": candidate.work_id,
                "attempt_id": attempt_id,
                "generation_id": "",
                "lease_id": (
                    f"{candidate.lease.run_id}:{candidate.lease.lease_seq}" if candidate.lease is not None else ""
                ),
                "envelope_id": envelope_id,
                "envelope": candidate.envelope.document(),
                "tier": candidate.tier,
                "budget_seconds": budget_seconds,
                "order_rank": candidate.order_rank,
                "order_version": candidate.order_version,
                "admitted_at": admitted_at,
                "hard_deadline": hard_deadline,
            }
        )
        self._write()

    def bind(self, binding: LaneBinding) -> None:
        for row in reversed(self._rows):
            if row["attempt_id"] == binding.attempt_id and row["state"] == LaneJournalState.RESERVED.value:
                row["generation_id"] = binding.generation.generation_id
                row["state"] = LaneJournalState.ACTIVE.value
                self._write()
                return
        raise ValueError("Lane generation has no durable admission reservation")

    def begin_settle(self, outcome: LaneOutcome) -> None:
        for row in reversed(self._rows):
            if (
                row["generation_id"] == outcome.binding.generation.generation_id
                and row["state"] == LaneJournalState.ACTIVE.value
            ):
                row["state"] = LaneJournalState.closing_for(outcome.outcome).value
                row["seconds"] = max(0.0, outcome.seconds)
                row["reason"] = outcome.reason
                self._write()
                return
        raise ValueError("Lane settlement has no durable admission")

    def finish_settle(self, generation_id: str) -> None:
        for row in reversed(self._rows):
            state = LaneJournalState(row["state"])
            if row["generation_id"] == generation_id and state.closing:
                row["state"] = state.verdict.value
                self._write()
                return
        raise ValueError("Lane settlement reservation is absent")

    def interrupt_attempt(self, attempt_id: str) -> None:
        for row in reversed(self._rows):
            if row["attempt_id"] == attempt_id and LaneJournalState(row["state"]).unsettled:
                row["state"] = LaneJournalState.FAILED.value
                row["seconds"] = 0.0
                row["reason"] = "restart-interrupted"
                self._write()
                return
        raise ValueError("interrupted Lane reservation is absent")

    @property
    def unsettled_attempt_ids(self) -> tuple[str, ...]:
        return tuple(row["attempt_id"] for row in self._rows if LaneJournalState(row["state"]).unsettled)

    @property
    def closing_rows(self) -> tuple[dict[str, object], ...]:
        return tuple(row for row in self._rows if LaneJournalState(row["state"]).closing)

    @property
    def digest(self) -> str:
        return digest_bytes(canonical_bytes(self.document()))

    def document(self) -> dict[str, object]:
        return {"schema_version": 1, "run_id": self.run_id, "admissions": self._rows}

    def _write(self) -> None:
        atomic_write(self.path, canonical_bytes(self.document()) + b"\n")

    def _load(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        document = json.loads(self.path.read_text())
        if document.get("schema_version") != 1 or document.get("run_id") != self.run_id:
            raise ValueError("Lane admission journal identity is invalid")
        rows = document.get("admissions")
        if not isinstance(rows, list):
            raise ValueError("Lane admission journal rows are invalid")
        for row in rows:
            LaneJournalState(row["state"])
            admitted = dt.datetime.fromisoformat(row["admitted_at"])
            deadline = dt.datetime.fromisoformat(row["hard_deadline"])
            if deadline <= admitted or row["budget_seconds"] > (deadline - admitted).total_seconds() + 1e-3:
                raise ValueError("Lane admission journal deadline is invalid")
        return rows


__all__ = ["JOURNAL_FILENAME", "LaneJournal"]
