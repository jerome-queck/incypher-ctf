"""The seven eval questions, asked of a Run that really wrote itself through the recorder.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) makes the schema
defensible only if every field earns its place by answering a named question, and the seven queries
are how that claim is checked rather than asserted. So the stream under test here is not a fixture
of hand-written JSON: it is written by `solver.record.Recorder`, in the order and the shapes
`solver/run.py` writes them, because a query that answers over a stream nobody produced answers
nothing.

The replay (question 2) gets the most attention, and deliberately. It is the one query that has to
drive the **real** stall call rather than a re-implementation of it, and the one whose whole value
is a threshold that was never live — so what is on trial is that it reproduces the cause that
actually fired, that a tighter threshold cuts earlier, and that a threshold cutting before a Flag is
reported as having cost one.
"""

import datetime as dt
import json

import pytest

import eval_alarm
import eval_banking
import eval_budget
import eval_context
import eval_instances
import eval_refusals
import eval_thresholds
import eval_tier
import stream
from solver.record import (
    CUT_BUDGET,
    CUT_INSTANCE_EXPIRED,
    CUT_NOVELTY,
    CUT_SELF_REPORTED_IMPOSSIBLE,
    FLAG,
    SOURCE_BOARD,
    SOURCE_SOLVER,
    Recorder,
    Usage,
)
from solver.redaction import Redactor
from solver.stall import Thresholds

SPAWN = "codex exec --json --skip-git-repo-check --sandbox danger-full-access --model a-model -"
TURN = Usage(model="a-model", tokens_in=4000, tokens_out=500, cache_read=20000, cache_write=4000)
# A turn the deadline killed: the vendor meters a turn on completion, so this one reports nothing at
# all and the record says so rather than writing the zeros down as a spend (ADR-0022).
KILLED = Usage(model="a-model", known=False)
NOTHING = Usage(model="")


class Clock:
    """A clock the Run is written against, so every duration in the record is a number we chose."""

    def __init__(self) -> None:
        self.at = dt.datetime(2026, 9, 22, 2, 30, tzinfo=dt.timezone.utc)
        self.mono = 1000.0

    def now(self) -> dt.datetime:
        return self.at

    def monotonic(self) -> float:
        return self.mono

    def on(self, seconds: float) -> None:
        self.at += dt.timedelta(seconds=seconds)
        self.mono += seconds


class Written:
    """One Run, written the way `solver/run.py` writes one — including the ordering that matters.

    The spawn's `step-begin` comes before the model's Steps and its `step-end` after them, which is
    what makes a spawn the closing bracket of a turn. A builder that wrote the pair adjacently would
    produce a stream in which no query could tell one turn from the next.
    """

    def __init__(self, tmp_path, run_id="gate-1"):
        self.clock = Clock()
        self.recorder = Recorder(
            tmp_path, run_id=run_id, redactor=Redactor({}), now=self.clock.now, mono=self.clock.monotonic
        )
        self.step = 0
        self.attempt_id = ""

    def opened(self, **profile):
        self.recorder.run_open(board_profile={"chall_manager": "absent", **profile})
        return self

    def triaged(self, *tiers):
        self.recorder.triage(tiers=list(tiers))
        return self

    def attempt(self, attempt_id, *, challenge_id, category="Web", tier=1, budget_s=600, **rest):
        # The deploy is Step 1 of the Attempt and its `attempt-open` comes *after* it, exactly as
        # `solver/run.py` writes them: there is no Instance to record until the deploy has answered.
        self.step, self.attempt_id = 0, attempt_id
        self._step("[instance] deploy", tool="deploy", output=b"[instance] no deploy", exit_code=0)
        self.recorder.attempt_open(
            attempt_id=attempt_id,
            challenge_id=challenge_id,
            challenge_name=f"challenge {challenge_id}",
            category=category,
            challenge_type=rest.get("challenge_type", "static"),
            solves_at_open=rest.get("solves_at_open", 0),
            tier=tier,
            budget_s=budget_s,
            attempt_sequence=rest.get("attempt_sequence", 1),
            instance_until=rest.get("instance_until"),
            order_ranks=rest.get("order_ranks", {}),
        )
        return _Attempt(self, attempt_id)

    def closed(self, cause="window-closed"):
        self.recorder.run_close(cause=cause)
        return stream.read(self.recorder.stream_path)

    def _step(self, command, *, tool, output, exit_code, usage=NOTHING, seconds=1.0, source=SOURCE_SOLVER):
        self.step += 1
        begun = self.recorder.step_begin(
            attempt_id=self.attempt_id,
            step_index=self.step,
            command_raw=command,
            command_normalised=command,
            tool=tool,
            source=source,
        )
        self.clock.on(seconds)
        return begun.end(exit_code=exit_code, output=output, usage=usage)


