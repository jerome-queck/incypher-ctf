"""The deterministic cascade every Attempt opens with, before any model is invoked.

The first action frames the whole run, so the Solver opens onto facts rather than onto a filename.
Three rules shape what is here, and each is a refusal:

- **Dispatch is on `file -b --mime-type`** — never on extension, never on Category. A `.txt` that
  is really a PNG is handled as a PNG, and Category is not a parameter of anything below, which is
  the cheapest way to keep it from becoming one.
- **Unknown is the default branch, not a failure branch.** The branch table is deliberately small:
  [#15](https://github.com/jerome-queck/incypher-ctf/issues/15)'s live census found **52 of 52**
  Brunner attachments are `.zip`, so extension dispatch has exactly one branch and the type
  diversity lives one layer *inside* the archive. What makes the cascade total over the types
  nobody anticipated is the floor, not the table.
- **A tool that fails produces an Observation**, because silence would leave the model to narrate
  what it thinks happened (`CONTEXT.md`, *Observation*). Every failure here — a missing binary, a
  killed command, a spent budget, an unreadable artefact — is written down as the output of the
  Step that met it.

Two of the floor's four probes need no binary: they are arithmetic over bytes the Solver has
already opened. They are recorded as Steps like everything else and their names carry `MARK`, so a
reader never takes one for a shell command that could be replayed.

The caps are enforced where the bytes are, rather than after they have arrived: output is capped as
it is read off the pipe, wall-clock kills the child and stops a scan mid-file, and the size cap
bounds what the in-process floor will read. Brunner links a **6.15 GB** archive from description
prose, and a cap applied after that is in memory is a cap in name.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from solver.record import NO_MODEL, SOURCE_BOARD, SOURCE_SOLVER, Recorder
from solver.shell import run
from solver.wrapper import compiled, found_in

# Every line the cascade writes about itself opens with this — the name of an in-process probe, a
# kill, a cap, a spent budget — so a reader of a stream can tell what the Solver said from what the
# tool said, and never mistakes the first for something they could replay in a shell.
MARK = "[recon]"

# The one call the whole cascade dispatches on.
DISPATCH = ("file", "-b", "--mime-type")

# The floor's two external tools. `strings` is bounded by the output and wall-clock caps rather
# than by a flag, because it has none; `od` is bounded by `-N` and asked for both ends of the file,
# since a header names a format and a footer often carries the appended thing.
STRINGS = ("strings", "-a", "-n", "6")
OD = ("od", "-A", "x", "-t", "x1z", "-v")
OD_EDGE_BYTES = 256

ENTROPY_WINDOW_BYTES = 4096
ENTROPY_WINDOWS = 8

SCAN_BLOCK_BYTES = 1 << 20
# Carried between blocks so a wrapper straddling a block boundary is still matched.
SCAN_OVERLAP_BYTES = 512
MATCHES_SHOWN = 20
# A Board's wrapper is its own regex and `brunner{.*}` is a real one, so a single match over binary
# input can be most of the artefact. What is worth showing is that it matched and where it starts.
MATCH_BYTES = 200

EXIFTOOL = ("exiftool", "-a", "-G1", "-s")

# Keyed by the full mime type, then by its major part, then by nothing at all — which is the
# unknown branch and is empty by design. Every entry is argv without the artefact, which is
# appended last. An archive is **listed and never extracted**: extraction moves the environment,
# which is a Checkpoint for the model to earn under the sandbox, and a zip bomb opened by recon
# would be the Attempt's whole budget spent before a model saw anything.
#
# The `exiftool` entries are here for the artefact whose name lies, which is the case dispatching
# on content exists for — and for whatever a Board we have not met ships, since the census that
# makes `.zip` the one branch is Brunner's and not everyone's.
#
# What a branch may hold is a tool that answers a *question about the artefact*. `zsteg -a` is the
# one measured against that and dropped: on a 16x16 PNG it filled the whole output cap with
# candidate extractions, which is a search rather than a fact, and recon's output is the frame the
# model opens onto. It stays in the image for the model to run with a budget behind it.
BRANCHES: dict[str, tuple[tuple[str, ...], ...]] = {
    "application/zip": (("unzip", "-l"),),
    "application/pdf": (EXIFTOOL,),
    "image": (EXIFTOOL,),
    "audio": (EXIFTOOL,),
    "video": (EXIFTOOL,),
}


@dataclass(frozen=True)
class Limits:
    """The hard caps, as parameters rather than constants — none of them is calibrated until a Run
    has been replayed, and a constant is a number nobody can move when it turns out to be wrong."""

    artefact_bytes: int = 64 * 1024 * 1024
    command_seconds: float = 20.0
    cascade_seconds: float = 180.0
    output_bytes: int = 8 * 1024


@dataclass(frozen=True)
class Probe:
    """One recon Step as the block will show it: what it was about, what was run, and what came
    back. The whole output is on disk under the Observation the Step recorded; `shown` is the
    bounded form, which is what reaches a model's context."""

    subject: str
    tool: str
    command: str
    exit_code: int | None
    shown: str


