import datetime as dt
import json
import threading
import time
from pathlib import Path
from dataclasses import replace

import pytest

from solver.event_store_contracts import GenerationDisposition
from solver.redaction import Redactor
from solver.specialist_contracts import (
    SpecialistCarry,
    SpecialistEvidence,
    SpecialistInvocation,
    SpecialistProfile,
    SpecialistProposal,
    SpecialistResult,
    SpecialistTask,
    SpecialistTermination,
    SpecialistVerdict,
)
from solver.lead_controller import LeadController
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.specialist_pool import SpecialistPool
from solver.specialist_pool_proof import load_controlled_proof
from solver.specialist_receipt import verify_receipt
from solver.specialist_receipt import link_manifest
from solver.manifest import attach_requirement_receipt, generate_manifest
from test_manifest import bind_profile_digest, release_candidate_profile
from solver.work_generation import GenerationFence


NOW = dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc)


def answer(summary, evidence_refs=(), *, turns=1):
    return SpecialistInvocation(SpecialistProposal(summary, evidence_refs), turns)


def task(ordinal, generation):
    evidence = SpecialistEvidence(f"evidence-{ordinal}", str(ordinal) * 64, generation.generation_id)
    return SpecialistTask(
        f"task-{ordinal}",
        f"specialist-{ordinal}",
        "lead-1",
        generation.generation_id,
        generation.attempt_id,
        "identify format",
        f"hypothesis-{ordinal}",
        (f"evidence-{ordinal}",),
        digest_bytes(canonical_bytes([evidence.__dict__])),
        3 - ordinal,
        "read-only",
        "source-identified artifact",
        ("answer found",),
        (NOW + dt.timedelta(minutes=5)).isoformat(),
    )


def setup(tmp_path, maximum=2, quota=2):
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), lambda: NOW.isoformat())
    generations = tuple(fence.acquire(f"work-{i}", f"attempt-{i}") for i in (1, 2))

    class Tools:
        profile = "read-only"
        revoked = False

        def invoke(self, operation, arguments):
            if self.revoked:
                raise PermissionError("revoked")
            return f"{operation}:{arguments}"

        def revoke(self):
            self.revoked = True

    def resolve(ref, generation_id):
        ordinal = ref.rsplit("-", 1)[-1]
        return SpecialistEvidence(ref, ordinal * 64, generation_id)

    pool = SpecialistPool(
        state,
        "run-1",
        SpecialistProfile(maximum, quota),
        fence,
        now=lambda: NOW,
        resolve_evidence=resolve,
        tools=lambda _profile: Tools(),
        terminate=lambda _engagement: SpecialistTermination(True, "a" * 64, 0),
    )
    return pool, fence, generations


def test_multiple_specialists_return_selected_sealed_evidence_to_lead(tmp_path):
    pool, fence, generations = setup(tmp_path)
    barrier = threading.Barrier(2)

    def invoke(view):
        barrier.wait(timeout=2)
        assert not hasattr(view, "submit") and not hasattr(view, "board") and not hasattr(view, "lease")
        return answer(f"answer-{view.task_id}", tuple(item.ref for item in view.evidence))

    batch = pool.dispatch(
        tuple(task(i, generation) for i, generation in enumerate(generations, 1)), invoke, cancelled=lambda: False
    )

    assert [result.verdict for result in batch.results] == [SpecialistVerdict.ACCEPTED] * 2, batch.results
    assert [result.proposal.evidence_refs for result in batch.results] == [("evidence-1",), ("evidence-2",)]
    assert len(verify_receipt(batch.receipt_path, fence)["quota_trace"]) == 2


