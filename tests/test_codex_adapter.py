"""The seam that runs an Attempt, cut at the process boundary.

What is substituted here is **the bytes the CLI wrote**, never the adapter, so the real event-stream
parser runs in every test below. That is what makes ADR-0014's deterministic-schema promise testable
at all, and the canned streams are an artefact we produce anyway, since shadow mode replays stored
streams offline.

`CAPTURED` is not a fixture anyone invented: it is a real `codex exec --json` run against
`codex-cli` 0.147.0, kept verbatim — a warning item, reasoning, a message, a command that failed, a
command that failed differently, the command that worked, and the turn's own token count.
"""

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import pytest
from solver.codex import CLAIM, CLOSE, COMMAND, SWITCH, Child, Credential, Invocation, run_attempt
from solver.credentials import SECRETS
from solver.record import Recorder
from solver.redaction import Redactor
from solver.stall import Deadline

FLAG = "brunner{base64_is_not_encryption}"

CAPTURED = "\n".join(
    [
        '{"type":"thread.started","thread_id":"01a037f7-e4da-71c1-8d80-f7cc3b82ce44"}',
        '{"type":"item.completed","item":{"id":"item_0","type":"error","message":'
        '"Under-development features enabled: chronicle."}}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_2","type":"reasoning","text":'
        '"**Planning command execution sequence**"}}',
        '{"type":"item.completed","item":{"id":"item_3","type":"agent_message","text":'
        '"I will run the required failing check first, then decode the note."}}',
        '{"type":"item.started","item":{"id":"item_4","type":"command_execution","command":'
        '"/bin/zsh -lc \'cat missing.txt\'","aggregated_output":"","exit_code":null,"status":"in_progress"}}',
        '{"type":"item.completed","item":{"id":"item_4","type":"command_execution","command":'
        '"/bin/zsh -lc \'cat missing.txt\'","aggregated_output":"cat: missing.txt: No such file or directory\\n",'
        '"exit_code":1,"status":"failed"}}',
        '{"type":"item.started","item":{"id":"item_5","type":"command_execution","command":'
        '"/bin/zsh -lc \'base64 --decode note.b64\'","aggregated_output":"","exit_code":null,"status":"in_progress"}}',
        '{"type":"item.completed","item":{"id":"item_5","type":"command_execution","command":'
        '"/bin/zsh -lc \'base64 --decode note.b64\'","aggregated_output":"base64: invalid argument note.b64\\n",'
        '"exit_code":64,"status":"failed"}}',
        '{"type":"item.completed","item":{"id":"item_7","type":"agent_message","text":'
        '"The local base64 rejected the GNU syntax, so I am using its input flag."}}',
        '{"type":"item.started","item":{"id":"item_8","type":"command_execution","command":'
        '"/bin/zsh -lc \'base64 -d -i note.b64\'","aggregated_output":"","exit_code":null,"status":"in_progress"}}',
        '{"type":"item.completed","item":{"id":"item_8","type":"command_execution","command":'
        '"/bin/zsh -lc \'base64 -d -i note.b64\'","aggregated_output":"' + FLAG + '","exit_code":0,'
        '"status":"completed"}}',
        '{"type":"item.completed","item":{"id":"item_9","type":"agent_message","text":"Flag: `' + FLAG + '`"}}',
        '{"type":"turn.completed","usage":{"input_tokens":99141,"cached_input_tokens":93184,'
        '"cache_write_input_tokens":0,"output_tokens":477,"reasoning_output_tokens":79}}',
        "",
    ]
).encode()

EXHAUSTED_STREAM = (
    '{"type":"turn.started"}\n'
    '{"type":"turn.failed","error":{"message":"You\'ve hit your usage limit. '
    'Visit https://chatgpt.com/codex/settings/usage to purchase more credits"}}\n'
).encode()

# Every rung here is written with the same placeholder home, which `attempt` swaps for one under
# the Run's own directory — so a test that cares about `CODEX_HOME` says so and no other test
# quietly depends on a path outside `tmp_path`.
NOWHERE = Path("/nowhere")
SUBSCRIPTION = Credential(slot="codex-subscription", model="gpt-5-codex", home=NOWHERE)
METERED = Credential(slot="codex-metered", model="gpt-5", home=NOWHERE)

