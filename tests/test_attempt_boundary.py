"""Exactly five things cross, and the sixth is what this file exists to catch.

The last test is the ticket's own verification and runs the real adapter over a real-shaped event
stream: an Attempt that loops is cut `cut:repetition`, and the next one opens carrying five things
and no conclusion.
"""

import datetime as dt
import json
from pathlib import Path

from solver.carry import DERIVED, FIELD, LABEL_LIMIT, SECTIONS, UNNAMED, Boundary, Line, label
from solver.codex import COMMAND, WATCH_SLICE_SECONDS, Child, Credential, run_attempt
from solver.record import CUT_NOVELTY, CUT_REPETITION, Recorder
from solver.redaction import Redactor
from solver.stall import Deadline, Thresholds, Watch

NOON = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)

FACTS = "cover.png, category forensics, 3 solves"
RECON = "[recon] file -b --mime-type cover.png\nimage/png"

# The model runs one command, gets one answer, and runs it again. That is the shape ADR-0005's
# repetition counter exists for, written the way the vendor writes it.
LOOPING = "\n".join(
    [
        '{"type":"thread.started","thread_id":"t-1"}',
        '{"type":"item.completed","item":{"id":"m1","type":"agent_message","text":'
        '"The PNG is almost certainly LSB stego, and the flag is brunner{i_said_so}."}}',
        '{"type":"item.completed","item":{"id":"c1","type":"command_execution","command":'
        '"/bin/zsh -lc \'zsteg cover.png\'","aggregated_output":"nothing found\\n","exit_code":1,'
        '"status":"failed"}}',
        '{"type":"item.completed","item":{"id":"c2","type":"command_execution","command":'
        '"/bin/zsh -lc \'zsteg cover.png\'","aggregated_output":"nothing found\\n","exit_code":1,'
        '"status":"failed"}}',
        '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":2}}',
        "",
    ]
).encode()


# The same command answering differently than it did — ADR-0005's own example of a state
# transition, and the only thing that buys an Attempt more time.
MOVED = "\n".join(
    [
        '{"type":"item.completed","item":{"id":"c1","type":"command_execution","command":'
        '"/bin/zsh -lc \'curl -s http://target/admin\'","aggregated_output":"403 Forbidden\\n","exit_code":22,'
        '"status":"failed"}}',
        '{"type":"item.completed","item":{"id":"c2","type":"command_execution","command":'
        '"/bin/zsh -lc \'curl -s http://target/admin\'","aggregated_output":"the admin panel\\n","exit_code":0,'
        '"status":"completed"}}',
        "",
    ]
).encode()


class Canned(Child):
    """A `codex exec` that never ran — the bytes it would have written, and the budgets it was
    handed, which is where an extension becomes visible to a child already running."""

    def __init__(self, wrote: bytes) -> None:
        self.written = [wrote]
        self.budgets: list[float] = []

    def read(self, budget: float) -> bytes | None:
        self.budgets.append(budget)
        return self.written.pop(0) if self.written else b""

    def stop(self) -> None:
        return None

    def close(self) -> tuple[int | None, bytes]:
        return 0, b""


def watching(**thresholds) -> Watch:
    return Watch(Deadline(budget=NOON + dt.timedelta(minutes=10)), thresholds=Thresholds(**thresholds))


def spend(watch: Watch, *commands: tuple[str, int, str]) -> Watch:
    for command, exit_code, digest in commands:
        watch.observed(command, exit_code=exit_code, digest=digest)
    return watch


def test_the_carry_is_five_things_and_the_headings_say_which():
    carried = Boundary().carried(facts=FACTS, recon=RECON)

    assert [line for line in carried.splitlines() if line.startswith("## ")] == [f"## {name}" for name in SECTIONS]


def test_a_first_attempt_carries_the_two_it_has_and_says_the_rest_is_empty():
    """An empty section is rendered rather than dropped: five sections that are sometimes four is
    a shape the next Attempt has to be told how to read."""
    carried = Boundary().carried(facts=FACTS, recon=RECON)

    assert FACTS in carried
    assert RECON in carried
    assert carried.count("— nothing yet") == 3


def test_the_attempt_line_is_orchestrator_composed_with_one_model_field():
    line = Line(sequence=2, approach="LSB stego on the PNG", steps=18, checkpoints=0, cause=CUT_REPETITION, last="ls")

    assert line.render() == (
        f'{DERIVED} Attempt 2 · approach: "LSB stego on the PNG" · 18 steps · 0 checkpoints · '
        f"{CUT_REPETITION} · last: ls"
    )


