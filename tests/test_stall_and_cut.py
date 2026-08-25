"""The stall call, held against the one rule it exists for: nothing the model says buys it time.

Every test below drives the `Watch` the way the orchestrator will — Observations in, a cause out —
and the only prose that ever reaches it goes through `said`, which can shorten a budget and can
never lengthen one.
"""

import datetime as dt

import pytest
from solver.record import (
    CAUSES,
    CRASHED,
    CUT_BUDGET,
    CUT_INSTANCE_EXPIRED,
    CUT_NOVELTY,
    CUT_REPETITION,
    CUT_SELF_REPORTED_IMPOSSIBLE,
    CUT_STEP_CLIFF,
)
from solver.stall import Breaker, Deadline, Thresholds, Watch, normalise, replayable

NOON = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)


def at(minutes: float) -> dt.datetime:
    return NOON + dt.timedelta(minutes=minutes)


def watching(**thresholds) -> Watch:
    return Watch(Deadline(budget=at(10)), thresholds=Thresholds(**thresholds))


def loop(watch: Watch, command: str, times: int, *, digest: str = "same") -> None:
    for _ in range(times):
        watch.observed(command, exit_code=0, digest=digest)


def test_the_vocabulary_has_no_no_flag_and_names_every_counter():
    """`no-flag` is the absence of a cause rather than one, and naming it would hide which counter
    fired — the only thing calibration needs to know."""
    assert "no-flag" not in CAUSES
    assert set(CAUSES) == {
        "flag",
        CUT_REPETITION,
        CUT_NOVELTY,
        CUT_STEP_CLIFF,
        CUT_BUDGET,
        CUT_INSTANCE_EXPIRED,
        CUT_SELF_REPORTED_IMPOSSIBLE,
        CRASHED,
    }


def test_repetition_is_counted_per_command_rather_than_as_one_tally():
    """The rule is *a* normalised command already seen — three different commands each run twice is
    three commands, not one looping."""
    watch = watching(repeats=3)

    for command in ("ls -la", "file note.txt", "cat note.txt"):
        loop(watch, command, 2, digest=command)

    assert watch.cause(NOON) == ""


def test_a_command_that_answers_the_same_way_twice_is_repetition():
    watch = watching()

    loop(watch, "/bin/zsh -lc 'ls -la'", 2)

    assert watch.cause(NOON) == CUT_REPETITION


def test_the_shell_wrapper_is_not_what_makes_two_commands_different():
    """The vendor wraps every command in a shell, and which shell it reached for is not a fact
    about what was tried."""
    watch = watching()

    watch.observed("/bin/zsh -lc 'cat note.txt'", exit_code=0, digest="same")
    watch.observed('/bin/bash -c "cat   note.txt"', exit_code=0, digest="same")

    assert watch.cause(NOON) == CUT_REPETITION


@pytest.mark.parametrize(
    ("command", "wanted"),
    [
        ("/bin/zsh -lc 'unzip -l a.zip'", "unzip -l a.zip"),
        ("  ls    -la  ;", "ls -la"),
        ("'echo hi'", "echo hi"),
    ],
)
def test_normalisation_is_the_repetition_rule_and_lives_with_the_counter(command, wanted):
    assert normalise(command) == wanted


def test_a_line_the_solver_wrote_about_itself_is_never_a_command():
    """Two rungs of the credential chain reporting the same failure is not the model looping."""
    watch = watching()

    loop(watch, "[codex] the CLI reported an error", 4)

    assert not replayable("[codex] the CLI reported an error")
    assert watch.cause(NOON) != CUT_REPETITION


def test_a_marked_line_still_counts_for_novelty_and_the_cliff():
    """It is an Observation like any other — what it is not is something anyone could replay."""
    watch = watching(novelty=3, cliff=99)

    loop(watch, "[recon] entropy over 8 windows", 4)

    assert watch.steps == 4
    assert watch.cause(NOON) == CUT_NOVELTY


def test_novelty_counts_steps_since_a_content_hash_nobody_had_seen():
    """The first `grep` answering with nothing is a hash nobody had seen; the three after it are
    three Steps that moved no further."""
    watch = watching(repeats=99, novelty=3)

    for index in range(4):
        watch.observed(f"grep -r pattern{index} .", exit_code=1, digest="empty")

    assert watch.cause(NOON) == CUT_NOVELTY


