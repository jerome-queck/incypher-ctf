"""The Run loop, offline: what ends it, what does not, and that it works more than one Challenge.

This is v1's gate rehearsed without a Board — Attempts across several distinct Challenges, a cut
that requeues rather than deadlocking, a correct Flag submitted, and a clean end. What it cannot
prove is the thing only a live Board can: that the Solver and a real CTFd agree. What it can prove
is every rule that would be invisible in a live Run because it never fired.
"""

import datetime as dt
import json
import re
from dataclasses import replace

from solver.board import Board
from solver.codex import Credential, Child
from solver.flag import Flags, Pace, ReplayLimits
from solver.instance import Instances
from solver.intake import Intake, Limits
from solver.profile import Rules, discovered
from solver.record import CUT_BUDGET, FLAG, Recorder
from solver.redaction import Redactor
from solver.run import STAGE, WINDOW_CLOSED, Run, Steps
from solver.schedule import Dials, Scheduler, Window

BOARD = "https://board.example"
NOON = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)
WRAPPER = r"brunner\{[^}]{1,256}\}"
CONTROL_REFUSED = (400, b'{"success": false, "errors": {"field": "not valid"}}')

RULES = Rules(
    event="offline",
    url=BOARD,
    flag_wrappers=(WRAPPER,),
    window_seconds=3600,
    prohibitions=("no broad automated enumeration — an immediate ban",),
)

# What a Board serves when a Challenge ships a file. A parameter on `Wire` rather than a literal,
# because a test about *whose* file the model was handed cannot make its point out of bytes every
# Board serves alike.
ARTEFACT = b"nothing to see, but a real artefact all the same\n"

# Ours rather than the defaults, so a test can reason about the arithmetic: a 3600 s window buys
# five 600 s Attempts above a 300 s tail, and the sixth is refused because it would eat into it.
DIALS = Dials(knee_seconds=600.0, floor_seconds=300.0, tail_seconds=300.0)


class Clock:
    """A clock nothing moves but the things that really cost time — a turn of the model, and a wait.

    Wall-clock is what ends a Run, so a test that could not advance it could only ever assert about
    a Run that never finished.
    """

    def __init__(self, at: dt.datetime = NOON) -> None:
        self.at = at

    def __call__(self) -> dt.datetime:
        return self.at

    def tick(self, seconds: float) -> None:
        self.at += dt.timedelta(seconds=seconds)


class Wire:
    """A CTFd with a handful of Challenges and one Flag each, remembering what it was sent."""

    def __init__(
        self, *, count: int = 6, mana=None, ledger=None, ships_files: bool = False, artefact: bytes = ARTEFACT
    ):
        self.listed = [
            {"id": one, "name": f"challenge-{one}", "type": "standard", "value": 100, "solves": one, "position": one}
            for one in range(1, count + 1)
        ]
        self.flags = {one["id"]: f"brunner{{flag-for-{one['id']}}}" for one in self.listed}
        self.mana = mana
        self.ledger = ledger
        # An attachment costs the recon cascade several Steps before the model has run anything,
        # which is the whole shape the step cliff must not be charged for.
        self.ships_files = ships_files
        self.artefact = artefact
        self.submitted: list[tuple[int, str]] = []

    def transport(self, request):
        path = request.full_url[len(BOARD) :]
        if "field=" in path:
            return (*CONTROL_REFUSED, "")
        if path == "/api/v1/challenges/attempt":
            return self._graded(json.loads(request.data))
        if path.startswith("/api/v1/challenges/"):
            found = next(one for one in self.listed if str(one["id"]) == path.rsplit("/", 1)[1])
            files = [f"files/aa{found['id']}/clue-{found['id']}.txt?token=signed"] if self.ships_files else []
            return self._answer({**found, "description": self.described(found), "max_attempts": 0, "files": files})
        if path == "/api/v1/challenges":
            return self._answer(self.listed)
        if path.startswith("/api/v1/scoreboard/top/"):
            return self._answer({})
        if path.startswith("/files/"):
            return (200, self.artefact, "")
        if path.endswith("/mana"):
            return (404, b'{"success": false}', "") if self.mana is None else self._answer(self.mana)
        return (404, b'{"success": false}', "")

    def described(self, found):
        """Brunner's own shape: a Challenge's prose commonly ends in a flag-format section, and
        every real one that does spells the wrapper out — which recon reads, the sweep finds, and
        the gate spends a slot on."""
        return f"Find the flag in {found['name']}. Flag format: brunner{{like_this}}"

    def _graded(self, sent):
        """CTFd's own shape: the verdict is in the body at HTTP 200, and a correct Flag moves
        `solved_by_me` on the list — which is the only thing that ever tells the Solver a Challenge
        is ours, since a local memory of it would come back wrong after a restart."""
        self.submitted.append((sent["challenge_id"], sent["submission"]))
        right = self.flags.get(sent["challenge_id"]) == sent["submission"]
        for one in self.listed if right else ():
            if one["id"] == sent["challenge_id"]:
                one["solved_by_me"] = True
        return self._answer({"status": "correct" if right else "incorrect", "message": ""})

    @staticmethod
    def _answer(data):
        return (200, json.dumps({"success": True, "data": data}).encode(), "")