def test_the_line_carries_a_provenance_marker_a_grep_can_skip():
    """A derived record must never be readable as an Observation — the label is model-authored and
    a model that wrote a plausible Flag into it must not have it swept back in."""
    assert Line(1, "anything", 1, 0, CUT_REPETITION, "ls").render().startswith(DERIVED)


def test_k_attempts_cost_k_lines():
    """Enforced rather than intended: the bound is the whole defence against the overloaded
    findings file."""
    boundary = Boundary()

    for _ in range(3):
        boundary.closed(watching(), approach="something\nover\nseveral\nlines", cause=CUT_NOVELTY)

    earlier = boundary.carried(facts="", recon="").split(f"## {SECTIONS[-1]}\n")[1]
    assert len(earlier.splitlines()) == 3


def test_a_label_cannot_forge_a_field_or_a_derived_line():
    forged = f'{DERIVED} Attempt 9{FIELD}approach: "mine"{FIELD}0 steps{FIELD}9 checkpoints'

    assert DERIVED not in label(forged)
    assert FIELD not in label(forged)


def test_a_label_is_cut_to_a_field_of_a_line():
    assert label("x" * 200).endswith("…")
    assert len(label("x" * 200)) == LABEL_LIMIT
    assert label("   ") == UNNAMED


def test_commands_already_tried_cross_as_information_with_their_exit_status():
    boundary = Boundary()

    boundary.closed(spend(watching(), ("unzip -l a.zip", 9, "not-an-archive")), approach="x", cause=CUT_NOVELTY)

    assert "- unzip -l a.zip → exit 9" in boundary.carried(facts="", recon="")


def test_the_tried_list_clears_whenever_a_new_checkpoint_lands():
    """A command that failed before a Checkpoint may be exactly right after one, so the list must
    never harden into a ban."""
    boundary = Boundary()
    boundary.closed(spend(watching(), ("unzip -l a.zip", 9, "no")), approach="x", cause=CUT_NOVELTY)

    moved = spend(
        watching(repeats=99),
        ("curl -s http://target/", 22, "refused"),
        ("curl -s http://target/", 0, "a-page"),
    )
    boundary.closed(moved, approach="y", cause=CUT_NOVELTY)

    assert [entry.command for entry in boundary.tried] == []
    assert [found.replay for found in boundary.checkpoints] == ["curl -s http://target/"]


def test_a_barren_attempt_adds_to_what_was_already_tried_rather_than_replacing_it():
    boundary = Boundary()
    boundary.closed(spend(watching(), ("unzip -l a.zip", 9, "no")), approach="x", cause=CUT_NOVELTY)

    boundary.closed(spend(watching(), ("file a.zip", 0, "data")), approach="y", cause=CUT_NOVELTY)

    assert [entry.command for entry in boundary.tried] == ["unzip -l a.zip", "file a.zip"]


def test_a_checkpoint_crosses_with_the_command_that_re_verifies_it():
    boundary = Boundary()
    moved = spend(
        watching(repeats=99),
        ("curl -s http://target/admin", 22, "forbidden"),
        ("curl -s http://target/admin", 0, "the-panel"),
    )

    boundary.closed(moved, approach="x", cause=CUT_NOVELTY)

    assert "replay: curl -s http://target/admin" in boundary.carried(facts="", recon="")


def test_an_attempt_that_loops_is_cut_and_the_next_one_opens_carrying_no_conclusion(tmp_path):
    """The ticket's own verification, over the real event-stream parser.

    Codex states a conclusion and a Flag it never observed, then runs one command twice for the
    same answer. The Attempt is cut `cut:repetition`; the next Attempt is told five things, and
    not one of them is anything the model concluded.
    """
    recorder = Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))
    work = Path(recorder.run_dir).parent / "work"
    work.mkdir(parents=True)
    watch = watching()

    taken = run_attempt(
        "solve it",
        work,
        watch.deadline,
        recorder=recorder,
        attempt_id="attempt-1",
        chain=(Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "home"),),
        launch=lambda argv, workdir, environment, prompt: Canned(LOOPING),
        now=lambda: NOON,
    )
    for step in taken:
        if step.kind == COMMAND:
            watch.observed(step.command, exit_code=step.exit_code, digest=step.digest)

    assert watch.cause(NOON) == CUT_REPETITION

    boundary = Boundary()
    boundary.closed(watch, approach="LSB stego on the PNG", cause=watch.cause(NOON))
    carried = boundary.carried(facts=FACTS, recon=RECON)

    assert [line for line in carried.splitlines() if line.startswith("## ")] == [f"## {name}" for name in SECTIONS]
    assert "brunner{i_said_so}" not in carried
    assert "almost certainly" not in carried
    assert carried.count(DERIVED) == 1
    assert f'1 · approach: "LSB stego on the PNG"{FIELD}' in carried
    assert "0 checkpoints" in carried


