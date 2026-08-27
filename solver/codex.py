"""The seam that runs an Attempt: `codex exec --json` spawned, watched, and turned into Steps.

[ADR-0014](../docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md) is
the whole design. `codex exec` has no door marked *just answer* — verified against 0.147.0, nothing
removes its shell — so the vendor's agent drives its own loop at full strength with the image's
real tools, and we watch the event stream it emits. That is what keeps
[ADR-0005](../docs/adr/0005-the-stall-call-lives-outside-the-solving-model.md) alive rather than
what breaks it: the counters still read only Observations, and the stream is where those
Observations now come from.

**This module owns exactly two things: how the CLI is spawned, and how its output becomes Steps.**
It spawns it two ways — `run_attempt`, which sets the agent to work, and `asking`, which puts a
question to it with as little to act on as the CLI permits (Triage's judge, ADR-0006).
The counters, the deadline, the kill and the Board are the orchestrator's, and none of them is
computed here. There is no abstract base — the shape below is one two files happen to share, and
`Taken` is the deterministic schema every adapter emits, load-bearing because two differently
shaped streams cannot be compared and comparing them is half of what per-model measurement is for.

Four rules shape the parser, and each is a refusal:

- **A Claim never lands in the Observation channel.** Everything the model said and everything it
  composed — its messages, its reasoning, its plan, the query behind a search, the text of a patch
  it wrote — goes to `claims/`, which Flag verification never sweeps. Anything this module cannot
  classify goes there too: a Claim mistaken for an Observation is what would authorise a fabricated
  Flag, and the opposite mistake only under-counts.
- **A proposed command is never echoed where it could be read back as output.** A command Step's
  Observation body is the command's own output and nothing else; the command itself lives in the
  record's `command_raw` field, which is not a body and is never swept.
- **A failure is an Observation.** A CLI that errored, a turn that failed, a rung that could not be
  used — each is written down as the output of the Step that met it, because silence would leave
  the model to narrate what it thinks happened (`CONTEXT.md`, *Observation*).
- **The child gets the allowlist environment and nothing else** (`solver/credentials.py`). Codex's
  subprocess is the process running challenge-supplied code, as root, in this container — so it
  holds no CTFd token, no LLM key and not even the Board's URL, which is the mechanism behind
  ADR-0014's *Codex never touches the Board*.

The credential the CLI *does* hold is a file, not a variable: `codex login` writes `auth.json`
under `CODEX_HOME`, and exporting a key does not log it in (`docs/credentials.md`). So a rung of
the chain is a directory that has already been logged in, this module only points at it, and the
one thing it must guarantee is that the directory **exists** — against a missing path the CLI
refuses to load configuration at all, and writable is not enough.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import select
import signal
import subprocess
import time
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from solver.credentials import CHILD_ENVIRONMENT
from solver.record import Recorder, Step, Usage
from solver.stall import Deadline

# Every line this module writes about itself opens with this, so a reader of a stream can tell what
# the adapter said from what a tool said — the same convention `solver/recon.py` holds, and for the
# same reason: none of these is a command anyone could replay in a shell.
MARK = "[codex]"

# The tool name the adapter puts on its **own** Steps — the spawn, a failure the CLI reported about
# itself, a switch to the next rung. Named rather than repeated, because the stall call has to be
# able to tell them from the model working the Challenge: one spawn per rung is the same command
# line every time, and a chain switch is not the model looping (`solver/stall.py`).
ADAPTER = "codex"

# What a `Taken` is. Four kinds, closed, and the same four whatever vendor is behind them.
# `COMMAND` is the only one the stall counters may read, because it is the only one that is an
# Observation; `CLAIM` is the model talking and is evidence of nothing.
COMMAND = "command"
CLAIM = "claim"
SWITCH = "switch"
CLOSE = "close"

# Why one invocation of the CLI ended. **Not** Cut causes — a Cut is the orchestrator's decision
# over a whole Attempt and its vocabulary is closed in `CONTEXT.md`. `STOPPED` in particular is not
# an ending: the vendor's agent ending its turn with budget left is a new Attempt (ADR-0014), and
# treating it as the end of one would hand the model the give-up button ADR-0005 removed.
STOPPED = "stopped"
KILLED = "killed"
FAILED = "failed"
EXHAUSTED = "exhausted"
UNUSABLE = "unusable"

# The two causes that are about our billing rather than about the Challenge, so the Attempt moves
# to the next rung instead of ending. ADR-0010 rules exhaustion a stall and explicitly not a Cut.
HANDS_ON = (EXHAUSTED, UNUSABLE)

# On the host mount, so a container restarted after a reboot inherits the login instead of asking
# for one at 13:00 with nobody there (`docs/credentials.md`).
CODEX_HOME = Path("/state/codex")

# Matched lowercased against what the CLI said, and deliberately short. The first is the sentence
# the CLI itself carries for a spent subscription.
#
# The third is broader than exhaustion and is here anyway, because the two mistakes cost different
# amounts. The chain is re-read from the top on **every** Attempt, so reading a transient 429 as
# exhaustion costs one Attempt starting a rung lower — where reading a spent subscription as an
# ordinary failure records our own billing as a Cut against the Challenge, which is what ADR-0010
# forbids. The CLI has also already retried internally by the time it surfaces one.
EXHAUSTED_SAYS = ("you've hit your usage limit", "usage limit reached", "rate limit")

# The vendor's item types mapped onto tool names that mean the same thing whoever emits them —
# half of what makes two adapters' streams comparable. Anything not here is prose until proven
# otherwise; see `_watched`.
TOOLS = {
    "command_execution": "shell",
    "file_change": "file_change",
    "mcp_tool_call": "mcp",
    "web_search": "web_search",
}

# Enough to carry the CLI's own last words about why it failed. Its stderr is diagnostics, not
# Observations, so what is kept is a tail rather than the stream.
STDERR_KEPT_BYTES = 4096
READ_BLOCK_BYTES = 65536

# After EOF on its stdout the CLI has said everything it is going to say, so anything past this
# grace is a process that will not exit rather than one still working. Longer than the cascade's
# (`solver/recon.py`) on purpose: what is shutting down here is an agent, its shell and its
# sandbox, where there a single tool is closing a pipe.
REAP_GRACE_SECONDS = 2.0

# The longest a single read waits before the deadline is looked at again. The deadline is an object
# precisely so the orchestrator can move it while a child is running — a Checkpoint lengthens it, a
# stall or a signal shortens it — and a read that blocked for the whole remaining budget could not
# hear either. Measured: `docker stop`'s ten-second grace expired with a turn in flight and PID 1
# was killed with the reserved tail unrun.
WATCH_SLICE_SECONDS = 1.0


@dataclass(frozen=True)
class Credential:
    """One rung of the chain (ADR-0010): a slot name, a model id, and a logged-in `CODEX_HOME`.

    All three are config values and none is code, which is what lets a practice Run lead with a
    different model, or the scored Run fall through to metered billing, without a rebuild. There is
    no key field on purpose: the CLI authenticates by file, and a key in the child's environment
    would be a secret in the environment of the process running challenge-supplied code.
    """

    slot: str
    model: str
    home: Path = CODEX_HOME


@dataclass(frozen=True)
class Invocation:
    """How the CLI is spawned — every value here is config, and the chain's order is the caller's.

    `--skip-git-repo-check` is not optional and so is not here: the CLI refuses to start outside a
    git worktree and the image is not one.
    """

    executable: str = "codex"
    # **The container is the only boundary, and this is where that is admitted.** `workspace-write`
    # is what the image ships `bubblewrap` for, and bubblewrap cannot build a sandbox inside an
    # unprivileged container: measured every way on 26 August 2026, it fails at
    # `bwrap: No permissions to create a new namespace` and, with seccomp relaxed and `SYS_ADMIN`
    # granted, at `bwrap: Failed to make / slave`. Landlock is refused as incompatible. The only two
    # configurations that run a command at all are `--privileged` with bubblewrap, and this one.
    #
    # This one, because `--privileged` spends the container boundary to buy a smaller one inside it,
    # and ADR-0008 is already explicit that the container boundary *is* the isolation here. v1
    # accepts what that costs and names it rather than hiding it (ADR-0018): challenge code runs as root with
    # nothing between it and `/state`, so *the Observation log is orchestrator-append-only* is a
    # claim about the model's cooperation rather than a fact about the filesystem. `run_attempt`
    # still refuses to put the record inside the working directory, which holds the accidental case
    # and not a determined one. The boundary that closes it is v2's uid separation
    # (`docs/credentials.md`), which is the same boundary that closes the credential on disk.
    sandbox: str = "danger-full-access"
    # Off by default in the sandbox, and a Challenge whose Target is a socket is unsolvable without
    # it. ADR-0014 makes web search a Board profile value for the same reason it is on here.
    network: bool = True
    # Passed on every invocation rather than left to whatever `config.toml` the credential
    # directory happens to hold — a dial nobody set is a dial that moves when a login is retaken.
    # Spec #63 pins it to a constant so a later version tunes a number rather than reshaping this.
    reasoning_effort: str = "medium"
    # ADR-0014 makes web search a Board profile value, default on: every advantage counts on a
    # 5.5-hour clock, and a Challenge shipping an image or an audio clip is often solvable only by
    # looking something up. A Board whose rules withdraw it sets `web_search` false in its tracked
    # profile, and it is passed either way for the reason `reasoning_effort` is.
    web_search: bool = True


@dataclass(frozen=True)
class Taken:
    """One thing the adapter watched happen, in the one shape every adapter emits.

    `digest` and `nbytes` describe an **Observation** and are empty on anything else, which is the
    Claim/Observation split made structural: a Claim has no digest, so nothing that counts digests
    can accidentally count one. `command` is what was run for a `COMMAND` and a `MARK` line for
    everything else — never output, and never somewhere output is read from.
    """

    kind: str
    slot: str
    model: str
    step_index: int
    tool: str
    command: str
    exit_code: int | None
    shown: str
    digest: str = ""
    nbytes: int = 0


class Child:
    """One `codex exec` in flight, as the adapter watches it: bytes, a kill, and how it ended.

    The seam is here rather than at the adapter, so the substitution a test makes is **the bytes
    the CLI wrote** and the real parser runs in every test that touches an Attempt. `read` answering
    `None` is the deadline arriving, which is what lets the kill path be exercised without a clock.
    """

    def read(self, budget: float) -> bytes | None:
        """The next bytes of stdout, `b""` at end of output, or `None` when the budget ran out."""
        raise NotImplementedError

    def stop(self) -> None:
        """Kill it and everything it spawned. The orchestrator's, never the model's."""
        raise NotImplementedError

    def close(self) -> tuple[int | None, bytes]:
        """Reap it, and answer with its exit code and the tail of its stderr."""
        raise NotImplementedError


# The adapter's one edge to a process: it is handed the argv, the working directory, the allowlist
# environment and the prompt, and answers with something to watch. Injectable because that is the
# honest place to stand a test — above it is this module's parser, below it is a fork.
Launch = Callable[[Sequence[str], Path, Mapping[str, str], bytes], Child]


def run_attempt(
    prompt: str,
    workdir: Path,
    deadline: Deadline,
    *,
    recorder: Recorder,
    attempt_id: str,
    chain: Sequence[Credential],
    invocation: Invocation = Invocation(),
    first_step: int = 1,
    launch: Launch | None = None,
    now: Callable[[], dt.datetime] | None = None,
) -> Iterator[Taken]:
    """Run one Attempt and stream what happened, Step by Step, as it happens.

    The three positional parameters are the seam ADR-0014 specifies and nothing has been added to
    them: the prompt the orchestrator composed, the working directory that **is** the memory
    carried between Attempts, and the moment this Attempt is killed. Everything after the star is
    plumbing — where to write, what to call this Attempt, and which rungs are available.

    The deadline is an object rather than a timestamp because the orchestrator may move it while a
    child is already running: a Checkpoint buys the kill deadline and never a Step, since the
    vendor's agent takes its next turn without asking (ADR-0005 as ADR-0014 amends it). It is read
    on every pass of the loop below and computed nowhere here.

    `first_step` continues the Attempt's numbering rather than restarting it: recon opened this
    Attempt and its probes were its first Steps.

    Refuses loudly, before anything is spent, if the Run's own record sits inside the working
    directory. The sandbox makes the workdir the one place the vendor's agent may write, so a Run
    that put its stream in there would be handing the model the file its own stall is judged from —
    and "the Observation log is orchestrator-append-only" would be a sentence rather than a fact.
    """
    root = Path(workdir).resolve()
    if Path(recorder.run_dir).resolve().is_relative_to(root):
        raise ValueError(f"the working directory {root} holds this Run's own record, which the model could rewrite")
    transcript = _Transcript(
        recorder=recorder,
        attempt_id=attempt_id,
        workdir=Path(workdir),
        invocation=invocation,
        step=first_step - 1,
        launch=launch or _spawn,
        now=now or _utcnow,
    )
    return transcript.run(prompt, deadline, tuple(chain))


def asking(
    credential: Credential,
    *,
    recorder: Recorder,
    workdir: Path,
    attempt_id: str = "triage",
    seconds: float = 180.0,
    launch: Launch | None = None,
    now: Callable[[], dt.datetime] | None = None,
) -> Callable[[str], str]:
    """The CLI asked a question rather than set to work — a prompt in, the model's prose out.

    The second of this module's two spawn shapes, here for the reason the first one is: **how the
    CLI is spawned** is what this module owns, and a caller that assembled its own argv would be a
    caller holding the invocation rules ADR-0014 put here. Triage is its one consumer today, and the
    seam it plugs into is a plain `Callable[[str], str]` so that nothing above imports this module
    to have a judge.

    What is given up is as much as the CLI allows, which is less than "no tools".

    **The shell cannot be taken away from it.** ADR-0014 measured that: `codex exec` has no door
    marked *just answer*, and nothing removes its tools. So what keeps this judgement off the Board
    is not a flag but the three things the invocation withholds — a **read-only sandbox**, so
    nothing it does changes anything; **no network**, so no Board, no Instance and no submission is
    reachable at all; and the allowlist environment every child gets, which holds no CTFd token and
    not even the Board's URL (`solver/credentials.py`). A judge cannot spend a submission slot it
    has no address for.

    The residual is named rather than hidden, in the same spirit as the vendor's own context
    compaction: a read-only sandbox can still *read*, so a judge that went looking could open a
    file under `/state`. What is guaranteed here is narrower and is the thing that matters — Triage
    itself never opens one, and the working directory it is pointed at holds nothing.

    Everything the judge says lands in `claims/`, which Flag verification never sweeps, and the
    tokens it spends are counted by the Steps the invocation writes.
    """
    clock = now or (lambda: dt.datetime.now(dt.timezone.utc))

    def ask(prompt: str) -> str:
        # The CLI is spawned *in* this directory, so it has to exist — and it stays empty, because
        # pointing the judge at the Run's own files would hand a judgement the contents Triage is
        # defined not to read. A directory that cannot be made ends as no answer at all, which every
        # Challenge then records as `unjudged`: visible, and never a reason to lose the other Tiers.
        try:
            workdir.mkdir(parents=True, exist_ok=True)
        except OSError:
            return ""
        deadline = Deadline(budget=clock() + dt.timedelta(seconds=seconds))
        said = run_attempt(
            prompt,
            workdir,
            deadline,
            recorder=recorder,
            attempt_id=attempt_id,
            chain=(credential,),
            invocation=Invocation(sandbox="read-only", network=False),
            launch=launch,
            now=clock,
        )
        return "\n".join(taken.shown for taken in said if taken.kind == CLAIM)

    return ask


@dataclass
class _Flight:
    """A Step of this Attempt that has begun and not yet ended — the handle, and what it is."""

    step: Step
    index: int
    tool: str
    command: str


@dataclass
class _Transcript:
    """One Attempt in flight: the Steps it has spent, the invocations it has made, and the one
    place a line of the vendor's stream becomes a record."""

    recorder: Recorder
    attempt_id: str
    workdir: Path
    invocation: Invocation
    step: int
    launch: Launch
    now: Callable[[], dt.datetime]
    # Per-invocation, reset at every spawn.
    _usage: dict[str, int] = field(default_factory=dict)
    _flights: dict[str, _Flight] = field(default_factory=dict)
    _said: list[str] = field(default_factory=list)
    _broke: bool = False
    _spawned: bool = False

    def run(self, prompt: str, deadline: Deadline, chain: Sequence[Credential]) -> Iterator[Taken]:
        """Every rung in turn, until one of them ends the Attempt or the chain is spent."""
        remaining = list(chain)
        if not remaining:
            # A chain with no rungs is a misconfiguration rather than an exhaustion, and it is the
            # orchestrator's circuit breaker that has to see it — so it arrives as a Step like any
            # other failure, and the Attempt closes with nothing having been spent.
            yield self._alone(f"{MARK} no credential was configured for this Attempt", EXHAUSTED, kind=CLOSE)
            return
        while remaining:
            credential = remaining.pop(0)
            cause, closed = yield from self._invoke(prompt, deadline, credential)
            if cause not in HANDS_ON or not remaining:
                yield closed
                return
            yield self._handed(credential, remaining[0], cause, closed.shown)

    def _invoke(
        self, prompt: str, deadline: Deadline, credential: Credential
    ) -> Generator[Taken, None, tuple[str, Taken]]:
        """One spawn of the CLI, watched to its end. Yields Steps; answers with how it ended.

        The invocation is itself a Step, and its `step-begin` is written before the child exists.
        That is what makes a CLI that hung visible rather than absent — the pair is the whole point
        of ADR-0009's begin/end — and it is where the turn's tokens land, since the vendor meters a
        turn and never a command.
        """
        self._usage, self._flights, self._said = {}, {}, []
        self._broke, self._spawned = False, False
        argv = _argv(credential, self.invocation)
        flight = self._open(" ".join(argv), "codex")
        if refused := _could_not_make(credential.home):
            return UNUSABLE, self._shut(flight, credential, None, refused.encode(), kind=CLOSE)
        try:
            child = self.launch(argv, self.workdir, _environment(credential), prompt.encode())
        except OSError as error:
            broken = f"{MARK} {argv[0]} did not run — {error}"
            return FAILED, self._shut(flight, credential, None, broken.encode(), kind=CLOSE)
        self._spawned = True
        cause = yield from self._watch(child, deadline, credential)
        if cause == KILLED:
            child.stop()
        exit_code, errors = child.close()
        cause = self._cause(cause, exit_code)
        return cause, self._shut(flight, credential, exit_code, _closing(cause, exit_code, errors), kind=CLOSE)

    def _watch(self, child: Child, deadline: Deadline, credential: Credential) -> Generator[Taken, None, str]:
        """Read the CLI's stdout to its end, or to the deadline, whichever comes first.

        Lines are reassembled here rather than by whatever is below the seam, because a JSONL
        record split across two reads is the parser's problem and a test that never splits one is
        a test of a parser nobody ships.
        """
        buffered = b""
        waited = 0.0
        while True:
            if (left := deadline.left(self.now())) <= 0:
                return self._cut_short(credential)
            # Read in slices, so a deadline the orchestrator moved — lengthened by a Checkpoint,
            # shortened by a stall or by a signal — reaches a child that is already blocked. `read`
            # answering `None` on a slice is the slice ending rather than the Attempt.
            #
            # The silence is counted as well as the clock, and that is not belt-and-braces: a caller
            # whose clock does not advance would otherwise loop here forever, and every test of this
            # module holds one still. Either bound ends the Attempt.
            slice_seconds = min(left, WATCH_SLICE_SECONDS)
            block = child.read(slice_seconds)
            if block is None:
                waited += slice_seconds
                if waited >= left:
                    return self._cut_short(credential)
                continue
            waited = 0.0
            if not block:
                yield from self._line(buffered, credential)
                return ""
            buffered += block
            while (newline := buffered.find(b"\n")) >= 0:
                line, buffered = buffered[:newline], buffered[newline + 1 :]
                yield from self._line(line, credential)

    def _cut_short(self, credential: Credential) -> str:
        """The deadline arrived. Every command still in flight is ended as stopped rather than left
        dangling: a Step that says it was killed is strictly more than one that says nothing, and
        the invocation's own begin is what still carries the case where we learn nothing at all."""
        for flight in self._flights.values():
            self._shut(flight, credential, None, f"{MARK} killed on the Attempt deadline".encode())
        self._flights.clear()
        return KILLED

    def _line(self, line: bytes, credential: Credential) -> Iterator[Taken]:
        if not line.strip():
            return
        event = _json(line)
        if not isinstance(event, dict):
            # Not a record we can classify, so it goes where an unclassified thing is safe: the
            # channel no check greps. The alternative — presuming it is output — is the one that
            # ends with a model's sentence swept as though a command had produced it.
            yield self._claimed(credential, ADAPTER, line)
            return
        yield from self._event(event, credential)

    def _event(self, event: dict[str, Any], credential: Credential) -> Iterator[Taken]:
        """One event of the vendor's stream, as this Attempt's record.

        `item.updated` is deliberately dropped: it repeats a command's output as it grows, and the
        completion carries the whole of it, so acting on both would count one Step twice.
        """
        kind = event.get("type")
        if kind == "turn.completed":
            self._usage = dict(event.get("usage") or {})
            return
        if kind == "turn.failed":
            self._broke = True
            yield self._reported(f"{MARK} the turn failed", _said(event.get("error")), credential)
            return
        if kind == "error":
            # The CLI's own failure, outside any item — a rejected model id arrives this way, and
            # so does a spent quota. It is the tool talking rather than the model, so it is an
            # Observation like every other failure.
            yield self._reported(f"{MARK} the CLI reported an error", _said(event), credential)
            return
        if kind == "item.started":
            self._start(event.get("item"))
            return
        if kind == "item.completed":
            yield from self._complete(event.get("item") or {}, credential)
            return
        if kind in ("thread.started", "turn.started"):
            return
        yield self._claimed(credential, "codex", json.dumps(event).encode())

    def _start(self, item: Any) -> None:
        """A command the CLI says is running. Its `step-begin` is written now, so a command that
        never comes back is visible as the prime suspect it is."""
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            return
        command = str(item.get("command", ""))
        self._flights[str(item.get("id"))] = self._open(command, TOOLS["command_execution"])

    def _complete(self, item: dict[str, Any], credential: Credential) -> Iterator[Taken]:
        shape = str(item.get("type", ""))
        if shape == "command_execution":
            yield self._ran(item, credential)
            return
        if shape == "error":
            yield self._reported(f"{MARK} the CLI reported an error", str(item.get("message", "")), credential)
            return
        if tool := TOOLS.get(shape):
            # A tool call that is not a shell command produces **two** records, and the split is
            # the same one everything here turns on. The item is largely the model's own writing —
            # a patch it authored, a query it composed — so it goes whole to the channel no check
            # greps, and nothing about it is lost. The Step beside it is the fact that this
            # happened, and its Observation carries only what a tool actually returned; a Flag the
            # model *wrote into a file* must never be swept as one a command produced.
            yield self._claimed(credential, shape, json.dumps(item, sort_keys=True).encode())
            yield self._alone(f"{MARK} {shape}", _returned(item, shape), tool=tool, credential=credential)
            return
        # Everything else — the model's message, its reasoning, its plan, and any item type a later
        # release adds — is prose until proven otherwise.
        yield self._claimed(credential, shape or "codex", _prose(item).encode())

    def _ran(self, item: dict[str, Any], credential: Credential) -> Taken:
        """A command that finished. Its Observation is its output and **only** its output: the
        command is on the Step's own record and putting it in the body too is how an intention gets
        read back as a result."""
        flight = self._flights.pop(str(item.get("id")), None) or self._open(
            str(item.get("command", "")), TOOLS["command_execution"]
        )
        output = str(item.get("aggregated_output", ""))
        exit_code = item.get("exit_code")
        if not output.strip():
            # A command that said nothing reaches a model as blank space under a prompt, which
            # reads as a command that was never run. What it said is that it had nothing to say.
            output = f"{MARK} {item.get('status', 'finished')}, exit {exit_code}, no output"
        return self._shut(flight, credential, exit_code if isinstance(exit_code, int) else None, output.encode())

    def _reported(self, about: str, told: str, credential: Credential) -> Taken:
        """A failure the CLI reported, written down as the Step that met it — and kept for the
        exhaustion read, because the CLI reports a spent quota as an ordinary failure."""
        self._said.append(told.lower())
        return self._alone(about, told, credential=credential)

    def _handed(self, spent: Credential, taking: Credential, cause: str, told: str) -> Taken:
        """The switch, written to the stream as a Step of its own.

        Recorded rather than merely done, because "why did quality fall off after 13:00" is a
        question only the record can answer, and a handover that left no line is a Run where the
        model silently changed underneath the numbers.
        """
        return self._alone(
            f"{MARK} {spent.slot} is {cause} — the Attempt goes to {taking.slot}",
            told,
            kind=SWITCH,
            credential=taking,
        )

    def _alone(
        self,
        command: str,
        told: str,
        *,
        tool: str = ADAPTER,
        kind: str = COMMAND,
        credential: Credential | None = None,
    ) -> Taken:
        """A Step that begins and ends in one breath, for something that has already happened.

        A body that says nothing is filled rather than left blank: an empty Observation reaches a
        model as a blank space under a command, which reads as a command that was never run.
        """
        body = told.strip() or f"{command} — and said nothing about it"
        return self._shut(self._open(command, tool), credential, None, body.encode(), kind=kind)

    def _claimed(self, credential: Credential, tool: str, text: bytes) -> Taken:
        """Something the model said, into the channel no check ever greps.

        It spends no Step index, because a Claim is not a Step (`CONTEXT.md`) and giving it one
        would put the model's prose into the count its own stall is judged from.
        """
        shown = self.recorder.claim(attempt_id=self.attempt_id, text=text)
        return Taken(
            kind=CLAIM,
            slot=credential.slot,
            model=credential.model,
            step_index=0,
            tool=tool,
            command=f"{MARK} the model spoke",
            exit_code=None,
            shown=shown,
        )

    def _open(self, command: str, tool: str) -> _Flight:
        self.step += 1
        step = self.recorder.step_begin(
            attempt_id=self.attempt_id,
            step_index=self.step,
            command_raw=command,
            # Normalisation *is* the repetition counter's rule and belongs to it; whitespace is all
            # this module is entitled to assume (`solver/record.py`).
            command_normalised=" ".join(command.split()),
            tool=tool,
        )
        return _Flight(step=step, index=self.step, tool=tool, command=command)

    def _shut(
        self,
        flight: _Flight,
        credential: Credential | None,
        exit_code: int | None,
        output: bytes,
        *,
        kind: str = COMMAND,
    ) -> Taken:
        """End a Step, and answer with the same facts the record just took.

        The turn's tokens ride the invocation's own Step and every command inside it reports zero,
        which is neither a rounding nor a guess: the vendor meters a turn, so summing tokens over
        an Attempt's Steps gives the truth and attributing a share to each command would not.
        """
        usage = self._spend(credential) if kind == CLOSE else Usage(model=credential.model if credential else "")
        observation = flight.step.end(exit_code=exit_code, output=output, usage=usage)
        return Taken(
            kind=kind,
            slot=credential.slot if credential else "",
            model=credential.model if credential else "",
            step_index=flight.index,
            tool=flight.tool,
            command=flight.command,
            exit_code=exit_code,
            shown=observation.shown,
            digest=observation.digest,
            nbytes=observation.nbytes,
        )

    def _spend(self, credential: Credential | None) -> Usage:
        """The turn's usage as the record wants it. The vendor counts cached input inside its input
        total where `tokens_in` is the part that was not cached, so the cached half is subtracted
        rather than double-counted — `tokens_in + cache_read` is context size either way.

        `known` is false where a turn ran and `turn.completed` never came, which is every turn the
        deadline killed. The vendor states a turn's usage on that one event and on no earlier one
        ([ADR-0022](../docs/adr/0022-an-unmeasured-turn-is-marked-and-never-guessed.md)), so the
        zeros below are the absence of a measurement — and a Run that reported them as a spend of
        zero was blindest about the turns that ran longest.
        """
        cached = _count(self._usage, "cached_input_tokens")
        return Usage(
            model=credential.model if credential else "",
            tokens_in=max(_count(self._usage, "input_tokens") - cached, 0),
            tokens_out=_count(self._usage, "output_tokens"),
            cache_read=cached,
            cache_write=_count(self._usage, "cache_write_input_tokens"),
            known=bool(self._usage) or not self._spawned,
        )

    def _cause(self, cause: str, exit_code: int | None) -> str:
        """Why this invocation ended, read from what happened rather than from what was said.

        Exhaustion outranks failure because the CLI reports a spent quota *as* a failure, and
        recording our own billing as evidence about the Challenge is what ADR-0010 forbids.
        """
        if cause:
            return cause
        if any(phrase in said for said in self._said for phrase in EXHAUSTED_SAYS):
            return EXHAUSTED
        if self._broke or exit_code != 0:
            return FAILED
        return STOPPED