NOON = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)


class Canned(Child):
    """A `codex exec` that never ran: the bytes it would have written, handed back in blocks.

    `blocks` exists so a JSONL record can be split across two reads, which is the parser's one
    structural risk and a test that never splits one is a test of a parser nobody ships. `hangs` is
    a child that stops answering rather than closing its output, which is what the deadline meets.
    """

    def __init__(
        self,
        wrote: bytes = b"",
        *,
        exit_code: int | None = 0,
        errors: bytes = b"",
        blocks: int = 1,
        hangs: bool = False,
    ) -> None:
        size = max(len(wrote) // blocks, 1)
        self.written = [wrote[at : at + size] for at in range(0, len(wrote), size)]
        self.exit_code = exit_code
        self.errors = errors
        self.hangs = hangs
        self.stopped = False
        self.budgets: list[float] = []

    def read(self, budget: float) -> bytes | None:
        self.budgets.append(budget)
        if self.written:
            return self.written.pop(0)
        return None if self.hangs else b""

    def stop(self) -> None:
        self.stopped = True

    def close(self) -> tuple[int | None, bytes]:
        return self.exit_code, self.errors


class Launcher:
    """Stands where the fork would be, and keeps what it was asked for."""

    def __init__(self, *children: Child) -> None:
        self.children = list(children)
        self.argv: list[tuple[str, ...]] = []
        self.workdirs: list[Path] = []
        self.environments: list[dict[str, str]] = []
        self.prompts: list[bytes] = []

    def __call__(self, argv, workdir, environment, prompt) -> Child:
        self.argv.append(tuple(argv))
        self.workdirs.append(workdir)
        self.environments.append(dict(environment))
        self.prompts.append(prompt)
        return self.children.pop(0)


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))


def workdir(recorder) -> Path:
    """A sibling of the Run's record and never its parent — the shape `run_attempt` insists on."""
    made = Path(recorder.run_dir).parent / "work"
    made.mkdir(parents=True, exist_ok=True)
    return made


def attempt(recorder, launcher, *, chain=(SUBSCRIPTION,), minutes=10, home=None, **rest):
    home = home or Path(recorder.run_dir) / "codex"
    chain = tuple(replace(credential, home=home) if credential.home == NOWHERE else credential for credential in chain)
    return list(
        run_attempt(
            "recon says: one file, note.b64",
            workdir(recorder),
            Deadline(budget=NOON + dt.timedelta(minutes=minutes)),
            recorder=recorder,
            attempt_id="attempt-1",
            chain=chain,
            launch=launcher,
            now=lambda: NOON,
            **rest,
        )
    )


def records(recorder) -> list[dict]:
    return [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]


def kind(taken, wanted) -> list:
    return [step for step in taken if step.kind == wanted]


def bodies(recorder, folder: str) -> str:
    return "\n".join(sorted(path.read_text() for path in (Path(recorder.run_dir) / folder).iterdir()))


def test_a_real_stream_becomes_the_commands_codex_actually_ran(recorder):
    """The whole point of watching. Every command the vendor's agent ran is a Step of ours, with
    the exit status it got — including the two that failed, because a failure is an Observation."""
    taken = attempt(recorder, Launcher(Canned(CAPTURED)))

    assert [(step.command, step.exit_code) for step in kind(taken, COMMAND)] == [
        ("[codex] the CLI reported an error", None),
        ("/bin/zsh -lc 'cat missing.txt'", 1),
        ("/bin/zsh -lc 'base64 --decode note.b64'", 64),
        ("/bin/zsh -lc 'base64 -d -i note.b64'", 0),
    ]


def test_a_records_pair_is_written_for_every_command_including_the_one_in_flight(recorder):
    """ADR-0009's begin/end pair, over the vendor's own started/completed pair — so a command that
    the CLI announced and never finished is visible rather than absent."""
    attempt(recorder, Launcher(Canned(CAPTURED)))

    written = [record["record"] for record in records(recorder)]
    assert written.count("step-begin") == written.count("step-end")
    assert written[0] == "step-begin"


