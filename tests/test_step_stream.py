"""The stream itself: one append-only JSONL file per Run, and what each record has to carry.

Every assertion here traces to
[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md). The record is what
makes every uncalibrated number in v1 measurable *after* a Run rather than guessed before one, so
what is on trial is not that the Solver logs — it is that nothing a rule computes has been frozen
into the log, and that a Step which hung is visible rather than absent.
"""

import json
import shutil
from pathlib import Path

import pytest
from solver.record import SCHEMA_VERSION, Recorder, Usage
from solver.redaction import Redactor

TOKEN = "sk-ant-oat01-" + "y" * 24  # gitleaks:allow - shaped like the real thing, opens nothing


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path, run_id="run-brunner-01", redactor=Redactor({"CTFD_API_TOKEN": TOKEN}))


def records(recorder) -> list[dict]:
    return [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]


def kinds(recorder) -> list[str]:
    return [record["record"] for record in records(recorder)]


def a_step(recorder, *, index=1, command="ls -la", attempt="attempt-1"):
    return recorder.step_begin(
        attempt_id=attempt, step_index=index, command_raw=command, command_normalised=command, tool="bash"
    )


def usage() -> Usage:
    return Usage(model="claude-opus-5", tokens_in=1200, tokens_out=90, cache_read=8000, cache_write=400)


def test_a_step_is_a_begin_and_an_end_pair(recorder):
    """One record at completion would make a Step that never finished invisible — and at a crash
    the command in flight is the prime suspect, while a hang produces no record at all."""
    a_step(recorder).end(exit_code=0, output=b"flag.txt\n", usage=usage())

    assert kinds(recorder) == ["step-begin", "step-end"]


def test_a_step_that_never_finished_leaves_its_begin_behind(recorder):
    """The whole reason for the pair. A begin with no end names both the hang and the crash."""
    a_step(recorder, command="nc 10.0.0.1 1337")

    assert kinds(recorder) == ["step-begin"]
    assert records(recorder)[0]["command_raw"] == "nc 10.0.0.1 1337"


def test_every_record_is_sequence_numbered_without_a_gap(recorder):
    recorder.run_open(board_profile={"flag_wrapper": "brunner{.*}"})
    a_step(recorder).end(exit_code=0, output=b"", usage=usage())
    recorder.run_close(cause="window-closed")

    assert [record["seq"] for record in records(recorder)] == [1, 2, 3, 4]


def test_a_reopened_run_continues_its_sequence(tmp_path):
    """A Run survives a restart (`CONTEXT.md`), and the file is per Run rather than per process.
    Counting from one again would put two records at the same address in one stream."""
    first = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}))
    first.run_open(board_profile={})

    second = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}))
    second.run_close(cause="window-closed")

    assert [record["seq"] for record in records(second)] == [1, 2]


def test_a_crash_costs_one_line_and_not_two(tmp_path):
    """JSONL was chosen over SQLite for exactly this: a crash mid-write truncates one line where a
    database would be corrupt. A truncated line has no newline of its own, so appending straight
    onto it would fuse the crash with the record after it and cost two.
    """
    first = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}))
    first.run_open(board_profile={})
    with first.stream_path.open("a") as stream:
        stream.write('{"seq": 2, "record": "step-be')

    Recorder(tmp_path, run_id="run-1", redactor=Redactor({})).run_close(cause="crashed")

    lines = first.stream_path.read_text().splitlines()
    assert lines[1] == '{"seq": 2, "record": "step-be', "the crash keeps its own line"
    assert json.loads(lines[2])["record"] == "run-close", "and the record after it is readable"