def _argv(credential: Credential, invocation: Invocation) -> tuple[str, ...]:
    """The one command line this module knows how to build.

    The prompt is not on it: it goes over stdin, so a working directory full of challenge-supplied
    code cannot read an Attempt's whole frame out of `/proc`, and so a long recon block can never
    meet `ARG_MAX`. `-` is how the CLI is told to expect it there.
    """
    network = "true" if invocation.network else "false"
    argv = [
        invocation.executable,
        "exec",
        "--json",
        # The CLI refuses to start outside a git worktree, and the image is not one.
        "--skip-git-repo-check",
        "--sandbox",
        invocation.sandbox,
        "--model",
        credential.model,
        "-c",
        f"sandbox_workspace_write.network_access={network}",
    ]
    argv += ["-c", f"model_reasoning_effort={invocation.reasoning_effort}"]
    argv += ["-c", f"tools.web_search={'true' if invocation.web_search else 'false'}"]
    return (*argv, "-")


def _environment(credential: Credential) -> dict[str, str]:
    """The allowlist, plus the one variable that is an address rather than a secret."""
    kept = {name: os.environ[name] for name in CHILD_ENVIRONMENT if name in os.environ}
    return {**kept, "CODEX_HOME": str(credential.home)}


def _could_not_make(home: Path) -> str:
    """Why `CODEX_HOME` is not there, or the empty string once it is.

    It has to **exist** rather than merely be writable: against a missing path the CLI refuses to
    load configuration and never reaches the credential at all.
    """
    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        return f"{MARK} {home} could not be made — {error}"
    return ""


