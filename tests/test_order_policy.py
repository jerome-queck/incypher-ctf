"""Public contract for truthful Order over coherent Intake authority."""

import datetime as dt
from fractions import Fraction

from solver.intake_qualification import (
    ChallengeSnapshot,
    IntakeSnapshot,
    PresentValue,
    PriorFence,
    TypedId,
    ValueFact,
)
from solver.order_policy import (
    ActiveGrant,
    AdmissionFact,
    CrowdSource,
    OrderAuthority,
    OrderInput,
    OrderRunFacts,
    PolicyDials,
    PriorCrowd,
    decide_order,
    _percentiles,
)

UTC = dt.timezone.utc
START = dt.datetime(2026, 9, 22, 1, 0, tzinfo=UTC)


def challenge(
    challenge_id: int | str,
    *,
    value: int = 100,
    value_outcome: str = "answered",
    solves: int = 0,
    position: int = 0,
    difficulty: str = "",
    category: str = "web",
) -> ChallengeSnapshot:
    typed = TypedId.parse(challenge_id)
    return ChallengeSnapshot(
        typed,
        f"challenge-{challenge_id}",
        category,
        "standard",
        f"Difficulty: {difficulty}" if difficulty else "",
        ValueFact(value, "list", value_outcome, START),
        ValueFact(solves, "list", "answered", START),
        ValueFact(False, "list", "answered", START),
        PresentValue("value", position),
        0,
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        (),
        f"{int(value):064x}"[-64:],
    )


def snapshot(
    minute: int,
    challenges: tuple[ChallengeSnapshot, ...],
    *,
    scoreboard: tuple[dict[str, object], ...] = (),
) -> IntakeSnapshot:
    observed = START + dt.timedelta(minutes=minute)
    return IntakeSnapshot(
        f"snapshot-{minute}",
        "a" * 64,
        observed,
        challenges,
        "not-empty",
        scoreboard=tuple({**row, "source": "scoreboard", "observed_at": observed.isoformat()} for row in scoreboard),
    )


def authority(current: IntakeSnapshot, *history: IntakeSnapshot, synthetic: bool = False) -> OrderAuthority:
    return OrderAuthority(
        current,
        PriorFence("a" * 64, f"event-{current.snapshot_id}", "b" * 64, current.digest),
        (current, *history),
        100_000,
        CrowdSource(True, synthetic, "c" * 64),
    )


def facts(*, minute: int = 16, **changes) -> OrderRunFacts:
    values = {
        "boundary_id": f"boundary-{minute}",
        "boundary_at": START + dt.timedelta(minutes=minute),
        "final_submission_cutoff": START + dt.timedelta(hours=2),
        "window_seconds": 7200,
        "next_generation": 1,
    }
    values.update(changes)
    return OrderRunFacts(**values)


def order(current: IntakeSnapshot, *history: IntakeSnapshot, run: OrderRunFacts | None = None):
    return decide_order(OrderInput(authority(current, *history), run or facts(), PolicyDials()))


def board(values: tuple[int, ...], solves: tuple[int, ...]) -> tuple[ChallengeSnapshot, ...]:
    return tuple(
        challenge(index + 1, value=value, solves=solve) for index, (value, solve) in enumerate(zip(values, solves))
    )


def standings(scores: tuple[int, ...]) -> tuple[dict[str, object], ...]:
    return tuple({"rank": index + 1, "name": f"team-{index}", "score": score} for index, score in enumerate(scores))


def test_one_unsettled_hard_deferred_value_forces_unit_payoff_for_the_whole_order_population():
    current = snapshot(
        10,
        (
            challenge(1, value=900),
            challenge(2, value=1, value_outcome="unsettled"),
        ),
    )
    run = facts(admission=(AdmissionFact(TypedId.parse(2), False, "resource", "d" * 64),))

    decision = order(current, run=run)

    assert decision.order_value_basis == "unit-fallback"
    assert decision.fallback_reason == "challenge-value-unsettled:integer:2"
    assert {row.components.value for row in decision.rows} == {1}
    assert next(row for row in decision.rows if row.challenge_id.value == 2).deferred_reason == "resource"


def test_two_qualifying_transitions_make_crowd_qualified_and_recompute_reverse_quartile_base_tiers():
    first = snapshot(0, board((100, 100, 100, 100), (0, 0, 0, 0)), scoreboard=standings((0, 0)))
    second = snapshot(5, board((100, 100, 100, 100), (1, 2, 3, 0)), scoreboard=standings((6, 0)))
    current = snapshot(10, board((100, 100, 100, 100), (2, 4, 7, 0)), scoreboard=standings((13, 0)))

    decision = order(current, second, first)

    by_id = {row.challenge_id.value: row for row in decision.rows}
    assert {row.crowd.state for row in decision.rows} == {"qualified"}
    assert by_id[3].base_tier == 1
    assert by_id[4].base_tier == 4
    assert by_id[1].base_tier > by_id[3].base_tier


def test_scoreboard_freshness_is_independent_of_solve_discrimination_and_synthetic_is_immediate():
    first = snapshot(0, board((1, 1, 1), (0, 0, 0)), scoreboard=standings((0,)))
    second = snapshot(5, board((1, 1, 1), (1, 2, 3)), scoreboard=standings((0,)))
    current = snapshot(10, board((1, 1, 1), (2, 4, 6)), scoreboard=standings((0,)))

    stale = order(current, second, first)
    synthetic = decide_order(OrderInput(authority(current, second, first, synthetic=True), facts(), PolicyDials()))

    assert {row.crowd.state for row in stale.rows} == {"provisional"}
    assert {row.crowd.reason for row in stale.rows} == {"activity-unproved"}
    assert {row.crowd.state for row in synthetic.rows} == {"unavailable"}
    assert {row.crowd.reason for row in synthetic.rows} == {"synthetic-source"}