@dataclass(frozen=True)
class Recon:
    """What the cascade found — the recon block, which is one of the few things ADR-0005 lets
    cross an Attempt boundary, because all of it is Observation and none of it is Claim."""

    probes: tuple[Probe, ...]

    def block(self) -> str:
        return "\n\n".join(_rendered(probe) for probe in self.probes)


def recon(
    description: str,
    artefacts: Sequence[Path],
    *,
    flag_wrappers: Sequence[str],
    recorder: Recorder,
    attempt_id: str,
    limits: Limits = Limits(),
    first_step: int = 1,
) -> Recon:
    """Work a Challenge's prose and its files, and return what was observed.

    The description is the first parameter and has no default because it is a mandatory input, not
    an optional one: a password or a second download host lives only in prose, and 22 of 74 Brunner
    Challenges ship no file at all, so it is the only input three Challenges in ten ever have.

    Steps are numbered from `first_step` — recon *is* the opening of an Attempt, so its Steps are
    the Attempt's first Steps, and how many it spent is `len(result.probes)`. The default is one and
    the parameter exists for the one Step that can precede it: a Challenge that needs an Instance is
    deployed before anything is reconned, and two counters would put two Steps at the same address
    (`solver/instance.py`).
    """
    cascade = _Cascade(recorder, attempt_id, limits, tuple(flag_wrappers), first_step - 1)
    cascade.read(description)
    for artefact in artefacts:
        cascade.work(Path(artefact))
    return Recon(tuple(cascade.probes))


