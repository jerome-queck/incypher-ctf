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

The floor runs for **every** artefact including the unrecognised ones: bounded `strings`, entropy
over fixed-offset windows, `od` head and tail, and a Flag-regex scan using the wrapper read from
the Board at runtime. Two of the four need no binary — arithmetic over bytes the Solver has already
opened — and they are recorded as Steps like any other, marked `(recon)` so a reader never mistakes
one for a shell command that could be replayed.

The caps are enforced where the bytes are, rather than after they have arrived: output is capped as
it is read off the pipe, wall-clock kills the child, and the size cap bounds what the in-process
floor will read. Brunner links a **6.15 GB** archive from description prose, and a cap applied
after that is in memory is a cap in name.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import math
import os
import re
import select
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from solver.record import Recorder, Usage

# Recon is the Attempt's opening move and runs before any model is invoked, so every Step it
# records cost no tokens and names no model. The empty name is the fact, not a placeholder.
NO_MODEL = Usage(model="")

# What a probe that ran nothing writes instead. Prefixed so a reader of a stream can tell the
# cascade's own account of a Step from the output of the tool the Step was for.
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

EXIFTOOL = ("exiftool", "-a", "-G1", "-s")

# Keyed by the full mime type, then by its major part, then by nothing at all — which is the
# unknown branch and is empty by design. Every entry is argv without the artefact, which is
# appended last. An archive is **listed and never extracted**: extraction moves the environment,
# which is a Checkpoint for the model to earn under the sandbox, and a zip bomb opened by recon
# would be the Attempt's whole budget spent before a model saw anything.
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

# After EOF on its stdout a child has said everything it is going to say, so anything past this
# grace is a process that will not exit rather than one still working — and a recon that waits on
# one is the hang the caps exist to make impossible.
REAP_GRACE_SECONDS = 1.0

# The child gets a named environment rather than ours. These commands read challenge-supplied
# bytes, and a tool that prints its own environment on a malformed input would put every declared
# credential into an Observation — redacted, but only for the values redaction was told about.
CHILD_ENVIRONMENT = ("PATH", "HOME", "TERM", "LANG")


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
    flag_pattern: str,
    recorder: Recorder,
    attempt_id: str,
    limits: Limits = Limits(),
) -> Recon:
    """Work a Challenge's prose and its files, and return what was observed.

    The description is the first parameter and has no default because it is a mandatory input, not
    an optional one: a password or a second download host lives only in prose, and 22 of 74 Brunner
    Challenges ship no file at all, so it is the only input three Challenges in ten ever have.

    Steps are numbered from one — recon *is* the opening of an Attempt, so its Steps are the
    Attempt's first Steps, and how many it spent is `len(result.probes)`.
    """
    cascade = _Cascade(recorder, attempt_id, limits, flag_pattern)
    cascade.read(description)
    for artefact in artefacts:
        cascade.work(Path(artefact))
    return Recon(tuple(cascade.probes))


