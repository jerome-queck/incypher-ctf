"""A whole Run written through the recorder, swept for the credential that was planted in it.

`/state` gets copied, zipped and pasted into issues, so
[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) puts redaction at
write time rather than at publication. What that costs, and what it must never cost, are both here:
the Flag field is exempt so a Flag can never be silently destroyed, and the digest is taken over the
redacted bytes so it never becomes an oracle for what was removed.
"""

import base64
import json
import urllib.parse

from solver.observation import digest_of
from solver.record import Recorder, Usage
from solver.redaction import Redactor

PLANTED = "sk-ant-oat01-" + "p" * 32  # gitleaks:allow - planted by this test, opens nothing
# The alignment that a naive encoder misses: base64 works three bytes at a time, so the token's own
# encoding does not appear inside this one. A leak here would be a whole credential in a header.
BASIC = base64.b64encode(f"solver:{PLANTED}".encode()).decode()
USAGE = Usage(model="claude-opus-5", tokens_in=10, tokens_out=1)


def a_run(state, redactor, *, flag: str = "brunner{ordinary}") -> Recorder:
    """A Run written end to end, with the planted credential pushed through every door it has."""
    recorder = Recorder(state, run_id="run-leak-sweep", redactor=redactor)
    recorder.run_open(board_profile={"url": "https://board.example", "token_seen": PLANTED})
    recorder.attempt_open(
        attempt_id="attempt-1",
        challenge_id=7,
        challenge_name="Leaky",
        category="web",
        challenge_type="standard",
        solves_at_open=3,
        tier=1,
        budget_s=600,
        attempt_sequence=1,
        instance_until=None,
        order_ranks={"7": 1},
    )
    recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw=f"curl -H 'Authorization: Token {PLANTED}' https://board.example/api",
        command_normalised="curl <url>",
        tool="bash",
    ).end(
        exit_code=0,
        output=(
            f"> Authorization: Token {PLANTED}\n"
            f"> Authorization: Basic {BASIC}\n"
            f"> X-Copy: {base64.b64encode(PLANTED.encode()).decode()}\n"
            f"> ?key={urllib.parse.quote(PLANTED, safe='')}\n"
        ).encode(),
        usage=USAGE,
        checkpoint=f"curl -H 'Authorization: Token {PLANTED}' https://board.example/api",
    )
    recorder.attempt_close(
        attempt_id="attempt-1",
        cause="flag",
        approach_label="read the verbose transcript",
        solves_at_close=4,
        extensions_granted=0,
        flag=flag,
    )
    recorder.run_close(cause="window-closed")
    return recorder


def test_a_planted_secret_reaches_neither_the_stream_nor_an_observation_body(tmp_path):
    """The sweep is over every byte under `/state`, not over the stream alone — a body file is the
    easier thing to forget and the larger thing to leak."""
    a_run(tmp_path, Redactor.for_declared_secrets({"CTFD_API_TOKEN": PLANTED}))

    written = [path for path in tmp_path.rglob("*") if path.is_file()]

    encoded_runs = {BASIC[at : at + 16] for at in range(len(BASIC) - 15)}

    assert written, "the sweep must have something to sweep"
    for path in written:
        held = path.read_bytes().decode(errors="replace")
        assert PLANTED not in held, f"the planted credential survived into {path.name}"
        assert not [run for run in encoded_runs if run in held], f"a usable run of it survived into {path.name}"


def test_the_flag_field_is_the_one_thing_redaction_never_touches(tmp_path):
    """A Flag that a declared value happened to sit inside would be silently destroyed, and a
    destroyed Flag is unrecoverable. It is also the one thing the sweep above cannot assert, which
    is why the contrived Flag is here and not there — the two rules meet only in this case."""
    planted_flag = "brunner{" + PLANTED + "}"
    recorder = a_run(tmp_path, Redactor.for_declared_secrets({"CTFD_API_TOKEN": PLANTED}), flag=planted_flag)

    closed = [json.loads(line) for line in recorder.stream_path.read_text().splitlines()][-2]

    assert closed["record"] == "attempt-close"
    assert closed["flag"] == planted_flag


def test_the_digest_is_over_the_redacted_bytes_so_it_is_never_an_oracle(tmp_path):
    """Novelty is a rule over digests, and a digest over the raw bytes would let anyone holding the
    stream confirm a guessed credential offline."""
    recorder = Recorder(tmp_path, run_id="run-1", redactor=Redactor({"CTFD_API_TOKEN": PLANTED}))

    observation = recorder.step_begin(
        attempt_id="attempt-1", step_index=1, command_raw="env", command_normalised="env", tool="bash"
    ).end(exit_code=0, output=f"CTFD_API_TOKEN={PLANTED}".encode(), usage=USAGE)

    assert observation.digest == digest_of(b"CTFD_API_TOKEN=[redacted:CTFD_API_TOKEN]")
    assert observation.digest != digest_of(f"CTFD_API_TOKEN={PLANTED}".encode())
