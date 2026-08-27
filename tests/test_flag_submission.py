"""Flag verification and submission — the moment the Claim/Observation split has to pay for itself.

What is on trial is that nothing the model *said* can ever spend a submission slot on its own
authority, and that the Solver knows which of four things it is holding before it spends one: a
candidate a replay brought back, one a command produced once, one only the model ever wrote down,
and one whose Instance died underneath it.

The second half is the branch nobody has run. Brunner reported `max_attempts` 0 on all 74
Challenges, so *unlimited* is the only submission policy a real Board has ever exercised here —
which is precisely why the reserve is tested rather than discovered at 14:00 on competition day.
"""

import datetime as dt
import json
from pathlib import Path

import pytest
from solver.board import ALREADY_SOLVED, ATTEMPT, CORRECT, INCORRECT, PAUSED, RATE_LIMITED, UNREAD, Board
from solver.carry import DERIVED
from solver.flag import (
    GUESSED,
    OBSERVED,
    REPRODUCED,
    UNVERIFIED,
    Candidate,
    Flags,
    Pace,
    ReplayLimits,
    Slots,
    confusables,
)
from solver.instance import EXPIRED_AT_SUBMIT_SAYS, INSTANCE_EXPIRED_AT_SUBMIT, Instances, Lease, Terms
from solver.record import NO_MODEL, Recorder
from solver.redaction import Redactor

NOON = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)

# A wrapper no Board we play uses, so a test that passes could not be passing on a hardcoded one.
WRAPPER = r"zephyr\{[^}]{1,64}\}"
FLAG = "zephyr{the_planted_one}"
OTHER = "zephyr{a_second_one}"

CHALLENGE = 42
ATTEMPT_ID = "attempt-1"
INSTANCE = "/api/v1/plugins/ctfd-chall-manager/instance"

# The replay is a fake in every test but the one that checks what it was handed, so nothing here
# ever opens this directory — naming it says so.
WORKDIR = Path("/the-fake-replay-never-opens-this")


def graded(status, message="", http=200):
    return http, json.dumps({"success": True, "data": {"status": status, "message": message}}).encode(), ""


class Wire:
    """A Board answering by path, keeping every Flag it was asked to grade in the order it got it."""

    def __init__(self, *verdicts, terminate=None):
        self.verdicts = list(verdicts) or [graded(INCORRECT)]
        self.terminate = terminate or (200, b'{"success": true, "data": {}}', "")
        self.submitted: list[str] = []
        self.deleted: list[str] = []

    def transport(self, request):
        path = request.full_url.split("board.example", 1)[1]
        if path == ATTEMPT:
            self.submitted.append(json.loads(request.data)["submission"])
            return self.verdicts.pop(0) if len(self.verdicts) > 1 else self.verdicts[0]
        if path.split("?")[0] == INSTANCE and request.get_method() == "DELETE":
            self.deleted.append(path)
            return self.terminate
        raise AssertionError(f"nothing was set up for {request.get_method()} {path}")