class _Cascade:
    """One Attempt's recon in flight: the deadline it shares, the Steps it has spent, and the one
    place a probe of any kind — external command or in-process reading — becomes a record."""

    def __init__(
        self, recorder: Recorder, attempt_id: str, limits: Limits, flag_wrappers: tuple[str, ...], spent: int = 0
    ) -> None:
        self._recorder = recorder
        self._attempt_id = attempt_id
        self._limits = limits
        self._wrappers = flag_wrappers
        self._deadline = time.monotonic() + limits.cascade_seconds
        self._step = spent
        self.probes: list[Probe] = []

    def read(self, description: str) -> None:
        """The one input that is the Board *stating* the Challenge rather than the Solver working
        it. Both probes here read bytes nothing produced, so both are recorded as the Board's
        (ADR-0019) — a flag-format example in the prose is the Board showing the wrapper's shape."""
        prose = description.strip().encode()
        self._probe(
            "description",
            f"{MARK} the description as the Board gave it",
            "description",
            lambda _budget: (0, prose or f"{MARK} the Board's description is empty".encode()),
            source=SOURCE_BOARD,
        )
        self._scan("description", prose, "the description", source=SOURCE_BOARD)

    def work(self, artefact: Path) -> None:
        """Dispatch, then the floor, then the branch — in that order, because the floor is what
        makes the cascade total and a type-specific tool must not be able to starve it of the
        cascade's remaining budget."""
        subject = artefact.name
        mime = self._dispatch(subject, artefact)
        self._floor(subject, artefact)
        for command in _branch_for(mime):
            self._command(subject, (*command, str(artefact)))

    def _dispatch(self, subject: str, artefact: Path) -> str:
        """What `file` said, read from its own bytes rather than from the record's rendering of
        them — dispatch is a function of the tool's answer, and never of how a Step is shown."""
        exit_code, answered = self._command(subject, (*DISPATCH, str(artefact)))
        if exit_code != 0 or not answered.strip():
            return ""
        return answered.decode("utf-8", "replace").strip().splitlines()[0]

    def _floor(self, subject: str, artefact: Path) -> None:
        self._command(subject, (*STRINGS, str(artefact)))
        self._probe(
            subject,
            f"{MARK} entropy over {ENTROPY_WINDOWS} windows of {ENTROPY_WINDOW_BYTES} bytes — {artefact}",
            "entropy",
            # No budget: the whole probe is eight window reads, so what bounds it is the window
            # count rather than a clock.
            lambda _budget: _entropy(artefact),
        )
        self._command(subject, (*OD, "-N", str(OD_EDGE_BYTES), str(artefact)))
        # An artefact the head already covered has no tail to show, and a second identical dump is
        # the opening frame's context spent on bytes the model has just read. A size that could not
        # be read is not a reason to dump the head twice — `od` will report that failure itself.
        if (size := _size_of(artefact)) > OD_EDGE_BYTES:
            self._command(subject, (*OD, "-j", str(size - OD_EDGE_BYTES), "-N", str(OD_EDGE_BYTES), str(artefact)))
        self._scan(subject, artefact, str(artefact))

    def _scan(self, subject: str, read_from: Path | bytes, where: str, *, source: str = SOURCE_SOLVER) -> None:
        """One Step per subject however many shapes the Board states — the bytes are read once and
        every matcher applied to each block, because the cascade's deadline is shared and a re-read
        per pattern would spend a later artefact's budget on bytes this one has already seen."""
        self._probe(
            subject,
            f"{MARK} flag-scan for {', '.join(self._wrappers)} — {where}",
            "flag-scan",
            lambda budget: _scanned(read_from, where, self._wrappers, self._limits.artefact_bytes, budget),
            source=source,
        )

    def _command(self, subject: str, argv: tuple[str, ...]) -> tuple[int | None, bytes]:
        return self._probe(
            subject,
            " ".join(argv),
            argv[0],
            lambda budget: run(argv, budget=budget, cap=self._limits.output_bytes, mark=MARK),
        )

    def _probe(
        self,
        subject: str,
        command: str,
        tool: str,
        produce: Callable[[float], tuple[int | None, bytes]],
        *,
        source: str = SOURCE_SOLVER,
    ) -> tuple[int | None, bytes]:
        """Record one probe, whatever it turned out to be — including one the budget left no room
        for, which is a fact about the Attempt and so is written like any other.

        Answers with what the probe produced rather than with the `Probe`, so a caller that acts on
        an output acts on the bytes and not on the record's rendering of them.
        """
        self._step += 1
        step = self._recorder.step_begin(
            attempt_id=self._attempt_id,
            step_index=self._step,
            command_raw=command,
            # Normalisation *is* #70's repetition rule and belongs to it; whitespace is all this
            # module is entitled to assume, and `command_raw` beside it is what lets a later rule
            # be applied to an earlier Run (`solver/record.py`).
            command_normalised=" ".join(command.split()),
            tool=tool,
            source=source,
        )
        left = self._deadline - time.monotonic()
        if left <= 0:
            exit_code, output = None, f"{MARK} not run — the {self._limits.cascade_seconds:g}s recon budget was spent"
            output = output.encode()
        else:
            exit_code, output = produce(min(left, self._limits.command_seconds))
        # A tool that ran and said nothing is a fact — `strings` finds nothing in a 36-byte PNG —
        # but an empty body reaches the model as a blank space under a command, which reads as a
        # tool that was never run. What it said is that it had nothing to say.
        if not output.strip():
            output = f"{MARK} {tool} produced no output, exit {exit_code}".encode()
        observation = step.end(exit_code=exit_code, output=output, usage=NO_MODEL)
        self.probes.append(Probe(subject, tool, command, exit_code, observation.shown))
        return exit_code, output


def _branch_for(mime: str) -> tuple[tuple[str, ...], ...]:
    major = mime.split("/")[0]
    return BRANCHES.get(mime) or BRANCHES.get(major) or ()


def _rendered(probe: Probe) -> str:
    """One probe as the model reads it. A shell prompt only where a shell command ran, and a
    stopped probe marked as stopped — a killed tool that reads like a clean one is the silence
    this module exists to refuse."""
    prompt = "" if probe.command.startswith(MARK) else "$ "
    status = {0: ""}.get(probe.exit_code, "  [stopped]" if probe.exit_code is None else f"  [exit {probe.exit_code}]")
    return f"## {probe.subject}\n{prompt}{probe.command}{status}\n{probe.shown.rstrip()}"