def test_zero_one_and_maximum_profiles_replay_deterministically(tmp_path):
    for maximum, expected in ((0, 0), (1, 1), (2, 2)):
        pool, fence, generations = setup(tmp_path / str(maximum), maximum, 2)
        calls = []
        tasks = tuple(task(i, generation) for i, generation in enumerate(generations, 1))
        first = pool.dispatch(tasks, lambda view: calls.append(view.task_id) or answer("ok"), cancelled=lambda: False)
        replay = pool.dispatch(
            tasks,
            lambda _view: (_ for _ in ()).throw(AssertionError("reinvoked")),
            cancelled=lambda: False,
        )
        assert sum(result.verdict is SpecialistVerdict.ACCEPTED for result in first.results) == expected
        assert len(calls) == expected
        assert all(result.replayed or result.verdict is SpecialistVerdict.REJECTED for result in replay.results)
        assert sum(result.verdict is SpecialistVerdict.ACCEPTED for result in replay.results) == expected
        verify_receipt(replay.receipt_path, fence)


def test_generation_close_cancellation_and_shared_bounds_reject_late_output(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=2, quota=1)
    fence.close(generations[0].generation_id, GenerationDisposition.INTERRUPT)
    batch = pool.dispatch(
        (task(1, generations[0]), task(2, generations[1])),
        lambda _view: answer("late"),
        cancelled=lambda: False,
    )

    assert [result.verdict for result in batch.results] == [SpecialistVerdict.REJECTED, SpecialistVerdict.ACCEPTED]
    assert len(verify_receipt(batch.receipt_path, fence)["quota_trace"]) == 1


def test_verified_receipt_links_selected_specialist_profile_to_manifest(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=1, quota=1)
    batch = pool.dispatch((task(1, generations[0]),), lambda _view: answer("ok"), cancelled=lambda: False)
    profile = release_candidate_profile()
    profile["specialists"] = 1
    profile.pop("profile_digest")
    draft = generate_manifest(
        image_digest="sha256:" + "1" * 64,
        release_candidate_profile=bind_profile_digest(profile),
    )
    draft = attach_requirement_receipt(
        draft,
        "core.lane-specialist-topology",
        {"ref": "receipt:lane-topology", "kind": "lane-topology", "digest": "a" * 64},
    )
    manifest = link_manifest(
        draft,
        batch.receipt_path,
        fence,
    )

    row = next(item for item in manifest["requirements"] if item["row_id"] == "core.lane-specialist-topology")
    assert row["receipt_ref"] == "receipt:specialist-pool"
    assert row["evidence_refs"] == ["receipt:lane-topology"]
    assert {item["ref"] for item in manifest["receipts"]} >= {
        "receipt:lane-topology",
        "receipt:specialist-pool",
    }


def test_lead_selects_and_imports_only_conflict_free_generation_bound_evidence(tmp_path):
    pool, fence, generations = setup(tmp_path)
    lead = LeadController(
        tmp_path / "state",
        "run-1",
        Redactor({}),
        lambda: NOW.isoformat(),
        lambda _request: None,
        fence=fence,
        specialists=pool,
    )
    carry = SpecialistCarry(tmp_path / "carry.json", generations[0].generation_id)
    selected = lead.dispatch_specialists(
        tuple(task(i, generations[0]) for i in (1, 2)),
        lambda view: answer("finding", tuple(item.ref for item in view.evidence)),
        cancelled=lambda: False,
        select=lambda result: result.task_id == "task-1",
        carry=carry,
        generation_id=generations[0].generation_id,
        attempt_id=generations[0].attempt_id,
        engagement_id="lead-1",
    )

    assert [item.ref for item in selected] == ["evidence-1"]
    assert [item.ref for item in carry.evidence] == ["evidence-1"]


def restarted(pool, fence):
    return SpecialistPool(
        pool.state,
        pool.run_id,
        pool.profile,
        fence,
        now=lambda: NOW,
        resolve_evidence=pool._resolve,
        tools=pool._tools,
        terminate=pool._terminate,
    )


