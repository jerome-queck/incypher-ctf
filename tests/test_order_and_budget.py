"""What to work and how long, held to ADR-0015 — and to the two mechanisms ADR-0017 put back.

Three claims are on trial here and each has a failure it is protecting against.

**Order is a pure, deterministic, total function.** Not tidiness: shadow mode replays a stored
stream offline to ask *"would other weights have banked Flags faster"*, and a ranking that cannot
be reproduced has no baseline to be compared against. So the tests below rank the same set twice,
shuffle it, and check the tie-break.

**Nothing is ever banned, and nothing is ever given less time for having failed.** A barren Attempt
costs a Challenge its place in line, never its budget — so the tests that sink a Challenge check it
is still *reachable*, and the ceiling tests check it comes back when the whole Board is past its
own.

**The clock buys a number of Attempts.** Dividing the remaining hours by the unsolved count puts
every Attempt below the length at which anything is ever solved, which is the allocation that looks
fair and solves nothing.

Nothing here is substituted: Order, the working-set arithmetic and the budget are pure functions
called with constructed inputs (spec #63, *What is deliberately not a seam*). The only seams that
appear are the clock, injected at the edge, and Triage's judge — left unasked, so a Tier here is
the floor rather than something a model in a test decided.
"""

import datetime as dt
import json

import pytest
from solver.instance import Lease, Terms
from solver.intake import Sighting, Snapshot
from solver.record import CRASHED, CUT_BUDGET, CUT_SELF_REPORTED_IMPOSSIBLE, Recorder
from solver.redaction import Redactor
from solver.schedule import Dials, Ended, Scheduler, Window, render
from solver.triage import FLOOR

OPENED = dt.datetime(2026, 9, 22, 10, 30, tzinfo=dt.timezone.utc)
COMPETITION = 5.5 * 3600


class Clock:
    """The one seam these tests need. Wall-clock decides `T_remaining`, `K`, both reserves and
    `L_min`, so it is injected rather than read."""

    def __init__(self, at: dt.datetime = OPENED) -> None:
        self.at = at

    def __call__(self) -> dt.datetime:
        return self.at

    def on(self, seconds: float) -> None:
        self.at += dt.timedelta(seconds=seconds)


def sighting(challenge_id, *, solves=0, value=100, position=0, solved=False, shared=False, kind="standard"):
    return Sighting(
        challenge_id=challenge_id,
        name=f"challenge-{challenge_id}",
        category="forensics",
        challenge_type=kind,
        value=value,
        solves=solves,
        position=position,
        description="",
        attempts=0,
        max_attempts=None,
        solved=solved,
        terms=Terms(challenge_id=challenge_id, challenge_type=kind, shared=shared),
    )


def seen(*challenges, at=OPENED, cycle=1):
    return Snapshot(at=at, cycle=cycle, challenges=tuple(challenges))


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def scheduler(recorder, clock):
    return Scheduler(
        Window.opened(recorder.run_dir, lasting=COMPETITION, now=OPENED),
        recorder,
        now=clock,
    )


def ranking(scheduler, snapshot, **held):
    return [one.challenge_id for one in scheduler.order(snapshot, **held)]


def worked(scheduler, snapshot, *, seconds=60.0, cause=CUT_BUDGET, checkpoints=0):
    """One whole Attempt boundary — acquire, work it, give it back."""
    pick = scheduler.acquire(snapshot)
    if pick is not None:
        scheduler.release(Ended(pick.challenge.challenge_id, cause=cause, seconds=seconds, checkpoints=checkpoints))
    return pick


# The public surface: one authority over both halves


def test_acquire_answers_a_challenge_and_its_budget_together(scheduler):
    """The pair is the point. A design where something else decides how long an Attempt gets is one
    where v3's paralleliser is a rewrite rather than *"hold N"*."""
    pick = scheduler.acquire(seen(sighting(1)))

    assert pick.challenge.challenge_id == 1
    assert pick.budget_s > 0
    assert pick.deadline == OPENED + dt.timedelta(seconds=pick.budget_s)


def test_v1_holds_exactly_one_attempt_at_a_time(scheduler):
    """Refused rather than quietly allowed: two Attempts sharing one ledger would each be ranked as
    though the other's spend had not happened."""
    board = seen(sighting(1), sighting(2))
    scheduler.acquire(board)

    with pytest.raises(ValueError, match="holds 1 Attempt"):
        scheduler.acquire(board)