class _Cascade:
    """One Attempt's recon in flight: the deadline it shares, the Steps it has spent, and the one
    place a probe of any kind — external command or in-process reading — becomes a record."""

    def __init__(self, recorder: Recorder, attempt_id: str, limits: Limits, flag_pattern: str) -> None:
        self._recorder = recorder
        self._attempt_id = attempt_id
        self._limits = limits
        self._pattern = flag_pattern
        self._deadline = time.monotonic() + limits.cascade_seconds
        self._step = 0
        self.probes: list[Probe] = []

    def read(self, description: str) -> None:
        prose = description.strip().encode()
        self._probe(
            "description",
            f"{MARK} the description as the Board gave it",
            "description",
            lambda _budget: (0, prose or f"{MARK} the Board's description is empty".encode()),
        )
        self._scan("description", prose)

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
        probe = self._command(subject, (*DISPATCH, str(artefact)))
        if probe.exit_code != 0 or not probe.shown.strip():
            return ""
        return probe.shown.strip().splitlines()[0]

    def _floor(self, subject: str, artefact: Path) -> None:
        size = _size_of(artefact)
        self._command(subject, (*STRINGS, str(artefact)))
        self._probe(
            subject,
            f"{MARK} entropy over {ENTROPY_WINDOWS} windows of {ENTROPY_WINDOW_BYTES} bytes — {artefact}",
            "entropy",
            lambda _budget: _entropy(artefact, size),
        )
        self._command(subject, (*OD, "-N", str(OD_EDGE_BYTES), str(artefact)))
        # An artefact the head already covered has no tail to show, and a second identical dump is
        # the opening frame's context spent on bytes the model has just read.
        if size > OD_EDGE_BYTES:
            self._command(subject, (*OD, "-j", str(size - OD_EDGE_BYTES), "-N", str(OD_EDGE_BYTES), str(artefact)))
        self._scan(subject, artefact)

    def _scan(self, subject: str, source: Path | bytes) -> None:
        where = source if isinstance(source, Path) else "the description"
        self._probe(
            subject,
            f"{MARK} flag-scan for {self._pattern} — {where}",
            "flag-scan",
            lambda _budget: _scanned(source, self._pattern, self._limits.artefact_bytes),
        )

    def _command(self, subject: str, argv: tuple[str, ...]) -> Probe:
        return self._probe(
            subject,
            " ".join(argv),
            argv[0],
            lambda budget: _run(argv, budget, self._limits.output_bytes),
        )

    def _probe(
        self,
        subject: str,
        command: str,
        tool: str,
        produce: Callable[[float], tuple[int | None, bytes]],
    ) -> Probe:
        """Record one probe, whatever it turned out to be — including one the budget left no room
        for, which is a fact about the Attempt and so is written like any other."""
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
        )
        left = self._deadline - time.monotonic()
        if left <= 0:
            exit_code, output = None, f"{MARK} not run — the {self._limits.cascade_seconds:g}s recon budget was spent"
            output = output.encode()
        else:
            exit_code, output = produce(min(left, self._limits.command_seconds))
        observation = step.end(exit_code=exit_code, output=output, usage=NO_MODEL)
        probe = Probe(subject, tool, command, exit_code, observation.shown)
        self.probes.append(probe)
        return probe


def _branch_for(mime: str) -> tuple[tuple[str, ...], ...]:
    major = mime.split("/")[0]
    return BRANCHES.get(mime) or BRANCHES.get(major) or ()


def _rendered(probe: Probe) -> str:
    status = "" if probe.exit_code in (0, None) else f"  [exit {probe.exit_code}]"
    return f"## {probe.subject}\n$ {probe.command}{status}\n{probe.shown.rstrip()}"


def _size_of(artefact: Path) -> int:
    try:
        return artefact.stat().st_size
    except OSError:
        return 0


def _run(argv: tuple[str, ...], budget: float, cap: int) -> tuple[int | None, bytes]:
    """One external tool, with its wall-clock and its output bounded as they are spent.

    A tool that is not installed is the ordinary case rather than the exceptional one — this runs
    on a laptop as readily as in the image — so it returns like any other failure.
    """
    try:
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={name: os.environ[name] for name in CHILD_ENVIRONMENT if name in os.environ},
        )
    except OSError as error:
        return None, f"{MARK} {argv[0]} did not run — {error}".encode()
    body, note = _captured(process, budget, cap)
    if not note:
        try:
            return process.wait(timeout=REAP_GRACE_SECONDS), body
        except subprocess.TimeoutExpired:
            note = f"\n{MARK} killed — it closed its output and did not exit".encode()
    process.kill()
    process.wait()
    return None, body + note


