"""Reading a Run's Step stream back — the floor the seven eval queries stand on.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) settles what the
queries are: **eval is seven questions, not a harness.** Each question is a script beside this one,
and each of them needs the same three things first — the records in sequence, the Attempts
reassembled out of them, and one table shape so that eight answers read alike. That is this module,
and it is the reason none of the seven contains a JSON parser.

**It is a reader, and a reader is where ADR-0009's third stability rule is spent.** Everything here
accesses by name with a default and skips what it cannot parse, so a stream written by a schema
this file has never met is read for the part it does understand rather than refused whole. The
record *kinds* are named here rather than imported for that same reason: `solver/record.py` writes
them and this reads them, and a reader that crashed on an unrecognised kind would be a reader that
made the writer unable to add one.

What is imported from `solver/` is everything that is a **rule** rather than a name — the Cut
vocabulary, and which tool names belong to the model rather than to the orchestrator. A rule kept
in two places is a rule that will disagree (`CODING_STANDARDS.md` §6), and the whole point of a
replay is that it applies the code that actually ran.

Bodies are the one thing a promoted stream does not carry. `runs/<run_id>.jsonl` is the JSONL and
nothing beside it, so `body()` answers `None` there and a query that needs prose says so rather
than reporting a zero it did not measure.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from solver.codex import ADAPTER, TOOLS  # noqa: E402
from solver.record import CLAIMS, FLAG, OBSERVATIONS, SOURCE_SOLVER  # noqa: E402

# Where a promoted stream lands, and what every query reads when it is given no path of its own.
# The live copy under `/state` is one argument away and carries the bodies with it.
PROMOTED = "runs"

# What a query prints where `Attempt.cause` is empty. Empty is the honest value — the stream never
# said — and this is what that reads as in a table, held here so four queries cannot spell it three
# ways and split one finding into three rows.
NEVER_CLOSED = "(never closed)"

# The names `solver/record.py` writes into the `record` field. Held here rather than imported: see
# the module docstring — a reader owns its own vocabulary so that the writer stays free to grow one.
RUN_OPEN = "run-open"
RUN_CLOSE = "run-close"
INTAKE = "intake"
TRIAGE = "triage"
ATTEMPT_OPEN = "attempt-open"
ATTEMPT_CLOSE = "attempt-close"
STEP_BEGIN = "step-begin"
STEP_END = "step-end"
CLAIMED = "claim"

# Which Steps were the **model** working the Challenge. Every other Step of an Attempt is the
# orchestrator's own — a deploy, the recon cascade, a Flag sweep, a submission, the spawn itself —
# and `solver/run.py` hands the stall call exactly this set. Taken from the adapter's own map so
# that a replay counts what the live Run counted (`solver/codex.py`).
MODEL_TOOLS = frozenset(TOOLS.values())


def at(moment: str) -> dt.datetime | None:
    """One timestamp, or `None` where the field is missing or unreadable.

    Public because a query needs it too, and a second copy of four lines is still a second reader —
    which is the one thing this module exists to stop (`CODING_STANDARDS.md` §6).
    """
    try:
        return dt.datetime.fromisoformat(moment)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Step:
    """One Step, as its `step-end` recorded it — or as its `step-begin` left it.

    `ended` is the pair ADR-0009 asks for, read back: a Step that hung or crashed mid-flight is
    here with `ended` false rather than missing, and at a crash it is the prime suspect.
    """

    seq: int
    ts: dt.datetime | None
    attempt_id: str
    step_index: int
    command_raw: str
    command_normalised: str
    tool: str
    # Where the bytes came from — the Solver's own work, or the Board stating the Challenge
    # (ADR-0019). A replay that could not see it could not re-derive what authorised a submission.
    source: str
    exit_code: int | None
    duration_ms: int
    observation_digest: str
    observation_bytes: int
    observation_ref: str
    checkpoint: str | None
    model: str
    tokens_in: int
    tokens_out: int
    cache_read: int
    cache_write: int
    # Whether the numbers above are the whole of what this Step spent, or `None` where the stream
    # never said — the four promoted gate Runs predate the field and are never rewritten (ADR-0022).
    usage_known: bool | None
    ended: bool

    @property
    def tokens(self) -> int:
        """What the turn drew fresh. Cache reads are context rather than new work and are counted
        beside it, never inside it — a rate over the two summed says the model got cheaper every
        time it re-read the same prompt."""
        return self.tokens_in + self.tokens_out

    @property
    def by_the_model(self) -> bool:
        return self.tool in MODEL_TOOLS

    @property
    def spawn(self) -> bool:
        """One invocation of the vendor's CLI — a **turn**, and the unit a premature quit is about.

        The adapter writes its own lines under the same tool name, so the test is the command: a
        spawn's is the argv it ran, and everything else the adapter says about itself opens with its
        `[codex] ` mark (`solver/codex.py`).
        """
        return self.tool == ADAPTER and not self.command_raw.startswith("[")

    @property
    def unmeasured(self) -> bool:
        """A turn ran here and what it cost was never stated, so these zeros are an absent
        measurement rather than an absent spend.

        `usage_known` says it outright, and a stream written before that field says it another way:
        an invocation is the only Step a turn's tokens ever land on, so one carrying none at all is
        one the deadline killed before the vendor reported any (ADR-0022).
        """
        if self.usage_known is not None:
            return not self.usage_known
        return self.spawn and not (self.tokens or self.cache_read or self.cache_write)


@dataclass(frozen=True)
class Turn:
    """One invocation of the vendor's agent, and everything that happened inside it.

    An Attempt is many turns: the vendor's agent ending its turn with budget left is a new turn over
    the same working directory and never the end of the Attempt (ADR-0023), so the turn is the unit
    a premature quit is about and the Attempt is the unit a Cut is about.

    `spawn` is `None` for a tail of Steps with no invocation after them, which is what a Run that
    died mid-turn leaves behind — visible rather than absent, for the same reason the Step pair is.
    """

    spawn: Step | None
    steps: tuple[Step, ...]
    claims: tuple[dict[str, Any], ...]

    @property
    def barren(self) -> bool:
        """The model ran nothing. It may have talked at length; that is the point of the word."""
        return not self.steps

    @property
    def ended_itself(self) -> bool:
        """The CLI came back of its own accord rather than being killed at the deadline."""
        return self.spawn is not None and self.spawn.exit_code == 0

    @property
    def model(self) -> str:
        return self.spawn.model if self.spawn else ""


@dataclass
class Attempt:
    """One Attempt, reassembled — what it was opened with, what it spent, and what ended it."""

    attempt_id: str
    # Which Run wrote it. `attempt_id` is `<challenge_id>-<attempt_sequence>` and is unique **within**
    # a Run and nowhere else — every Run of one Board reuses the same strings — so an Attempt that
    # did not know its Run could be keyed on by mistake, and was: over the four gate Runs the 27
    # attempts carry 11 distinct ids between them.
    run_id: str = ""
    opened: dict[str, Any] = field(default_factory=dict)
    closed: dict[str, Any] | None = None
    steps: list[Step] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ref(self) -> str:
        """This Attempt's name across every Run there is — the only safe key for a table."""
        return f"{self.run_id}/{self.attempt_id}" if self.run_id else self.attempt_id

    @property
    def cause(self) -> str:
        """Why this Attempt ended, or the empty string where the stream never says.

        Empty is a finding rather than a default: an Attempt with no `attempt-close` is a Run that
        died with it in flight, and reporting it as though it had ended would hide the crash.
        """
        return str((self.closed or {}).get("cause", ""))

    @property
    def flag(self) -> str | None:
        return (self.closed or {}).get(FLAG)

    @property
    def category(self) -> str:
        return str(self.opened.get("category", "")) or "(unknown)"

    @property
    def tier(self) -> int | None:
        tier = self.opened.get("tier")
        return tier if isinstance(tier, int) else None

    @property
    def budget_s(self) -> int:
        budget = self.opened.get("budget_s")
        return budget if isinstance(budget, int) else 0

    @property
    def instance_until(self) -> str | None:
        return self.opened.get("instance_until")

    @property
    def model_steps(self) -> list[Step]:
        return [step for step in self.steps if step.by_the_model]

    @property
    def turns(self) -> list[Turn]:
        """This Attempt cut into turns, at the spawns that bracket them.

        A Step's sequence number is its `step-end`'s, so a spawn's lands **after** every Step of the
        turn it ran — which is what makes the spawn the closing bracket rather than the opening one.
        """
        turns, held, since = [], [], 0
        for step in sorted(self.steps, key=lambda one: one.seq):
            if not step.spawn:
                if step.by_the_model:
                    held.append(step)
                continue
            turns.append(Turn(step, tuple(held), tuple(self._claims_between(since, step.seq))))
            held, since = [], step.seq
        if held:
            turns.append(Turn(None, tuple(held), tuple(self._claims_between(since, None))))
        return turns

    def _claims_between(self, after: int, before: int | None) -> list[dict[str, Any]]:
        return [
            claim
            for claim in self.claims
            if after < int(claim.get("seq", 0)) and (before is None or int(claim.get("seq", 0)) < before)
        ]

    @property
    def checkpoints(self) -> list[Step]:
        return [step for step in self.steps if step.checkpoint]

    @property
    def tokens(self) -> int:
        """What this Attempt was **measured** spending. It is a floor wherever `unmeasured` is not
        empty, and a query that prints it as a total without saying so reports a fact it does not
        have (ADR-0022)."""
        return sum(step.tokens for step in self.steps)

    @property
    def cache_read(self) -> int:
        return sum(step.cache_read for step in self.steps)

    @property
    def unmeasured(self) -> list[Step]:
        """The turns of this Attempt that ran and never reported what they cost."""
        return [step for step in self.steps if step.unmeasured]

    def spent(self, tool: str) -> list[Step]:
        return [step for step in self.steps if step.tool == tool]

    @property
    def seconds(self) -> float:
        """Wall-clock from the Attempt's first Step to its close.

        The first Step rather than `attempt-open`: the deploy is Step 1 of an Attempt whose open
        line is written after it (`solver/run.py`), so the open would under-report by exactly the
        one operation most able to hang.
        """
        began = self.steps[0].ts if self.steps else at(str(self.opened.get("ts", "")))
        ended = at(str((self.closed or {}).get("ts", "")))
        if began is None or ended is None:
            return 0.0
        return (ended - began).total_seconds()