class Runner:
    """A replay, standing where a fork would be: it answers with bytes and remembers what it ran."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.ran: list[str] = []

    def __call__(self, command, workdir, limits):
        self.ran.append(command)
        self.workdir, self.limits = workdir, limits
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))


def flags_of(recorder, wire, *, runner=None, now=NOON, pace=None, instances=None, sleep=None):
    board = Board("https://board.example", "not-a-real-token", wire.transport)
    return Flags(
        board,
        recorder,
        flag_pattern=WRAPPER,
        instances=instances,
        pace=pace,
        runner=runner or Runner((0, FLAG.encode())),
        now=lambda: now,
        sleep=sleep or (lambda _seconds: None),
    )


def spend(flags, candidates, *, slots=Slots(max_attempts=0), lease=None, last_call=False, workdir=WORKDIR):
    """One pass over the candidates. Everything a test is not making a point about is defaulted, so
    what a test does say is the thing it is about."""
    return flags.submit(
        candidates,
        attempt_id=ATTEMPT_ID,
        challenge_id=CHALLENGE,
        slots=slots,
        workdir=workdir,
        lease=lease,
        last_call=last_call,
    )


def observe(recorder, command, output, *, attempt_id=ATTEMPT_ID, tool="shell"):
    """One Step of an Attempt, exactly as the adapter would have written it."""
    step = recorder.step_begin(
        attempt_id=attempt_id,
        step_index=1,
        command_raw=command,
        command_normalised=command,
        tool=tool,
    )
    step.end(exit_code=0, output=output, usage=NO_MODEL)


def records(recorder) -> list[dict]:
    return [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]


def submitted(recorder) -> list[str]:
    ends = (record for record in records(recorder) if record["record"] == "step-end")
    return [record["command_raw"] for record in ends if record["tool"] == "flag-submit"]


def test_a_flag_in_a_commands_output_is_a_candidate_and_the_command_is_kept_with_it(recorder):
    observe(recorder, "cat note.txt", f"the note says {FLAG}\n".encode())

    found = flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID)

    assert [(candidate.text, candidate.strength) for candidate in found] == [(FLAG, OBSERVED)]
    assert found[0].command == "cat note.txt"


def test_a_flag_the_model_only_stated_is_never_authorised_by_its_own_prose(recorder):
    """The whole point. A Claim goes to a channel no check greps, so the only way this string can
    reach a candidate at all is by being nominated — and nominated is not authorised."""
    recorder.claim(attempt_id=ATTEMPT_ID, text=f"I have solved it, the flag is {FLAG}".encode())

    found = flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID, said=[f"the flag is {FLAG}"])

    assert [(candidate.text, candidate.strength) for candidate in found] == [(FLAG, UNVERIFIED)]
    assert found[0].command == ""


def test_a_claim_alone_puts_nothing_in_the_sweep(recorder):
    """Even the nomination is the orchestrator's to make: what is written to `claims/` is not read
    by the sweep at all, so a model that writes a Flag into its reasoning has produced nothing."""
    recorder.claim(attempt_id=ATTEMPT_ID, text=f"the flag is {FLAG}".encode())

    assert flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID) == ()


def test_a_stated_flag_a_command_also_produced_comes_back_observed(recorder):
    observe(recorder, "cat note.txt", FLAG.encode())

    found = flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID, said=[f"the flag is {FLAG}"])

    assert [(candidate.text, candidate.strength) for candidate in found] == [(FLAG, OBSERVED)]


def test_the_derived_attempt_line_is_never_swept(recorder):
    """`carry.DERIVED` marks the one line per Attempt carrying a model-authored field. A model that
    wrote a plausible Flag into its own approach label must not have it swept back in."""
    observe(recorder, "cat carried.txt", f'{DERIVED} Attempt 2 · approach: "{FLAG}" · 3 steps\n'.encode())

    assert flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID) == ()


def test_a_model_that_quotes_its_carried_line_still_nominates_the_flag_it_states(recorder):
    """The derived rule is a **body** rule, and applying it to the model's prose only costs.

    `said` arrives as one whole message per element (`solver/run.py`), so a `[derived]` line quoted
    anywhere in a message would take every Flag in that message down with it — and the message most
    likely to quote one is a model summarising what it tried in the same breath as what it found.
    Nothing is protected by refusing it: a candidate reaching the sweep through prose alone is
    `unverified` by construction, which is exactly what the rule wants
    ([#107](https://github.com/jerome-queck/incypher-ctf/issues/107)).
    """
    said = [
        f'The carried line reads {DERIVED} Attempt 1 · approach: "LSB stego on the PNG" · 18 steps.\n'
        f"That approach worked once I fixed the bit order; the flag is {FLAG}."
    ]

    found = flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID, said=said)

    assert [(candidate.text, candidate.strength) for candidate in found] == [(FLAG, UNVERIFIED)]


def test_this_modules_own_report_never_authorises_a_candidate(recorder):
    """The sweep writes what it found into an Observation like everything else, so a second sweep
    reads its own last report. A candidate must not survive on the strength of being mentioned."""
    observe(recorder, "cat note.txt", FLAG.encode())
    flags = flags_of(recorder, Wire())

    flags.candidates(attempt_id=ATTEMPT_ID)
    again = flags.candidates(attempt_id=ATTEMPT_ID)

    assert [(candidate.text, candidate.command) for candidate in again] == [(FLAG, "cat note.txt")]


def test_another_attempts_observations_are_not_this_ones(recorder):
    observe(recorder, "cat note.txt", FLAG.encode(), attempt_id="attempt-9")

    assert flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID) == ()


def test_the_wrapper_comes_from_the_board_profile_rather_than_from_this_code(recorder):
    observe(recorder, "cat note.txt", b"flag{a_different_boards_wrapper}\nzephyr{ours}\n")

    found = flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID)

    assert [candidate.text for candidate in found] == ["zephyr{ours}"]


def test_a_wrapper_that_does_not_compile_is_an_observation_rather_than_a_raise(recorder):
    board = Board("https://board.example", "", Wire().transport)
    flags = Flags(board, recorder, flag_pattern="zephyr{[", now=lambda: NOON)

    assert flags.candidates(attempt_id=ATTEMPT_ID) == ()
    assert "did not compile" in recorder.run_dir.joinpath("observations").glob("*.out").__next__().read_text()


def test_a_candidate_is_reproduced_by_replaying_its_exact_command_once(recorder):
    observe(recorder, "cat note.txt", FLAG.encode())
    runner = Runner((0, f"the note says {FLAG}\n".encode()))
    flags = flags_of(recorder, Wire(graded(INCORRECT)), runner=runner)

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID))

    assert runner.ran == ["cat note.txt"]
    assert [answer.candidate.strength for answer in outcome.graded] == [REPRODUCED]


def test_a_replay_that_answers_differently_leaves_the_candidate_observed(recorder):
    observe(recorder, "curl http://target/flag", FLAG.encode())
    flags = flags_of(recorder, Wire(), runner=Runner((7, b"connection refused")))

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID))

    assert [answer.candidate.strength for answer in outcome.graded] == [OBSERVED]


def test_the_solvers_own_probe_is_never_replayed_as_a_shell_command(recorder):
    """A recon probe's line is the Solver talking, not a command anyone could run. It is where a
    Flag most often turns up, and the candidate is still submitted — as `observed`."""
    observe(recorder, "[recon] flag-scan for zephyr — note.txt", f"1 match(es): {FLAG}".encode(), tool="flag-scan")
    runner = Runner((0, b""))
    flags = flags_of(recorder, Wire(), runner=runner)

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID))

    assert runner.ran == []
    assert [answer.candidate.strength for answer in outcome.graded] == [OBSERVED]


def test_where_attempts_are_unlimited_a_reproduced_candidate_is_submitted_at_once(recorder):
    """Branch A. Speed wins, and holding a Flag back is the no-sandbagging rule Brunner bans."""
    observe(recorder, "cat note.txt", FLAG.encode())
    wire = Wire(graded(CORRECT, "That's correct!"))
    flags = flags_of(recorder, wire)

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID))

    assert wire.submitted == [FLAG]
    assert (outcome.solved, outcome.flag) == (True, FLAG)


def test_where_attempts_are_unlimited_an_unverified_candidate_is_still_submitted(recorder):
    wire = Wire(graded(INCORRECT))
    flags = flags_of(recorder, wire)

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID, said=[FLAG]))

    assert wire.submitted == [FLAG]
    assert [answer.candidate.strength for answer in outcome.graded] == [UNVERIFIED]


def test_where_attempts_are_limited_the_last_one_is_reserved_for_a_reproduced_candidate(recorder):
    """Branch B, the untested one. Two attempts, one spent already, and a candidate no replay
    confirmed: it may not take the slot that is the Challenge's last chance."""
    observe(recorder, "cat note.txt", FLAG.encode())
    wire = Wire()
    flags = flags_of(recorder, wire, runner=Runner((1, b"no such file")))

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID), slots=Slots(max_attempts=2, spent=1))

    assert wire.submitted == []
    assert [candidate.strength for candidate in outcome.held] == [OBSERVED]
    assert "the last attempt is reserved" in _observation_for(recorder, "flag-submit")


