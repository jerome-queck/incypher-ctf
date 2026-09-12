import datetime as dt
import json
import threading
import time
from pathlib import Path

import pytest

from solver.lane_topology import LaneController
from solver.lane_topology_contracts import LaneOutcome, LaneProfile, OwnerTermination, WorkCandidate
from solver.lane_topology_receipt import link_manifest, verify_receipt
from solver.lane_topology_proof import load_controlled_proof
from solver.manifest import generate_manifest
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.attempt_executor_contracts import EnvelopeSpec, NetworkPolicy
from solver.instance_lease_contracts import LeaseIdentity
from solver.redaction import Redactor
from solver.work_generation import GenerationFence
from test_manifest import bind_profile_digest, release_candidate_profile


NOW = dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc)


def controller(tmp_path: Path, lanes: int = 2, hook=None, terminate=None) -> LaneController:
    fence = GenerationFence(tmp_path / "state", "run-1", Redactor({}), lambda: NOW.isoformat())
    return LaneController(
        state=tmp_path / "state",
        run_id="run-1",
        profile=LaneProfile(lanes=lanes, global_resource_units=2),
        generations=fence,
        timestamp=lambda: NOW.isoformat(),
        terminate=terminate or (lambda binding: OwnerTermination(True, "a" * 64)),
        hook=hook,
    )


def candidates():
    envelope = EnvelopeSpec(10, 50_000, 1024, 4, 2048, NetworkPolicy.DENY, 600, 10)
    return (
        WorkCandidate("challenge-1", 1, 600, 1, LeaseIdentity("run-1", 1), envelope),
        WorkCandidate("challenge-2", 2, 600, 1, LeaseIdentity("run-1", 2), envelope),
    )


@pytest.mark.parametrize("lanes, expected", [(1, 1), (2, 2)])
def test_profiles_bind_independent_work_attempt_resources_and_leases(tmp_path, lanes, expected):
    topology = controller(tmp_path, lanes)
    barriers = threading.Barrier(expected)

    def execute(binding):
        barriers.wait(timeout=2)
        return LaneOutcome.complete(binding, seconds=3)

    result = topology.run_cycle(lambda excluded: tuple(c for c in candidates() if c.work_id not in excluded), execute)

    assert len(result.timelines) == 2
    assert len({line.work_id for line in result.timelines}) == 2
    assert len({line.attempt_id for line in result.timelines}) == 2
    assert len({line.envelope_id for line in result.timelines}) == 2
    assert len({line.lease_id for line in result.timelines}) == 2
    assert len({line.lane_id for line in result.timelines}) == expected


def test_order_is_recomputed_for_each_free_lane_without_a_queue(tmp_path):
    calls = []

    def order(excluded):
        calls.append(tuple(sorted(excluded)))
        return tuple(c for c in candidates() if c.work_id not in excluded)

    result = controller(tmp_path).run_cycle(order, lambda binding: LaneOutcome.complete(binding, seconds=1))

    assert calls == [(), ("challenge-1",), ("challenge-1", "challenge-2"), ("challenge-1", "challenge-2")]
    assert result.parked == ()


def test_completed_lane_is_refilled_while_its_peer_remains_active(tmp_path):
    envelope = candidates()[0].envelope
    work = candidates() + (WorkCandidate("challenge-3", 3, 600, 1, LeaseIdentity("run-1", 3), envelope),)
    slow_started = threading.Event()
    replacement_started = threading.Event()
    release_slow = threading.Event()

    def execute(binding):
        if binding.work_id == "challenge-1":
            slow_started.set()
            release_slow.wait(timeout=2)
        if binding.work_id == "challenge-3":
            replacement_started.set()
            release_slow.set()
        return LaneOutcome.complete(binding, seconds=1)

    result = controller(tmp_path).run_cycle(
        lambda excluded: tuple(candidate for candidate in work if candidate.work_id not in excluded), execute
    )

    assert slow_started.is_set() and replacement_started.is_set()
    assert [line.work_id for line in result.timelines] == ["challenge-1", "challenge-2", "challenge-3"]