def test_the_model_never_writes_into_the_channel_a_flag_is_swept_from(recorder):
    """The load-bearing split. Codex *stated* the Flag in a message and *observed* it in real
    output; only the second may authorise a submit, so the two live in different directories."""
    attempt(recorder, Launcher(Canned(CAPTURED)))

    assert "Flag: `" in bodies(recorder, "claims")
    assert "Flag: `" not in bodies(recorder, "observations")
    assert FLAG in bodies(recorder, "observations")


def test_a_claim_carries_no_digest_and_spends_no_step(recorder):
    """A Claim is not a Step (`CONTEXT.md`), so it takes no index in the count its own stall is
    judged from — and it has no Observation digest, so nothing counting digests can count one."""
    claims = kind(attempt(recorder, Launcher(Canned(CAPTURED))), CLAIM)

    assert [claim.step_index for claim in claims] == [0, 0, 0, 0]
    assert {claim.digest for claim in claims} == {""}


def test_the_stream_line_for_a_claim_carries_no_word_of_the_prose(recorder):
    """ "A channel no check ever greps" has to hold for the JSONL too, or the sweep finds the
    model's sentence in the stream instead of in the directory it was kept out of."""
    attempt(recorder, Launcher(Canned(CAPTURED)))

    claimed = [record for record in records(recorder) if record["record"] == "claim"]
    assert claimed and all("Flag" not in json.dumps(record) for record in claimed)
    assert all(record["claim_ref"].startswith("claims/") for record in claimed)


def test_a_proposed_command_is_never_echoed_into_its_own_output(recorder):
    """An intention read back as a result is the second failure mode ADR-0014 closes by name. The
    Observation is the command's output and nothing else; the command is a field, not a body."""
    attempt(recorder, Launcher(Canned(CAPTURED)))

    assert "base64 -d -i" not in bodies(recorder, "observations")


def test_a_command_that_said_nothing_says_so(recorder):
    """An empty body reaches the model as blank space under a prompt, which reads as a command
    that was never run. What it said is that it had nothing to say."""
    silent = (
        '{"type":"item.completed","item":{"id":"a","type":"command_execution","command":"true",'
        '"aggregated_output":"","exit_code":0,"status":"completed"}}\n'
    ).encode()

    taken = attempt(recorder, Launcher(Canned(silent)))

    assert "no output" in kind(taken, COMMAND)[0].shown


def test_the_invocation_carries_the_flags_the_cli_cannot_start_without(recorder):
    """Both were found the hard way: the CLI refuses to run outside a git worktree, and the image
    is not one. The model id is config, so it is read from the rung rather than written here."""
    launcher = Launcher(Canned(CAPTURED))

    attempt(recorder, launcher)

    argv = launcher.argv[0]
    assert argv[:4] == ("codex", "exec", "--json", "--skip-git-repo-check")
    assert "gpt-5-codex" in argv
    assert "sandbox_workspace_write.network_access=true" in argv


def test_the_chain_order_and_the_model_are_config_and_never_code(recorder):
    """A practice Run leads with a different model and a different sandbox by passing different
    values, which is what "config value, never code" has to mean to be worth claiming."""
    launcher = Launcher(Canned(CAPTURED))

    attempt(
        recorder,
        launcher,
        chain=(Credential(slot="practice", model="gpt-5-mini", home=NOWHERE),),
        invocation=Invocation(sandbox="read-only", network=False, reasoning_effort="low"),
    )

    argv = launcher.argv[0]
    assert "gpt-5-mini" in argv and "read-only" in argv
    assert "sandbox_workspace_write.network_access=false" in argv
    assert "model_reasoning_effort=low" in argv


def test_the_prompt_goes_over_stdin_and_never_onto_the_command_line(recorder):
    """A working directory full of challenge-supplied code should not be able to read an Attempt's
    whole frame out of `/proc`, and a long recon block should never meet `ARG_MAX`."""
    launcher = Launcher(Canned(CAPTURED))

    attempt(recorder, launcher)

    assert launcher.argv[0][-1] == "-"
    assert launcher.prompts[0] == b"recon says: one file, note.b64"
    assert not any("recon says" in argument for argument in launcher.argv[0])


