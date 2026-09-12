"""`python3 -m solver` — what it refuses, what it writes before it does, and what it exits with.

The exit code is the interface: a supervisor at v2 and a human at 16:05 read the same number, and
v1's gate has to be able to tell a clean exit from a restart loop. So a Refusal is a sentence and a
2, a Run that could not reclaim what it held is a 1, and a Run that ended on its own clock with
nothing left behind is a 0.

The other thing on trial here is what this file alone decides: **which collaborator each half of
the Solver is handed.** A seam left at its own default composes and imports and passes every test
below it, and is only ever visible from the entry point — so a rule that lives in a default
argument is asserted here, over the record a Run actually wrote.
"""

import json
import pathlib
from typing import NamedTuple

import pytest
from solver import __main__ as entry
from solver import codex
from solver import supervisor as supervisor_entry
from solver.__main__ import BROKEN, CLEAN, REFUSED, main
from solver.board import Board
from solver.codex import Child
from solver.event_store import EventStore, ObservationRecorded
from solver.record import Recorder, Usage
from solver.redaction import Redactor
from solver.replay_proof import load_controlled_proof, subject_digest
from solver.triage import QUESTION
from test_board_profile import CONTROL_AGREEABLE, Wire
from test_run_loop import BOARD
from test_run_loop import Wire as PlayableBoard


@pytest.fixture
def boards(tmp_path):
    """One tracked profile, for a Board nothing in `docs/competitions/` claims."""
    (tmp_path / "one.board.json").write_text(
        json.dumps(
            {
                "event": "offline",
                "url": BOARD,
                "flag_wrappers": [r"brunner\{[^}]{1,256}\}"],
                "window_seconds": 3600,
                "prohibitions": ["no broad automated enumeration"],
            }
        )
    )
    return tmp_path


@pytest.fixture
def logged_in(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "auth.json").write_text("{}")
    monkeypatch.setattr(entry.boot, "CODEX_HOME", home)
    return home


def env(**overrides):
    return {"CTFD_URL": BOARD, "CTFD_API_TOKEN": "token", "RUN_ID": "gate-1", **overrides}


def wired(monkeypatch, wire):
    """Both Boards the entry point builds, over one scripted CTFd."""
    monkeypatch.setattr(entry, "TEST_DIRECT_BOARD_FACTORY", lambda url, token: Board(url, token, wire.transport))


class Answered(Child):
    """One `codex exec` that wrote a transcript and exited, with nothing behind it but bytes."""

    def __init__(self, wrote: bytes) -> None:
        self._blocks = [wrote, b""]

    def read(self, _budget: float) -> bytes | None:
        return self._blocks.pop(0) if self._blocks else b""

    def stop(self) -> None:
        self._blocks.clear()

    def close(self) -> tuple[int | None, bytes]:
        return 0, b""


class Spawn(NamedTuple):
    """One `codex exec` as the launcher below saw it — the four things a `Launch` is handed.

    All four are kept because all four are wiring this file alone decides, and none of them shows in
    the answer: a judge spawned with the Board's rules ignored, in an Attempt's directory, on the
    rung below the one this Run leads with, returns a Tier that reads exactly like a judge wired
    right.
    """

    argv: tuple[str, ...]
    workdir: pathlib.Path
    environment: dict[str, str]
    prompt: str