def test_an_unaffordable_order_head_is_deferred_without_skipping_to_lower_rank(tmp_path):
    work = list(candidates())
    work[0] = WorkCandidate(work[0].work_id, 1, 600, 3, work[0].lease, work[0].envelope)

    result = controller(tmp_path).run_cycle(
        lambda excluded: tuple(work), lambda binding: LaneOutcome.complete(binding, seconds=1)
    )

    assert result.timelines == ()


@pytest.mark.parametrize(
    "boundary, generation_id, dispositions",
    [
        ("after_reservation", "generation-000001", ["complete", "complete"]),
        ("after_generation", "generation-000002", ["interrupt", "complete", "complete"]),
        ("after_admission", "generation-000002", ["interrupt", "complete", "complete"]),
    ],
)
def test_restart_fences_a_durably_admitted_lane_before_replacement(tmp_path, boundary, generation_id, dispositions):
    def crash(point):
        if point == boundary:
            raise OSError("death")

    with pytest.raises(OSError, match="death"):
        controller(tmp_path, lanes=1, hook=crash).run_cycle(
            lambda excluded: candidates(), lambda binding: LaneOutcome.complete(binding, seconds=1)
        )

    restarted = controller(tmp_path, lanes=1)
    result = restarted.run_cycle(
        lambda excluded: candidates(), lambda binding: LaneOutcome.complete(binding, seconds=1)
    )

    assert result.timelines[0].generation_id == generation_id
    assert [state.disposition.value for state in restarted.generations.projection().generations] == dispositions


def test_one_lane_failure_cannot_close_or_relabel_the_other(tmp_path):
    def execute(binding):
        if binding.lane_id == "lane-1":
            return LaneOutcome.failed(binding, "executor-crash")
        return LaneOutcome.complete(binding, seconds=5)

    topology = controller(tmp_path)
    result = topology.run_cycle(lambda excluded: tuple(c for c in candidates() if c.work_id not in excluded), execute)
    projection = topology.generations.projection()

    assert [line.outcome for line in result.timelines] == ["failed", "complete"]
    assert len(projection.generations) == 2
    assert projection.generations[0].disposition.value == "interrupt"
    assert projection.generations[1].disposition.value == "complete"
    assert result.total_resource_units <= 2


@pytest.mark.parametrize("boundary", ["after_settlement_reservation", "after_generation_close"])
def test_restart_finishes_the_reserved_lane_close_without_replacement(tmp_path, boundary):
    def crash(point):
        if point == boundary:
            raise OSError("death")

    only = candidates()[:1]
    with pytest.raises(OSError, match="death"):
        controller(tmp_path, lanes=1, hook=crash).run_cycle(
            lambda excluded: tuple(item for item in only if item.work_id not in excluded),
            lambda binding: LaneOutcome.complete(binding, seconds=1),
        )

    restarted = controller(tmp_path, lanes=1)

    assert [state.disposition.value for state in restarted.generations.projection().generations] == ["complete"]
    journal = json.loads((tmp_path / "state" / "run-1" / "lane-topology.control.json").read_text())
    assert journal["admissions"][0]["state"] == "complete"


def test_a_stalled_lane_is_fenced_without_waiting_past_its_budget_or_touching_its_peer(tmp_path):
    stop = threading.Event()

    def terminate(binding):
        stop.set()
        return OwnerTermination(True, "b" * 64)

    topology = controller(tmp_path, terminate=terminate)
    work = list(candidates())
    work[0] = WorkCandidate(work[0].work_id, 1, 1, 1, work[0].lease, work[0].envelope)

    def execute(binding):
        if binding.lane_id == "lane-1":
            stop.wait(timeout=2)
        return LaneOutcome.complete(binding, seconds=0.1)

    began = time.monotonic()
    result = topology.run_cycle(lambda excluded: tuple(c for c in work if c.work_id not in excluded), execute)

    assert time.monotonic() - began < 1.5
    assert [line.outcome for line in result.timelines] == ["stalled", "complete"]
    assert result.timelines[0].reason == f"budget-expired:{'b' * 64}"
    assert verify_receipt(result.receipt_path, topology.generations)["timelines"][0]["outcome"] == "stalled"