def test_how_many_are_held_at_once_is_the_dial_and_not_the_shape_of_the_code(recorder, clock):
    """*"v3 tunes a number rather than reshaping the scheduler"* is only true if the number is the
    thing `acquire` reads. What a second concurrent hold means for the ranking is still v3's to
    settle; the cap is what is settled here."""
    holding_two = Scheduler(
        Window.opened(recorder.run_dir, lasting=COMPETITION, now=OPENED),
        recorder,
        dials=Dials(concurrency=2),
        now=clock,
    )
    board = seen(sighting(1), sighting(2))

    first, second = holding_two.acquire(board), holding_two.acquire(board)

    assert (first.challenge.challenge_id, second.challenge.challenge_id) == (1, 2)
    with pytest.raises(ValueError, match="holds 2 Attempt"):
        holding_two.acquire(board)


def test_a_released_challenge_is_eligible_again_immediately(scheduler):
    """There is no queue. A cut Challenge is not placed anywhere — where it next ranks falls out of
    the function, and on a Board of one that is straight back to the top."""
    board = seen(sighting(1))
    worked(scheduler, board)

    again = scheduler.acquire(board)

    assert again.challenge.challenge_id == 1
    assert again.attempt_sequence == 2


# Order is pure, deterministic and total


def test_every_eligible_challenge_is_ranked_exactly_once(scheduler):
    ranked = scheduler.order(seen(sighting(1), sighting(2), sighting(3)))

    assert [one.rank for one in ranked] == [1, 2, 3]
    assert {one.challenge_id for one in ranked} == {1, 2, 3}


def test_the_same_board_ranks_the_same_way_twice_and_in_any_order(scheduler):
    """Reproducibility is what makes an offline replay a baseline rather than a second opinion."""
    challenges = [sighting(1, value=300), sighting(2, value=100), sighting(3, value=200)]

    once = ranking(scheduler, seen(*challenges))
    again = ranking(scheduler, seen(*challenges))
    shuffled = ranking(scheduler, seen(*reversed(challenges)))

    assert once == again == shuffled == [1, 3, 2]


def test_a_tie_breaks_on_the_boards_own_position_and_then_on_id(scheduler):
    """Both are stable across a Run, which is what an offline replay needs. Equal positions fall
    through to the id rather than to whatever order the list happened to arrive in."""
    on_position = ranking(scheduler, seen(sighting(7, position=9), sighting(8, position=2)))
    on_id = ranking(scheduler, seen(sighting(9, position=1), sighting(4, position=1)))

    assert on_position == [8, 7]
    assert on_id == [4, 9]


# Eligibility: two facts, and nothing else ever takes a Challenge out


def test_a_solved_challenge_is_out_and_an_unsolved_one_never_is(scheduler):
    ranked = ranking(scheduler, seen(sighting(1, solved=True), sighting(2)))

    assert ranked == [2]


def test_a_shared_isolated_challenge_is_ineligible_because_it_is_undeployable_by_us(scheduler):
    """POST, PATCH *and* DELETE all fail on a `shared` Instance — only an admin ever deploys one
    (`solver/instance.py`). That is a fact about the Board rather than a judgement about the
    Challenge, which is why it sits beside "already solved" and nowhere near a penalty."""
    board = seen(sighting(1, shared=True, kind="dynamic_iac"), sighting(2, shared=True), sighting(3))

    ranked = ranking(scheduler, board)

    assert ranked == [2, 3], "a shared Challenge that is not Isolated is deployed by nobody, so it is ordinary"


def test_a_challenge_the_model_called_impossible_is_penalised_and_never_excluded(scheduler):
    """ADR-0009 keeps this cause as an alarm the aim is for it never to fire, and a cause that
    silently deletes Challenges is an alarm nobody can watch trending to zero."""
    board = seen(sighting(1, value=500), sighting(2, value=100))
    scheduler.release(Ended(1, cause=CUT_SELF_REPORTED_IMPOSSIBLE, seconds=120.0))

    assert ranking(scheduler, board) == [2, 1]
    assert scheduler.acquire(seen(sighting(1, value=500))).challenge.challenge_id == 1


def test_the_penalty_for_calling_something_impossible_does_not_decay(scheduler):
    """A model that called something impossible will call it impossible again, and a decaying
    penalty re-buys the same refusal every hour."""
    board = seen(sighting(1, value=500), sighting(2, value=100))
    scheduler.release(Ended(1, cause=CUT_SELF_REPORTED_IMPOSSIBLE, seconds=1.0))

    for _ in range(4):
        worked(scheduler, board)

    assert ranking(scheduler, board)[-1] == 1