class _Attempt:
    """The handle a Run hands back, so a test reads as the Attempt it is describing."""

    def __init__(self, written: Written, attempt_id: str):
        self.written = written
        self.attempt_id = attempt_id

    def turn(self, *commands, exit_code=0, said=(), seconds=1.0):
        """One invocation of the vendor's agent: the spawn, what the model ran inside it, its end."""
        self.written.step += 1
        spawn = self.written.recorder.step_begin(
            attempt_id=self.attempt_id,
            step_index=self.written.step,
            command_raw=SPAWN,
            command_normalised=SPAWN,
            tool="codex",
        )
        for prose in said:
            self.written.recorder.claim(attempt_id=self.attempt_id, text=prose.encode())
        for command, output in commands:
            self.written._step(command, tool="shell", output=output, exit_code=0, seconds=seconds)
        self.written.clock.on(seconds)
        spawn.end(exit_code=exit_code, output=b"[codex] the turn ended", usage=TURN if exit_code == 0 else KILLED)
        return self

    def submitted(self, flag, *, accepted=True):
        self.written._step(
            f"[flag] submit {flag}", tool="flag-submit", output=b"[flag] submitted", exit_code=0 if accepted else 1
        )
        return self

    def swept(self, *, left_held=False):
        self.written._step(
            "[instance] sweep", tool="sweep", output=b"[instance] swept", exit_code=1 if left_held else 0
        )
        return self

    def over(self, cause, *, flag=None):
        self.written.recorder.attempt_close(
            attempt_id=self.attempt_id,
            cause=cause,
            approach_label="",
            solves_at_close=0,
            extensions_granted=0,
            flag=flag,
        )
        return self.written


@pytest.fixture
def run(tmp_path):
    """A Run with two Attempts: one that banked a Flag after a Checkpoint, one that went nowhere.

    The Checkpoint is the same command answering differently, which is `solver/stall.py`'s rule
    exactly; the second Attempt runs four different commands that all answer identically, which
    trips novelty rather than repetition and is the distinction the counters turn on.
    """
    written = Written(tmp_path).opened()
    written.triaged(
        {"challenge_id": 7, "name": "challenge 7", "tier": 1, "provenance": "extracted", "stated": "Easy"},
        {"challenge_id": 9, "name": "challenge 9", "tier": 3, "provenance": "judged", "stated": ""},
    )
    written.attempt("7-1", challenge_id=7, category="Web", tier=1).turn(
        ("curl -s http://target/admin", b"403 Forbidden"),
        ("ls -la", b"loot.txt\n"),
        ("curl -s http://target/admin", b"200 OK brunner{sunk}"),
    ).submitted("brunner{sunk}").over(FLAG, flag="brunner{sunk}")

    written.attempt("9-1", challenge_id=9, category="Pwn", tier=3).turn(
        ("file blob", b"data"),
        ("strings blob", b"data"),
        ("xxd blob", b"data"),
        ("od -c blob", b"data"),
        ("head blob", b"data"),
        ("tail blob", b"data"),
    ).over(CUT_NOVELTY)
    return written.closed()


def answer(query, paths, capsys):
    """Run one query the way a person runs it, and hand back what it printed and what it exited."""
    code = query.main([str(path) for path in paths])
    return code, capsys.readouterr().out


def test_the_seven_queries_all_answer_over_a_real_stream(run, capsys):
    """The whole acceptance test, in one place: seven questions, one script each, over a stream
    that was written rather than typed. A query that raises here answers nothing at 22:00 on gate
    night, which is the only time any of them will ever be run."""
    for query in (
        eval_context,
        eval_thresholds,
        eval_budget,
        eval_tier,
        eval_banking,
        eval_alarm,
        eval_instances,
    ):
        code, said = answer(query, [run.path], capsys)
        assert code == 0, f"{query.__name__} exited {code}"
        assert run.run_id in said, f"{query.__name__} did not name the Run it read"