def stream(*, commands=(), says=(), reported=(), failed=""):
    """One `codex exec` JSONL transcript, in the shape the real CLI writes it.

    Built as objects rather than as text, because what is under test here is the loop above the
    adapter — a hand-written line with a brace out of place is a test that proves the parser's
    fallback and calls it a Step.
    """
    metered = {
        "input_tokens": 10,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 5,
        "reasoning_output_tokens": 0,
    }
    events = [{"type": "turn.started"}]
    for index, (command, output, exit_code) in enumerate(commands, start=1):
        item = {"id": f"item_{index}", "type": "command_execution", "command": command}
        events.append(
            {
                "type": "item.started",
                "item": {**item, "aggregated_output": "", "exit_code": None, "status": "in_progress"},
            }
        )
        events.append(
            {
                "type": "item.completed",
                "item": {**item, "aggregated_output": output, "exit_code": exit_code, "status": "completed"},
            }
        )
    events += [
        {"type": "item.completed", "item": {"id": "item_say", "type": "agent_message", "text": said}} for said in says
    ]
    events.append({"type": "turn.completed", "usage": metered})
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


class Canned(Child):
    """A `codex exec` that never ran. Closing it is what costs the Attempt its wall-clock, which is
    where a turn's time really goes."""

    def __init__(self, wrote: bytes, clock: Clock, seconds: float) -> None:
        self.written = [wrote]
        self._clock = clock
        self._seconds = seconds

    def read(self, budget: float) -> bytes | None:
        return self.written.pop(0) if self.written else b""

    def stop(self) -> None:
        self.written.clear()

    def close(self) -> tuple[int | None, bytes]:
        self._clock.tick(self._seconds)
        return 0, b""


class Agent:
    """The vendor's agent, answering from what the prompt asked it about.

    Keyed on the Challenge's name because that is what the prompt actually carries, so a test that
    scripts one Challenge's behaviour is also asserting the prompt named the right Challenge.
    """

    def __init__(self, clock: Clock, *, wire: Wire, solving=(), seconds: float = 300.0, scripted=None):
        self.clock = clock
        self.wire = wire
        self.solving = set(solving)
        self.seconds = seconds
        self.scripted = scripted or {}
        self.prompts: list[str] = []

    def __call__(self, argv, workdir, environment, prompt: bytes) -> Child:
        text = prompt.decode()
        self.prompts.append(text)
        found = next((one for one in self.wire.listed if one["name"] in text), None)
        challenge_id = found["id"] if found else 0
        if scripted := self.scripted.get(challenge_id):
            return Canned(scripted, self.clock, self.seconds)
        if challenge_id in self.solving:
            flag = self.wire.flags[challenge_id]
            wrote = stream(
                commands=((f"cat /flag-{challenge_id}", flag, 0),),
                says=[f"APPROACH: read the file on {found['name']}"],
            )
        else:
            wrote = stream(
                commands=((f"ls -la {workdir}", "total 0\n", 0),),
                says=[f"APPROACH: looked around {found['name'] if found else 'nothing'}"],
            )
        return Canned(wrote, self.clock, self.seconds)