def test_global_clock_clips_attempt_and_receipt(tmp_path):
    stop = threading.Event()
    fence = GenerationFence(tmp_path / "state", "run-1", Redactor({}), lambda: NOW.isoformat())
    topology = LaneController(
        state=tmp_path / "state",
        run_id="run-1",
        profile=LaneProfile(lanes=1, global_resource_units=1, global_wall_seconds=0.05),
        generations=fence,
        timestamp=lambda: NOW.isoformat(),
        terminate=lambda binding: stop.set() or OwnerTermination(True, "c" * 64),
    )

    result = topology.run_cycle(
        lambda excluded: candidates()[:1] if not excluded else (),
        lambda binding: stop.wait(timeout=1) and LaneOutcome.complete(binding, seconds=1),
    )
    receipt = verify_receipt(result.receipt_path, topology.generations)

    assert result.timelines[0].budget_seconds <= 0.05
    assert receipt["cycle_seconds"] <= (
        receipt["profile"]["global_wall_seconds"] + receipt["profile"]["global_cleanup_seconds"]
    )


def test_global_clock_refuses_cleanup_overrun(tmp_path):
    fence = GenerationFence(tmp_path / "state", "run-1", Redactor({}), lambda: NOW.isoformat())
    stop = threading.Event()

    def terminate(binding):
        stop.set()
        time.sleep(0.03)
        return OwnerTermination(True, "d" * 64)

    topology = LaneController(
        state=tmp_path / "state",
        run_id="run-1",
        profile=LaneProfile(
            lanes=1,
            global_resource_units=1,
            global_wall_seconds=0.01,
            global_cleanup_seconds=0.01,
        ),
        generations=fence,
        timestamp=lambda: NOW.isoformat(),
        terminate=terminate,
    )

    with pytest.raises(RuntimeError, match="declared work and cleanup clock"):
        topology.run_cycle(
            lambda excluded: candidates()[:1] if not excluded else (),
            lambda binding: stop.wait(timeout=1),
        )


def test_receipt_rejects_duplicate_claims_or_unproved_generations_and_links_manifest(tmp_path):
    topology = controller(tmp_path)
    result = topology.run_cycle(
        lambda excluded: tuple(c for c in candidates() if c.work_id not in excluded),
        lambda binding: LaneOutcome.complete(binding, seconds=1),
    )

    receipt = verify_receipt(result.receipt_path, topology.generations)
    profile = release_candidate_profile()
    profile["lanes"] = 2
    profile.pop("profile_digest")
    profile = bind_profile_digest(profile)
    draft = generate_manifest(
        image_digest="sha256:" + "1" * 64,
        release_candidate_profile=profile,
    )
    manifest = link_manifest(draft, result.receipt_path, topology.generations)

    assert receipt["profile"]["lanes"] == 2
    row = next(row for row in manifest["requirements"] if row["row_id"] == "core.lane-specialist-topology")
    linked = next(item for item in manifest["receipts"] if item["ref"] == row["receipt_ref"])
    assert row["status"] == "implemented"
    assert linked["digest"] == receipt["receipt_digest"]

    forged = json.loads(Path(result.receipt_path).read_text())
    forged["timelines"][1]["work_id"] = forged["timelines"][0]["work_id"]
    forged["receipt_digest"] = digest_bytes(canonical_bytes({k: v for k, v in forged.items() if k != "receipt_digest"}))
    Path(result.receipt_path).write_bytes(canonical_bytes(forged) + b"\n")
    with pytest.raises(ValueError, match="canonical Work generation|overlaps"):
        verify_receipt(result.receipt_path, topology.generations)


def test_controlled_proof_binds_both_profiles_and_candidate_manifest_row():
    proof = load_controlled_proof()

    assert proof["profiles"] == {"one-lane": "pass", "two-lane": "pass"}
    assert proof["manifest_row_id"] == "core.lane-specialist-topology"