def test_pool_refuses_cross_run_and_cross_state_generation_authority(tmp_path):
    pool, _fence, _generations = setup(tmp_path)
    other_run = GenerationFence(pool.state, "run-2", Redactor({}), lambda: NOW.isoformat())
    other_state = GenerationFence(tmp_path / "other-state", "run-1", Redactor({}), lambda: NOW.isoformat())
    for authority in (other_run, other_state):
        with pytest.raises(ValueError, match="canonical state or Run"):
            SpecialistPool(
                pool.state,
                "run-1",
                pool.profile,
                authority,
                now=lambda: NOW,
                resolve_evidence=pool._resolve,
                tools=pool._tools,
                terminate=pool._terminate,
            )


def test_lead_refuses_pool_from_a_different_fence_authority(tmp_path):
    pool, _fence, _generations = setup(tmp_path)
    distinct_fence = GenerationFence(pool.state, "run-1", Redactor({}), lambda: NOW.isoformat())
    with pytest.raises(ValueError, match="different canonical authority"):
        LeadController(
            pool.state,
            "run-1",
            Redactor({}),
            lambda: NOW.isoformat(),
            lambda _request: None,
            fence=distinct_fence,
            specialists=pool,
        )


@pytest.mark.parametrize("boundary", ("after_private_generation", "after_admission", "after_invoke", "after_close"))
def test_crash_boundaries_replay_closes_private_generation_without_reinvoke(tmp_path, boundary):
    pool, fence, generations = setup(tmp_path, maximum=1, quota=1)
    pool._hook = lambda point: (_ for _ in ()).throw(OSError("crash")) if point == boundary else None
    with pytest.raises(OSError, match="crash"):
        pool.dispatch((task(1, generations[0]),), lambda _view: answer("never"), cancelled=lambda: False)
    calls = []
    replay = restarted(pool, fence).dispatch(
        (task(1, generations[0]),),
        lambda view: calls.append(view) or answer("recovered"),
        cancelled=lambda: False,
    )

    if boundary == "after_private_generation":
        assert len(calls) == 1
        assert replay.results[0].verdict is SpecialistVerdict.ACCEPTED
    else:
        assert calls == []
        expected = SpecialistVerdict.ACCEPTED if boundary == "after_close" else SpecialistVerdict.CANCELLED
        assert replay.results[0].verdict is expected
        assert replay.results[0].replayed
        assert len(verify_receipt(replay.receipt_path, fence)["quota_trace"]) == 1


def test_hard_timeout_terminates_without_waiting_for_model_thread(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=1, quota=1)
    pool.profile = SpecialistProfile(1, 1, max_wall_seconds=0.01)
    requested = replace(task(1, generations[0]), deadline=(NOW + dt.timedelta(milliseconds=10)).isoformat())
    began = time.monotonic()
    result = pool.dispatch((requested,), lambda _view: time.sleep(1), cancelled=lambda: False).results[0]

    assert time.monotonic() - began < 0.2
    assert result.verdict is SpecialistVerdict.CANCELLED


def test_inflight_parent_cancel_rejects_late_output_and_records_termination(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=1, quota=1)
    stopped = threading.Event()
    release = threading.Event()
    pool._terminate = lambda _engagement: SpecialistTermination(True, "b" * 64, 0)

    def invoke(_view):
        release.wait(timeout=1)
        return answer("late")

    worker = threading.Timer(0.02, stopped.set)
    worker.start()
    result = pool.dispatch((task(1, generations[0]),), invoke, cancelled=stopped.is_set).results[0]
    release.set()

    assert result.verdict is SpecialistVerdict.CANCELLED
    assert "termination" in result.reason