def test_the_field_set_of_every_record_is_pinned(recorder):
    """Two of the three stability rules, made enforceable rather than remembered.

    A field's meaning never changes once written, and a retired name is never reused — neither
    survives as a convention, so renaming or repurposing anything below is a failing test that
    sends the author to ADR-0009 first. The step-end list is that record's, field for field.
    """
    recorder.run_open(board_profile={})
    recorder.attempt_open(
        attempt_id="attempt-1",
        challenge_id=42,
        challenge_name="Baking Bad",
        category="forensics",
        challenge_type="standard",
        solves_at_open=17,
        tier=2,
        budget_s=900,
        attempt_sequence=1,
        instance_until=None,
        order_ranks={"42": 1},
    )
    a_step(recorder).end(exit_code=0, output=b"", usage=usage(), checkpoint="unzip -l archive.zip")
    recorder.attempt_close(
        attempt_id="attempt-1",
        cause="flag",
        approach_label="LSB stego on the cover image",
        solves_at_close=18,
        extensions_granted=1,
        flag="brunner{found}",
    )
    recorder.run_close(cause="window-closed")

    envelope = {"schema_version", "seq", "ts", "mono", "record", "run_id"}
    step = {"attempt_id", "step_index", "command_raw", "command_normalised", "tool", "source"}
    expected = {
        "run-open": {"board_profile"},
        "attempt-open": {
            "attempt_id",
            "challenge_id",
            "challenge_name",
            "category",
            "challenge_type",
            "solves_at_open",
            "tier",
            "budget_s",
            "attempt_sequence",
            "instance_until",
            "order_ranks",
            "exploring",
        },
        "step-begin": step,
        "step-end": step
        | {
            "exit_code",
            "duration_ms",
            "observation_digest",
            "observation_bytes",
            "observation_ref",
            "checkpoint",
            "model",
            "tokens_in",
            "tokens_out",
            "cache_read",
            "cache_write",
        },
        "attempt-close": {"attempt_id", "cause", "approach_label", "solves_at_close", "extensions_granted", "flag"},
        "run-close": {"cause", "write_failures"},
    }

    written = {record["record"]: set(record) for record in records(recorder)}

    assert written == {kind: envelope | fields for kind, fields in expected.items()}


def test_the_raw_command_is_kept_beside_the_normalised_one(recorder):
    """Normalisation *is* the repetition counter's rule, and calibration will change it. Keep only
    the normalised form and no future rule can be applied to a past Run."""
    recorder.step_begin(
        attempt_id="attempt-1",
        step_index=4,
        command_raw="curl -s http://10.0.0.7:31337/robots.txt",
        command_normalised="curl <url>",
        tool="bash",
    ).end(exit_code=0, output=b"", usage=usage())

    end = records(recorder)[-1]

    assert end["command_raw"] == "curl -s http://10.0.0.7:31337/robots.txt"
    assert end["command_normalised"] == "curl <url>"


def test_no_judgement_a_rule_computes_is_frozen_into_the_stream(recorder):
    """ADR-0009's principle, as a check. Novelty is a rule over digests; storing a `novel` boolean
    answers for exactly the one threshold that was live when it was written."""
    a_step(recorder).end(exit_code=0, output=b"nothing new", usage=usage())

    written = set().union(*(set(record) for record in records(recorder)))

    assert not written & {"novel", "progress", "stalled", "repeated", "last_novel_step"}


def test_tokens_are_recorded_and_cost_is_not(recorder):
    """Cost is tokens times a price table that lives outside the record and changes underneath it.
    `tokens_in + cache_read` is the context size, derived rather than stored."""
    a_step(recorder).end(exit_code=0, output=b"", usage=usage())

    end = records(recorder)[-1]

    assert (end["tokens_in"], end["cache_read"]) == (1200, 8000)
    assert not [field for field in end if any(word in field for word in ("cost", "price", "usd", "dollar"))]


