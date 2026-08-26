"""The reserved tail: four jobs, in the order they have to happen in, and nothing left held.

The tail is time the scheduler already held back — `acquire` never returns a budget that eats into
`Dials.tail_seconds` — so what is on trial here is the *doing*, not the finding of room. Two of the
four are the ones a Run can silently skip: a candidate the gate reserved is a Flag we found and
never sent, and an Instance still held when the process exits is capacity nobody reclaims, because
chall-manager never evicts.
"""

import datetime as dt
import json

from solver.board import Board
from solver.codex import Credential
from solver.flag import Flags, Pace, ReplayLimits
from solver.instance import Instances
from solver.intake import Intake, Limits
from solver.profile import Rules, discovered
from solver.record import Recorder
from solver.redaction import Redactor
from solver.flag import OBSERVED, Candidate
from solver.run import SIGNALLED, TAIL, WINDOW_CLOSED, Pending, Run, Steps
from solver.schedule import Dials, Scheduler, Window
from test_run_loop import BOARD, CONTROL_REFUSED, NOON, WRAPPER, Agent, Clock, records

RULES = Rules(event="offline", url=BOARD, flag_wrapper=WRAPPER, window_seconds=3600, prohibitions=())
DIALS = Dials(knee_seconds=600.0, floor_seconds=300.0, tail_seconds=300.0)

# A candidate that came out of a command is `observed`; the Board stating no maximum is read as
# *limited*, so the gate reserves the last attempt for a `reproduced` one and holds this back. That
# pairing is the only way to arrive at the tail with something to submit.
UNSTATED_MAX = object()


class Wire:
    """A CTFd with chall-manager, whose ledger is whatever it currently has deployed."""

    def __init__(
        self,
        *,
        count=2,
        instanced=True,
        max_attempts=UNSTATED_MAX,
        terminates=True,
        plugin=None,
        ledger_breaks_after=None,
    ):
        self.listed = [
            {
                "id": one,
                "name": f"challenge-{one}",
                "type": "dynamic_iac" if instanced else "standard",
                "value": 100,
                "solves": one,
                "position": one,
            }
            for one in range(1, count + 1)
        ]
        self.flags = {one["id"]: f"brunner{{flag-for-{one['id']}}}" for one in self.listed}
        self.max_attempts = max_attempts
        self.terminates = terminates
        self.plugin = instanced if plugin is None else plugin
        self.ledger_breaks_after = ledger_breaks_after
        self.ledger_reads = 0
        self.spent = 0
        self.deployed: dict[int, str] = {}
        self.submitted: list[tuple[int, str]] = []
        self.terminated: list[int] = []

    def transport(self, request):
        path = request.full_url[len(BOARD) :]
        if "field=" in path:
            return (*CONTROL_REFUSED, "")
        if path == "/api/v1/challenges/attempt":
            sent = json.loads(request.data)
            self.submitted.append((sent["challenge_id"], sent["submission"]))
            right = self.flags.get(sent["challenge_id"]) == sent["submission"]
            return self._answer({"status": "correct" if right else "incorrect", "message": ""})
        if path.startswith("/api/v1/plugins/ctfd-chall-manager") or path == "/plugins/ctfd-chall-manager/instances":
            if not self.plugin:
                return (404, b'{"success": false}', "")
            if path == "/plugins/ctfd-chall-manager/instances":
                self.ledger_reads += 1
                if self.ledger_breaks_after is not None and self.ledger_reads > self.ledger_breaks_after:
                    return (500, b"", "")
                return (200, self._ledger(), "")
            if path.endswith("/mana"):
                return self._answer({"used": len(self.deployed), "total": 8})
            return self._instance(request, path)
        if path.startswith("/api/v1/challenges/"):
            found = next(one for one in self.listed if str(one["id"]) == path.rsplit("/", 1)[1])
            detail = {
                **found,
                "description": f"Find the flag in {found['name']}.",
                "timeout": 600,
                "attempts": self.spent,
            }
            if self.max_attempts is not UNSTATED_MAX:
                detail["max_attempts"] = self.max_attempts
            return self._answer(detail)
        if path == "/api/v1/challenges":
            return self._answer(self.listed)
        if path.startswith("/api/v1/scoreboard/top/"):
            return self._answer({})
        return (404, b'{"success": false}', "")

    def _instance(self, request, path):
        challenge_id = int((json.loads(request.data) if request.data else {}).get("challengeId") or path.split("=")[-1])
        if request.get_method() == "DELETE":
            if not self.terminates:
                return (429, b"", "")
            self.terminated.append(challenge_id)
            self.deployed.pop(challenge_id, None)
            return self._answer({})
        until = (NOON + dt.timedelta(hours=3)).isoformat()
        self.deployed[challenge_id] = until
        return self._answer({"connectionInfo": f"nc target-{challenge_id} 31337", "until": until})

    def _ledger(self):
        rows = "".join(f"<tr><td>challenge-{one}</td><td>later</td></tr>" for one in self.deployed)
        return (
            f"<table><thead><tr><th>Challenge</th><th>Until</th></tr></thead><tbody>{rows}</tbody></table>"
        ).encode()

    @staticmethod
    def _answer(data):
        return (200, json.dumps({"success": True, "data": data}).encode(), "")