def test_simultaneous_dispatches_share_one_durable_pool_capacity(tmp_path):
    pool, _fence, generations = setup(tmp_path, maximum=1, quota=2)
    entered = threading.Event()
    release = threading.Event()
    active = 0
    peak = 0
    guard = threading.Lock()

    def invoke(_view):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        entered.set()
        release.wait(timeout=1)
        with guard:
            active -= 1
        return answer("ok")

    batches = []
    first = threading.Thread(
        target=lambda: batches.append(pool.dispatch((task(1, generations[0]),), invoke, cancelled=lambda: False))
    )
    second = threading.Thread(
        target=lambda: batches.append(pool.dispatch((task(2, generations[1]),), invoke, cancelled=lambda: False))
    )
    first.start()
    assert entered.wait(timeout=1)
    second.start()
    second.join(timeout=1)
    release.set()
    first.join(timeout=1)

    assert peak == 1
    assert sum(result.verdict is SpecialistVerdict.ACCEPTED for batch in batches for result in batch.results) == 1


def test_multi_turn_work_precharges_shared_quota_and_reports_truthful_turns(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=2, quota=2)
    first = replace(task(1, generations[0]), turn_limit=2)
    batch = pool.dispatch(
        (first, task(2, generations[1])),
        lambda _view: answer("two turns", turns=2),
        cancelled=lambda: False,
    )
    receipt = verify_receipt(batch.receipt_path, fence)

    accepted = next(item for item in batch.results if item.verdict is SpecialistVerdict.ACCEPTED)
    assert accepted.proposal.turns == 2
    assert [row["turn_index"] for row in receipt["quota_trace"]] == [1, 2]
    assert any(item.verdict is SpecialistVerdict.REJECTED for item in batch.results)


def test_surviving_owner_keeps_generation_open_after_tool_revocation(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=1, quota=1)
    tools = pool._tools("read-only")
    pool._tools = lambda _profile: tools
    pool._terminate = lambda _engagement: SpecialistTermination(False, "c" * 64, 1)
    pool.profile = SpecialistProfile(1, 1, max_wall_seconds=0.01)
    requested = replace(task(1, generations[0]), deadline=(NOW + dt.timedelta(milliseconds=10)).isoformat())
    result = pool.dispatch((requested,), lambda _view: time.sleep(1), cancelled=lambda: False).results[0]

    assert result.verdict is SpecialistVerdict.UNSETTLED
    assert tools.revoked
    private = next(item for item in fence.projection().generations if item.work_id == "specialist:task-1")
    assert private.active
    verify_receipt(pool._receipt, fence)

    replay = restarted(pool, fence).dispatch(
        (requested,), lambda _view: (_ for _ in ()).throw(AssertionError("reinvoked")), cancelled=lambda: False
    )
    assert replay.results[0].verdict is SpecialistVerdict.UNSETTLED
    assert private.generation_id in {item.generation_id for item in fence.projection().generations if item.active}
    assert len(json.loads(pool._path.read_bytes())["results"]) == 1
    verify_receipt(pool._receipt, fence)


def test_multi_item_crash_replay_settles_every_admission_and_leaks_no_owner(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=2, quota=2)
    pool._hook = lambda point: (_ for _ in ()).throw(OSError("crash")) if point == "after_invoke" else None
    requested = tuple(task(i, generation) for i, generation in enumerate(generations, 1))
    with pytest.raises(OSError, match="crash"):
        pool.dispatch(requested, lambda _view: answer("done"), cancelled=lambda: False)

    replay = restarted(pool, fence).dispatch(
        requested, lambda _view: (_ for _ in ()).throw(AssertionError("reinvoked")), cancelled=lambda: False
    )
    assert [item.verdict for item in replay.results] == [SpecialistVerdict.CANCELLED] * 2
    assert all(item.replayed for item in replay.results)
    assert not any(item.active for item in fence.projection().generations if item.work_id.startswith("specialist:"))
    assert not {key for key in SpecialistPool._owners if key[:2] == (str(pool.state.resolve()), pool.run_id)}