# Spend: monotone, and the anti-livelock guarantee


def test_an_attempt_that_dies_in_two_seconds_still_costs_its_challenge_its_place(scheduler):
    """`spend_norm` is `max(time_norm, attempts_norm)` and the `max` is the whole claim. A penalty
    on time alone leaves a degenerate Attempt top of Order, picked again immediately, at ~22k input
    tokens a turn — a quota fire as well as a wasted Run."""
    board = seen(sighting(1, value=500), sighting(2, value=100))

    for _ in range(6):
        worked(scheduler, board, seconds=2.0)

    assert ranking(scheduler, board) == [2, 1]


def test_a_challenge_worked_without_progress_eventually_ranks_below_an_untouched_one(scheduler):
    """The Board gets covered by construction rather than by a threshold somebody tuned."""
    board = seen(sighting(1, value=500), sighting(2, value=100), sighting(3, value=90))
    reached = []

    for _ in range(8):
        reached.append(worked(scheduler, board, seconds=600.0).challenge.challenge_id)

    assert set(reached) == {1, 2, 3}


def test_checkpoints_hold_a_challenge_near_the_top_that_spend_would_otherwise_sink(scheduler):
    """It moved the environment last time, which is the one piece of evidence about a Challenge we
    ever generate ourselves."""
    board = seen(sighting(1, value=100), sighting(2, value=100, position=1))
    scheduler.release(Ended(1, cause=CUT_BUDGET, seconds=600.0, checkpoints=3))
    scheduler.release(Ended(2, cause=CUT_BUDGET, seconds=600.0, checkpoints=0))

    assert ranking(scheduler, board) == [1, 2]


def test_a_live_lease_lifts_a_challenge_because_the_mana_is_already_spent(scheduler):
    board = seen(sighting(1, value=100), sighting(2, value=100, position=1))

    assert ranking(scheduler, board, leased=[2]) == [2, 1]


# The spend ceiling demotes, and gives back


def test_a_challenge_past_its_ceiling_ranks_below_every_challenge_not_past_its_own(scheduler):
    """`f × T_total` caps the compounding of three pressures that all push the same way:
    consecutive Attempts are ordinary, a live Lease boosts Order, and Checkpoints raise Tier."""
    board = seen(sighting(1, value=500), sighting(2, value=10))
    scheduler.release(Ended(1, cause=CUT_BUDGET, seconds=0.2 * COMPETITION, checkpoints=3))

    assert ranking(scheduler, board) == [2, 1]


def test_a_demoted_challenge_returns_when_the_whole_board_is_past_its_ceiling(scheduler):
    """Demoted, never excluded — the ordering says *later*, and later arrives."""
    board = seen(sighting(1, value=500), sighting(2, value=10))
    for challenge_id in (1, 2):
        scheduler.release(Ended(challenge_id, cause=CUT_BUDGET, seconds=0.2 * COMPETITION))

    assert ranking(scheduler, board) == [1, 2]


def test_the_circuit_breakers_verdict_hard_demotes_and_is_not_recounted_here(scheduler):
    """`crashed` is evidence about *us* rather than about the Challenge, and `stall.Breaker` owns
    the counter that reaches it. A second counter here would be a second thing to disagree."""
    board = seen(sighting(1, value=500), sighting(2, value=10))
    scheduler.release(Ended(1, cause=CRASHED, seconds=1.0))

    assert ranking(scheduler, board) == [2, 1]


# The clock buys a number of Attempts


def test_the_budget_is_the_knee_scaled_by_tier_and_never_a_slice_of_the_clock(scheduler):
    """74 Challenges into 5.5 hours is four minutes each, and it solves nothing."""
    board = seen(*(sighting(one) for one in range(74)))

    pick = scheduler.acquire(board)

    assert pick.tier == FLOOR
    assert pick.budget_s == int(Dials().knee_seconds)


def test_the_working_set_is_the_top_of_order_and_narrows_as_the_run_burns_down(scheduler, clock):
    """`K = (T_remaining − tail) / L̄`, so the final stretch is a consequence of the arithmetic
    rather than a mode with rules of its own — a second thing to calibrate that would fire exactly
    once per Run."""
    board = seen(*(sighting(one, value=200 - one) for one in range(40)))

    at_the_start = _committed(scheduler.order(board))
    clock.on(COMPETITION - 3600)
    an_hour_left = _committed(scheduler.order(board))

    assert len(at_the_start) == 32
    assert an_hour_left == [0, 1, 2, 3, 4]


