"""Authority-space reservation is observable at the effect boundary and after restart."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from solver.write_reservation import (
    Capacity,
    CONTROLLED_CRASH_OUTCOMES,
    EffectIndeterminate,
    Pool,
    ReservationConflict,
    ReservationState,
    ReservationUnavailable,
    ReservedEffect,
    WriteAuthority,
    WriteProfile,
    manifest_receipt,
)
from solver.manifest import generate_manifest
from solver.event_store_storage import canonical_bytes
from test_manifest import release_candidate_profile


PROFILE = WriteProfile(
    ordinary=Capacity(bytes=8_192, objects=1, operations=12),
    shared=Capacity(bytes=16_384, objects=2, operations=24),
    terminal=Capacity(bytes=8_192, objects=1, operations=12),
)
NEED = Capacity(bytes=4_096, objects=1, operations=3)


def identity(candidate="sha256:candidate-one"):
    return {"operation": "board.submit", "challenge_id": "42", "candidate_digest": candidate}


def test_full_ordinary_area_preserves_physical_authority_and_terminal_capacity(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    ordinary_extent = authority.extent_path(Pool.ORDINARY)
    shared_extent = authority.extent_path(Pool.SHARED)
    terminal_extent = authority.extent_path(Pool.TERMINAL)

    ordinary_need = Capacity(bytes=8_192, objects=1, operations=3)
    ordinary = authority.reserve("ordinary:bulk", {"operation": "body.write"}, ordinary_need, pool=Pool.ORDINARY)
    authority.object_path(ordinary).write_bytes(b"x" * ordinary_need.bytes)
    authority.start(ordinary)
    authority.commit(ordinary, {"digest": "sha256:ordinary"})
    assert ordinary_extent.stat().st_size == 0
    with pytest.raises(ReservationUnavailable):
        authority.reserve("ordinary:second", {"operation": "body.write"}, ordinary_need, pool=Pool.ORDINARY)

    reservation = authority.reserve("submit:42:one", identity(), NEED)
    authority.start(reservation)
    authority.commit(reservation, {"outcome": "incorrect", "http_status": 200})
    terminal = authority.record_terminal(
        "run:terminal",
        {"operation": "run.terminal", "run_id": "run-1"},
        Capacity(bytes=4_096, objects=0, operations=3),
        classification="storage-terminal",
    )

    assert shared_extent.stat().st_size < PROFILE.shared.bytes
    assert terminal_extent.stat().st_size < PROFILE.terminal.bytes
    assert authority.current(reservation.key).state is ReservationState.COMMITTED
    assert WriteAuthority(tmp_path, PROFILE).current(terminal.key).state is ReservationState.TERMINAL


def test_effect_trace_places_durable_reserve_before_wire_and_commit_after_observation(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    seen = []

    def wire():
        seen.append(authority.current("submit:42:one").state)
        return {"outcome": "correct", "http_status": 200}

    result = ReservedEffect(authority).execute(
        "submit:42:one",
        identity(),
        NEED,
        wire,
        encode=lambda verdict: verdict,
        decode=lambda verdict: verdict,
    )

    assert result == {"outcome": "correct", "http_status": 200}
    assert seen == [ReservationState.STARTED]
    assert [row["state"] for row in authority.trace("submit:42:one")] == [
        "reserved",
        "started",
        "committed",
    ]


@pytest.mark.parametrize("crash_point", ("before_reserve", "after_reserve", "after_effect"))
def test_crash_boundaries_never_repeat_an_effect(tmp_path, crash_point):
    calls = []

    def crash(point):
        if point == crash_point:
            raise RuntimeError(f"crash at {point}")

    authority = WriteAuthority(tmp_path, PROFILE, hook=crash)
    effect = ReservedEffect(authority)

    with pytest.raises(RuntimeError, match="crash at"):
        effect.execute(
            "submit:42:one",
            identity(),
            NEED,
            lambda: calls.append("wire") or {"outcome": "unread"},
            encode=lambda verdict: verdict,
            decode=lambda verdict: verdict,
        )

    restarted = WriteAuthority(tmp_path, PROFILE)
    if crash_point == "before_reserve":
        assert restarted.current("submit:42:one") is None
        assert calls == []
        return

    with pytest.raises(EffectIndeterminate):
        ReservedEffect(restarted).execute(
            "submit:42:one",
            identity(),
            NEED,
            lambda: calls.append("duplicate") or {"outcome": "incorrect"},
            encode=lambda verdict: verdict,
            decode=lambda verdict: verdict,
        )
    assert calls == ([] if crash_point == "after_reserve" else ["wire"])
    expected = ReservationState.ABORTED if crash_point == "after_reserve" else ReservationState.POSSIBLY_SENT
    assert restarted.current("submit:42:one").state is expected


def test_exhaustion_refuses_before_the_effect_and_terminal_pool_is_not_borrowed(tmp_path):
    profile = WriteProfile(
        ordinary=Capacity(0, 0, 0),
        shared=Capacity(2_048, 0, 6),
        terminal=Capacity(4_096, 0, 6),
    )
    authority = WriteAuthority(tmp_path, profile)
    calls = []

    with pytest.raises(ReservationUnavailable) as refused:
        ReservedEffect(authority).execute(
            "submit:42:one",
            identity(),
            Capacity(4_096, 0, 3),
            lambda: calls.append("wire"),
            encode=lambda result: {"result": result},
            decode=lambda result: result,
        )

    assert refused.value.state is ReservationState.REFUSED
    assert calls == []
    assert authority.extent_path(Pool.TERMINAL).stat().st_size == profile.terminal.bytes


def test_one_idempotency_key_binds_one_effect_across_concurrency_and_reopen(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    calls = []

    def execute(_index):
        return ReservedEffect(authority).execute(
            "submit:42:one",
            identity(),
            NEED,
            lambda: calls.append("wire") or {"outcome": "correct"},
            encode=lambda verdict: verdict,
            decode=lambda verdict: verdict,
        )

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(execute, range(4)))

    assert results == [{"outcome": "correct"}] * 4
    assert calls == ["wire"]
    with pytest.raises(ReservationConflict):
        authority.reserve("submit:42:one", identity("sha256:different"), NEED)


def test_concurrent_distinct_effects_cannot_overcommit_one_physical_grant(tmp_path):
    profile = WriteProfile(
        ordinary=Capacity(0, 0, 0),
        shared=Capacity(4_096, 1, 6),
        terminal=Capacity(4_096, 1, 6),
    )
    authority = WriteAuthority(tmp_path, profile)

    def reserve(index):
        try:
            return authority.reserve(f"submit:{index}", identity(f"sha256:{index}"), NEED).state.value
        except ReservationUnavailable as error:
            return error.state.value

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(reserve, range(2)))

    assert sorted(outcomes) == ["refused", "reserved"]
    assert authority.extent_path(Pool.SHARED).stat().st_size == 0
    assert authority.extent_path(Pool.TERMINAL).stat().st_size == profile.terminal.bytes


def test_receipt_binds_profile_identity_capacity_and_durable_trace(tmp_path):
    authority = WriteAuthority(tmp_path, PROFILE)
    reservation = authority.reserve("submit:42:one", identity(), NEED)
    authority.start(reservation)
    authority.commit(reservation, {"outcome": "incorrect"})

    receipt_path = authority.write_receipt("submit:42:one")
    receipt = json.loads(receipt_path.read_text())

    assert receipt["receipt_type"] == "write-reservation"
    assert receipt["profile_digest"] == authority.profile_digest
    assert receipt["effect_fingerprint"] == reservation.effect_fingerprint
    assert receipt["need"] == {"bytes": 4_096, "objects": 1, "operations": 3}
    assert receipt["crash_point_outcomes"] == CONTROLLED_CRASH_OUTCOMES
    assert receipt["manifest_link"] == {
        "row_id": "core.canonical-state-replay-restart",
        "receipt_ref": "receipt:write-reservation",
    }
    assert authority.verify_receipt(receipt_path)["state"] == "committed"

    descriptor = manifest_receipt(receipt_path)
    draft = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    requirements = copy.deepcopy(draft["requirements"])
    row = next(item for item in requirements if item["row_id"] == receipt["manifest_link"]["row_id"])
    row.update(status="implemented", receipt_ref=descriptor["ref"])
    linked = generate_manifest(
        image_digest=draft["candidate"]["image_digest"],
        release_candidate_profile=draft["selected_profile"],
        requirements=requirements,
        receipts=[descriptor],
    )
    linked_row = next(item for item in linked["requirements"] if item["row_id"] == row["row_id"])
    assert linked_row["receipt_ref"] == "receipt:write-reservation"

    receipt["trace"][1]["state"] = "committed"
    receipt_path.write_bytes(canonical_bytes(receipt) + b"\n")
    with pytest.raises(ValueError, match="digest"):
        authority.verify_receipt(receipt_path)