def spawning(monkeypatch, tiers: dict[int, int]) -> list[Spawn]:
    """Every `codex exec` this Run spawns, answered by the shape of the prompt it was handed.

    Substituted at the adapter's own edge onto a process, so the real argv, the real parser and the
    real Triage all run and only the fork is stood in for. Triage's question is answered with the
    Tiers asked for and nothing else is answered at all: this Run is on trial as far as Triage, and
    a launcher that also set the model to work would spend the window on Attempts nothing here
    asserts about — so the first prompt that is not Triage's ends the Run here, deterministically,
    rather than wherever the host's filesystem happens to stop it.
    """
    asked: list[Spawn] = []

    def launch(argv, workdir, environment, prompt: bytes) -> Child:
        asked.append(Spawn(tuple(argv), workdir, dict(environment), prompt.decode()))
        if QUESTION.splitlines()[0] not in asked[-1].prompt:
            raise AssertionError(f"{len(asked)} spawn(s) in, this Run was set to work rather than asked to judge")
        # Built as objects rather than as text, for the reason `test_run_loop` builds its
        # transcripts that way: a hand-written line with a brace out of place proves the parser's
        # fallback and calls it an answer.
        answer = "\n".join(f"{challenge_id} {tier}" for challenge_id, tier in tiers.items())
        events = [
            {"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": answer}},
            {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 2}},
        ]
        return Answered(("\n".join(json.dumps(one) for one in events) + "\n").encode())

    monkeypatch.setattr(codex, "_spawn", launch)
    return asked


def restated(boards, **moved) -> None:
    """The tracked profile with a rule the Board moved — its own file rewritten, because two
    profiles claiming one URL is a Board nothing can resolve rather than a Board with two rules."""
    written = json.loads((boards / "one.board.json").read_text())
    (boards / "one.board.json").write_text(json.dumps({**written, **moved}))


def composed(monkeypatch) -> list:
    """Every judge the entry point built, caught at the seam it builds them with.

    Held so that a second Triage can be asked for after the Run, which is the only way to reach one
    from here: `Scheduler._triage_arrivals` asks again on every `order()` that meets an arrival, and
    a Run that reached a second `order()` would first have to outlive an Attempt.
    """
    building, built = entry.asking, []

    def remember(*args, **keywords):
        built.append(building(*args, **keywords))
        return built[-1]

    monkeypatch.setattr(entry, "asking", remember)
    return built


def canonical_state(state, *, events=1):
    store = EventStore(state, run_id="gate-1")
    for step_index in range(1, events + 1):
        store.append(
            ObservationRecorded(
                attempt_id="attempt-1",
                step_index=step_index,
                command_raw="printf output",
                command_normalised="printf output",
                tool="bash",
            ),
            body=f"output-{step_index}".encode(),
        )
    return store


def damaged_canonical_state(state, classification):
    store = canonical_state(state, events=2 if classification == "previous-digest-mismatch" else 1)
    envelopes = [json.loads(line) for line in store.events_path.read_text().splitlines()]
    if classification == "event-digest-mismatch":
        envelopes[0]["event_digest"] = "f" * 64
    elif classification == "previous-digest-mismatch":
        envelopes[1]["prev_digest"] = "0" * 64
    elif classification == "missing-blob":
        (store.sealed_dir / envelopes[0]["payload"]["blob_digest"]).unlink()
        return
    elif classification == "unknown-schema":
        envelopes[0]["schema_version"] = 999
    store.events_path.write_text("\n".join(json.dumps(envelope) for envelope in envelopes) + "\n")


def stop_at_board_construction(monkeypatch):
    class BoardConstructed(RuntimeError):
        pass

    def construct(*_args):
        raise BoardConstructed

    monkeypatch.setattr(entry, "TEST_DIRECT_BOARD_FACTORY", construct)
    return BoardConstructed


def test_an_empty_environment_refuses_with_a_sentence_and_never_a_traceback(capsys, tmp_path, boards):
    assert main({}, run_state=tmp_path / "state", boards=boards) == REFUSED
    assert "CTFD_URL" in capsys.readouterr().err


def test_a_board_nothing_holds_rules_for_refuses_before_a_single_request(capsys, tmp_path, logged_in, boards):
    code = main(env(CTFD_URL="https://danmark.brunnerctf.dk"), run_state=tmp_path / "state", boards=boards)

    assert code == REFUSED
    assert "no Board profile" in capsys.readouterr().err


def test_production_refuses_direct_board_transport_without_an_explicit_test_injection(
    capsys, tmp_path, logged_in, boards
):
    code = main(env(), run_state=tmp_path / "state", boards=boards)

    assert code == REFUSED
    assert "qualified Board broker is required" in capsys.readouterr().err


