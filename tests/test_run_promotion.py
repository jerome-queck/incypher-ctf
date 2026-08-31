"""Promotion: getting a Run's record out of `/state` and into the repository without leaking it.

Two things are on trial, and they pull against each other.

The first is that the record **survives whole**. `runs/<run_id>.jsonl` is the stream as written, not
a projection of it, because [ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md)'s
whole payoff is that a projection can be redefined in v3 *against v1 runs* — and a promotion that
quietly dropped the `step-begin` half of a pair, or replaced a Run with a shorter copy of itself,
would take that away without anyone noticing.

The second is that it **refuses rather than leaks**. `conformance/check-secrets.sh` fires after the
push, at which point a credential is already burned; there is no human between the file this writes
and the push, so the guard has to be here. The planted-secret test is the one that matters: a
declared credential in a stream stops the whole batch, and nothing at all moves.
"""

import base64
import datetime as dt
import fcntl
import json

import promote_run
import pytest
import stream
from solver.record import CUT_BUDGET, Recorder, Usage
from solver.redaction import Redactor

# Shaped like a real CTFd token and opens nothing anywhere.
PLANTED = "ctfd_" + "9f3c" * 16  # gitleaks:allow - a fixture credential, worthless by construction

CLEAN = "#!/bin/sh\nexit 0\n"
FINDS_SOMETHING = "#!/bin/sh\nprintf 'RuleID: generic-api-key\\n'\nexit 1\n"

# A stand-in for the rules gitleaks actually ships: anchored on a quote character, which is exactly
# the shape JSON escaping defeats. `$2` is the directory the scanner was pointed at.
QUOTE_ANCHORED = '#!/bin/sh\ngrep -rq \'SEKRIT = "\' "$2" && exit 1\nexit 0\n'
IN_A_QUOTED_ASSIGNMENT = 'SEKRIT = "hT8sPq2Lx9VbN4mZaR7kJdW1cYeUoI3fGnQpXsAv"'  # gitleaks:allow - fixture


@pytest.fixture
def scanner(tmp_path):
    """A stand-in for the pinned scanner, so these tests exercise the real subprocess call without
    needing a 20 MB binary on the runner. What gitleaks itself finds is gitleaks' business."""

    def build(script=CLEAN, name="gitleaks-stub"):
        stub = tmp_path / name
        stub.write_text(script)
        stub.chmod(0o755)
        return str(stub)

    return build


def a_run(state, run_id="gate-1", *, hours_ago=3.0, close=True, attempt=True, output=b"loot.txt\n"):
    """One Run written through the real recorder, finished a few hours ago.

    Aged deliberately: promotion refuses a stream that was written moments ago, because a Run
    between two writes and a Run that has stopped look identical from the outside.
    """
    at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)
    recorder = Recorder(state, run_id=run_id, redactor=Redactor({}), now=lambda: at, mono=lambda: 1.0)
    recorder.run_open(board_profile={"chall_manager": "absent"})
    if attempt:
        recorder.attempt_open(
            attempt_id="1-1",
            challenge_id=1,
            challenge_name="challenge 1",
            category="Web",
            challenge_type="static",
            solves_at_open=0,
            tier=1,
            budget_s=600,
            attempt_sequence=1,
            instance_until=None,
            order_ranks={},
        )
        step = recorder.step_begin(
            attempt_id="1-1", step_index=1, command_raw="ls -la", command_normalised="ls -la", tool="shell"
        )
        step.end(exit_code=0, output=output, usage=Usage(model="a-model", tokens_in=10, tokens_out=2))
        recorder.attempt_close(
            attempt_id="1-1", cause=CUT_BUDGET, approach_label="", solves_at_close=0, extensions_granted=0, flag=None
        )
    if close:
        recorder.run_close(cause="window-closed")
    return recorder.stream_path


def promote(state, into, scanner, *extra):
    return promote_run.main(["--state", str(state), "--into", str(into), "--gitleaks", scanner, *extra])