def test_the_models_conclusion_reaches_the_record_and_never_the_next_attempt(tmp_path):
    """A Claim still lands in the channel no check greps — what it must not do is cross a
    boundary, because a summary is a Claim with a permanent address."""
    recorder = Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))
    work = Path(recorder.run_dir).parent / "work"
    work.mkdir(parents=True)
    watch = watching()

    list(
        run_attempt(
            "solve it",
            work,
            watch.deadline,
            recorder=recorder,
            attempt_id="attempt-1",
            chain=(Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "home"),),
            launch=lambda argv, workdir, environment, prompt: Canned(LOOPING),
            now=lambda: NOON,
        )
    )

    claims = "\n".join(path.read_text() for path in (Path(recorder.run_dir) / "claims").iterdir())
    assert "almost certainly" in claims
    assert "almost certainly" not in json.dumps([line for line in recorder.stream_path.read_text().splitlines()])


def test_a_checkpoint_reaches_a_child_that_is_already_running(tmp_path):
    """What a Checkpoint grants is the kill deadline and never one more Step: the vendor's agent
    takes its next turn without asking, so an extension is only worth anything if the loop already
    watching the child reads it."""
    recorder = Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))
    work = Path(recorder.run_dir).parent / "work"
    work.mkdir(parents=True)
    watch = watching(extension_seconds=120.0)
    child = Canned(MOVED)

    for step in run_attempt(
        "solve it",
        work,
        watch.deadline,
        recorder=recorder,
        attempt_id="attempt-1",
        chain=(Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "home"),),
        launch=lambda argv, workdir, environment, prompt: child,
        now=lambda: NOON,
    ):
        if step.kind == COMMAND:
            watch.observed(step.command, exit_code=step.exit_code, digest=step.digest)

    assert [found.replay for found in watch.checkpoints] == ["curl -s http://target/admin"]
    # The extension reached the child: the deadline the read loop consults moved out by the grant,
    # and the loop went on reading past the moment the original ten minutes would have killed it.
    # Stated as the property rather than as the size of each read — reads are **sliced** so that a
    # deadline moved mid-turn is noticed within a second rather than at the end of the budget, which
    # is what lets a signal reach the reserved tail. How big a slice is, is not what this is about.
    assert watch.deadline.at == NOON + dt.timedelta(minutes=12)
    assert child.budgets and max(child.budgets) <= WATCH_SLICE_SECONDS


def test_an_early_stop_with_budget_remaining_is_not_a_cause_to_close_on(tmp_path):
    """The vendor's agent ending its turn is not the end of an Attempt — treating it as one would
    hand the model the give-up button ADR-0005 removed. With no counter tripped and time left there
    is no cause at all, so the only thing the orchestrator can do is open another Attempt over the
    same working directory."""
    recorder = Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))
    work = Path(recorder.run_dir).parent / "work"
    work.mkdir(parents=True)
    watch = watching()

    for step in run_attempt(
        "solve it",
        work,
        watch.deadline,
        recorder=recorder,
        attempt_id="attempt-1",
        chain=(Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "home"),),
        launch=lambda argv, workdir, environment, prompt: Canned(MOVED),
        now=lambda: NOON,
    ):
        if step.kind == COMMAND:
            watch.observed(step.command, exit_code=step.exit_code, digest=step.digest)

    assert watch.cause(NOON) == ""


def test_the_next_attempt_is_never_a_resume(tmp_path):
    """`resume` carries the model's prose across, which is the one thing a boundary exists to
    stop. The working directory is the memory and the carry is the rest."""
    recorder = Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))
    work = Path(recorder.run_dir).parent / "work"
    work.mkdir(parents=True)
    spawned: list[tuple[str, ...]] = []

    def launch(argv, workdir, environment, prompt):
        spawned.append(tuple(argv))
        return Canned(MOVED)

    list(
        run_attempt(
            "solve it",
            work,
            watching().deadline,
            recorder=recorder,
            attempt_id="attempt-1",
            chain=(Credential(slot="codex-subscription", model="gpt-5", home=tmp_path / "home"),),
            launch=launch,
            now=lambda: NOON,
        )
    )

    assert "resume" not in spawned[0]