def test_the_replay_reaches_the_cause_that_really_fired(run):
    """Question 2's floor. The counters are pure functions over the stream, so the live thresholds
    replayed over a recorded Attempt have to reproduce what actually happened — a replay that does
    not agree with the Run it is replaying cannot be trusted about a threshold that was never live."""
    went_nowhere = next(one for one in run.attempts if one.attempt_id == "9-1")

    assert eval_thresholds.replay(went_nowhere, Thresholds()).cause == went_nowhere.cause


def test_a_tighter_cliff_cuts_earlier_and_a_looser_one_does_not(run):
    """The whole point of replaying at N thresholds: the same recorded Attempt, two answers."""
    went_nowhere = next(one for one in run.attempts if one.attempt_id == "9-1")

    tight = eval_thresholds.replay(went_nowhere, Thresholds(novelty=2, cliff=2))
    loose = eval_thresholds.replay(went_nowhere, Thresholds(novelty=99, cliff=99))

    assert tight.steps < loose.steps
    assert loose.cause == ""


def test_a_threshold_that_would_have_cut_before_the_flag_is_reported_as_costing_one(run):
    """*Against known outcomes* is the phrase ADR-0009 uses, and this is what it buys. Saved Steps
    are worthless if the Attempt they were saved from was the one that won."""
    won = next(one for one in run.attempts if one.attempt_id == "7-1")

    brutal = eval_thresholds.Verdict(Thresholds(cliff=1))
    brutal.took(won, eval_thresholds.replay(won, Thresholds(cliff=1)))
    live = eval_thresholds.Verdict(Thresholds())
    live.took(won, eval_thresholds.replay(won, Thresholds()))

    assert brutal.flags_lost == 1
    assert live.flags_lost == 0


def test_checkpoints_are_derived_from_the_stream_rather_than_read_off_it(run):
    """A Checkpoint is a judgement, and ADR-0009 forbids a stored judgement — so the field on a
    `step-end` stays empty and the count comes out of the replay. Question 1 is unanswerable any
    other way, and re-running it after the rule changes re-reads every past Run under the new one."""
    won = next(one for one in run.attempts if one.attempt_id == "7-1")

    assert won.checkpoints == []
    assert eval_thresholds.replay(won, Thresholds()).checkpoints == (3,)


def test_the_alarm_exits_non_zero_only_when_it_fired(tmp_path, capsys):
    """`cut:self-reported-impossible` is recorded because the aim is for it never to fire, so the
    query that watches it says so in the one channel an unattended caller cannot miss."""
    quiet = Written(tmp_path, run_id="quiet").opened()
    quiet.attempt("1-1", challenge_id=1).turn(("ls", b"a")).over(CUT_NOVELTY)
    quiet_run = quiet.closed()

    loud = Written(tmp_path, run_id="loud").opened()
    loud.attempt("1-1", challenge_id=1).turn(("ls", b"a"), said=("this is impossible",)).over(
        CUT_SELF_REPORTED_IMPOSSIBLE
    )
    loud_run = loud.closed()

    assert answer(eval_alarm, [quiet_run.path], capsys)[0] == 0
    code, said = answer(eval_alarm, [loud_run.path], capsys)
    assert code == 1
    assert "this is impossible" in said


def test_a_premature_quit_is_derived_from_the_lines_and_never_from_a_field(tmp_path, capsys):
    """ADR-0014: refusal and premature-quit are queries, not fields, and nothing is added to the
    closed Cut vocabulary. A turn that came back of its own accord with another turn after it is
    the orchestrator having re-invoked, which it only does where nothing had ended the Attempt."""
    written = Written(tmp_path, run_id="quitter").opened()
    attempt = written.attempt("1-1", challenge_id=1, category="Forensics")
    attempt.turn(("ls", b"a"), exit_code=0)
    attempt.turn(("cat b", b"b"), exit_code=-9)
    run = attempt.over(CUT_NOVELTY).closed()

    code, said = answer(eval_refusals, [run.path], capsys)

    assert code == 0
    assert "Forensics" in said
    assert [one.ended_itself for one in run.attempts[0].turns] == [True, False]


def test_a_refusal_is_a_barren_turn_whose_prose_says_so(tmp_path, capsys):
    """The shape comes first and the phrase second: a turn in which the model ran nothing is what
    a refusal looks like, and the prose is what names it one. Bodies live beside a `/state` stream,
    so this is the leg a promoted stream cannot answer."""
    written = Written(tmp_path, run_id="refuser").opened()
    written.attempt("1-1", challenge_id=1).turn(said=("I can't help with attacking that host.",)).over(CUT_NOVELTY)
    run = written.closed()

    code, said = answer(eval_refusals, [run.path], capsys)

    everything = next(line.split() for line in said.splitlines() if line.strip().startswith("everything"))

    assert code == 0
    assert run.attempts[0].turns[0].barren
    # name, attempts, turns, barren, refused, quit early, quit rate
    assert everything[:5] == ["everything", "1", "1", "1", "1"]