def test_the_reserved_last_attempt_is_spent_by_a_reproduced_candidate(recorder):
    observe(recorder, "cat note.txt", FLAG.encode())
    wire = Wire(graded(CORRECT))
    flags = flags_of(recorder, wire)

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID), slots=Slots(max_attempts=2, spent=1))

    assert (wire.submitted, outcome.solved) == ([FLAG], True)


def test_an_unknown_maximum_is_treated_as_limited(recorder):
    """Every attempt could be the last, so an unreproduced candidate never spends one. A Board that
    does not say how many attempts we have is not a Board that said we have many."""
    wire = Wire()
    flags = flags_of(recorder, wire)

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID, said=[FLAG]), slots=Slots(max_attempts=None))

    assert wire.submitted == []
    assert [candidate.strength for candidate in outcome.held] == [UNVERIFIED]
    assert "treated as limited" in _observation_for(recorder, "flag-submit")


def test_the_runs_reserved_tail_releases_the_reserve(recorder):
    """There is no later Attempt for the last attempt to be reserved for, so a candidate carried out
    of the Run unsubmitted is a candidate nobody was saving it for."""
    wire = Wire()
    flags = flags_of(recorder, wire)

    spend(flags, flags.candidates(attempt_id=ATTEMPT_ID, said=[FLAG]), slots=Slots(max_attempts=None), last_call=True)

    assert wire.submitted == [FLAG]