def test_controlled_replay_proof_matches_the_current_entry_point(capsys, monkeypatch, tmp_path, logged_in, boards):
    classifications = (
        "event-digest-mismatch",
        "previous-digest-mismatch",
        "missing-blob",
        "unknown-schema",
    )
    constructed = []
    monkeypatch.setattr(supervisor_entry, "launch_boot", lambda *args: constructed.append(args))
    results = {}
    for classification in classifications:
        state = tmp_path / classification
        damaged_canonical_state(state, classification)
        code = supervisor_entry.main(env(), state=state, stay_quiescent=False)
        refusal = capsys.readouterr().err
        observed = refusal.rsplit("— ", 1)[-1].strip()
        assert refusal == f"[boot] canonical state verification refused this Run — {classification}\n"
        results[classification] = {
            "status": "refused" if code == REFUSED else "accepted",
            "observed": observed,
        }

    proof = load_controlled_proof()
    assert proof == {
        "schema_version": 1,
        "proof_type": "verified-replay-controlled-proof",
        "subject_digest": subject_digest(),
        "corruption_fixture_results": results,
        "pre_authority_refusal": {
            "entry_point": "solver.supervisor.main",
            "refusal_exit_code": REFUSED,
            "external_clients_constructed": len(constructed),
        },
    }


def test_clean_new_state_reaches_board_construction(monkeypatch, tmp_path, logged_in, boards):
    board_constructed = stop_at_board_construction(monkeypatch)

    with pytest.raises(board_constructed):
        main(env(), run_state=tmp_path / "state", boards=boards)


def test_a_fresh_boot_materializes_the_same_replay_as_its_successor(monkeypatch, tmp_path, logged_in, boards):
    state = tmp_path / "state"
    recorder = Recorder(state, run_id="gate-1", redactor=Redactor({}))
    recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="printf output",
        command_normalised="printf output",
        tool="bash",
    ).end(exit_code=0, output=b"output", usage=Usage(model=""))
    del recorder

    board_constructed = stop_at_board_construction(monkeypatch)
    with pytest.raises(board_constructed):
        main(env(), run_state=state, boards=boards)

    canonical = state / "runs" / "gate-1" / "canonical"
    projection_path = canonical / "projections" / "observation-v1.jsonl"
    receipt_path = canonical / "verified-replay.receipt.json"
    first_projection = projection_path.read_bytes()
    first_receipt = receipt_path.read_bytes()

    with pytest.raises(board_constructed):
        main(env(), run_state=state, boards=boards)

    assert first_projection
    assert json.loads(first_projection)["attempt_id"] == "attempt-1"
    assert first_projection == projection_path.read_bytes()
    assert first_receipt == receipt_path.read_bytes()


def test_a_board_that_fails_the_read_contract_control_refuses_the_run(capsys, monkeypatch, tmp_path, logged_in, boards):
    wired(monkeypatch, Wire(control=CONTROL_AGREEABLE))

    code = main(env(), run_state=tmp_path / "state", boards=boards)

    assert code == REFUSED
    assert "not a CTFd field refusal" in capsys.readouterr().err