def test_codex_home_is_made_to_exist_rather_than_assumed(recorder, tmp_path):
    """Writable is not enough: against a missing path the CLI refuses to load configuration and
    never reaches the login at all."""
    home = tmp_path / "state" / "codex"
    launcher = Launcher(Canned(CAPTURED))

    attempt(recorder, launcher, home=home)

    assert home.is_dir()
    assert launcher.environments[0]["CODEX_HOME"] == str(home)


def test_a_home_that_cannot_be_made_hands_the_attempt_on_rather_than_ending_it(recorder, tmp_path):
    """A rung whose credential directory cannot exist is a rung we cannot use — which is a fact
    about our plumbing, and recording it as evidence about the Challenge is what ADR-0010 forbids."""
    blocked = tmp_path / "wall"
    blocked.write_text("not a directory")
    launcher = Launcher(Canned(CAPTURED))

    taken = attempt(
        recorder,
        launcher,
        chain=(Credential(slot="broken", model="gpt-5-codex", home=blocked / "codex"), METERED),
    )

    assert [step.slot for step in kind(taken, SWITCH)] == ["codex-metered"]
    assert kind(taken, CLOSE)[0].slot == "codex-metered"


def test_the_child_gets_the_allowlist_and_not_one_declared_secret(recorder, monkeypatch):
    """Codex's subprocess *is* the process running challenge-supplied code, as root, in this
    container. Every name below is one it would otherwise inherit."""
    for name in (*SECRETS, "CTFD_URL"):
        monkeypatch.setenv(name, "planted")
    monkeypatch.setenv("PATH", "/usr/bin")
    launcher = Launcher(Canned(CAPTURED))

    attempt(recorder, launcher)

    assert set(launcher.environments[0]) == {"PATH", "HOME", "TERM", "LANG", "CODEX_HOME"} & set(
        launcher.environments[0]
    )
    assert not {*SECRETS, "CTFD_URL"} & set(launcher.environments[0])
    assert launcher.environments[0]["PATH"] == "/usr/bin"


def test_codex_is_never_told_where_the_board_is(recorder, monkeypatch):
    """Half of "Codex never touches the Board" is that it holds no token; the other half is that it
    does not know the address either."""
    monkeypatch.setenv("CTFD_URL", "https://mirror-ctf.compfest.id")
    launcher = Launcher(Canned(CAPTURED))

    attempt(recorder, launcher)

    assert "mirror-ctf" not in json.dumps(launcher.environments[0])
    assert "mirror-ctf" not in " ".join(launcher.argv[0])


def test_a_record_split_across_two_reads_is_still_one_step(recorder):
    """Nothing guarantees a read ends on a newline, and a parser that assumes one drops the Step it
    was in the middle of."""
    taken = attempt(recorder, Launcher(Canned(CAPTURED, blocks=97)))

    assert [step.command for step in kind(taken, COMMAND)][1:] == [
        "/bin/zsh -lc 'cat missing.txt'",
        "/bin/zsh -lc 'base64 --decode note.b64'",
        "/bin/zsh -lc 'base64 -d -i note.b64'",
    ]


def test_the_deadline_kills_the_cli_and_the_kill_is_ours(recorder):
    """The model has no say in this and never learns it is coming. What it gets is a process that
    stops existing."""
    child = Canned(CAPTURED, hangs=True)

    taken = attempt(recorder, Launcher(child))

    assert child.stopped
    assert kind(taken, CLOSE)[0].shown.startswith("[codex] killed")


def test_a_killed_turn_says_its_cost_is_unknown_rather_than_zero(recorder):
    """The vendor meters a turn on `turn.completed` and on nothing before it, so a turn the deadline
    killed reports nothing — and a record that wrote that down as a spend of zero was blindest about
    the turns that ran longest (#104). The commands inside the turn keep their zeros, which are the
    accounting rule rather than a gap: the turn's tokens ride the invocation's own Step."""
    attempt(recorder, Launcher(Canned(CAPTURED.rsplit(b"\n", 2)[0], hangs=True)))

    ended = [record for record in records(recorder) if record["record"] == "step-end"]
    invocation = next(record for record in ended if record["tool"] == "codex" and record["step_index"] == 1)
    assert (invocation["tokens_in"], invocation["tokens_out"]) == (0, 0)
    assert invocation["usage_known"] is False
    assert all(record["usage_known"] is True for record in ended if record is not invocation)