def solver(tmp_path, wire, agent, clock, *, lasting=1200.0, reproduces=True):
    board = Board(BOARD, "token", wire.transport)
    recorder = Recorder(tmp_path / "state", "gate", Redactor({}), now=clock)
    found = discovered(board, Board(BOARD, "", wire.transport), RULES)
    window = Window.opened(recorder.run_dir, lasting=lasting, now=clock())
    intake = Intake(board, recorder, limits=Limits(cycle_seconds=300.0), now=clock)
    intake.sync()
    steps = Steps()
    instances = Instances(board, recorder, step_numbers=steps.spend, now=clock)
    flags = Flags(
        board,
        recorder,
        flag_pattern=WRAPPER,
        instances=instances,
        pace=Pace(per_minute=found.submissions_per_minute),
        limits=ReplayLimits(seconds=1.0),
        # A replay that does not bring the candidate back leaves it `observed` rather than
        # `reproduced`, which is what the submission gate reserves the last attempt against.
        runner=lambda command, _workdir, _limits: (0, _echo(wire, command) if reproduces else b"nothing"),
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
        workdirs=tmp_path / "work",
        launch=agent,
        idle_seconds=30.0,
        now=clock,
        sleep=lambda seconds: clock.tick(seconds),
    )
    return run, recorder


def _echo(wire, command: str) -> bytes:
    challenge_id = int(command.rsplit("-", 1)[1]) if command.startswith("cat /flag-") else 0
    return wire.flags.get(challenge_id, "nothing here").encode()


def test_the_tail_submits_what_the_gate_held_back(tmp_path):
    """`last_call` releasing the reserve: there is no later Attempt for the last attempt to be
    reserved *for*, so a candidate carried out of a Run unsubmitted is a Flag nobody sent."""
    clock = Clock()
    wire = Wire(count=1, instanced=False)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire, solving=(1,)), clock, reproduces=False)

    ending = run.work()

    assert wire.submitted, "the reserved tail submitted nothing"
    assert wire.submitted[-1] == (1, wire.flags[1])
    assert ending.flags == (wire.flags[1],)
    assert any(one["attempt_id"] == TAIL for one in records(recorder, "step-begin"))


def test_nothing_is_held_back_where_the_board_states_a_budget(tmp_path):
    """A Board that says `max_attempts: 0` is unlimited, so the reserve never engages and the Flag
    is submitted in the Attempt that found it rather than five hours later."""
    clock = Clock()
    wire = Wire(count=1, instanced=False, max_attempts=0)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire, solving=(1,)), clock, reproduces=False)

    run.work()

    assert wire.submitted[0] == (1, wire.flags[1])


def test_every_instance_is_destroyed_and_the_ledger_is_empty_afterwards(tmp_path):
    clock = Clock()
    wire = Wire(count=2)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    ending = run.work()

    assert wire.terminated, "no Instance was ever terminated"
    assert wire.deployed == {}
    assert ending.left_held == ()
    assert ending.clean