def test_a_query_reads_a_promoted_stream_and_says_when_the_bodies_are_gone(run, tmp_path, capsys):
    """A promoted stream is the JSONL alone. Every query still answers over it; the ones that need
    prose report that they could not ask rather than reporting a zero they never measured."""
    promoted = tmp_path / "promoted" / f"{run.run_id}.jsonl"
    promoted.parent.mkdir()
    promoted.write_bytes(run.path.read_bytes())

    code, said = answer(eval_refusals, [promoted], capsys)

    assert code == 0
    assert "bodies not beside this stream" in said
    assert "unmeasured rather than zero" in said


def test_a_read_back_step_carries_where_its_bytes_came_from(tmp_path):
    """A projection that dropped `source` would leave an offline replay unable to re-derive what
    authorised a submission, which is the one thing a replay of a stored stream exists to do
    (ADR-0019)."""
    written = Written(tmp_path, run_id="sourced").opened()
    written.attempt("1-1", challenge_id=1)
    written._step(
        "[recon] the description as the Board gave it",
        tool="description",
        output=b"prose",
        exit_code=0,
        source=SOURCE_BOARD,
    )
    written._step("cat note.txt", tool="shell", output=b"a fact", exit_code=0)

    steps = written.closed().attempts[0].steps

    assert [one.source for one in steps if one.tool in ("description", "shell")] == [SOURCE_BOARD, SOURCE_SOLVER]


def test_a_sweep_that_left_something_held_is_named_as_the_leak(tmp_path, capsys):
    """chall-manager never evicts, so an Instance still held when the process exits is capacity
    nobody reclaims. `solver/instance.py` records the verdict as the sweep's exit code."""
    written = Written(tmp_path, run_id="leaky").opened(chall_manager="installed")
    written.attempt("1-1", challenge_id=1, challenge_type="dynamic", instance_until="2026-09-22T03:00:00+00:00").turn(
        ("ls", b"a")
    ).swept(left_held=True).over(CUT_NOVELTY)
    run = written.closed()

    code, said = answer(eval_instances, [run.path], capsys)

    assert code == 0
    assert "LEFT HELD" in said
    assert "left something held" in said


def test_the_replay_can_reach_an_expired_instance(tmp_path):
    """The replay carries both of an Attempt's clocks, not just the budget. `instance_until` is the
    Board's own deadline off the open line, and without it `cut:instance-expired` would be a cause
    of the closed vocabulary that no threshold could ever produce — a silent hole in question 2."""
    written = Written(tmp_path, run_id="leased").opened()
    attempt = written.attempt(
        "1-1",
        challenge_id=1,
        budget_s=6000,
        challenge_type="dynamic",
        # Inside the Attempt rather than beyond it: the Lease runs out while the model is working.
        instance_until="2026-09-22T02:30:03+00:00",
    )
    run = attempt.turn(("ls", b"a"), ("cat b", b"b"), ("cat c", b"c")).over(CUT_INSTANCE_EXPIRED).closed()

    replayed = eval_thresholds.replay(run.attempts[0], Thresholds())

    assert replayed.cause == CUT_INSTANCE_EXPIRED


def test_a_ratio_over_no_checkpoint_at_all_is_blank_rather_than_zero():
    """Steps-to-last-Checkpoint is undefined where there was no Checkpoint, and `0.00` would file an
    Attempt that never moved the environment in the same column as one that moved it immediately and
    then went quiet. Those are opposite findings — the same reason the token rate blanks."""
    assert eval_context._ratio((), 12) == ""
    assert eval_context._ratio((6,), 12) == "0.50"


