"""One Challenge's submission budget, spent a turn at a time.

The count is the Board's and is held server-side, so the Solver's copy of it is only ever as fresh
as the last Intake — and an Attempt takes many turns, each one sweeping and submitting, with no
Intake anywhere between them. What is on trial is that the reserve survives that gap: a gate handed
the count as it stood when the Challenge was picked re-derives *the whole budget remains* on every
turn of the same Attempt, and walks a limited Board's slots down with candidates no command ever
produced.

Four rules make up the number the gate is handed, and each is proved here on its own: which answers
cost a slot and which the Board refused to grade at all, what a fresher count from the Board does to
the Run's own tally, where the next slot is counted from once it has, and what a detail GET that
failed does to any of it.

Brunner reported `max_attempts` 0 on all 74 Challenges, so this is the branch no live Run has
exercised — which is exactly why it is proved here rather than at 14:00 on competition day.
"""

import json

from solver.board import Board
from solver.codex import Child, Credential
from solver.flag import Flags, Pace, ReplayLimits
from solver.instance import Instances
from solver.intake import Intake, Limits
from solver.profile import Rules, discovered
from solver.record import Recorder
from solver.redaction import Redactor
from solver.run import Run, Steps
from solver.schedule import Dials, Scheduler, Window
from test_run_loop import BOARD, CONTROL_REFUSED, WRAPPER, Canned, Clock, stream

RULES = Rules(event="offline", url=BOARD, flag_wrappers=(WRAPPER,), window_seconds=1200, prohibitions=())

# Ours rather than the defaults, so a test can reason about the arithmetic: a 1200 s window buys a
# 600 s Attempt and a 300 s one above a 300 s tail, and every turn inside them costs 120 s.
DIALS = Dials(knee_seconds=600.0, floor_seconds=300.0, tail_seconds=300.0)
TURN_SECONDS = 120.0


def guess(turn: int) -> str:
    """The string the model names on one turn. Distinct per turn, because a Run offers a candidate
    to the gate once (`Run._offered`) and a model repeating itself would prove nothing."""
    return f"brunner{{a-guess-{turn}}}"


def real(turn: int) -> str:
    """The string a command actually emits on one turn. An Observation authorises it and the replay
    confirms it, so it is `reproduced` — the one strength the gate will spend a last attempt on."""
    return f"brunner{{a-real-{turn}}}"


class Wire:
    """A CTFd that counts the way CTFd counts: `attempts` is the number of Flags it has graded
    wrong, and the per-Challenge detail GET is the only place the Solver can ever read it."""

    def __init__(self, *, max_attempts: int):
        self.listed = [{"id": 1, "name": "challenge-1", "type": "standard", "value": 100, "solves": 0, "position": 1}]
        self.max_attempts = max_attempts
        self.attempts = 0
        self.submitted: list[str] = []

    def transport(self, request):
        path = request.full_url[len(BOARD) :]
        if "field=" in path:
            return (*CONTROL_REFUSED, "")
        if path == "/api/v1/challenges/attempt":
            self.submitted.append(json.loads(request.data)["submission"])
            return self.answered()
        if path.startswith("/api/v1/challenges/"):
            return self.detailed()
        if path == "/api/v1/challenges":
            return self._answer(self.listed)
        if path.startswith("/api/v1/scoreboard/top/"):
            return self._answer({})
        return (404, b'{"success": false}', "")

    def answered(self):
        """How this Board grades one submission, and what it costs: `incorrect` is the verdict CTFd
        writes a Fail for, and a Fail is the unit `max_attempts` is counted in."""
        self.attempts += 1
        return self._answer({"status": "incorrect", "message": ""})

    def detailed(self):
        found = self.listed[0]
        return self._answer(
            {
                **found,
                "description": f"Find the flag in {found['name']}.",
                "max_attempts": self.max_attempts,
                "attempts": self.stated(),
            }
        )

    def stated(self) -> int:
        """What the detail GET says `attempts` is — every Fail against this Challenge, which on a
        team board is not only the ones this Run sent."""
        return self.attempts

    @staticmethod
    def _answer(data, status: int = 200):
        return (status, json.dumps({"success": True, "data": data}).encode(), "")


class Refusing(Wire):
    """A Board that refuses the first submission instead of grading it.

    CTFd answers its own limiter with `ratelimited` at HTTP 429 and the verdict is in the body, not
    the status. Nothing was graded, so `attempts` does not move — and a Solver that counted it would
    walk the budget down over a Flag the Board never looked at.
    """

    def __init__(self, *, max_attempts: int):
        super().__init__(max_attempts=max_attempts)
        self.refused = 0

    def answered(self):
        if self.refused:
            return super().answered()
        self.refused += 1
        return self._answer({"status": "ratelimited", "message": "You're submitting flags too fast"}, status=429)


class Unanswered(Wire):
    """A Board whose submission POST answers something no verdict can be read out of.

    A proxy's 502 in front of a CTFd that may well have graded the Flag and written the Fail: the
    submission is gone and the Board's own count is not going to tell us, because this Board's
    `attempts` never moves.
    """

    def answered(self):
        return (502, b"<html>bad gateway</html>", "")