@dataclass
class Run:
    """One Run's stream, as the queries read it: the records, the Attempts, and the bodies if any."""

    run_id: str
    path: Path
    records: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    unreadable: int = 0

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [record for record in self.records if record.get("record") == kind]

    @property
    def opened(self) -> dt.datetime | None:
        return at(str(next(iter(self.of(RUN_OPEN)), {}).get("ts", "")))

    @property
    def closed(self) -> dict[str, Any] | None:
        return next(iter(self.of(RUN_CLOSE)), None)

    @property
    def last_written(self) -> dt.datetime | None:
        """When this stream was last added to — how a Run still being written is told from one that
        stopped being written some time ago."""
        return max((moment for record in self.records if (moment := at(str(record.get("ts", ""))))), default=None)

    @property
    def worked(self) -> bool:
        """Whether this Run reached Attempt-open — which is what decides it gets promoted.

        An `attempt-open` rather than any record carrying an `attempt_id`: the pre-flight probes
        write Steps under a name of their own without ever opening an Attempt, and promoting one of
        those would put a probe in `runs/` beside the Runs it is meant to be compared against.
        """
        return any(attempt.opened for attempt in self.attempts)

    @property
    def bodies(self) -> Path | None:
        """Where the Observation and Claim bodies sit, or `None` for a promoted stream.

        A promoted stream is the JSONL alone (ADR-0009), so this is how a query that needs prose
        finds out before it reports a count it could not take.
        """
        beside = self.path.parent
        return beside if (beside / OBSERVATIONS).is_dir() or (beside / CLAIMS).is_dir() else None

    def body(self, ref: str) -> bytes | None:
        beside = self.bodies
        if not ref or beside is None:
            return None
        try:
            return (beside / ref).read_bytes()
        except OSError:
            return None

    def said(self, attempt: Attempt) -> list[tuple[int, str]]:
        """Everything the model said in one Attempt, each with the sequence number it said it at.

        The half a promoted stream drops, so this is empty there. Paired with the sequence rather
        than returned as bare prose because *when* something was said is the whole of what a
        volunteered "impossible" does — it shortens a budget where it lands, not at the end.
        """
        return [
            (int(claim.get("seq", 0)), body.decode("utf-8", "replace"))
            for claim in attempt.claims
            if (body := self.body(str(claim.get("claim_ref", "")))) is not None
        ]