def test_a_killed_turn_reaches_the_queries_as_unmeasured_and_never_as_a_zero(tmp_path, capsys):
    """The other face of the ratio above, and the defect the four gate Runs surfaced: 22 of 27
    Attempts reported a spend nobody measured, and the per-Category table read Web, Pwn and
    Forensics as having cost nothing over 226 model Steps (#104). The turns ran for minutes — what
    is missing is the count, not the spend, and a table saying `0` states a fact it does not have."""
    written = Written(tmp_path, run_id="killed").opened()
    written.attempt("1-1", challenge_id=1, category="Pwn").turn(("ls", b"a"), exit_code=-9).over(CUT_BUDGET)
    run = written.closed()
    attempt = run.attempts[0]

    spend = eval_budget.Spend()
    spend.took(attempt)

    assert [len(attempt.unmeasured), attempt.tokens] == [1, 0]
    assert spend.row("Pwn")[4] == "", "a group nobody measured owes no total, and a zero is not one"

    code, said = answer(eval_context, [run.path], capsys)

    assert code == 0
    assert "1 turn(s) never reported what they cost" in said


def test_a_measured_total_beside_an_unmeasured_turn_is_marked_as_a_floor():
    """An Attempt that mixes metered turns with killed ones is the case a blank would over-correct:
    something *was* measured, and what it is not is the whole of what was spent."""
    assert eval_budget.Spend(unmeasured=2).counted(0) == ""
    assert eval_budget.Spend(unmeasured=1).counted(4500) == "4500+"
    assert eval_budget.Spend().counted(4500) == "4500"


def test_a_stream_written_before_the_field_still_reads_a_killed_turn_as_unmeasured(tmp_path):
    """The four promoted gate Runs predate `usage_known` and are committed as they were written, so
    the reader reaches the same verdict without it: an invocation is the only Step a turn's tokens
    ever land on, and one carrying none at all was killed before the vendor reported any."""
    written = Written(tmp_path, run_id="before").opened()
    written.attempt("1-1", challenge_id=1).turn(("ls", b"a"), exit_code=-9).over(CUT_BUDGET)
    path = written.closed().path
    older = (json.loads(line) for line in path.read_text().splitlines())
    path.write_text(
        "\n".join(json.dumps({key: value for key, value in one.items() if key != "usage_known"}) for one in older)
    )

    attempt = stream.read(path).attempts[0]

    assert all(one.usage_known is None for one in attempt.steps), "the field must be gone, or this proves nothing"
    assert [one.command_raw for one in attempt.unmeasured] == [SPAWN]


def two_runs_sharing_an_attempt_id(tmp_path):
    """Two Runs at the same Board, which is what a practice weekend produces.

    `attempt_id` is `<challenge_id>-<attempt_sequence>`, so both Runs call their first Attempt at
    Challenge 94 `94-1`. That is correct — the id is unique within a Run — and it is exactly what a
    query keying on the bare id gets wrong.
    """
    for run_id in ("gate-a", "gate-b"):
        written = Written(tmp_path / run_id, run_id=run_id).opened()
        written.attempt("94-1", challenge_id=94, category="Web").turn(("ls", b"a")).over(CUT_NOVELTY)
        written.closed()
    return [tmp_path / run_id / "runs" / run_id / "stream.jsonl" for run_id in ("gate-a", "gate-b")]


def test_two_runs_sharing_an_attempt_id_are_two_attempts(tmp_path, capsys):
    """The defect the four gate Runs surfaced: they all worked one Board, so 27 attempts carried 11
    distinct `attempt_id` strings and a query keying on the bare id reported 11. Single-Run testing
    could never see it, which is why this test loads two."""
    paths = two_runs_sharing_an_attempt_id(tmp_path)

    runs = stream.load([str(path) for path in paths])
    attempts = [one for run in runs for one in run.attempts if one.opened]

    assert len(attempts) == 2
    assert len({one.attempt_id for one in attempts}) == 1, "the ids must collide, or this proves nothing"
    assert len({one.ref for one in attempts}) == 2

    code, said = answer(eval_refusals, paths, capsys)
    everything = next(line.split() for line in said.splitlines() if line.strip().startswith("everything"))
    assert code == 0
    assert everything[1] == "2", f"counted {everything[1]} attempts across two Runs"


def test_the_budget_does_not_merge_two_runs_attempts_into_one_row(tmp_path, capsys):
    """Merging them summed the minutes of separate Attempts into a row nobody asked for."""
    paths = two_runs_sharing_an_attempt_id(tmp_path)

    code, said = answer(eval_budget, paths, capsys)
    rows = [line for line in said.splitlines() if "94-1" in line]

    assert code == 0
    assert len(rows) == 2, f"expected one row per Run, got {len(rows)}"
    assert any("gate-a" in row for row in rows) and any("gate-b" in row for row in rows)