class Teammate(Wire):
    """A Board whose count moves for a reason that is not this Run — a human on the team submitting
    a wrong Flag from the web UI against the Challenge the Solver is working."""

    def stated(self) -> int:
        return self.attempts + (1 if self.submitted else 0)


class Silent(Wire):
    """A Board whose per-Challenge detail GET has started failing.

    Intake carries the previous cycle's `attempts` forward rather than reading a zero into it, so
    the Board's own count stops moving where the Solver can see it and the Run's own tally is the
    only thing left holding the budget down.
    """

    def detailed(self):
        return (500, b'{"success": false}', "") if self.submitted else super().detailed()


class Nominating:
    """A model that names a new Flag-shaped string in its prose every turn and runs nothing that
    produces one.

    Every candidate it offers is therefore `unverified` — nominated by the model and authorised by
    nothing — which is the weakest strength the per-turn gate will still spend a slot on while
    attempts remain, and so the sharpest thing to hold the reserve against.
    """

    def __init__(self, clock: Clock, *, seconds: float = TURN_SECONDS) -> None:
        self.clock = clock
        self.seconds = seconds
        self.turns = 0

    def __call__(self, argv, workdir, environment, prompt: bytes) -> Child:
        self.turns += 1
        wrote = stream(
            commands=(self.commanded(),),
            says=[f"APPROACH: reading around\nthe flag might be {guess(self.turns)}"],
        )
        return Canned(wrote, self.clock, self.seconds)

    def commanded(self) -> tuple[str, str, int]:
        """The one command this turn runs, and it finds nothing — so the turn's only candidate is
        the string the model named in its prose."""
        return (f"cat notes-{self.turns}.txt", "no such file\n", 1)


class Working(Nominating):
    """A model that keeps nominating, and from one turn on also *works*: its command emits a fresh
    Flag-shaped string every turn, which the replay confirms.

    Reproduced candidates are what the reserve is held for, so this is the model that finds out
    whether it was still there — a Run that spent the last slot on a guess has nothing left for the
    one string its own work authorised.
    """

    def __init__(self, clock: Clock, *, working_from: int, seconds: float = TURN_SECONDS) -> None:
        super().__init__(clock, seconds=seconds)
        self.working_from = working_from

    def commanded(self) -> tuple[str, str, int]:
        if self.turns < self.working_from:
            return super().commanded()
        return (f"cat dump-{self.turns}.txt", f"a dump of bytes\n{real(self.turns)}\nand more\n", 0)


def replaying(model):
    """The replay standing in for the model's command being run a second time and emitting the same
    string. It answers with everything this model has emitted so far, which is not a shortcut: the
    replay looks for one candidate's exact string per call and asks only whether it came back."""
    return lambda _command, _workdir, _limits: (0, " ".join(real(turn) for turn in range(1, model.turns + 1)).encode())


def solver(tmp_path, wire, model, clock, *, lasting=1200.0, runner=None):
    """Everything `solver/__main__.py` composes, against a Board that states a budget."""
    board = Board(BOARD, "token", wire.transport)
    recorder = Recorder(tmp_path / "state", "budget", Redactor({}), now=clock)
    found = discovered(board, Board(BOARD, "", wire.transport), RULES)
    window = Window.opened(recorder.run_dir, lasting=lasting, now=clock())
    intake = Intake(board, recorder, limits=Limits(cycle_seconds=300.0), now=clock)
    intake.sync()
    steps = Steps()
    instances = Instances(board, recorder, step_numbers=steps.spend, now=clock)
    flags = Flags(
        board,
        recorder,
        flag_wrappers=(WRAPPER,),
        instances=instances,
        pace=Pace(per_minute=found.submissions_per_minute),
        limits=ReplayLimits(seconds=1.0),
        # A model that only nominates has nothing to replay, so the default runner is here to be one
        # thing a test never has to reason about; a model that works passes a replay of its own.
        runner=runner or (lambda _command, _workdir, _limits: (0, b"nothing")),
        step_numbers=steps.spend,
        now=clock,
        sleep=lambda _seconds: None,
    )
    run = Run(
        profile=found,
        recorder=recorder,
        intake=intake,
        scheduler=Scheduler(window, recorder, dials=DIALS, now=clock),
        flags=flags,
        instances=instances,
        steps=steps,
        chain=(Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "codex"),),
        work_root=tmp_path / "work",
        launch=model,
        idle_seconds=30.0,
        now=clock,
        sleep=lambda seconds: clock.tick(seconds),
    )
    return run, recorder