def _size_of(artefact: Path) -> int:
    try:
        return artefact.stat().st_size
    except OSError:
        return 0


def _entropy(artefact: Path) -> tuple[int | None, bytes]:
    """Shannon entropy per window, at fixed offsets spanning the artefact end to end.

    The last window is the artefact's tail rather than one stride short of it, for the same reason
    `od` is asked for both ends: an appended blob lives past everything a head-first sweep reaches.

    Measurements and no verdict: whether 7.99 bits/byte means compressed, encrypted or neither is a
    judgement, and ADR-0009 keeps judgements out of the record so they stay recomputable.
    """
    try:
        size = artefact.stat().st_size
        if size == 0:
            return 0, f"{MARK} the artefact is empty".encode()
        lines = []
        with artefact.open("rb") as reading:
            for offset in _window_offsets(size):
                reading.seek(offset)
                if not (window := reading.read(ENTROPY_WINDOW_BYTES)):
                    break
                lines.append(f"0x{offset:08x}  {len(window):>6} bytes  {_shannon(window):.3f} bits/byte")
    except OSError as error:
        return None, f"{MARK} {artefact} could not be read — {error}".encode()
    return 0, "\n".join(lines).encode()


def _window_offsets(size: int) -> list[int]:
    last = max(size - ENTROPY_WINDOW_BYTES, 0)
    if last == 0:
        return [0]
    spread = [round(step * last / (ENTROPY_WINDOWS - 1)) for step in range(ENTROPY_WINDOWS)]
    return sorted(set(spread))


def _shannon(window: bytes) -> float:
    return sum(-(share := window.count(value) / len(window)) * math.log2(share) for value in set(window))


def _scanned(
    read_from: Path | bytes, where: str, wrappers: tuple[str, ...], cap: int, budget: float
) -> tuple[int | None, bytes]:
    """Every Flag shape the Board states, over as much of the artefact as the caps allow.

    The patterns come from the Board profile at runtime, so a wrapper this code has never seen costs
    a config value — and a Board that states two shapes costs two entries rather than one alternation,
    which `solver/wrapper.py` exists to explain. One it publishes that we cannot compile is a fact
    about the Board, reported as such rather than raised at an Attempt that was about to start; the
    other shapes are still scanned for.
    """
    matchers, broken = compiled(wrappers)
    if not matchers:
        return None, f"{MARK} {'; '.join(broken) or 'the Board states no Flag wrapper'}".encode()
    deadline = time.monotonic() + budget
    found: list[bytes] = []
    read = 0
    carried = b""
    stopped = ""
    try:
        for block in _blocks(read_from, cap):
            read += len(block)
            for match in found_in(carried + block, matchers):
                if match not in found:
                    found.append(match)
            carried = block[-SCAN_OVERLAP_BYTES:]
            if time.monotonic() > deadline:
                stopped = f", stopping after {budget:.1f}s"
                break
        else:
            stopped = f", stopping at the {cap}-byte size cap" if read >= cap else ""
    except OSError as error:
        return None, f"{MARK} {where} could not be read — {error}".encode()
    return 0, _scan_report(found, read, stopped, broken)


def _scan_report(found: list[bytes], read: int, stopped: str, broken: tuple[str, ...] = ()) -> bytes:
    """What the scan saw, and any shape it could not look for — a wrapper that would not compile is
    a fact about the Board, and a report that omitted it would read as prose that held nothing."""
    said = "".join(f"\n{MARK} {one}" for one in broken)
    if not found:
        return f"{MARK} scanned {read} bytes{stopped}\nno match{said}".encode()
    shown = b", ".join(match[:MATCH_BYTES] for match in found[:MATCHES_SHOWN])
    more = f" (+{len(found) - MATCHES_SHOWN} more)" if len(found) > MATCHES_SHOWN else ""
    return f"{MARK} scanned {read} bytes{stopped}\n{len(found)} match(es): ".encode() + shown + f"{more}{said}".encode()


def _blocks(read_from: Path | bytes, cap: int) -> Iterator[bytes]:
    if isinstance(read_from, bytes):
        yield read_from[:cap]
        return
    with read_from.open("rb") as reading:
        while cap > 0 and (block := reading.read(min(SCAN_BLOCK_BYTES, cap))):
            cap -= len(block)
            yield block