def _step(record: dict[str, Any], *, ended: bool) -> Step:
    return Step(
        seq=int(record.get("seq", 0)),
        ts=at(str(record.get("ts", ""))),
        attempt_id=str(record.get("attempt_id", "")),
        step_index=int(record.get("step_index", 0)),
        command_raw=str(record.get("command_raw", "")),
        command_normalised=str(record.get("command_normalised", "")),
        tool=str(record.get("tool", "")),
        source=str(record.get("source", SOURCE_SOLVER)),
        exit_code=record.get("exit_code"),
        duration_ms=int(record.get("duration_ms", 0)),
        observation_digest=str(record.get("observation_digest", "")),
        observation_bytes=int(record.get("observation_bytes", 0)),
        observation_ref=str(record.get("observation_ref", "")),
        checkpoint=record.get("checkpoint"),
        model=str(record.get("model", "")),
        tokens_in=int(record.get("tokens_in", 0)),
        tokens_out=int(record.get("tokens_out", 0)),
        cache_read=int(record.get("cache_read", 0)),
        cache_write=int(record.get("cache_write", 0)),
        usage_known=known if isinstance(known := record.get("usage_known"), bool) else None,
        ended=ended,
    )


def read(path: Path) -> Run:
    """One stream, in the order it was written, with its Attempts put back together.

    A line that will not parse is counted and stepped over — the last line of a crashed Run is a
    truncated one by design, and refusing the whole stream over it would lose the Run that produced
    it. A `step-begin` with no `step-end` survives as a Step that never ended, which is the pair's
    whole purpose.
    """
    path = Path(path)
    run = Run(run_id=path.stem if path.suffix else path.parent.name, path=path)
    attempts: dict[str, Attempt] = {}
    in_flight: dict[tuple[str, int], int] = {}

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            run.unreadable += 1
            continue
        if not isinstance(record, dict):
            run.unreadable += 1
            continue
        run.records.append(record)
        run.run_id = str(record.get("run_id", run.run_id)) or run.run_id
        kind, attempt_id = record.get("record"), str(record.get("attempt_id", ""))

        if kind == ATTEMPT_OPEN:
            attempts.setdefault(attempt_id, Attempt(attempt_id, run.run_id)).opened = record
            continue
        if kind == ATTEMPT_CLOSE:
            attempts.setdefault(attempt_id, Attempt(attempt_id, run.run_id)).closed = record
            continue
        if kind == CLAIMED:
            attempts.setdefault(attempt_id, Attempt(attempt_id, run.run_id)).claims.append(record)
            continue
        if kind == STEP_BEGIN:
            attempt = attempts.setdefault(attempt_id, Attempt(attempt_id, run.run_id))
            in_flight[attempt_id, int(record.get("step_index", 0))] = len(attempt.steps)
            attempt.steps.append(_step(record, ended=False))
            continue
        if kind == STEP_END:
            attempt = attempts.setdefault(attempt_id, Attempt(attempt_id, run.run_id))
            at = in_flight.pop((attempt_id, int(record.get("step_index", 0))), None)
            if at is None:
                attempt.steps.append(_step(record, ended=True))
            else:
                attempt.steps[at] = _step(record, ended=True)

    run.attempts = list(attempts.values())
    return run


