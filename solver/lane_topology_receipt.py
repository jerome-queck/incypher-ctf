"""Sanitized, replay-verifiable Lane-topology receipt and manifest link."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from solver.attempt_executor_contracts import EnvelopeSpec, NetworkPolicy
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.lane_topology_contracts import LaneProfile, LaneTimeline
from solver.lane_topology_journal import LaneJournal
from solver.work_generation import GenerationFence


RECEIPT_FILENAME = "lane-topology.receipt.json"
MANIFEST_ROW_ID = "core.lane-specialist-topology"
MANIFEST_RECEIPT_REF = "receipt:lane-topology"


def _subject(document: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in document.items() if key != "receipt_digest"}


def write_receipt(
    state: Path,
    *,
    run_id: str,
    profile: LaneProfile,
    timelines: Sequence[LaneTimeline],
    generation_digest: str,
    control_digest: str,
    peak_resource_units: int,
    peak_envelope: Mapping[str, int],
    cycle_seconds: float,
    timestamp: str,
) -> Path:
    document: dict[str, object] = {
        "proof_type": "lane-topology",
        "schema_version": 1,
        "run_id": run_id,
        "profile": profile.document(),
        "timelines": [line.document() for line in timelines],
        "generation_digest": generation_digest,
        "control_digest": control_digest,
        "peak_resource_units": peak_resource_units,
        "peak_envelope": dict(peak_envelope),
        "cycle_seconds": max(0.0, cycle_seconds),
        "timestamp": timestamp,
        "parked": [],
    }
    document["receipt_digest"] = digest_bytes(canonical_bytes(document))
    path = Path(state) / run_id / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: str | Path, generations: GenerationFence) -> Mapping[str, object]:
    document = json.loads(Path(path).read_text())
    if document.get("proof_type") != "lane-topology" or document.get("schema_version") != 1:
        raise ValueError("Lane-topology receipt schema is unsupported")
    if document.get("receipt_digest") != digest_bytes(canonical_bytes(_subject(document))):
        raise ValueError("Lane-topology receipt digest does not match its subject")
    profile = LaneProfile(**document["profile"])
    timelines = document.get("timelines")
    if not isinstance(timelines, list):
        raise ValueError("Lane-topology receipt exceeds its selected profile")
    projection = generations.projection()
    if document.get("generation_digest") != projection.digest:
        raise ValueError("Lane-topology receipt is not bound to canonical Work generations")
    journal = LaneJournal(Path(path).parent.parent, str(document["run_id"]))
    if document.get("control_digest") != journal.digest:
        raise ValueError("Lane-topology receipt is not bound to durable Lane admissions")
    states = {state.generation_id: state for state in projection.generations}
    work_ids: set[str] = set()
    lane_ids: set[str] = set()
    lease_ids: set[str] = set()
    admissions = {row["generation_id"]: row for row in journal.document()["admissions"] if row["generation_id"]}
    for line in timelines:
        state = states.get(line.get("generation_id"))
        if state is None or state.work_id != line.get("work_id") or state.attempt_id != line.get("attempt_id"):
            raise ValueError("Lane binding is not a canonical Work generation")
        if line["work_id"] in work_ids:
            raise ValueError("Lane-topology receipt overlaps a Lane or Challenge claim")
        if line["lane_id"] not in {f"lane-{ordinal}" for ordinal in range(1, profile.lanes + 1)}:
            raise ValueError("Lane-topology receipt names a Lane outside its selected profile")
        if line["lease_id"] and (
            line["lease_id"] in lease_ids or not str(line["lease_id"]).startswith(f"{document['run_id']}:")
        ):
            raise ValueError("Lane-topology receipt overlaps or crosses a Run Lease")
        envelope_document = dict(line["envelope"])
        envelope_document["network"] = NetworkPolicy(envelope_document["network"])
        envelope = EnvelopeSpec(**envelope_document)
        if line["budget_seconds"] > envelope.wall_seconds or line["seconds"] > line["budget_seconds"]:
            raise ValueError("Lane timeline exceeds its frozen Attempt or Resource budget")
        admission = admissions.get(line["generation_id"])
        bound_fields = (
            "lane_id",
            "work_id",
            "attempt_id",
            "envelope_id",
            "lease_id",
            "tier",
            "order_rank",
            "order_version",
            "budget_seconds",
            "admitted_at",
            "hard_deadline",
        )
        if admission is None or any(admission[field] != line[field] for field in bound_fields):
            raise ValueError("Lane timeline differs from its durable admission authority")
        allowed = {"complete"} if state.disposition and state.disposition.value == "complete" else {"failed", "stalled"}
        if line.get("outcome") not in allowed:
            raise ValueError("Lane outcome disagrees with its canonical generation disposition")
        if admission["state"] != line["outcome"] or admission["reason"] != line["reason"]:
            raise ValueError("Lane outcome differs from its durable settlement authority")
        if line["outcome"] == "stalled":
            evidence = str(line["reason"]).removeprefix("budget-expired:")
            if len(evidence) != 64 or any(character not in "0123456789abcdef" for character in evidence):
                raise ValueError("stalled Lane lacks bounded owner-termination evidence")
        work_ids.add(line["work_id"])
        lane_ids.add(line["lane_id"])
        if line["lease_id"]:
            lease_ids.add(line["lease_id"])
        if line["resource_units"] > profile.global_resource_units:
            raise ValueError("Lane Attempt exceeds global Resource capacity")
    peak = document.get("peak_resource_units")
    envelope_peak = document.get("peak_envelope")
    envelope_limits = {
        "cpu_quota_us": profile.global_cpu_quota_us,
        "memory_bytes": profile.global_memory_bytes,
        "pids": profile.global_pids,
        "filesystem_bytes": profile.global_filesystem_bytes,
    }
    if (
        not isinstance(peak, int)
        or peak < 0
        or peak > profile.global_resource_units
        or not isinstance(envelope_peak, dict)
        or set(envelope_peak) != set(envelope_limits)
        or any(
            not isinstance(envelope_peak[key], int) or envelope_peak[key] > limit
            for key, limit in envelope_limits.items()
        )
        or document.get("parked") != []
        or not isinstance(document.get("cycle_seconds"), (int, float))
        or document["cycle_seconds"] > profile.global_wall_seconds + profile.global_cleanup_seconds
    ):
        raise ValueError("Lane topology overspent capacity or persisted a parked queue")
    return document


def link_manifest(manifest: Mapping[str, object], path: str | Path, generations: GenerationFence):
    from solver.manifest import attach_requirement_receipt

    receipt = verify_receipt(path, generations)
    return attach_requirement_receipt(
        manifest,
        MANIFEST_ROW_ID,
        {"ref": MANIFEST_RECEIPT_REF, "kind": "lane-topology", "digest": receipt["receipt_digest"]},
    )


__all__ = ["RECEIPT_FILENAME", "link_manifest", "verify_receipt", "write_receipt"]