def test_a_novel_observation_resets_the_novelty_counter():
    watch = watching(repeats=99, novelty=3)

    for index in range(2):
        watch.observed(f"grep -r pattern{index} .", exit_code=1, digest="empty")
    watch.observed("strings artefact", exit_code=0, digest="something-new")
    watch.observed("file artefact", exit_code=0, digest="empty")

    assert watch.cause(NOON) == ""


def test_the_step_cliff_is_a_backstop_nothing_argues_with():
    watch = watching(repeats=99, novelty=99, cliff=5)

    for index in range(5):
        watch.observed(f"echo {index}", exit_code=0, digest=f"digest-{index}")

    assert watch.cause(NOON) == CUT_STEP_CLIFF


def test_recon_steps_are_the_attempts_first_steps():
    """Recon *is* the opening of an Attempt, so its probes are counted against the same cliff."""
    watch = Watch(Deadline(budget=at(10)), thresholds=Thresholds(cliff=5), steps=4)

    watch.observed("echo one", exit_code=0, digest="one")

    assert watch.cause(NOON) == CUT_STEP_CLIFF


def test_a_repeat_that_answers_differently_is_a_checkpoint_rather_than_repetition():
    """ADR-0005's own example: a route that was 403 and is now 200. The command that found the
    transition is its own replay, which is what makes the Checkpoint re-verifiable."""
    watch = watching()

    watch.observed("curl -s http://target/admin", exit_code=22, digest="forbidden")
    found = watch.observed("curl -s http://target/admin", exit_code=0, digest="the-panel")

    assert found is not None
    assert found.replay == "curl -s http://target/admin"
    assert watch.cause(NOON) == ""


def test_a_first_success_buys_nothing():
    """A transition needs a before as well as an after; treating every command that worked as
    progress would max the extension cap out inside a minute."""
    watch = watching()

    assert watch.observed("ls -la", exit_code=0, digest="a-listing") is None
    assert watch.deadline.granted == 0


def test_a_checkpoint_buys_the_kill_deadline_and_never_a_step():
    watch = watching(extension_seconds=60.0)

    watch.observed("curl -s http://target/admin", exit_code=22, digest="forbidden")
    watch.observed("curl -s http://target/admin", exit_code=0, digest="the-panel")

    assert watch.deadline.at == at(11)
    assert watch.deadline.granted == 1


def test_extensions_are_capped_at_a_small_k():
    """An approach that keeps yielding cheap Checkpoints still ends."""
    watch = watching(extensions=2, extension_seconds=60.0)

    for index in range(6):
        watch.observed("curl -s http://target/admin", exit_code=index, digest=f"body-{index}")

    assert watch.deadline.granted == 2
    assert watch.deadline.at == at(12)


def test_a_checkpoint_clears_the_counters_and_the_commands_already_tried():
    """A command that failed before a Checkpoint may be exactly right after one."""
    watch = watching(repeats=99)

    watch.observed("unzip -l a.zip", exit_code=9, digest="not-an-archive")
    watch.observed("ls -la", exit_code=0, digest="a-listing")
    watch.observed("unzip -l a.zip", exit_code=0, digest="a-listing-of-members")

    assert watch.tried == []
    assert watch.cause(NOON) == ""


def test_the_budget_running_out_is_its_own_cause():
    watch = watching()

    assert watch.cause(at(9)) == ""
    assert watch.cause(at(10)) == CUT_BUDGET


def test_an_instance_expiring_first_is_the_more_specific_fact():
    """ "The Instance expired" and "the Attempt ran out of budget" want different responses."""
    watch = Watch(Deadline(budget=at(10), instance=at(4)))

    assert watch.cause(at(5)) == CUT_INSTANCE_EXPIRED


def test_an_extension_can_never_outlive_the_instance_that_bounds_it():
    """The Instance's expiry is the Board's fact and not ours to move."""
    watch = Watch(Deadline(budget=at(10), instance=at(4)), thresholds=Thresholds(extension_seconds=600.0))

    watch.observed("curl -s http://target/", exit_code=22, digest="refused")
    watch.observed("curl -s http://target/", exit_code=0, digest="a-page")

    assert watch.deadline.at == at(4)