def test_exact_thresholds_typed_id_ties_and_shuffled_input_are_deterministic():
    first = snapshot(0, (challenge("2"), challenge(10), challenge(2)), scoreboard=standings((0,)))
    current = snapshot(
        5, (challenge(10, solves=1), challenge(2, solves=1), challenge("2", solves=1)), scoreboard=standings((5,))
    )
    shuffled = snapshot(5, tuple(reversed(current.challenges)), scoreboard=standings((5,)))

    one = order(current, first)
    two = order(shuffled, first)

    assert [(row.challenge_id.kind, row.challenge_id.value) for row in one.rows] == [
        ("integer", 2),
        ("integer", 10),
        ("string", "2"),
    ]
    assert [row.document() for row in one.rows] == [row.document() for row in two.rows]
    assert one.order_value_basis == two.order_value_basis
    assert one.grant == two.grant


def test_an_active_grant_is_frozen_while_unacquired_work_reorders():
    active = ActiveGrant(
        "attempt-1",
        "generation-000001",
        TypedId.parse(1),
        4,
        900,
        START + dt.timedelta(minutes=30),
        "e" * 64,
    )
    earlier = snapshot(0, (challenge(1, value=1000), challenge(2, value=10)))
    later = snapshot(10, (challenge(1, value=1), challenge(2, value=1000)))

    decision = order(later, earlier, run=facts(active_grants=(active,), next_generation=2))

    assert decision.active_grants == (active,)
    assert decision.rows[0].challenge_id == TypedId.parse(2)
    assert next(row for row in decision.rows if row.challenge_id == TypedId.parse(1)).final_tier != active.tier


def test_ordinary_grants_respect_l_min_and_sub_floor_time_is_an_explicit_final_interval():
    current = snapshot(0, (challenge(1, difficulty="hard"), challenge(2)))
    ordinary = order(current, run=facts(minute=1))
    final = order(
        current,
        run=facts(
            minute=110,
            final_submission_cutoff=START + dt.timedelta(minutes=114),
        ),
    )

    assert ordinary.interval == "ordinary"
    assert ordinary.grant is not None and ordinary.grant.budget_s >= PolicyDials().floor_seconds
    assert final.interval == "final-interval"
    assert final.final_interval_seconds == 240
    assert final.grant is None
    assert "park" not in str(final.document()).lower()


def test_board_solved_rows_leave_the_population_and_string_id_decisions_are_serializable():
    current = snapshot(
        0,
        (
            challenge(1),
            challenge("é"),
            challenge(2),
        ),
    )
    solved = current.challenges[0]
    solved = solved.__class__(**{**solved.__dict__, "solved": ValueFact(True, "list", "answered", START)})
    current = snapshot(0, (solved, *current.challenges[1:]))

    decision = order(current)

    assert [row.challenge_id.value for row in decision.rows] == [2, "é"]
    assert isinstance(decision.digest, str)


def test_every_fourth_grant_explores_best_unqualified_without_its_crowd_component():
    current = snapshot(0, (challenge(1, value=1), challenge(2, value=1000)))
    prior = (
        PriorCrowd(TypedId.parse(1), "provisional"),
        PriorCrowd(TypedId.parse(2), "qualified", velocity=__import__("fractions").Fraction(1), last_qualified_tier=1),
    )

    decision = order(current, run=facts(next_generation=4, prior_crowd=prior))

    assert decision.grant is not None
    assert decision.grant.challenge_id == TypedId.parse(1)
    assert decision.grant_exploring is True


def test_last_qualified_crowd_expires_after_thirty_minutes_without_new_proof():
    observed = START
    current = snapshot(31, (challenge(1),))
    prior = PriorCrowd(
        TypedId.parse(1),
        "qualified",
        consecutive_failures=1,
        velocity=__import__("fractions").Fraction(1),
        last_qualified_tier=1,
        qualified_at=observed,
    )

    decision = order(current, run=facts(minute=31, prior_crowd=(prior,)))

    assert decision.rows[0].crowd.state == "unavailable"
    assert decision.rows[0].crowd.reason == "last-qualified-expired"


def test_one_attempt_working_set_projects_over_admissible_rows_and_contains_its_grant():
    current = snapshot(0, (challenge(1, value=1000), challenge(2, value=1)))
    run = facts(
        minute=1,
        final_submission_cutoff=START + dt.timedelta(minutes=11),
        admission=(AdmissionFact(TypedId.parse(1), False, "unsafe", "d" * 64),),
    )

    decision = order(current, run=run)

    assert decision.rows[0].challenge_id == TypedId.parse(1)
    assert decision.working_set == (TypedId.parse(2),)
    assert decision.grant is not None and decision.grant.challenge_id in decision.working_set


def test_percentile_midranks_scale_to_the_supported_population_without_pairwise_scans():
    values = {("integer", index): Fraction(index % 100) for index in range(100_000)}

    observed = _percentiles(values)

    assert len(observed) == 100_000
    assert observed[("integer", 0)] < observed[("integer", 99)]