def test_a_challenge_whose_attempts_are_all_spent_submits_nothing(recorder):
    observe(recorder, "cat note.txt", FLAG.encode())
    wire = Wire()
    flags = flags_of(recorder, wire)

    outcome = spend(
        flags,
        flags.candidates(attempt_id=ATTEMPT_ID),
        slots=Slots(max_attempts=3, spent=3),
        last_call=True,
    )

    assert (wire.submitted, outcome.held[0].text) == ([], FLAG)


def test_a_homoglyph_never_spends_a_slot(recorder):
    """`zеphyr` with a Cyrillic е is the Flag to a reader and a wrong answer to the Board."""
    homoglyph = "zephyr{thе_planted_one}"
    observe(recorder, "cat note.txt", homoglyph.encode())
    wire = Wire()
    flags = flags_of(recorder, wire, runner=Runner((0, homoglyph.encode())))

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID))

    assert wire.submitted == []
    assert [candidate.text for candidate in outcome.held] == [homoglyph]
    assert "confusable-character guard" in _observation_for(recorder, "flag-submit")


def test_the_guard_leaves_a_flag_a_board_is_entitled_to_ship_alone():
    """It looks for a character *substituted* for an ASCII one, which an accent is not — refusing
    `café` for a whole Run would be the guard costing a solve rather than saving one."""
    assert confusables("zephyr{a-plain_one.2026}") == ()
    assert confusables("zephyr{café_au_lait}") == ()
    assert confusables("zephyr{niño}") == ()
    assert confusables("zephyr{🚩}") == ()


def test_the_guard_names_every_way_an_ascii_character_gets_impersonated():
    assert confusables("zephyr{thе_one}") != ()  # a Cyrillic е — another script's letter
    assert confusables("zephyr{ｈello}") != ()  # a fullwidth ｈ — a compatibility form of one
    assert confusables("zephyr{th​e_one}") != ()  # a zero-width space


def test_the_run_reserved_tail_submits_the_homoglyph_it_held(recorder):
    """The guard exists so a homoglyph does not spend a slot. At last call the slot has no later
    use, so holding it back stops being caution and becomes a Flag we found and never sent."""
    homoglyph = "zephyr{thе_planted_one}"
    observe(recorder, "cat note.txt", homoglyph.encode())
    wire = Wire()
    flags = flags_of(recorder, wire, runner=Runner((0, homoglyph.encode())))

    spend(flags, flags.candidates(attempt_id=ATTEMPT_ID), last_call=True)

    assert wire.submitted == [homoglyph]


def test_a_paused_board_is_neither_a_solve_nor_a_wrong_flag(recorder):
    """CTFd pauses events mid-run and answers every submission `paused` at HTTP 403. Reading that
    as a graded Flag would record a Challenge as failed on a verdict about the Board."""
    flags = flags_of(recorder, Wire(graded(PAUSED, "CTF is paused", http=403)))

    outcome = spend(flags, [Candidate(FLAG, UNVERIFIED)])

    assert (outcome.solved, outcome.flag) == (False, "")
    assert outcome.graded[0].verdict.outcome == PAUSED


def test_the_verdict_is_read_from_the_body_and_never_from_the_status(recorder):
    """A wrong Flag answers HTTP 200 carrying `incorrect`. A Solver reading the status records a
    solve for every Flag it ever submits."""
    observe(recorder, "cat note.txt", FLAG.encode())
    flags = flags_of(recorder, Wire(graded(INCORRECT, "Incorrect")))

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID))

    assert outcome.solved is False
    assert outcome.graded[0].verdict.http_status == 200


