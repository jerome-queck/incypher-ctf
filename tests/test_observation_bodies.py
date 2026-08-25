"""Where an Observation's bytes actually go, and why the line pointing at them stays small.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) keeps bodies whole in
files under `observation_ref` so that a stream from a Run that produced gigabytes is still cheap to
parse — and shows the *model* a bounded rendering of the same bytes. These hold the seam between
the two apart: what is stored is never what is shown, and neither is allowed to become the other.
"""

import json

import pytest
from solver.record import Recorder, Usage
from solver.redaction import Redactor

USAGE = Usage(model="claude-opus-5", tokens_in=10, tokens_out=1)


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path, run_id="run-1", redactor=Redactor({}), observation_limit=500)


def a_step(recorder):
    return recorder.step_begin(
        attempt_id="attempt-1", step_index=1, command_raw="xxd blob", command_normalised="xxd <file>", tool="bash"
    )


def test_the_body_is_stored_whole_where_the_record_says_it_is(recorder):
    body = b"\x89PNG" + b"payload" * 5_000

    observation = a_step(recorder).end(exit_code=0, output=body, usage=USAGE)

    assert (recorder.run_dir / observation.ref).read_bytes() == body
    assert observation.nbytes == len(body)


def test_the_reference_is_relative_to_the_run_directory(recorder):
    """The stream and its bodies travel together — to a zip, to another machine — so a line
    carrying an absolute path would point at a directory that does not exist there."""
    observation = a_step(recorder).end(exit_code=0, output=b"x", usage=USAGE)

    assert not observation.ref.startswith("/")
    assert json.loads(recorder.stream_path.read_text().splitlines()[-1])["observation_ref"] == observation.ref


def test_the_line_stays_small_however_large_the_observation(recorder):
    """The fixed-size claim, which is what makes the stream cheap to parse after a long Run."""
    a_step(recorder).end(exit_code=0, output=b"z" * 2_000_000, usage=USAGE)

    assert len(recorder.stream_path.read_text().splitlines()[-1]) < 600


def test_the_model_is_shown_an_elided_body_while_the_file_keeps_all_of_it(recorder):
    body = b"HEAD\n" + b"m" * 20_000 + b"\nTAIL"

    observation = a_step(recorder).end(exit_code=0, output=body, usage=USAGE)

    assert "elided" in observation.shown
    assert len(observation.shown) < 800
    assert (recorder.run_dir / observation.ref).read_bytes() == body


def test_a_failing_tool_still_produces_an_observation(recorder):
    """The failure *is* the output. Recording silence instead would leave the model to narrate what
    it thinks happened."""
    observation = a_step(recorder).end(exit_code=127, output=b"binwalk: command not found\n", usage=USAGE)

    assert (recorder.run_dir / observation.ref).read_bytes() == b"binwalk: command not found\n"
    assert json.loads(recorder.stream_path.read_text().splitlines()[-1])["exit_code"] == 127
