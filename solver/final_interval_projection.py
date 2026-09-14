"""Replay projection for canonical final-interval facts."""

from __future__ import annotations

from collections.abc import Sequence

from solver.event_store_contracts import CommittedEvent
from solver.final_interval_contracts import (
    FINAL_INTERVAL_RECORDED,
    AdmissionMode,
    FinalIntervalRecord,
)


def project_final_interval(events: Sequence[CommittedEvent]) -> dict[str, object]:
    """Replay final-interval facts from the already verified canonical chain."""
    result: dict[str, object] = {
        "mode": AdmissionMode.ORDINARY.value,
        "final_chances": {},
        "drain_requests": {},
        "submissions": {},
        "cleanup_requested": False,
        "cleanup_targets": (),
        "cleanup_authority": "",
        "cleanup": {},
        "terminal": None,
        "trace": [],
    }
    for event in events:
        if event.event_type != FINAL_INTERVAL_RECORDED:
            continue
        row = dict(event.payload)
        record = FinalIntervalRecord(row["record"])
        trace = result["trace"]
        assert isinstance(trace, list)
        trace.append(
            {
                key: value
                for key, value in row.items()
                if key not in {"blob_digest", "blob_bytes", "event_id"} and value != ""
            }
        )
        chances, submissions, cleanup = result["final_chances"], result["submissions"], result["cleanup"]
        assert isinstance(chances, dict) and isinstance(submissions, dict) and isinstance(cleanup, dict)
        if record is FinalIntervalRecord.TRANSITION:
            result["mode"] = row["state"]
        elif record in {
            FinalIntervalRecord.ENTITLEMENT_RESERVED,
            FinalIntervalRecord.ENTITLEMENT_SPENT,
            FinalIntervalRecord.ENTITLEMENT_CLOSED,
        }:
            chances[row["lane_id"]] = {"challenge_id": row["challenge_id"], "state": row["state"]}
        elif record is FinalIntervalRecord.DRAIN_REQUESTED:
            drain_requests = result["drain_requests"]
            assert isinstance(drain_requests, dict)
            drain_requests[row["candidate_id"]] = row["reservation_id"]
        elif record in {FinalIntervalRecord.DRAIN_RESULT, FinalIntervalRecord.SUBMISSION_RECONCILED}:
            submissions[row["candidate_id"]] = {"outcome": row["outcome"], "observed_at": row["observed_at"]}
        elif record is FinalIntervalRecord.CLEANUP_REQUESTED:
            result["cleanup_requested"] = True
            result["cleanup_authority"] = row["reservation_id"]
            inventory = row.get("inventory") or {}
            result["cleanup_targets"] = tuple(inventory.get("targets", ()))
        elif record is FinalIntervalRecord.CLEANUP_RESULT:
            cleanup[row["challenge_id"]] = row["outcome"]
        elif record is FinalIntervalRecord.TERMINAL_INVENTORY:
            result["terminal"] = dict(row["inventory"])
            result["mode"] = AdmissionMode.CLOSED.value
    return result


__all__ = ["project_final_interval"]