def test_no_attempt_is_started_shorter_than_the_floor(scheduler, clock):
    """Cause-neutral, and there is no deploy-specific late-run gate: `min(budget, TTL)` already
    stops an Attempt outliving its Instance, so gating deploys would forbid Attempts that were
    perfectly viable."""
    board = seen(sighting(1))
    clock.on(COMPETITION - Dials().tail_seconds - Dials().floor_seconds + 1)

    assert scheduler.acquire(board) is None


def test_a_budget_is_cut_to_what_is_left_over_the_tail_rather_than_refused(scheduler, clock):
    board = seen(sighting(1))
    clock.on(COMPETITION - Dials().tail_seconds - 400)

    pick = scheduler.acquire(board)

    assert pick.budget_s == 400
    assert scheduler.window.left(pick.deadline) == Dials().tail_seconds


def test_the_run_tail_and_the_instances_submission_reserve_are_two_separate_reserves(scheduler, clock):
    """One bounds a Run — submit pending candidates, destroy Instances, flush telemetry, exit clean
    — and the other bounds a Lease. Merging them would silently apply whichever was larger to both."""
    clock.on(COMPETITION - Dials().tail_seconds - 900)
    pick = scheduler.acquire(seen(sighting(1, kind="dynamic_iac")))
    lease = Lease(1, "nc host 1", clock.at + dt.timedelta(seconds=200), Terms(1, "dynamic_iac"))

    assert pick.budget_s == 600, "the run tail is the scheduler's and knows nothing about an Instance"
    assert lease.attempt_deadline(pick.deadline) == clock.at + dt.timedelta(seconds=140)


# Tier rises on Checkpoints, and nothing lowers it


def test_a_tier_rises_on_checkpoints_earned_across_attempts(scheduler):
    board = seen(sighting(1))
    first = scheduler.acquire(board)
    scheduler.release(Ended(1, cause=CUT_BUDGET, seconds=60.0, checkpoints=1))

    second = scheduler.acquire(board)

    assert (first.tier, second.tier) == (FLOOR, FLOOR + 1)
    assert second.budget_s > first.budget_s


def test_the_rise_is_capped_and_a_barren_attempt_never_takes_it_back(scheduler):
    """Symmetry is the obvious design and it is a livelock: a Challenge that fails twice would get
    less time, so it would fail again faster, so it would get less time."""
    board = seen(sighting(1))
    for _ in range(9):
        worked(scheduler, board, checkpoints=1)

    at_the_cap = scheduler.acquire(board).tier
    scheduler.release(Ended(1, cause=CUT_BUDGET, seconds=600.0, checkpoints=0))

    assert at_the_cap == FLOOR + Dials().tier_cap
    assert scheduler.acquire(board).tier == at_the_cap


# Order never sees a null Tier


def test_a_challenge_released_mid_run_is_triaged_at_the_next_attempt_boundary(scheduler, recorder):
    """Triage is one callable thing rather than a stage of a pipeline, so it runs on whatever the
    Board dropped since the last boundary — and Order never handles a Challenge with no Tier."""
    worked(scheduler, seen(sighting(1)))

    ranked = scheduler.order(seen(sighting(1), sighting(2)))

    assert [one.tier for one in ranked if one.challenge_id == 2] == [FLOOR]
    assert [line["tiers"] for line in _records(recorder, "triage")] == [
        [_tier_record(1)],
        [_tier_record(2)],
    ], "only the arrival is triaged — a prior is never revisited, because nothing lowers a Tier"


# What the pick leaves behind


def test_every_ranked_challenge_is_recorded_at_each_pick(scheduler):
    """A Run that passed over sixty Challenges and a Run that only ever had fourteen read
    identically without it, and eval question 5 cannot be answered from either."""
    board = seen(sighting(1, value=300), sighting(2, value=100), sighting(3, value=200))

    pick = scheduler.acquire(board)

    assert pick.order_ranks == {"1": 1, "3": 2, "2": 3}


def test_the_rank_vector_reads_as_one_line_per_challenge(scheduler):
    board = seen(sighting(1, value=300), sighting(2, value=100))

    printed = render(scheduler.order(board)).splitlines()

    assert len(printed) == 2
    assert "1. " in printed[0] and "challenge-1" in printed[0]


# ADR-0017: the reserved exploration share, and solve velocity