def test_the_stream_is_promoted_as_written_and_never_as_a_projection(tmp_path, scanner):
    """Byte for byte. A per-Attempt summary row would be a judgement frozen at the moment it was
    taken, and every number v1 turns on is uncalibrated."""
    state, into = tmp_path / "state", tmp_path / "runs"
    source = a_run(state)

    assert promote(state, into, scanner()) == 0
    assert (into / "gate-1.jsonl").read_bytes() == source.read_bytes()


def test_both_halves_of_a_step_pair_survive_promotion(tmp_path, scanner):
    """The pair is what makes a Step that hung visible rather than absent, and it is exactly what a
    projection would collapse — so it is asserted on the committed file rather than on the source."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state)

    assert promote(state, into, scanner()) == 0
    kinds = [record["record"] for record in stream.read(into / "gate-1.jsonl").records]
    assert kinds.count("step-begin") == kinds.count("step-end") == 1


def test_the_bodies_stay_behind(tmp_path, scanner):
    """Observation and Claim bodies live only in `/state` (ADR-0009). The repository keeps the
    digests, and what that costs — they can never be resolved back to content — is accepted there."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state, output=b"a whole megabyte of command output")

    assert promote(state, into, scanner()) == 0
    assert sorted(path.name for path in into.iterdir()) == ["gate-1.jsonl"]


def test_a_run_that_never_opened_an_attempt_is_skipped_rather_than_promoted(tmp_path, scanner, capsys):
    """`runs/` holds Runs. A pre-flight probe writes Steps under a name of its own and opens no
    Attempt, and promoting one would put it beside the Runs it is meant to be compared against."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state, run_id="ctfd-probe", attempt=False)

    assert promote(state, into, scanner()) == 0
    assert "reached no Attempt" in capsys.readouterr().out
    assert not into.exists() or not list(into.iterdir())


def test_a_stream_a_writer_still_holds_is_refused(tmp_path, scanner, capsys):
    """Never during a live Run. The recorder takes this lock on every append, so a lock we cannot
    get is a writer standing there — the one liveness test that is a fact rather than an inference."""
    state, into = tmp_path / "state", tmp_path / "runs"
    source = a_run(state)

    with source.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX)
        code = promote(state, into, scanner())

    assert code == 1
    assert "the Run is live" in capsys.readouterr().out
    assert not into.exists()


def test_a_stream_written_moments_ago_is_refused(tmp_path, scanner, capsys):
    """The lock is only held during a write, so a Run between two Steps would pass it. The staleness
    window is the second half of the same question."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state, hours_ago=0.0)

    assert promote(state, into, scanner()) == 1
    assert "may be live" in capsys.readouterr().out


def test_a_run_killed_without_a_close_is_promoted_and_says_so(tmp_path, scanner, capsys):
    """Every Run that reached Attempt-open is promoted, and a killed one reached it. Refusing it
    would lose exactly the record a post-mortem wants; the missing close goes on its line instead."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state, close=False)

    assert promote(state, into, scanner()) == 0
    assert "no run-close" in capsys.readouterr().out
    assert (into / "gate-1.jsonl").is_file()


def test_a_stream_carrying_a_declared_credential_is_refused_and_nothing_moves(tmp_path, scanner, capsys, monkeypatch):
    """The acceptance test for the whole guard. The credential is planted in the form a verbose HTTP
    call writes one, so what catches it is `solver/redaction.py`'s own rule rather than a pattern
    this script invented — and the batch is refused whole, including the Run that was clean."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state, run_id="clean-gate")
    leaky = a_run(state, run_id="leaky-gate")
    # Base64 behind `user:`, which is the Basic-auth header a `curl -v` writes — a spelling that
    # matches the value we were handed nowhere, and the reason `solver/redaction.py` encodes at all
    # three alignments. A test that planted the raw value would prove only the easy half.
    header = base64.b64encode(b"user:" + PLANTED.encode()).decode()
    with leaky.open("a") as growing:
        growing.write(
            json.dumps({"seq": 99, "record": "step-begin", "run_id": "leaky-gate", "command_raw": header}) + "\n"
        )
    monkeypatch.setattr(promote_run, "held", lambda directory: [("CTFD_API_TOKEN", PLANTED)])

    code = promote(state, into, scanner())

    assert code == 1
    assert "the stream carries CTFD_API_TOKEN" in capsys.readouterr().out
    assert not into.exists()