def _closing(cause: str, exit_code: int | None, errors: bytes) -> bytes:
    """What the invocation's Step observed: how it ended, and the CLI's own last words.

    Its stderr is diagnostics rather than output — a login that expired, a binary that is not
    there — and it is kept because a failed invocation with no trace of why is the one shape a
    post-mortem cannot work with.
    """
    told = f"{MARK} {cause}, exit {exit_code}"
    tail = errors.decode("utf-8", "replace").strip()
    return f"{told}\n{tail}".encode() if tail else told.encode()


def _returned(item: dict[str, Any], shape: str) -> str:
    """The part of a tool item that is real output rather than the model's own writing.

    Only a call out to a server has one, and a server's answer is the same class of thing as a
    shell command's stdout. A search's query and a patch's text are the model writing; the record
    keeps both, in `claims/` and in the Step's own `command_raw`, and neither is a body a Flag
    could be swept out of.
    """
    if isinstance(result := item.get("result"), str) and result.strip():
        return result
    if isinstance(result, (dict, list)) and result:
        return json.dumps(result, sort_keys=True)
    return f"{MARK} the {shape} returned no output"


def _prose(item: dict[str, Any]) -> str:
    """A prose item as the orchestrator will read it — its text where it has one, and the whole
    item where it does not, since an item type we have never met has no field we can name."""
    for name in ("text", "message"):
        if isinstance(said := item.get(name), str):
            return said
    return json.dumps(item, sort_keys=True)