def test_a_verdict_that_arrived_under_a_non_two_hundred_is_still_the_bodys(recorder):
    """CTFd answers its own rate limiter with a 429 and a paused Board with a 403, and puts the
    verdict in the body of both."""
    flags = flags_of(recorder, Wire(graded(RATE_LIMITED, "You're submitting flags too fast", http=429)))

    outcome = spend(flags, [Candidate(FLAG, UNVERIFIED)])

    assert outcome.graded[0].verdict.outcome == RATE_LIMITED
    assert outcome.solved is False


def test_a_board_that_graded_nothing_is_never_recorded_as_having_graded_it_wrong(recorder):
    wire = Wire((502, b"<html>bad gateway</html>", ""))
    flags = flags_of(recorder, wire)

    outcome = spend(flags, [Candidate(FLAG, UNVERIFIED)])

    assert outcome.graded[0].verdict.outcome == UNREAD


def test_already_solved_ends_the_challenge_and_says_nothing_about_the_flag(recorder):
    """Read live on Brunner, 25 Aug 2026: a deliberately wrong Flag against a Challenge the team had
    solved answered `already_solved` with *"Incorrect but you already solved this"*. There is
    nothing left to win, and the string that was sent is not the Flag that won it."""
    flags = flags_of(recorder, Wire(graded(ALREADY_SOLVED, "Incorrect but you already solved this")))

    outcome = spend(flags, [Candidate(FLAG, UNVERIFIED)])

    assert (outcome.solved, outcome.flag) == (True, "")
    assert outcome.graded[0].verdict.correct is False


def test_a_late_flag_is_named_rather_than_read_as_a_wrong_one(recorder):
    """It grades `incorrect` and spends a slot for nothing, and the only thing that tells it apart
    from a wrong Flag is the message."""
    flags = flags_of(recorder, Wire(graded(INCORRECT, f"Error: {EXPIRED_AT_SUBMIT_SAYS}")))

    outcome = spend(flags, [Candidate(FLAG, UNVERIFIED)])

    assert outcome.graded[0].shape == INSTANCE_EXPIRED_AT_SUBMIT
    assert INSTANCE_EXPIRED_AT_SUBMIT in _observation_for(recorder, "flag-submit")


def test_an_expiring_instance_degrades_a_reproduced_candidate_by_our_own_clock(recorder):
    """The string is what it was; the Instance that minted it is gone, so it is a guess again — and
    a guess may not take the reserved last attempt."""
    observe(recorder, "cat /flag", FLAG.encode())
    wire = Wire()
    flags = flags_of(recorder, wire, now=NOON)
    lease = Lease(CHALLENGE, "1.2.3.4:31000", NOON - dt.timedelta(seconds=1), Terms(CHALLENGE, "dynamic_iac"))

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID), slots=Slots(max_attempts=2, spent=1), lease=lease)

    assert [candidate.strength for candidate in outcome.held] == [GUESSED]
    assert wire.submitted == []


def test_an_instance_still_inside_its_deadline_degrades_nothing(recorder):
    observe(recorder, "cat /flag", FLAG.encode())
    wire = Wire(graded(INCORRECT))
    flags = flags_of(recorder, wire, now=NOON)
    lease = Lease(CHALLENGE, "1.2.3.4:31000", NOON + dt.timedelta(minutes=5), Terms(CHALLENGE, "dynamic_iac"))

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID), slots=Slots(max_attempts=2, spent=1), lease=lease)

    assert [answer.candidate.strength for answer in outcome.graded] == [REPRODUCED]


def test_a_correct_flag_terminates_the_instance_it_was_found_on(recorder):
    observe(recorder, "cat /flag", FLAG.encode())
    wire = Wire(graded(CORRECT), terminate=(404, b'{"success": false, "data": {}}', ""))
    board = Board("https://board.example", "not-a-real-token", wire.transport)
    instances = Instances(board, recorder, now=lambda: NOON)
    flags = flags_of(recorder, wire, instances=instances)
    lease = Lease(CHALLENGE, "1.2.3.4:31000", NOON + dt.timedelta(minutes=5), Terms(CHALLENGE, "dynamic_iac"))

    spend(flags, flags.candidates(attempt_id=ATTEMPT_ID), lease=lease)

    assert wire.deleted, "a solve that leaves its Instance held leaks capacity nothing ever evicts"
    assert "instance-destroyed-on-flag" in _observation_for(recorder, "terminate")