def _captured(process: subprocess.Popen[bytes], budget: float, cap: int) -> tuple[bytes, bytes]:
    """Read what the child says until it stops, the cap is reached, or the budget is gone.

    The cap is applied here and not to a finished buffer, which is the difference between a bound
    and an intention: `strings` over a multi-gigabyte artefact will happily fill memory first.
    """
    deadline = time.monotonic() + budget
    blocks: list[bytes] = []
    taken = 0
    with process.stdout as stream:
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([stream], [], [], left)[0]:
                return b"".join(blocks)[:cap], f"\n{MARK} killed after {budget:.1f}s".encode()
            block = os.read(stream.fileno(), 65536)
            if not block:
                return b"".join(blocks), b""
            blocks.append(block)
            taken += len(block)
            if taken >= cap:
                return b"".join(blocks)[:cap], f"\n{MARK} output capped at {cap} bytes".encode()


def _entropy(artefact: Path, size: int) -> tuple[int | None, bytes]:
    """Shannon entropy per window, at fixed offsets across the artefact.

    Measurements and no verdict: whether 7.99 bits/byte means compressed, encrypted or neither is a
    judgement, and ADR-0009 keeps judgements out of the record so they stay recomputable.
    """
    if size == 0:
        return 0, f"{MARK} the artefact is empty".encode()
    stride = max(ENTROPY_WINDOW_BYTES, size // ENTROPY_WINDOWS)
    lines = []
    try:
        with artefact.open("rb") as reading:
            for offset in range(0, size, stride)[:ENTROPY_WINDOWS]:
                reading.seek(offset)
                if not (window := reading.read(ENTROPY_WINDOW_BYTES)):
                    break
                lines.append(f"0x{offset:08x}  {len(window):>6} bytes  {_shannon(window):.3f} bits/byte")
    except OSError as error:
        return None, f"{MARK} {artefact} could not be read — {error}".encode()
    return 0, "\n".join(lines).encode()


def _shannon(window: bytes) -> float:
    return sum(-(share := count / len(window)) * math.log2(share) for count in _counts(window))


def _counts(window: bytes) -> list[int]:
    return [count for count in (window.count(value) for value in set(window)) if count]


def _scanned(source: Path | bytes, pattern: str, cap: int) -> tuple[int | None, bytes]:
    """The Board's own Flag wrapper, over as much of the artefact as the size cap allows.

    The pattern comes from the Board profile at runtime, so a wrapper this code has never seen
    costs a config value. A Board that publishes one we cannot compile is a fact about the Board,
    and is reported as such rather than raised at an Attempt that was about to start.
    """
    try:
        matcher = re.compile(pattern.encode())
    except re.error as broken:
        return None, f"{MARK} the Board's Flag wrapper {pattern!r} did not compile — {broken}".encode()
    found: list[bytes] = []
    read = 0
    carried = b""
    try:
        for block in _blocks(source, cap):
            read += len(block)
            for match in matcher.finditer(carried + block):
                if match.group(0) not in found:
                    found.append(match.group(0))
            carried = block[-SCAN_OVERLAP_BYTES:]
    except OSError as error:
        return None, f"{MARK} {source} could not be read — {error}".encode()
    return 0, _scan_report(found, read, cap)


def _scan_report(found: list[bytes], read: int, cap: int) -> bytes:
    where = f"{MARK} scanned {read} bytes"
    if read >= cap:
        where += f", stopping at the {cap}-byte size cap"
    if not found:
        return f"{where}\nno match".encode()
    shown = b", ".join(found[:MATCHES_SHOWN])
    more = f" (+{len(found) - MATCHES_SHOWN} more)" if len(found) > MATCHES_SHOWN else ""
    return f"{where}\n{len(found)} match(es): ".encode() + shown + more.encode()


def _blocks(source: Path | bytes, cap: int):
    if isinstance(source, bytes):
        yield source[:cap]
        return
    with source.open("rb") as reading:
        while cap > 0 and (block := reading.read(min(SCAN_BLOCK_BYTES, cap))):
            cap -= len(block)
            yield block