def _said(told: Any) -> str:
    """What a failure said, from wherever this vendor put it — a nested `error` object on one event
    and a bare `message` on another, and the whole record where it is neither."""
    if isinstance(told, dict):
        return str(told.get("message", "")) or json.dumps(told, sort_keys=True)
    return "" if told is None else str(told)


def _count(usage: Mapping[str, Any], name: str) -> int:
    return value if isinstance(value := usage.get(name), int) else 0


def _json(line: bytes) -> Any:
    try:
        return json.loads(line)
    except ValueError:
        return None


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _spawn(argv: Sequence[str], workdir: Path, environment: Mapping[str, str], prompt: bytes) -> Child:
    return _Spawned(argv, workdir, environment, prompt)


class _Spawned(Child):
    """The real child: one `codex exec`, in its own process group so the kill reaches its shell.

    The prompt is written and stdin closed before anything is read back. That is safe rather than
    lucky: the CLI drains stdin to end-of-file before it does any work, which is the same behaviour
    that makes `-` mean what it says.
    """

    def __init__(self, argv: Sequence[str], workdir: Path, environment: Mapping[str, str], prompt: bytes) -> None:
        self._process = subprocess.Popen(
            list(argv),
            cwd=str(workdir),
            env=dict(environment),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self._errors = b""
        with self._process.stdin as writing:
            writing.write(prompt)
        self._talking = [self._process.stdout, self._process.stderr]

    def read(self, budget: float) -> bytes | None:
        deadline = time.monotonic() + budget
        while self._talking:
            if (left := deadline - time.monotonic()) <= 0:
                return None
            ready, _, _ = select.select(self._talking, [], [], left)
            if not ready:
                return None
            for stream in ready:
                block = os.read(stream.fileno(), READ_BLOCK_BYTES)
                if not block:
                    self._talking.remove(stream)
                elif stream is self._process.stderr:
                    self._errors = (self._errors + block)[-STDERR_KEPT_BYTES:]
                else:
                    return block
        return b""

    def stop(self) -> None:
        """The whole process group, because the CLI's shell is what is holding the Challenge open
        and killing only the parent leaves it running inside a container nobody is watching."""
        try:
            os.killpg(os.getpgid(self._process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            self._process.kill()

    def close(self) -> tuple[int | None, bytes]:
        try:
            exit_code = self._process.wait(timeout=REAP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            self.stop()
            exit_code = self._process.wait()
        for stream in (self._process.stdout, self._process.stderr):
            stream.close()
        return exit_code, self._errors