def test_a_challenge_with_no_instance_terminates_nothing(recorder):
    wire = Wire(graded(CORRECT))
    flags = flags_of(recorder, wire)

    spend(flags, [Candidate(FLAG, UNVERIFIED)])

    assert wire.deleted == []


def test_submissions_are_paced_under_the_boards_incorrect_per_minute_limit(recorder):
    """The limit is Board-wide and unreadable to a non-admin, so CTFd's default is assumed. One
    Challenge burning it is every other Challenge's slots spent."""
    waits = []
    pace = Pace(per_minute=2, wrong=[NOON - dt.timedelta(seconds=30), NOON - dt.timedelta(seconds=10)])
    flags = flags_of(recorder, Wire(graded(INCORRECT)), pace=pace, sleep=waits.append)

    spend(flags, [Candidate(FLAG, UNVERIFIED)])

    assert waits == [30.0]


def test_only_a_flag_the_board_graded_wrong_counts_against_the_limiter(recorder):
    """It is what CTFd itself counts — a Run that is solving things is never paced by this."""
    pace = Pace(per_minute=1)
    flags = flags_of(recorder, Wire(graded(CORRECT)), pace=pace)

    spend(flags, [Candidate(FLAG, UNVERIFIED)])

    assert pace.wrong == []


def test_a_planted_flag_goes_from_a_real_observation_to_a_graded_submission(recorder):
    """End to end over the whole path: a Flag in a real Observation, swept out of the record,
    replayed, guarded, submitted, and graded — with every step of it in the stream afterwards."""
    observe(recorder, "unzip -p archive.zip note.txt", f"nothing here\n{FLAG}\nnor here\n".encode())
    wire = Wire(graded(CORRECT, "That's correct!"))
    flags = flags_of(recorder, wire, runner=Runner((0, FLAG.encode())))

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID))

    assert (outcome.solved, outcome.flag) == (True, FLAG)
    assert wire.submitted == [FLAG]
    assert [record["tool"] for record in records(recorder) if record["record"] == "step-end"] == [
        "shell",
        "flag-sweep",
        "flag-replay",
        "flag-submit",
    ]
    assert submitted(recorder) == [f"[flag] submit {FLAG}"]


def test_a_second_candidate_is_left_alone_once_one_of_them_graded(recorder):
    observe(recorder, "cat note.txt", f"{FLAG}\n{OTHER}\n".encode())
    wire = Wire(graded(CORRECT))
    flags = flags_of(recorder, wire)

    outcome = spend(flags, flags.candidates(attempt_id=ATTEMPT_ID))

    assert wire.submitted == [FLAG]
    assert outcome.solved is True


def test_a_flag_straddling_a_read_boundary_is_still_matched(recorder, monkeypatch):
    """Observation bodies are read in blocks because `aggregated_output` is whatever a command
    wrote, and a Flag on a block boundary would otherwise be the one nobody finds."""
    monkeypatch.setattr("solver.flag.SCAN_BLOCK_BYTES", 16)
    observe(recorder, "strings big.bin", b"a" * 30 + FLAG.encode() + b"b" * 30)

    found = flags_of(recorder, Wire()).candidates(attempt_id=ATTEMPT_ID)

    assert [candidate.text for candidate in found] == [FLAG]


def test_the_replay_runs_in_the_working_directory_under_caps_that_are_parameters(recorder):
    """A command is only *the exact command* if it runs where it ran, and the caps are parameters
    because none of the numbers in v1 is calibrated."""
    observe(recorder, "cat note.txt", FLAG.encode())
    runner = Runner((0, FLAG.encode()))
    board = Board("https://board.example", "", Wire().transport)
    limits = ReplayLimits(seconds=1.5, output_bytes=4096)
    flags = Flags(board, recorder, flag_pattern=WRAPPER, runner=runner, limits=limits, now=lambda: NOON)

    spend(flags, flags.candidates(attempt_id=ATTEMPT_ID), workdir=WORKDIR)

    assert (runner.limits, runner.workdir) == (limits, WORKDIR)


def _observation_for(recorder, tool: str) -> str:
    """What the Step this module wrote for `tool` actually observed, read back off disk."""
    ends = (record for record in records(recorder) if record["record"] == "step-end")
    refs = [record["observation_ref"] for record in ends if record.get("tool") == tool]
    return "\n".join((recorder.run_dir / ref).read_text() for ref in refs)