def test_attempt_open_carries_the_attempts_sequence_number_for_that_challenge(recorder):
    """Without it, "requeued twice and cut twice" and "reached once" read identically."""
    recorder.attempt_open(
        attempt_id="attempt-3",
        challenge_id=42,
        challenge_name="Baking Bad",
        category="forensics",
        challenge_type="standard",
        solves_at_open=17,
        tier=2,
        budget_s=900,
        attempt_sequence=3,
        instance_until=None,
        order_ranks={"42": 1, "43": 2},
    )

    opened = records(recorder)[-1]

    assert opened["attempt_sequence"] == 3
    assert opened["order_ranks"] == {"42": 1, "43": 2}, "a Challenge never reached differs from one reached and cut"


def test_attempt_close_records_a_cause_rather_than_an_outcome(recorder):
    """`no-flag` is the absence of a cause rather than one, and naming it hides which counter
    fired — the only thing calibration needs to know."""
    recorder.attempt_close(
        attempt_id="attempt-3",
        cause="cut:novelty",
        approach_label="LSB stego on the cover image",
        solves_at_close=18,
        extensions_granted=1,
        flag=None,
    )

    closed = records(recorder)[-1]

    assert closed["cause"] == "cut:novelty"
    assert closed["approach_label"] == "LSB stego on the cover image"
    assert closed["extensions_granted"] == 1


def test_run_open_carries_the_board_profile_as_discovered(recorder):
    """ADR-0008's named failure is a profile that discovers the *wrong* thing, and a post-mortem
    cannot otherwise tell "behaved wrongly" from "read the Board wrongly"."""
    profile = {"url": "https://global.brunnerctf.dk", "flag_wrapper": "brunner{.*}", "chall_manager": False}

    recorder.run_open(board_profile=profile)

    assert records(recorder)[0]["board_profile"] == profile


def test_every_record_carries_the_schema_version(recorder):
    recorder.run_open(board_profile={})
    a_step(recorder).end(exit_code=1, output=b"", usage=usage())
    recorder.run_close(cause="window-closed")

    assert {record["schema_version"] for record in records(recorder)} == {SCHEMA_VERSION}


def test_wall_clock_and_monotonic_are_both_recorded(recorder):
    """Wall clock joins the record to the Board's own timestamps; monotonic is the only one that
    survives a clock step, and duration is measured from it."""
    a_step(recorder).end(exit_code=0, output=b"", usage=usage())

    end = records(recorder)[-1]

    assert end["ts"].endswith("+00:00") or end["ts"].endswith("Z")
    assert isinstance(end["mono"], float)
    assert end["duration_ms"] >= 0


def test_state_deleted_mid_run_costs_the_record_and_never_the_run(recorder, tmp_path):
    """ADR-0008 committed to `/state` being deletable mid-Run without costing the ability to solve.
    So a write that cannot land is counted, and the Step still ends."""
    recorder.run_open(board_profile={})
    shutil.rmtree(tmp_path / "runs")

    a_step(recorder).end(exit_code=0, output=b"still solving", usage=usage())

    assert recorder.write_failures == 3, "the begin, the body and the end"


def test_a_failed_write_is_surfaced_at_run_close(recorder, tmp_path):
    """Never fatal, never silent. A Run whose evidence is holed has to say so on its own face."""
    recorder.run_open(board_profile={})
    shutil.rmtree(tmp_path / "runs")
    a_step(recorder).end(exit_code=0, output=b"", usage=usage())
    (tmp_path / "runs" / "run-brunner-01" / "observations").mkdir(parents=True)

    recorder.run_close(cause="window-closed")

    assert records(recorder)[-1]["write_failures"] == 3


def test_a_write_that_fails_once_is_retried_rather_than_counted(recorder, monkeypatch):
    """Retried once, *then* counted — a transient failure must not show up as a hole in the record."""
    write_bytes, calls = Path.write_bytes, []

    def failing_first(self, data):
        calls.append(self)
        if len(calls) == 1:
            raise OSError("transient")
        return write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", failing_first)
    observation = a_step(recorder).end(exit_code=0, output=b"second time lucky", usage=usage())

    assert len(calls) == 2
    assert recorder.write_failures == 0
    assert (recorder.run_dir / observation.ref).read_bytes() == b"second time lucky"