def test_a_first_intake_that_cannot_be_believed_refuses_after_writing_what_it_read(
    capsys, monkeypatch, tmp_path, logged_in, boards
):
    """Two things at once, because they are one moment.

    Mid-Run an unbelievable sync keeps the last snapshot and carries on — a Board that cannot be read
    is not a Board that emptied. At boot there is no last snapshot to keep, so the Run would spend
    its window ranking nothing while reporting success, and it refuses instead. And the profile is
    already on disk when it does: ADR-0008's named failure needs a post-mortem to tell *the Solver
    behaved wrongly* from *the Solver read the Board wrongly*, and a Run that refused without saying
    what it read cannot answer that.
    """
    state = tmp_path / "state"
    wire = Wire(listed=[{"id": 1, "name": "alpha", "type": "standard"}], ledger=None, mana=None)

    def falls_over_after_the_profile(request):
        answered = wire.transport(request)
        if (
            request.full_url.endswith("/api/v1/challenges")
            and sum(path == "/api/v1/challenges" for path in wire.asked) == 4
        ):
            wire.listed, wire.control = [], CONTROL_AGREEABLE
        return answered

    monkeypatch.setattr(
        entry,
        "TEST_DIRECT_BOARD_FACTORY",
        lambda url, token: Board(url, token, falls_over_after_the_profile),
    )

    code = main(env(TEAM_KEY="never-print-me"), run_state=state, boards=boards)

    assert code == REFUSED
    assert "did not believe the Board" in capsys.readouterr().err
    written = [json.loads(line) for line in (state / "runs" / "gate-1" / "stream.jsonl").read_text().splitlines()]
    assert [one for one in written if one["record"] == "run-close"][0]["cause"].startswith(entry.REFUSED_AT_BOOT)

    opened = [one for one in written if one["record"] == "run-open"][0]["board_profile"]
    assert opened["event"] == "offline"
    assert opened["prohibitions"] == ["no broad automated enumeration"]
    assert opened["flag_wrappers"] == [r"brunner\{[^}]{1,256}\}"]
    assert opened["chall_manager"] == "absent"
    assert opened["run"]["run_id"] == "gate-1"
    assert opened["run"]["restarted"] is False
    # What is *held*, never what is held. A record that leaked the key it was proving we had would
    # be the worst possible trade (ADR-0010).
    assert opened["run"]["credentials_held"]["TEAM_KEY"] == "set"
    assert "never-print-me" not in json.dumps(opened)


def test_the_read_contract_is_refused_before_a_recorder_is_ever_made(monkeypatch, tmp_path, logged_in, boards):
    """A Board whose replies were not composed by CTFd is not a Run with a hole in its record — it
    is not a Run. Verification may prove canonical state first, but no v1 Run fact is written."""
    state = tmp_path / "state"
    wired(monkeypatch, Wire(listed=[], control=CONTROL_AGREEABLE))

    assert main(env(), run_state=state, boards=boards) == REFUSED
    assert not (state / "runs" / "gate-1" / "stream.jsonl").exists()