def test_owner_registry_is_scoped_by_state_root_and_run(tmp_path):
    first, _first_fence, first_generations = setup(tmp_path / "first", maximum=1, quota=1)
    entered = threading.Event()
    release = threading.Event()
    worker = threading.Thread(
        target=lambda: first.dispatch(
            (task(1, first_generations[0]),),
            lambda _view: entered.set() or release.wait(timeout=1) or answer("first"),
            cancelled=lambda: False,
        )
    )
    worker.start()
    assert entered.wait(timeout=1)

    second, second_fence, second_generations = setup(tmp_path / "second", maximum=1, quota=1)
    second._hook = lambda point: (_ for _ in ()).throw(OSError("crash")) if point == "after_admission" else None
    with pytest.raises(OSError, match="crash"):
        second.dispatch((task(1, second_generations[0]),), lambda _view: answer("second"), cancelled=lambda: False)
    replay = restarted(second, second_fence).dispatch(
        (task(1, second_generations[0]),),
        lambda _view: (_ for _ in ()).throw(AssertionError("reinvoked")),
        cancelled=lambda: False,
    )
    release.set()
    worker.join(timeout=1)
    assert replay.results[0].verdict is SpecialistVerdict.CANCELLED
    assert replay.results[0].replayed


def test_carry_refuses_stored_generation_mismatch(tmp_path):
    path = tmp_path / "carry.json"
    path.write_bytes(canonical_bytes({"generation_id": "other", "evidence": [], "acceptances": []}) + b"\n")
    with pytest.raises(ValueError, match="stored generation"):
        SpecialistCarry(path, "expected").evidence


def test_lead_refuses_cross_parent_generation_batch(tmp_path):
    pool, fence, generations = setup(tmp_path)
    lead = LeadController(
        tmp_path / "state",
        "run-1",
        Redactor({}),
        lambda: NOW.isoformat(),
        lambda _request: None,
        fence=fence,
        specialists=pool,
    )
    with pytest.raises(ValueError, match="crosses Lead"):
        lead.dispatch_specialists(
            tuple(task(i, generation) for i, generation in enumerate(generations, 1)),
            lambda _view: answer("never"),
            cancelled=lambda: False,
            select=lambda _result: True,
            carry=SpecialistCarry(tmp_path / "carry.json", generations[0].generation_id),
            generation_id=generations[0].generation_id,
            attempt_id=generations[0].attempt_id,
            engagement_id="lead-1",
        )


def test_lead_acceptance_reservation_replays_selection_once_and_imports_once(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=1, quota=1)
    requested = task(1, generations[0])
    pool.dispatch((requested,), lambda view: answer("finding", (view.evidence[0].ref,)), cancelled=lambda: False)
    lead = LeadController(
        tmp_path / "state",
        "run-1",
        Redactor({}),
        lambda: NOW.isoformat(),
        lambda _request: None,
        fence=fence,
        specialists=restarted(pool, fence),
    )
    calls = []
    carry_path = tmp_path / "carry.json"
    crashing = SpecialistCarry(
        carry_path,
        generations[0].generation_id,
        hook=lambda point: (_ for _ in ()).throw(OSError("crash")) if point == "after_acceptance_reservation" else None,
    )
    arguments = dict(
        tasks=(requested,),
        invoke=lambda _view: (_ for _ in ()).throw(AssertionError("reinvoked")),
        cancelled=lambda: False,
        generation_id=generations[0].generation_id,
        attempt_id=generations[0].attempt_id,
        engagement_id="lead-1",
    )
    with pytest.raises(OSError, match="crash"):
        lead.dispatch_specialists(
            **arguments,
            select=lambda result: calls.append(result.task_id) or result.verdict is SpecialistVerdict.ACCEPTED,
            carry=crashing,
        )
    carry = SpecialistCarry(carry_path, generations[0].generation_id)
    recovered = lead.dispatch_specialists(
        **arguments, select=lambda _result: (_ for _ in ()).throw(AssertionError("reselected")), carry=carry
    )
    repeated = lead.dispatch_specialists(
        **arguments, select=lambda _result: (_ for _ in ()).throw(AssertionError("reselected")), carry=carry
    )
    assert calls == ["task-1"]
    assert [item.ref for item in recovered] == ["evidence-1"]
    assert repeated == ()
    assert json.loads(carry_path.read_bytes())["acceptances"][0]["acceptance_id"]
    empty = SpecialistCarry(tmp_path / "empty-carry.json", generations[0].generation_id)
    assert (
        empty.accept_results(
            (SpecialistResult("empty", SpecialistVerdict.ACCEPTED, SpecialistProposal("no evidence")),),
            (),
            lambda _result: True,
            generation_id=generations[0].generation_id,
        )
        == ()
    )
    assert json.loads(empty.path.read_bytes())["acceptances"][0]["imported"]