def test_a_finding_from_the_scanner_refuses_the_batch_too(tmp_path, scanner, capsys):
    """The second leg. It is the scan CI will run over the commit, moved to the one place it can
    still prevent something — a credential this repository never declared is only ever caught here."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state)

    assert promote(state, into, scanner(FINDS_SOMETHING)) == 1
    assert "generic-api-key" in capsys.readouterr().err
    assert not into.exists()


def test_no_scanner_is_a_usage_error_and_never_a_pass(tmp_path, capsys):
    """`conformance/check-secrets.sh`'s rule, held to here: a check that quietly exits 0 because its
    tool is missing is the silent green this repository keeps building controls against. Exit 2, so
    a caller can tell "could not run" from "found a credential"."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state)

    assert promote(state, into, str(tmp_path / "no-scanner-here")) == 2
    assert "install-gitleaks.sh" in capsys.readouterr().err


def test_a_promotion_that_would_shrink_the_record_is_refused(tmp_path, scanner, capsys):
    """A Run survives a restart and its stream grows, so a shorter file replacing a longer one is a
    record being lost rather than updated — and nobody is standing here to notice."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state)
    assert promote(state, into, scanner()) == 0

    # The same Run, shorter — a truncated copy restored over the top of the real one. It still
    # opens an Attempt, so nothing but the shrink guard stands between it and the committed record.
    source = state / "runs" / "gate-1" / "stream.jsonl"
    source.write_text("\n".join(source.read_text().splitlines()[:4]) + "\n")

    assert promote(state, into, scanner()) == 1
    assert "already holds more records" in capsys.readouterr().out


def test_a_dry_run_scans_and_moves_nothing(tmp_path, scanner, capsys):
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state)

    assert promote(state, into, scanner(), "--dry-run") == 0
    assert "scanned clean" in capsys.readouterr().out
    assert not into.exists()


def test_the_scanner_is_given_a_decoded_view_because_json_hides_a_secret_from_it(tmp_path, scanner, capsys):
    """The finding that made this necessary, pinned so it cannot come back.

    Nearly every gitleaks rule is anchored on a quote character, and JSON writes a quote as `\\"` —
    so a secret the scanner flags instantly in a plain file is invisible inside a JSONL string
    value. Measured against the real binary: the same AWS-shaped key is found in a `.txt` and missed
    in a `.jsonl` carrying the identical bytes. Without the decoded companion, `runs/` being *"not
    excluded from the secret scan"* would buy almost nothing.
    """
    state, into = tmp_path / "state", tmp_path / "runs"
    source = a_run(state)
    with source.open("a") as growing:
        growing.write(json.dumps({"seq": 99, "record": "step-begin", "command_raw": IN_A_QUOTED_ASSIGNMENT}) + "\n")

    # The escaping is the whole point: the committed bytes never carry the un-escaped shape.
    assert 'SEKRIT = "' not in source.read_text()

    assert promote(state, into, scanner(QUOTE_ANCHORED)) == 1
    assert not into.exists()


def test_the_decoded_companion_is_scanned_and_never_promoted(tmp_path, scanner):
    """It exists for the scanner and for nothing else. `runs/` holds the stream as written, so a
    second rendering of the same Run landing beside it would be a projection arriving by the back
    door — the one thing promotion is defined not to write."""
    state, into = tmp_path / "state", tmp_path / "runs"
    a_run(state)

    assert promote(state, into, scanner()) == 0
    assert sorted(path.name for path in into.iterdir()) == ["gate-1.jsonl"]


def test_a_truncated_last_line_still_reaches_the_scanner(tmp_path):
    """A crashed Run's last line is truncated by design, and it can carry a credential exactly as
    well as a whole one. It is scanned as the text it is rather than skipped for not parsing."""
    half = b'{"seq": 9, "record": "claim", "command_raw": "half a line with a sec'

    assert b"half a line with a sec" in promote_run.decoded(half)