def test_a_turn_the_vendor_reported_says_its_cost_is_known(recorder):
    """The other half of the pair, and the one that makes the first mean anything: a turn that
    reported is marked as reported, so `usage_known` never becomes a synonym for `killed`."""
    attempt(recorder, Launcher(Canned(CAPTURED)))

    ended = [record for record in records(recorder) if record["record"] == "step-end"]

    assert all(record["usage_known"] is True for record in ended)


def test_a_cli_that_never_started_spent_nothing_rather_than_an_unknown_amount(recorder):
    """A binary that is not there ran no model, so its zeros are the fact. Marking them unknown
    would put a failure to launch in the same column as an expensive turn nobody counted."""

    def missing(*_):
        raise OSError(2, "No such file or directory")

    attempt(recorder, missing)

    ended = [record for record in records(recorder) if record["record"] == "step-end"]

    assert all(record["usage_known"] is True for record in ended)


def test_a_command_in_flight_when_the_kill_lands_is_ended_as_killed(recorder):
    """Left dangling it would be indistinguishable from a crash. A Step that says it was killed is
    strictly more than one that says nothing."""
    started = (
        '{"type":"item.started","item":{"id":"a","type":"command_execution","command":"nc 10.0.0.1 1337",'
        '"aggregated_output":"","exit_code":null,"status":"in_progress"}}\n'
    ).encode()

    attempt(recorder, Launcher(Canned(started, hangs=True)))

    ended = [record for record in records(recorder) if record["record"] == "step-end"]
    assert any(record["command_raw"] == "nc 10.0.0.1 1337" and record["exit_code"] is None for record in ended)


def test_a_deadline_already_spent_never_spawns_a_second_read(recorder):
    """The budget handed to the child is what is left of the Attempt, so an Attempt with nothing
    left does not get one more command's worth of grace."""
    child = Canned(CAPTURED)

    attempt(recorder, Launcher(child), minutes=-1)

    assert child.budgets == []
    assert child.stopped


def test_credential_exhaustion_hands_the_attempt_over_rather_than_cutting_it(recorder):
    """Our billing is never recorded as evidence about the Challenge. The next rung gets the
    Challenge, the Instance and the Observations — not the model's context, which is the reset."""
    launcher = Launcher(Canned(EXHAUSTED_STREAM, exit_code=1), Canned(CAPTURED))

    taken = attempt(recorder, launcher, chain=(SUBSCRIPTION, METERED))

    switch = kind(taken, SWITCH)[0]
    assert "codex-subscription is exhausted" in switch.command and switch.slot == "codex-metered"
    assert launcher.argv[1][launcher.argv[1].index("--model") + 1] == "gpt-5"
    assert kind(taken, CLOSE)[0].shown.startswith("[codex] stopped")


def test_every_switch_is_written_to_the_stream(recorder):
    """ "Why did quality fall off after 13:00" is a question only the record can answer."""
    attempt(
        recorder,
        Launcher(Canned(EXHAUSTED_STREAM, exit_code=1), Canned(CAPTURED)),
        chain=(SUBSCRIPTION, METERED),
    )

    written = [record.get("command_raw", "") for record in records(recorder)]
    assert "[codex] codex-subscription is exhausted — the Attempt goes to codex-metered" in written


def test_a_spent_chain_closes_the_invocation_and_does_not_loop(recorder):
    """Exhaustion is a stall and not an ending, but a chain with no rung left has nowhere to hand
    the Attempt to — and the cause has to say which of the two happened."""
    taken = attempt(recorder, Launcher(Canned(EXHAUSTED_STREAM, exit_code=1)))

    assert kind(taken, SWITCH) == []
    assert kind(taken, CLOSE)[0].shown.startswith("[codex] exhausted")