def test_a_state_mount_that_is_not_there_refuses_rather_than_raising(capsys, monkeypatch, tmp_path, logged_in, boards):
    """The mount is the one thing outside the image a Run depends on, and Colima mounts `$HOME` and
    nothing else — a `-v` from outside it hands the container an empty directory in silence."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    wired(monkeypatch, Wire(listed=[{"id": 1, "name": "alpha", "type": "standard"}], ledger=None, mana=None))

    assert main(env(), run_state=blocked / "state", boards=boards) == REFUSED
    assert "not usable as this Run's state" in capsys.readouterr().err


def test_a_board_that_states_no_difficulty_is_judged_rather_than_left_at_the_floor(
    monkeypatch, tmp_path, logged_in, boards
):
    """The model is Triage's last resort, and a Run that never asks it has no third source at all.

    Brunner published a difficulty on nearly every Challenge, so every Tier of all four gate Runs
    was extracted and the judge's absence cost nothing and showed nothing. This is the Board that
    states none and whose solve counts are flat: what the Board says is unreadable, `solves` has
    ranked nothing, and an entry point that composed the scheduler without a judge would put the
    whole Board on the floor Tier and record every one of them `unjudged` — which is a Run that
    budgets every Challenge alike for want of one argument (ADR-0006).
    """
    state = tmp_path / "state"
    wire = PlayableBoard(count=2)
    for one in wire.listed:
        # Flat rather than absent: a Board where everything sits on the same count has ranked
        # nothing, which is the case `solves` turns down and hands to the judge.
        one["solves"] = 5
    asked = spawning(monkeypatch, {1: 4, 2: 1})
    wired(monkeypatch, wire)

    main(env(), run_state=state, boards=boards)

    # The Run does not outlive its first Attempt here — the launcher above refuses to be set to
    # work — and it does not have to. Triage runs before the first Attempt is ever taken, which is
    # the whole of the ordering this assertion rests on.
    written = [json.loads(line) for line in (state / "runs" / "gate-1" / "stream.jsonl").read_text().splitlines()]
    judged = [one for one in written if one["record"] == "triage"][0]["tiers"]
    assert {one["challenge_id"]: (one["tier"], one["provenance"]) for one in judged} == {
        1: (4, "judged"),
        2: (1, "judged"),
    }
    # One prompt for the whole remainder, and the manifest in it — the judgement ADR-0006 measured
    # as worth anything is the one made with the artefacts named and every Challenge alongside.
    assert "id 1 " in asked[0].prompt and "id 2 " in asked[0].prompt
    # And the rest of the wiring, which the Tiers above would read the same without: the rung this
    # Run leads with, and the judge's own directory under the mount this Run was pointed at rather
    # than an Attempt's, which holds a Challenge's files.
    assert asked[0].workdir == state / entry.JUDGE_WORKDIR
    assert asked[0].environment["CODEX_HOME"] == str(logged_in)


def test_a_board_that_withdraws_web_search_withdraws_it_from_the_judge_too(monkeypatch, tmp_path, logged_in, boards):
    """A dial the Board holds binds every invocation this Run makes, and the judge is one of them.

    Web search is a Board profile value (ADR-0014) because a Board's rules can withdraw the tool,
    and it is the one dial the judge's narrowing does not already cover: `--sandbox read-only` and
    `network=False` withhold the filesystem and the wire, and neither gates a model-side tool the
    vendor runs at its own end. So a judge left at the module default is the one spawn of a Run that
    plays by rules the Attempt two lines below it obeys.
    """
    wire = PlayableBoard(count=1)
    wire.listed[0]["solves"] = 5
    restated(boards, web_search=False)
    asked = spawning(monkeypatch, {1: 4})
    wired(monkeypatch, wire)

    main(env(), run_state=tmp_path / "state", boards=boards)

    assert "tools.web_search=false" in asked[0].argv
    # Both dials on the one command line, which is the whole point: the judge is still the narrowed
    # invocation, and the narrowing is not what withdrew the search.
    assert asked[0].argv[asked[0].argv.index("--sandbox") + 1] == "read-only"


def test_a_second_triage_writes_its_steps_where_the_first_did_not(monkeypatch, tmp_path, logged_in, boards):
    """Every Triage after the first is a fresh invocation of the CLI numbering its Steps from 1.

    A Board that drops Challenges mid-event is the case `Scheduler._triage_arrivals` exists for
    (ADR-0015), and it asks the judge once per batch of arrivals. Two of those under one
    `attempt_id` would put two Steps at the same address — which is exactly what `run.Steps` and
    `Flags` share a counter to prevent — and a reader of the stream could no longer tell the second
    judgement from the first.

    The arrival is stood in for by asking the captured judge a second time: reaching a second
    `order()` would mean outliving an Attempt, and this Run is on trial only as far as Triage.
    """
    state = tmp_path / "state"
    wire = PlayableBoard(count=1)
    wire.listed[0]["solves"] = 5
    spawning(monkeypatch, {1: 4})
    wired(monkeypatch, wire)
    judges = composed(monkeypatch)

    main(env(), run_state=state, boards=boards)
    judges[0](QUESTION)

    written = [json.loads(line) for line in (state / "runs" / "gate-1" / "stream.jsonl").read_text().splitlines()]
    spent = [
        (one["attempt_id"], one["step_index"])
        for one in written
        if one["record"] == "step-begin" and one["attempt_id"].startswith("triage")
    ]
    assert spent == [("triage-1", 1), ("triage-2", 1)]


def test_the_exit_codes_are_the_three_a_reader_has_to_tell_apart():
    assert (CLEAN, BROKEN, REFUSED) == (0, 1, 2)