def test_a_turn_reads_the_budget_now_rather_than_as_it_stood_when_the_challenge_was_picked(tmp_path):
    """Two attempts stated, so the second one is the reserve — and a candidate the model only
    nominated may never take it while there is a later Attempt to hold it for.

    The Board's own count moves with the first submission and the Solver's copy of it does not,
    because an Intake stands between Attempts and never between turns. So the whole rule turns on
    the Run counting what it has spent since: without that, every turn is handed two attempts
    again, and a Run spends a Board's entire budget on strings nothing ever produced.
    """
    clock = Clock()
    wire = Wire(max_attempts=2)
    run, _recorder = solver(tmp_path, wire, Nominating(clock), clock)

    run.work()

    # The first slot goes on the first turn's nomination, every turn after it is held, and the
    # reserve is spent in the tail — where there is no later Attempt to hold it for.
    assert wire.submitted == [guess(1), guess(2)]


def test_a_submission_the_board_refused_to_grade_spends_no_slot(tmp_path):
    """`ratelimited` is the Board declining to look at a Flag, and CTFd writes no Fail for one — so
    the slot is still there and the next turn is entitled to it.

    Counting it would be the expensive kind of wrong: `max_attempts` is counted in Fails, and a Run
    that walked its own budget down over answers nobody graded would refuse a reproduced candidate
    the last slot the Board would have taken, with no `last_call` to release it.
    """
    clock = Clock()
    wire = Refusing(max_attempts=2)
    run, _recorder = solver(tmp_path, wire, Nominating(clock), clock)

    run.work()

    # The first turn's answer cost nothing, so the second turn spends the first real slot, the rest
    # are held against the reserve, and the tail spends it.
    assert wire.submitted == [guess(1), guess(2), guess(3)]


def test_a_submission_the_board_never_answered_for_is_counted_as_spent(tmp_path):
    """A POST that reached CTFd and whose reply we could not read is a Fail we have to assume was
    written: the Board is the one holding the count, and it is not going to tell us before the next
    Intake — or at all, where the failure is on the reply and not the request.

    This is the conservative side of a fence on purpose. The whole budget arithmetic is a *floor*
    under the Board's real count, and an answer we could not read is exactly the moment a floor is
    worth having: a flaky Board is what a venue at 14:00 looks like, and the alternative is spending
    the reserve on an unverified candidate over a slot that was already gone.
    """
    clock = Clock()
    wire = Unanswered(max_attempts=2)
    run, _recorder = solver(tmp_path, wire, Nominating(clock), clock)

    run.work()

    # Two submissions and no more, though this Board's stated count never moved off zero: the one
    # that says the budget is nearly spent is the Run's own.
    assert wire.submitted == [guess(1), guess(2)]


def test_a_fresher_count_from_the_board_wins_over_what_this_run_has_spent(tmp_path):
    """The count is the Board's, not ours: a teammate submitting from the web UI spends a slot this
    Run never sent and never sees until the next Intake reads it back.

    So the two numbers are reconciled by taking the larger rather than by believing whichever was
    read last. A Run that trusted its own tally here would hold a reserve the Board no longer has
    and spend it in the tail on a Challenge with nothing left.
    """
    clock = Clock()
    wire = Teammate(max_attempts=3)
    run, _recorder = solver(tmp_path, wire, Nominating(clock), clock)

    run.work()

    # Two of the three go to the Solver's first two turns; the third went to the teammate, so the
    # tail finds the budget spent and sends nothing.
    assert wire.submitted == [guess(1), guess(2)]
    assert wire.attempts == 2


def test_what_the_run_spends_after_a_fresher_reading_is_counted_on_top_of_it(tmp_path):
    """The larger of the two numbers is where the next slot is counted from, so a reading that
    overtook the Run's tally re-bases it rather than being absorbed by it.

    A tally kept as a total *since the Run began* reads correctly right up to the moment the Board's
    count moves for a reason that is not this Run, and from then on every submission it makes is
    swallowed by the larger number instead of added to it. It walks the Run past the Board's real
    budget, and the submission it spends over the line is the one the model's own work authorised.
    """
    clock = Clock()
    wire = Teammate(max_attempts=8)
    model = Working(clock, working_from=7)
    run, _recorder = solver(tmp_path, wire, model, clock, runner=replaying(model))

    run.work()

    # Eight stated and one of them the teammate's, so seven are the Solver's to spend — every one of
    # them, and not one more. The seventh is the reproduced candidate turn seven found, which is
    # what the reserve was being held for.
    assert wire.submitted[-1] == real(7)
    assert wire.attempts == 7


def test_a_detail_read_that_failed_does_not_hand_the_budget_back(tmp_path):
    """A failed detail GET carries the last cycle's `attempts` forward rather than reading a zero
    into it (`solver/intake.py`) — but the carried number is from before this Run spent anything.

    The Run's own tally is what still holds, and it has to hold *across* the sync: a floor that a
    stale reading could walk back down is not a floor at all.
    """
    clock = Clock()
    wire = Silent(max_attempts=2)
    run, _recorder = solver(tmp_path, wire, Nominating(clock), clock)

    run.work()

    # The Board's stated count is stuck at zero from the sync before the first submission, and the
    # budget is still spent exactly once per-turn and once in the tail.
    assert wire.submitted == [guess(1), guess(2)]