def solver(
    tmp_path, wire, agent, clock, *, lasting=3600.0, dials=DIALS, cycle_seconds=300.0, event=RULES.event, work_root=None
):
    """Everything `solver/__main__.py` composes, with the clock and the child under the test's hand.

    `event` and `work_root` are separable because the real ones are: `/state/work` is one host
    mount across every Board the image plays, and the event is the only thing under it that tells
    two of them apart (ADR-0025).
    """
    board = Board(BOARD, "token", wire.transport)
    recorder = Recorder(tmp_path / "state", "gate", Redactor({}), now=clock)
    found = discovered(board, Board(BOARD, "", wire.transport), replace(RULES, event=event))
    window = Window.opened(recorder.run_dir, lasting=lasting, now=clock())
    intake = Intake(board, recorder, limits=Limits(cycle_seconds=cycle_seconds), now=clock)
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
        # The replay is the same command the Observation came from, so a fake that echoes it back is
        # the honest stand-in: what is on trial in these tests is the loop, not the shell.
        runner=lambda command, _workdir, _limits: (0, _replayed(wire, command)),
        step_numbers=steps.spend,
        now=clock,
        sleep=lambda _seconds: None,
    )
    run = Run(
        profile=found,
        recorder=recorder,
        intake=intake,
        scheduler=Scheduler(window, recorder, dials=dials, now=clock),
        flags=flags,
        instances=instances,
        steps=steps,
        chain=(Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "codex"),),
        work_root=work_root or tmp_path / "work",
        launch=agent,
        idle_seconds=30.0,
        now=clock,
        sleep=lambda seconds: clock.tick(seconds),
    )
    return run, recorder


def _replayed(wire: Wire, command: str) -> bytes:
    challenge_id = int(command.rsplit("-", 1)[1]) if command.startswith("cat /flag-") else 0
    return wire.flags.get(challenge_id, "nothing here").encode()


def records(recorder: Recorder, kind: str) -> list[dict]:
    lines = recorder.stream_path.read_text().splitlines()
    return [json.loads(line) for line in lines if json.loads(line)["record"] == kind]


# ---------------------------------------------------------------- what ends a Run


def test_a_run_ends_when_the_window_closes(tmp_path):
    clock = Clock()
    wire = Wire()
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    ending = run.work()

    assert ending.cause == WINDOW_CLOSED
    assert records(recorder, "run-close")[0]["cause"] == WINDOW_CLOSED
    assert clock.at >= NOON + dt.timedelta(seconds=3000)


def test_a_run_never_ends_because_the_board_looks_finished(tmp_path):
    """Every Challenge solved, or every remaining one undeployable, makes `acquire` answer `None`
    with time still on the clock. A caller that read that as *the window is spent* would end a Run
    at 11:00 on a Board that had merely gone quiet (`CONTEXT.md`, *Run*)."""
    clock = Clock()
    wire = Wire(count=1)
    wire.listed[0]["solved_by_me"] = True
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    ending = run.work()

    assert ending.cause == WINDOW_CLOSED
    assert ending.attempts == 0
    # It waited rather than concluding. The clock only moved because the wait moved it.
    assert clock.at > NOON


def test_a_run_that_crashes_still_ends_and_still_says_so(tmp_path):
    """A crash is one of the two endings, and it still owes the tail: an Instance held when the
    process exits is capacity nobody reclaims."""
    clock = Clock()
    wire = Wire()
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)
    run._scheduler.acquire = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("the ranking exploded"))

    ending = run.work()

    assert ending.cause == "crashed"
    assert "the ranking exploded" in ending.detail
    assert "crashed" in records(recorder, "run-close")[0]["cause"]


# ---------------------------------------------------------------- working Attempts


def test_the_run_works_at_least_five_distinct_challenges_and_requeues_without_deadlocking(tmp_path):
    """v1's gate, offline. A cut Challenge is not placed anywhere — it simply becomes eligible
    again — so a Run that stalls on one Challenge and a Run that rotates are told apart here."""
    clock = Clock()
    wire = Wire(count=8)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    run.work()

    opened = records(recorder, "attempt-open")
    assert len({one["challenge_id"] for one in opened}) >= 5
    assert all(one["cause"] for one in records(recorder, "attempt-close"))