def test_a_terminate_the_board_refuses_is_reported_rather_than_read_as_a_release(tmp_path):
    """The per-team lock 429s a DELETE, and back-to-back DELETEs in one sweep are the likeliest way
    to meet it. Reading that as a release is how a leak is recorded as reclaimed."""
    clock = Clock()
    wire = Wire(count=1, terminates=False)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    ending = run.work()

    assert ending.left_held == ("challenge-1",)
    assert not ending.clean


def test_the_leak_sweep_is_skipped_entirely_on_a_board_with_no_chall_manager(tmp_path):
    """`Board.instances_held` raises on a Board with no plugin — the ledger is a plugin page — so a
    tail that asked anyway would die reclaiming Instances that could never have existed."""
    clock = Clock()
    wire = Wire(count=1, instanced=False)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    ending = run.work()

    assert run.profile.instances_reachable is False
    assert ending.left_held == ()
    assert ending.clean


def test_telemetry_is_flushed_and_the_run_close_says_which_ending_this_was(tmp_path):
    clock = Clock()
    wire = Wire(count=1, instanced=False)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    run.work()

    closed = records(recorder, "run-close")
    assert len(closed) == 1
    assert closed[0]["cause"] == WINDOW_CLOSED
    assert closed[0]["write_failures"] == 0


def test_a_stop_reaches_the_tail_rather_than_killing_the_run_where_it_stands(tmp_path):
    """PID 1 is handed no default action for a signal it has no handler for, so `docker stop` would
    otherwise be ten seconds of nothing and a SIGKILL — with every Instance still held."""
    clock = Clock()
    wire = Wire(count=2)
    run, recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock, lasting=3600.0)
    stopping = Agent(clock, wire=wire)

    def once(argv, workdir, environment, prompt):
        child = stopping(argv, workdir, environment, prompt)
        run.stop()
        return child

    run._launch = once
    ending = run.work()

    assert ending.cause == SIGNALLED
    assert records(recorder, "run-close")[0]["cause"] == SIGNALLED
    assert wire.deployed == {}
    assert clock.at < NOON + dt.timedelta(seconds=3600)


def test_a_crash_mid_run_still_reclaims_what_it_was_holding(tmp_path):
    clock = Clock()
    wire = Wire(count=1)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)
    exploding = run._scheduler.acquire

    def boom(*args, **kwargs):
        run._scheduler.acquire = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("the ranking exploded"))
        return exploding(*args, **kwargs)

    run._scheduler.acquire = boom
    ending = run.work()

    assert ending.cause == "crashed"
    assert wire.deployed == {}


def test_a_sweep_that_could_not_reach_the_board_is_never_reported_as_a_clean_run(tmp_path):
    """The silent-success shape. A sweep that found nothing held and one that never looked are
    otherwise byte-identical, and only the first of them is a Run that left nothing behind."""
    clock = Clock()
    wire = Wire(count=1, ledger_breaks_after=2)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)

    ending = run.work()

    assert ending.unswept, "a ledger that could not be read was reported as nothing held"
    assert not ending.clean


def test_the_tail_reads_the_submission_budget_now_rather_than_remembering_it(tmp_path):
    """The count is the Board's and is held server-side, so it survives a restart and moves under
    us. A Challenge whose budget was spent since the Attempt that found the candidate is one whose
    last slot the tail would otherwise send a Flag into."""
    clock = Clock()
    wire = Wire(count=1, instanced=False, max_attempts=1)
    run, _recorder = solver(tmp_path, wire, Agent(clock, wire=wire), clock)
    workdir = tmp_path / "work" / "1"
    workdir.mkdir(parents=True)
    run._pending[1] = Pending(1, workdir, (Candidate("brunner{held}", OBSERVED, command="cat flag"),))

    # The Board has recorded a submission against this Challenge since, and states a maximum of one.
    wire.spent = 1
    run._intake.sync()
    run._tail()

    assert not [sent for sent in wire.submitted if sent[1] == "brunner{held}"]