def test_a_turn_that_merely_failed_is_not_treated_as_exhaustion(recorder):
    """Handing the Attempt on for every failure would hide a broken adapter behind a credential
    switch, and the circuit breaker exists to see exactly that."""
    broke = ('{"type":"turn.failed","error":{"message":"stream disconnected before completion"}}\n').encode()

    taken = attempt(recorder, Launcher(Canned(broke, exit_code=1), Canned(CAPTURED)), chain=(SUBSCRIPTION, METERED))

    assert kind(taken, SWITCH) == []
    assert kind(taken, CLOSE)[0].shown.startswith("[codex] failed")


def test_a_cli_that_is_not_installed_is_a_failure_of_ours_and_says_so(recorder):
    """The adapter running on a machine without the binary is the shape a zero-Step Attempt takes,
    and an anonymous tool error is what would make it unreadable."""

    def missing(*_):
        raise OSError(2, "No such file or directory")

    taken = attempt(recorder, missing)

    assert "did not run" in kind(taken, CLOSE)[0].shown


def test_the_turns_tokens_land_on_the_invocation_and_never_on_a_command(recorder):
    """The vendor meters a turn, not a command, so summing tokens over an Attempt's Steps gives
    the truth — where splitting the turn across its commands would give a fabrication."""
    attempt(recorder, Launcher(Canned(CAPTURED)))

    ended = [record for record in records(recorder) if record["record"] == "step-end"]
    invocation = next(record for record in ended if record["tool"] == "codex" and record["step_index"] == 1)
    assert (invocation["tokens_in"], invocation["cache_read"], invocation["tokens_out"]) == (5957, 93184, 477)
    assert sum(record["tokens_out"] for record in ended) == 477
    assert {record["model"] for record in ended} == {"gpt-5-codex"}


def test_the_attempt_keeps_counting_from_where_recon_stopped(recorder):
    """Recon *is* the opening of an Attempt, so its probes were this Attempt's first Steps and a
    count that restarted here would make an Attempt look half as long as it was."""
    taken = attempt(recorder, Launcher(Canned(CAPTURED)), first_step=12)

    assert [step.step_index for step in taken if step.kind != CLAIM] == [13, 14, 15, 16, 12]


def test_an_item_type_nobody_has_met_is_prose_until_proven_otherwise(recorder):
    """The safe side of the one mistake that matters: a Claim swept as an Observation is what
    authorises a fabricated Flag, and the opposite mistake only under-counts."""
    unheard_of = (
        '{"type":"item.completed","item":{"id":"z","type":"daydream","text":"the flag is probably ' + FLAG + '"}}\n'
    ).encode()

    taken = attempt(recorder, Launcher(Canned(unheard_of)))

    assert [step.tool for step in kind(taken, CLAIM)] == ["daydream"]
    assert FLAG not in bodies(recorder, "observations")


def test_a_tool_call_that_is_not_a_shell_command_is_still_a_step(recorder):
    """A search or a patch moved the world as surely as a command did, and a counter that cannot
    see it reads an Attempt that was working as an Attempt that was idle."""
    searched = '{"type":"item.completed","item":{"id":"w","type":"web_search","query":"wiener attack"}}\n'.encode()

    taken = attempt(recorder, Launcher(Canned(searched)))

    assert kind(taken, COMMAND)[0].tool == "web_search"
    assert kind(taken, COMMAND)[0].command == "[codex] web_search"


def test_what_the_model_composed_is_kept_and_still_never_swept(recorder):
    """A query the model wrote and a patch it authored are the model writing. The record keeps
    both — nothing is lost — but the channel a Flag is swept from carries neither."""
    patched = (
        '{"type":"item.completed","item":{"id":"p","type":"file_change","changes":'
        '[{"path":"/tmp/notes.txt","kind":"add"}],"diff":"+' + FLAG + '"}}\n'
    ).encode()

    attempt(recorder, Launcher(Canned(patched)))

    assert FLAG in bodies(recorder, "claims")
    assert FLAG not in bodies(recorder, "observations")


def test_a_server_that_answered_is_output_and_is_swept_like_any_other(recorder):
    """The one part of a tool call that is not the model writing. A server's answer is the same
    class of thing as a shell command's stdout, so it belongs where a Flag can be found."""
    called = (
        '{"type":"item.completed","item":{"id":"m","type":"mcp_tool_call","server":"oracle",'
        '"tool":"decrypt","result":"' + FLAG + '","status":"completed"}}\n'
    ).encode()

    attempt(recorder, Launcher(Canned(called)))

    assert FLAG in bodies(recorder, "observations")