def test_an_attempt_the_clock_ends_is_cut_on_the_budget_and_never_on_no_flag(tmp_path):
    clock = Clock()
    wire = Wire(count=2)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    run.work()

    assert {one["cause"] for one in records(recorder, "attempt-close")} == {CUT_BUDGET}


def test_an_early_stop_with_budget_left_opens_another_turn_over_the_same_working_directory(tmp_path):
    """The vendor's agent ending its turn is not an ending. Treating it as one would hand the model
    back the give-up button ADR-0005 removed — so the orchestrator re-invokes, and it never
    `resume`s, which would carry the model's prose across the reset."""
    clock = Clock()
    wire = Wire(count=1)
    agent = Agent(clock, wire=wire, seconds=120.0)
    run, recorder = solver(tmp_path, wire, agent, clock, lasting=1200.0)

    run.work()

    opened = records(recorder, "attempt-open")
    assert len(agent.prompts) > len(opened), "a turn that stopped early bought no further turn"
    assert all("/state/work" not in text for text in agent.prompts)
    assert all(str(tmp_path / "work" / RULES.event / "1") in text for text in agent.prompts)


def test_a_correct_flag_is_submitted_and_closes_the_attempt_as_a_flag(tmp_path):
    clock = Clock()
    wire = Wire(count=3)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire, solving=(2,)), clock)

    ending = run.work()

    assert (2, wire.flags[2]) in wire.submitted
    assert ending.flags == (wire.flags[2],)
    assert FLAG in {one["cause"] for one in records(recorder, "attempt-close")}


def test_a_solved_challenge_is_not_attempted_again(tmp_path):
    """The Board's own `solved_by_me` is what says so, re-read every Intake cycle — a local memory
    of what we solved would come back wrong after a restart, in both directions."""
    clock = Clock()
    wire = Wire(count=3)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire, solving=(2,)), clock, cycle_seconds=1.0)

    run.work()

    after = [one["challenge_id"] for one in records(recorder, "attempt-open")]
    assert after.count(2) == 1


def test_the_whole_board_profile_as_discovered_reaches_the_prompt_as_prohibitions(tmp_path):
    clock = Clock()
    wire = Wire(count=1)
    agent = Agent(clock, wire=wire)
    run, _recorder = solver(tmp_path, wire, agent, clock, lasting=1000.0)

    run.work()

    assert all("no broad automated enumeration — an immediate ban" in text for text in agent.prompts)


def test_steps_of_one_attempt_never_share_an_address(tmp_path):
    """Recon, the model's own commands and a Flag submission are all Steps of one Attempt, numbered
    from one counter — two would put two Steps at the same address."""
    clock = Clock()
    wire = Wire(count=3)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire, solving=(1,)), clock)

    run.work()

    for attempt in {one["attempt_id"] for one in records(recorder, "step-begin")}:
        indexes = [one["step_index"] for one in records(recorder, "step-begin") if one["attempt_id"] == attempt]
        assert len(indexes) == len(set(indexes)), f"{attempt} wrote two Steps at one index"


def test_a_candidate_is_put_to_the_submission_gate_once_and_never_twice(tmp_path):
    """Turns of one Attempt share an `attempt_id`, so the second turn's sweep re-finds the first
    turn's Observations. The limiter is Board-wide, so the same wrong Flag sent twice is a slot
    spent on every other Challenge's behalf."""
    clock = Clock()
    wire = Wire(count=2)
    wrong = stream(commands=(("echo guess", "brunner{not-the-flag}", 0),), says=["APPROACH: guessing"])
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire, scripted={1: wrong, 2: wrong}), clock)

    run.work()

    sent = wire.submitted
    assert sent, "nothing was submitted at all, so this proves nothing"
    assert len(sent) == len(set(sent)), f"a candidate went to the gate twice: {sent}"


def test_recon_opens_onto_the_boards_own_files_and_never_onto_the_models_output(tmp_path):
    """The working directory *is* the memory, so by the second Attempt it also holds whatever the
    model made — and a recon cascade over that is recon over a Claim."""
    clock = Clock()
    wire = Wire(count=1)
    agent = Agent(clock, wire=wire)
    litter = "notes-the-model-wrote.txt"

    def scribbles(argv, workdir, environment, prompt):
        (workdir / litter).write_text("a hypothesis")
        return agent(argv, workdir, environment, prompt)

    run, recorder = solver(tmp_path, wire, scribbles, clock, lasting=2000.0)
    run.work()

    assert len(records(recorder, "attempt-open")) > 1, "only one Attempt ran, so nothing was re-reconned"
    assert not [one for one in records(recorder, "step-begin") if litter in one["command_raw"]]