def test_replay_reconstructs_proposal_for_durable_lead_carry(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=1, quota=1)
    requested = task(1, generations[0])
    pool.dispatch((requested,), lambda view: answer("finding", (view.evidence[0].ref,)), cancelled=lambda: False)
    lead = LeadController(
        tmp_path / "state",
        "run-1",
        Redactor({}),
        lambda: NOW.isoformat(),
        lambda _request: None,
        fence=fence,
        specialists=restarted(pool, fence),
    )
    carry = SpecialistCarry(tmp_path / "carry.json", generations[0].generation_id)
    selected = lead.dispatch_specialists(
        (requested,),
        lambda _view: (_ for _ in ()).throw(AssertionError("reinvoked")),
        cancelled=lambda: False,
        select=lambda _result: True,
        carry=carry,
        generation_id=generations[0].generation_id,
        attempt_id=generations[0].attempt_id,
        engagement_id="lead-1",
    )

    assert [item.ref for item in selected] == ["evidence-1"]
    with pytest.raises(ValueError, match="changed digest"):
        carry.import_selected(
            (SpecialistEvidence("evidence-1", "f" * 64, generations[0].generation_id),),
            generation_id=generations[0].generation_id,
        )


def test_controlled_proof_names_every_criterion_scenario():
    proof = load_controlled_proof()

    assert set(proof["profiles"]) == {"zero", "one", "maximum-two"}
    assert set(proof["scenarios"]) == {
        "bounds-and-quota",
        "canonical-authority-fencing",
        "cancel-and-late-output",
        "carry-acceptance-replay",
        "crash-replay",
        "generation-bound-evidence",
        "global-capacity",
        "lead-carry-integration",
        "manifest-coexistence",
        "multi-item-crash",
        "replay-preserves-verdict",
        "scoped-ownership",
        "surviving-owner",
        "unsettled-replay",
    }
    assert (
        proof["subject_digest"]
        == __import__("solver.specialist_pool_proof", fromlist=["subject_digest"]).subject_digest()
    )


def test_forged_cross_generation_evidence_and_returned_refs_are_rejected(tmp_path):
    pool, _fence, generations = setup(tmp_path)
    pool._resolve = lambda ref, _generation: SpecialistEvidence(ref, "1" * 64, generations[1].generation_id)
    crossed = pool.dispatch((task(1, generations[0]),), lambda _view: answer("bad"), cancelled=lambda: False)
    assert crossed.results[0].reason == "cross-generation-evidence"

    pool, _fence, generations = setup(tmp_path / "returned")
    returned = pool.dispatch(
        (task(1, generations[0]),),
        lambda _view: answer("bad", ("other-generation",)),
        cancelled=lambda: False,
    )
    assert returned.results[0].reason == "unauthorized-evidence"


def test_receipt_tamper_cannot_forge_quota_or_measured_turns(tmp_path):
    pool, fence, generations = setup(tmp_path, maximum=1, quota=1)
    batch = pool.dispatch((task(1, generations[0]),), lambda _view: answer("ok"), cancelled=lambda: False)
    path = Path(batch.receipt_path)
    forged = json.loads(path.read_bytes())
    forged["results"][0]["turns"] = 2
    path.write_bytes(canonical_bytes(forged) + b"\n")

    with pytest.raises(ValueError, match="durable control state"):
        verify_receipt(path, fence)