def locate(paths: Sequence[str]) -> list[Path]:
    """The streams a caller named, or every promoted one where they named nothing.

    A directory is walked for both spellings, because the same stream has two homes: `runs/<id>.jsonl`
    once promoted, and `state/runs/<id>/stream.jsonl` while the bodies are still beside it.
    """
    found: list[Path] = []
    for name in paths or [PROMOTED]:
        where = Path(name)
        if where.is_file():
            found.append(where)
        elif where.is_dir():
            found.extend(sorted(where.glob("*.jsonl")))
            found.extend(sorted(where.glob("*/stream.jsonl")))
    return found


def load(paths: Sequence[str]) -> list[Run]:
    """Every stream a caller named, read — Runs that reached no Attempt included, because a query
    reporting nothing over a Run that never worked one is a different answer from a missing file."""
    return [read(path) for path in locate(paths)]


def table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    """One table shape for eight queries, so that eight answers read alike.

    Here rather than in a module of its own: a query with no way to print what it found is not a
    query, and the alternative is the same nine lines copied into every one of them.
    """
    body = [[("" if cell is None else str(cell)) for cell in row] for row in rows]
    if not body:
        return "  (nothing to report)"
    widths = [max(len(str(headers[column])), *(len(row[column]) for row in body)) for column in range(len(headers))]
    ruled = ["  ".join(str(head).ljust(width) for head, width in zip(headers, widths)).rstrip()]
    ruled.append("  ".join("-" * width for width in widths))
    ruled.extend("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in body)
    return "\n".join("  " + line for line in ruled)


def counted(tokens: int, unmeasured: int) -> str:
    """A token figure, marked for what it is: a total, a floor, or nothing measured at all.

    Here rather than in the two queries that print one, for the reason `table` is here — the same
    three lines copied twice is how `1234+` in one query and `0+` in the other happen, and a zero
    wearing a marker is still the zero ADR-0022 exists to stop being read as a fact.
    """
    if not unmeasured:
        return str(tokens)
    return f"{tokens}+" if tokens else ""


def heading(runs: Sequence[Run]) -> str:
    """What was read, said once at the top of every query — including the Runs that carry no bodies,
    since that is the difference between a query answering nothing and a query unable to ask."""
    if not runs:
        return "no stream found — name one, or promote a Run into runs/ first"
    return "\n".join(
        f"{run.run_id}: {len(run.attempts)} attempt(s), {len(run.records)} record(s)"
        + (f", {run.unreadable} unreadable line(s)" if run.unreadable else "")
        + ("" if run.bodies else " — bodies not beside this stream")
        for run in runs
    )