def test_an_attempt_that_observes_nothing_again_and_again_is_crashed_rather_than_cut(tmp_path):
    """A Solver broken at its own end, told apart from a Challenge that is merely hard. A Cut would
    say the Challenge stopped this Attempt, and a dead adapter is not something the Challenge did —
    and `crashed` is what hard-demotes the Challenge rather than merely costing it a place."""
    clock = Clock()
    wire = Wire(count=1)
    silent = stream()
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire, scripted={1: silent}), clock, lasting=2400.0)

    run.work()

    assert "crashed" in {one["cause"] for one in records(recorder, "attempt-close")}


def test_a_crash_inside_an_attempt_still_closes_it_in_the_record(tmp_path):
    """A stream holding an `attempt-open` with no terminator is a Run the eval cannot read at all,
    and #16's schema is meant stable from v1. So the Attempt is closed whatever happened to it, and
    the crash is recorded twice — on the Attempt, and again at Run close."""
    clock = Clock()
    wire = Wire(count=2)

    def explodes(argv, workdir, environment, prompt):
        raise RuntimeError("the adapter went out from under us")

    run, recorder = solver(tmp_path, wire, explodes, clock)
    ending = run.work()

    opened = records(recorder, "attempt-open")
    closed = records(recorder, "attempt-close")
    assert opened and len(opened) == len(closed)
    assert closed[-1]["cause"] == "crashed"
    assert ending.cause == "crashed"
    assert "the adapter went out from under us" in ending.detail


def test_the_invocations_own_steps_are_not_the_model_working_the_challenge(tmp_path):
    """Measured against the live Board: a container with no Codex login spends four Steps a turn on
    the CLI reporting its own failure, so three turns reached the 25-Step cliff and every Attempt
    closed `cut:step-cliff` — blaming the Challenge for our own broken end. The stall call is handed
    the model's Steps and never the invocation's own, so what this records now is `crashed`."""
    clock = Clock()
    wire = Wire(count=1)
    unauthenticated = stream(
        reported=["Code Mode is unavailable", "stream error: 401 Unauthorized"],
        failed="We're having trouble connecting",
    )
    run, recorder = solver(
        tmp_path, wire, Agent(clock, wire=wire, scripted={1: unauthenticated}), clock, lasting=2400.0
    )

    run.work()

    causes = {one["cause"] for one in records(recorder, "attempt-close")}
    assert "crashed" in causes
    assert "cut:step-cliff" not in causes


def test_what_the_orchestrator_spends_after_a_turn_is_not_the_models_next_step_count(tmp_path):
    """The stall call counts an Attempt's opening and the model's own Steps, and nothing the
    orchestrator spent afterwards. A Flag sweep, a replay and a submission happen *after* the
    model's turn, so counting them toward ADR-0005's step cliff closes an Attempt for work the
    model never did — which is what a live Run against Brunner recorded as `cut:step-cliff` on a
    container that never reached the model at all."""
    clock = Clock()
    wire = Wire(count=1)
    unauthenticated = stream(reported=["stream error: 401 Unauthorized"], failed="trouble connecting")
    agent = Agent(clock, wire=wire, scripted={1: unauthenticated}, seconds=60.0)
    run, recorder = solver(tmp_path, wire, agent, clock, lasting=1200.0)

    run.work()

    counted = re.findall(r"(\d+) steps", agent.prompts[-1])
    assert len(counted) > 1, "no run of turns to compare"
    assert len(set(counted)) == 1, f"the count grew while the model did nothing: {counted}"
    assert "cut:step-cliff" not in {one["cause"] for one in records(recorder, "attempt-close")}