def test_an_in_progress_update_is_never_counted_as_a_second_step(recorder):
    """The completion carries the whole aggregated output, so acting on both would count one
    command twice and hand the repetition counter a Step that never happened."""
    twice = (
        '{"type":"item.started","item":{"id":"a","type":"command_execution","command":"ls",'
        '"aggregated_output":"","exit_code":null,"status":"in_progress"}}\n'
        '{"type":"item.updated","item":{"id":"a","type":"command_execution","command":"ls",'
        '"aggregated_output":"flag.txt","exit_code":null,"status":"in_progress"}}\n'
        '{"type":"item.completed","item":{"id":"a","type":"command_execution","command":"ls",'
        '"aggregated_output":"flag.txt\\n","exit_code":0,"status":"completed"}}\n'
    ).encode()

    taken = attempt(recorder, Launcher(Canned(twice)))

    assert [step.command for step in kind(taken, COMMAND)] == ["ls"]


def test_the_cli_failing_outside_an_item_is_still_a_failure_of_a_tool(recorder):
    """Found on a live Attempt: a rejected model id arrives as a bare `error` event rather than as
    an item, and treating it as prose would file the one thing that explained the Run as a Claim."""
    rejected = ('{"type":"error","message":"The \'gpt-5-codex\' model is not supported for this account"}\n').encode()

    taken = attempt(recorder, Launcher(Canned(rejected, exit_code=1)))

    assert "is not supported" in kind(taken, COMMAND)[0].shown
    assert kind(taken, CLAIM) == []


def test_a_quota_reported_outside_a_turn_still_hands_the_attempt_on(recorder):
    """The exhaustion read has to cover every channel the CLI says it through, or a spent
    subscription is recorded as a failed Attempt and the Challenge wears our billing."""
    spent = '{"type":"error","message":"You\'ve hit your usage limit."}\n'.encode()

    taken = attempt(recorder, Launcher(Canned(spent, exit_code=1), Canned(CAPTURED)), chain=(SUBSCRIPTION, METERED))

    assert [step.slot for step in kind(taken, SWITCH)] == ["codex-metered"]


def test_a_line_the_parser_cannot_read_goes_where_an_unclassified_thing_is_safe(recorder):
    """A line that is not a record is a line we cannot classify, and presuming it is output is the
    presumption that ends with a sentence swept as though a command had produced it."""
    noise = b"warning: something the CLI said to a human\n"

    taken = attempt(recorder, Launcher(Canned(noise)))

    assert kind(taken, CLAIM)[0].shown.startswith("warning:")
    assert "warning:" not in bodies(recorder, "observations")


def test_the_clis_own_last_words_reach_the_record_when_it_fails(recorder):
    """A failed invocation with no trace of why is the one shape a post-mortem cannot work with."""
    taken = attempt(recorder, Launcher(Canned(b"", exit_code=1, errors=b"error sending request for url")))

    assert "error sending request for url" in kind(taken, CLOSE)[0].shown


def test_a_working_directory_holding_the_record_is_refused_before_anything_is_spent(recorder):
    """The sandbox makes the workdir the one place the vendor's agent may write. A Run that put its
    stream in there would hand the model the file its own stall is judged from."""
    with pytest.raises(ValueError, match="own record"):
        run_attempt(
            "recon says: nothing",
            Path(recorder.run_dir),
            Deadline(budget=NOON + dt.timedelta(minutes=10)),
            recorder=recorder,
            attempt_id="attempt-1",
            chain=(SUBSCRIPTION,),
            launch=Launcher(Canned(CAPTURED)),
        )


def test_a_chain_with_no_rung_is_our_defect_and_arrives_as_one(recorder):
    """A missing chain is a misconfiguration rather than an exhaustion, and the circuit breaker has
    to be able to see it — so it arrives as a Step like any other failure."""
    taken = attempt(recorder, Launcher(), chain=())

    assert "no credential was configured" in kind(taken, CLOSE)[0].command