def test_one_attempt_in_four_goes_to_a_challenge_nobody_has_solved(scheduler):
    """`+ w_solves × solves_norm` weights high-solve Challenges up, which is the opposite pressure —
    so without the share a zero-solve Challenge is systematically last and the unsolved set is
    starved by construction."""
    board = seen(sighting(1, solves=50, value=500), sighting(2, solves=0, value=10))

    reached = [worked(scheduler, board, seconds=1.0) for _ in range(4)]

    assert [one.challenge.challenge_id for one in reached] == [1, 1, 1, 2]
    assert [one.exploring for one in reached] == [False, False, False, True]


def test_an_exploration_turn_that_lands_on_orders_top_is_still_an_exploration_turn(scheduler):
    """The flag says which mechanism chose the pick, not whether the pick differed. A Run where the
    two coincided every time is exactly the measurement that would say the share is redundant, and
    inferring the flag from the rank would report that Run as having never explored."""
    board = seen(sighting(1, solves=0))

    reached = [worked(scheduler, board, seconds=1.0) for _ in range(4)]

    assert [one.exploring for one in reached] == [False, False, False, True]


def test_an_exploration_turn_with_nothing_to_explore_falls_through_to_orders_top(scheduler):
    """The share is not spent on nothing. A Board where everything has solves has already told us
    what the share was there to find out."""
    board = seen(sighting(1, solves=50, value=500), sighting(2, solves=7, value=10))

    reached = [worked(scheduler, board, seconds=1.0) for _ in range(4)]

    assert [one.challenge.challenge_id for one in reached] == [1, 1, 1, 1]
    assert not any(one.exploring for one in reached)


def test_solve_velocity_is_preferred_to_solve_count_once_two_samples_exist(scheduler):
    """At t=0 every Challenge reads zero solves; by t+2h *"no movement while its neighbours gained
    40"* is what separates *nobody can* from *nobody has yet*. The count keeps crediting a
    Challenge for the rush that happened before we arrived."""
    early = seen(sighting(1, solves=0), sighting(2, solves=40), at=OPENED)
    later = seen(sighting(1, solves=20), sighting(2, solves=40), at=OPENED + dt.timedelta(hours=2), cycle=2)

    on_count = ranking(scheduler, early)
    on_velocity = ranking(scheduler, later)

    assert on_count == [2, 1]
    assert on_velocity == [1, 2]


def test_a_board_where_nothing_moved_falls_back_to_the_count(scheduler):
    """A Board that is simply quiet has not told us anything, and reading every Challenge as
    stalled would put the term to sleep for the rest of the Run."""
    early = seen(sighting(1, solves=0), sighting(2, solves=40), at=OPENED)
    later = seen(sighting(1, solves=0), sighting(2, solves=40), at=OPENED + dt.timedelta(hours=2), cycle=2)
    scheduler.order(early)

    assert ranking(scheduler, later) == [2, 1]


def test_a_challenge_that_arrived_late_is_ranked_on_its_count_and_not_as_though_it_had_stalled(scheduler):
    """One sample is not a velocity. A Challenge released at 13:00 has had no chance to move, and
    reading it as stalled would bury every mid-Run arrival on a moving Board."""
    early = seen(sighting(1, solves=0), sighting(2, solves=10), at=OPENED)
    later = seen(
        sighting(1, solves=1),
        sighting(2, solves=10),
        sighting(3, solves=40),
        at=OPENED + dt.timedelta(hours=2),
        cycle=2,
    )
    scheduler.order(early)
    places = ranking(scheduler, later)

    assert places.index(3) < places.index(2), "its own count carries it, rather than a velocity of zero"


# The slots v3 tunes rather than reshapes


def test_concurrency_and_reasoning_effort_are_pinned_config_rather_than_shapes_of_the_code():
    """v1 varies neither — ADR-0014 gives it one brain per Run, switching on exhaustion alone."""
    assert (Dials().concurrency, Dials().reasoning_effort) == (1, "medium")
    assert Dials().version == 1


def _committed(ranked):
    return [one.challenge_id for one in ranked if one.committed]


def _records(recorder, kind):
    lines = (json.loads(line) for line in recorder.stream_path.read_text().splitlines())
    return [line for line in lines if line["record"] == kind]


def _tier_record(challenge_id):
    return {
        "challenge_id": challenge_id,
        "name": f"challenge-{challenge_id}",
        "tier": FLOOR,
        "provenance": "unjudged",
        "stated": "",
    }