def test_the_step_cliff_is_charged_for_the_models_steps_and_not_for_reconning_a_file(tmp_path):
    """Measured against the live Board on 26 August 2026: a Challenge shipping files gave the model
    10 Steps before ADR-0005's 25-Step cliff and one shipping none gave it 18. Same cliff, same
    budget — the difference was entirely what recon had spent opening the Attempt. What an Attempt
    costs in wall-clock is the `Deadline`'s question; the cliff's is only ever *is the model going
    anywhere*, so the Attempt's opening is not charged to it."""
    clock = Clock()
    ran = stream(commands=(("ls -la", "total 0\n", 0), ("cat clue-1.txt", "nothing\n", 0)), says=["APPROACH: looked"])
    wire = Wire(count=1, ships_files=True)
    agent = Agent(clock, wire=wire, scripted={1: ran}, seconds=120.0)
    run, recorder = solver(tmp_path, wire, agent, clock, lasting=1400.0)

    run.work()

    opened = records(recorder, "attempt-open")
    assert opened, "no Attempt ran"
    reconned = [
        one
        for one in records(recorder, "step-begin")
        if one["attempt_id"] == opened[0]["attempt_id"] and one["tool"] not in ("shell", "codex")
    ]
    assert len(reconned) > 2, "recon spent nothing, so this proves nothing about what it is charged"
    # The carried line is what the next turn is told, and it is the count the cliff reads.
    assert re.search(r"\b2 steps\b", agent.prompts[-1]), agent.prompts[-1].split("Earlier Attempts")[-1][:200]


def test_two_boards_that_mint_the_same_challenge_id_do_not_share_a_working_directory(tmp_path):
    """A `challenge_id` is a per-installation auto-increment integer, so Brunner's 13 and COMPFEST's
    13 are one address holding two Challenges — and the working directory is the one thing under
    `/state` a later Attempt reads back as input (ADR-0025). Keyed by the id alone, the second Board
    is handed the first Board's files under a prompt telling the model they are its own, and every
    check downstream passes: Intake fetched correctly, the Run exits clean, nothing is recorded.

    Both Runs share one `work_root` here because the real ones do — `/state/work` is a single
    host mount across every event this image plays, and only the event tells two of them apart.
    """
    root = tmp_path / "work"
    boards = (("brunner-offline", b"brunner's own clue\n"), ("compfest-offline", b"compfest's own clue\n"))

    for event, served in boards:
        clock = Clock()
        wire = Wire(count=1, ships_files=True, artefact=served)
        run, _recorder = solver(tmp_path / event, wire, Agent(clock, wire=wire), clock, event=event, work_root=root)
        run.work()

    assert sorted(one.name for one in root.iterdir()) == ["brunner-offline", "compfest-offline"]
    for event, served in boards:
        landed = root / event / "1" / "clue-1.txt"
        assert landed.read_bytes() == served, f"{event} was handed the other Board's file"


def test_the_flag_format_example_in_the_boards_prose_is_never_submitted(tmp_path):
    """Four of the nine Brunner descriptions our Runs persisted end in a flag-format section, and
    every one of those four spells the wrapper out.

    The Board stating the *shape* of a Flag is not the Solver having found one, so the example must
    never reach the gate — it costs a slot on a decoy in the Run's first cycle, before the model has
    done anything ([#98](https://github.com/jerome-queck/incypher-ctf/issues/98)).
    """
    clock = Clock()
    wire = Wire(count=3)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire, solving=(2,)), clock)

    run.work()

    assert wire.submitted, "nothing was submitted at all, so this proves nothing"
    assert "brunner{like_this}" not in [flag for _challenge_id, flag in wire.submitted]


def test_a_model_that_repeats_the_boards_flag_format_is_held_rather_than_submitted(tmp_path):
    """#98 closed the sweep's route to the flag-format example. This is the model's.

    The description reaches the model verbatim, so a model narrating what it tried can restate the
    example — and an `unverified` candidate is submitted outright where the Board states unlimited
    attempts, which every Brunner Challenge does. It arrives `stated` instead, and waits for the
    tail ([#111](https://github.com/jerome-queck/incypher-ctf/issues/111)).
    """
    clock = Clock()
    wire = Wire(count=2)
    parroting = stream(
        commands=(("echo hello", "hello\n", 0),),
        says=["APPROACH: reading the brief — the flag format is brunner{like_this}"],
    )
    agent = Agent(clock, wire=wire, scripted={1: parroting, 2: parroting})
    run, recorder = solver(tmp_path, wire, agent, clock)

    run.work()

    held = [one["command_raw"] for one in records(recorder, "step-end") if one["command_raw"].startswith("[flag] hold")]
    assert any("brunner{like_this}" in one for one in held), "the Board's own example went straight to the gate"