def test_a_counter_is_read_before_the_clock():
    """A counter that tripped ended the Attempt when it tripped; the deadline is only ever the
    cause when nothing else was."""
    watch = watching()

    loop(watch, "ls -la", 2)

    assert watch.cause(at(30)) == CUT_REPETITION


def test_a_volunteered_impossible_shortens_the_budget():
    watch = watching()

    assert watch.said("I have exhausted every angle; this is impossible without the key.", now=at(2))
    assert watch.deadline.at == at(2)
    assert watch.cause(at(2)) == CUT_SELF_REPORTED_IMPOSSIBLE


def test_nothing_the_model_says_lengthens_a_budget():
    """The exception runs in one direction only, and a Checkpoint after it hands nothing back."""
    watch = watching(extension_seconds=600.0)

    watch.said("this is impossible", now=at(2))
    watch.observed("curl -s http://target/", exit_code=22, digest="refused")
    watch.observed("curl -s http://target/", exit_code=0, digest="a-page")

    assert watch.deadline.at == at(2)
    assert watch.deadline.granted == 0


def test_ordinary_confidence_is_not_a_self_report():
    """A loose matcher would turn "impossible to read without the password" into a cut Attempt, and
    ADR-0005 expects this cause never to fire."""
    watch = watching()

    assert not watch.said("It is impossible to read the archive without the password, so I will crack it.", now=NOON)
    assert watch.cause(NOON) == ""


def test_the_watch_cannot_speak_to_the_model():
    """ADR-0005's silent reset, made structural: there is no method here that reaches a prompt or a
    running child, so no mid-Attempt cut can tell a model it appears stuck."""
    surface = {name for name in dir(watching()) if not name.startswith("_")}

    assert surface == {"observed", "said", "cause", "deadline", "thresholds", "steps", "checkpoints", "tried", "last"}


def test_three_barren_attempts_on_one_challenge_are_recorded_crashed():
    """A Cut would say the Challenge stopped this Attempt, and a Solver that spent no Steps did not
    learn anything about the Challenge at all."""
    breaker = Breaker(limit=3)

    assert breaker.closed("web-1", steps=0) == ""
    assert breaker.closed("web-1", steps=0) == ""
    assert breaker.closed("web-1", steps=0) == CRASHED


def test_one_attempt_that_spent_a_step_clears_the_breaker():
    breaker = Breaker(limit=3)

    breaker.closed("web-1", steps=0)
    breaker.closed("web-1", steps=0)
    breaker.closed("web-1", steps=7)

    assert breaker.closed("web-1", steps=0) == ""


def test_barren_across_different_challenges_backs_off_and_the_backoff_is_capped():
    """A broken Solver must fail slowly enough that the quota window it is burning stays
    recoverable."""
    breaker = Breaker(limit=99, base_seconds=30.0, cap_seconds=120.0)

    breaker.closed("web-1", steps=0)
    assert breaker.backoff() == 0.0
    breaker.closed("crypto-2", steps=0)
    assert breaker.backoff() == 30.0
    breaker.closed("rev-3", steps=0)
    assert breaker.backoff() == 60.0
    breaker.closed("misc-4", steps=0)
    breaker.closed("pwn-5", steps=0)
    assert breaker.backoff() == 120.0


def test_the_streak_grows_the_backoff_even_when_the_working_set_is_two_challenges():
    """The crossing is what starts it and the length is what grows it. A Solver cycling two
    Challenges is exactly as broken on the tenth barren Attempt as on the twentieth, and one that
    stayed at the first step forever would burn the quota window at full speed."""
    breaker = Breaker(limit=99, base_seconds=30.0, cap_seconds=900.0)

    for challenge in ("web-1", "crypto-2", "web-1", "crypto-2", "web-1"):
        breaker.closed(challenge, steps=0)

    assert breaker.backoff() == 240.0


def test_one_challenge_failing_alone_is_not_the_solver_backing_off():
    """That shape is the circuit breaker's, and slowing the whole Run down for it would spend the
    clock on a Challenge nothing is wrong with."""
    breaker = Breaker(limit=99)

    breaker.closed("web-1", steps=0)
    breaker.closed("web-1", steps=0)

    assert breaker.backoff() == 0.0