def test_a_same_named_file_that_is_not_the_boards_is_never_worked_as_though_it_were(tmp_path):
    """The working directory is the memory that crosses an Attempt boundary, so a landing that is
    already there is skipped rather than re-copied — and the skip used to compare names and nothing
    else. A model that wrote `clue-1.txt` into its own directory on Attempt 1, or a Board that
    replaced the file mid-event, therefore handed recon and the model a stranger under a prompt
    calling it this Challenge's own file, and every check downstream passed
    ([#119](https://github.com/jerome-queck/incypher-ctf/issues/119)).
    """
    clock = Clock()
    wire = Wire(count=1, ships_files=True)
    root = tmp_path / "work"
    workdir = root / RULES.event / "1"
    workdir.mkdir(parents=True)
    stranger = b"a hypothesis the model wrote down, under the Board's own name\n"
    (workdir / "clue-1.txt").write_bytes(stranger)

    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock, work_root=root)
    run.work()

    assert (workdir / "clue-1.txt").read_bytes() == stranger, "the model's own file was clobbered"
    landed = [one for one in workdir.iterdir() if one.read_bytes() == ARTEFACT]
    assert landed, "the Board's file never reached the working directory at all"
    reconned = [one["command_raw"] for one in records(recorder, "step-begin") if str(landed[0]) in one["command_raw"]]
    assert reconned, "recon never opened onto the Board's own file"


def test_what_the_solver_did_about_a_taken_name_is_in_the_record(tmp_path):
    """It fails silently and in the direction of a wrong answer, which is what makes the record the
    fix rather than a decoration on it: the Run exits clean, Intake reports a correct fetch, and a
    reader of the stream has to be able to see that the name was taken and what was done about it.
    """
    clock = Clock()
    wire = Wire(count=1, ships_files=True)
    root = tmp_path / "work"
    workdir = root / RULES.event / "1"
    workdir.mkdir(parents=True)
    (workdir / "clue-1.txt").write_bytes(b"not the Board's bytes\n")

    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock, work_root=root)
    run.work()

    staged = [one for one in records(recorder, "step-end") if one["tool"] == STAGE]
    assert staged, "nothing in the stream says the working directory already held that name"
    # The exit code is the operation's own verdict here as everywhere else, and it is what an eval
    # query filters on — a Step that could not use the Board's own name reading as a clean one is
    # the silence again, one layer further out.
    assert staged[0]["exit_code"] == 1
    said = (recorder.run_dir / staged[0]["observation_ref"]).read_text()
    assert "clue-1.txt" in said and str(workdir) in said


def test_the_copy_an_earlier_attempt_staged_is_kept_and_never_re_copied(tmp_path):
    """The legitimate half of the same skip, which the fix must not cost: the directory is what
    carries what was learned across a Turn's reset, so re-copying the Board's file over the archive
    the model unpacked around it would clobber the memory the boundary exists to keep.
    """
    clock = Clock()
    wire = Wire(count=1, ships_files=True)
    agent = Agent(clock, wire=wire)
    root = tmp_path / "work"
    workdir = root / RULES.event / "1"

    def unpacks(argv, launched_in, environment, prompt):
        (launched_in / "unpacked.txt").write_text("what the model got out of it")
        return agent(argv, launched_in, environment, prompt)

    run, recorder = solver(tmp_path, wire, unpacks, clock, lasting=2000.0, work_root=root)
    run.work()

    assert len(records(recorder, "attempt-open")) > 1, "only one Attempt ran, so nothing was re-staged"
    assert (workdir / "unpacked.txt").exists(), "the model's own work did not survive the boundary"
    assert [one.name for one in workdir.iterdir() if one.read_bytes() == ARTEFACT] == ["clue-1.txt"]
    kept = [one for one in records(recorder, "step-end") if one["tool"] == STAGE]
    assert kept and {one["exit_code"] for one in kept} == {0}, "keeping the copy we staged is not a failed Step"
