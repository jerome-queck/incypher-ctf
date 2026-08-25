"""The Step stream — one append-only, sequence-numbered JSONL file per Run, under `/state`.

[ADR-0009](../docs/adr/0009-store-what-was-observed-derive-every-judgement.md) is the whole design:
**store what was observed, derive every judgement.** Anything a rule computes — novelty,
repetition, stall, "was that progress" — is recomputable from this stream and is never written into
it, because a stored judgement answers for exactly the one threshold that was live when it was
written, and every threshold in v1 is uncalibrated.

Three shapes follow from that and are the reason this module is not a logger:

- **Steps are a `step-begin` / `step-end` pair.** A single record at completion makes a Step that
  hung or crashed mid-flight invisible, and at a crash the command in flight is the prime suspect.
- **Observation bodies stay whole, in files beside the stream**, so a line is small and fixed-size
  and a Run that produced gigabytes is still cheap to parse.
- **Redaction happens here**, before anything reaches disk. `/state` gets copied, zipped and pasted
  into issues, so treating it as clean because it is untracked is how the leak happens anyway.

JSONL rather than SQLite, which is equally standard-library: a crash mid-write makes a corrupt
database where this makes a truncated last line, and losing one Step beats losing a Run.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solver.observation import OBSERVATION_LIMIT_BYTES, Observation, digest_of, elide
from solver.redaction import Redactor

# Bumped only for a change that the three stability rules cannot absorb — a field's meaning never
# changes once written, a retired name is never reused, and readers access by name with a default.
# Those rules are what let v5 analysis read a v1 Run, and this integer is what tells it which it has.
SCHEMA_VERSION = 1

# Redaction is exact-value replacement over everything written, with one exception. A Flag that a
# declared secret happened to be a substring of would be silently destroyed, and a destroyed Flag is
# unrecoverable where an over-long one is merely ugly.
NEVER_REDACTED = ("flag",)


@dataclass(frozen=True)
class Usage:
    """What one Step cost, in tokens and never in money.

    Cost is tokens times a price table that lives outside the record and changes underneath it, so
    computing it at analysis time keeps the record true when prices move. Context size per Step
    needs no field either — it is `tokens_in + cache_read`.
    """

    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0


class Step:
    """A Step in flight — the handle a `step-begin` returns, and the only route to its `step-end`.

    The pair cannot be mismatched because the end is reachable only through the begin, and the
    duration is measured here rather than passed in, so no caller can report one it did not spend.
    """

    def __init__(self, recorder: Recorder, identity: dict[str, Any], began_mono: float) -> None:
        self._recorder = recorder
        self._identity = identity
        self._began_mono = began_mono

    def end(
        self,
        *,
        exit_code: int | None,
        output: bytes,
        usage: Usage,
        checkpoint: str | None = None,
    ) -> Observation:
        """Close the Step: store its Observation whole, and write the line that points at it."""
        observation = self._recorder._store(output)
        self._recorder._write(
            "step-end",
            {
                **self._identity,
                "exit_code": exit_code,
                "duration_ms": round((self._recorder._mono() - self._began_mono) * 1000),
                "observation_digest": observation.digest,
                "observation_bytes": observation.nbytes,
                "observation_ref": observation.ref,
                "checkpoint": checkpoint,
                "model": usage.model,
                "tokens_in": usage.tokens_in,
                "tokens_out": usage.tokens_out,
                "cache_read": usage.cache_read,
                "cache_write": usage.cache_write,
            },
        )
        return observation


class Recorder:
    """The one writer of a Run's stream, and the only thing that ever touches its files.

    Everything above it hands over what it observed; nothing above it learns about sequence
    numbers, locks, body files or redaction. The Solver has no git binary and never commits during
    a Run — promoting a stream to the repository is a separate, human-run step afterwards.
    """

    def __init__(
        self,
        state: Path,
        run_id: str,
        redactor: Redactor,
        *,
        observation_limit: int = OBSERVATION_LIMIT_BYTES,
        now: Callable[[], dt.datetime] | None = None,
        mono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.run_id = run_id
        self.run_dir = Path(state) / "runs" / run_id
        self.stream_path = self.run_dir / "stream.jsonl"
        # Surfaced in the run-close record. A Run whose evidence is holed has to say so on its own
        # face; a Run that died because it could not write its evidence would be the worse trade.
        self.write_failures = 0
        self._redactor = redactor
        self._observation_limit = observation_limit
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._mono = mono
        self._bodies = self.run_dir / "observations"
        self._bodies.mkdir(parents=True, exist_ok=True)
        self._seq = _resume_after_a_crash(self.stream_path)

    def run_open(self, *, board_profile: dict[str, Any]) -> None:
        """Open the Run with the whole Board profile **as discovered**.

        ADR-0008's named failure is a profile that discovers the wrong thing, and without this a
        post-mortem cannot tell "the Solver behaved wrongly" from "the Solver read the Board
        wrongly" — which want completely different fixes.
        """
        self._write("run-open", {"board_profile": board_profile})

    def run_close(self, *, cause: str) -> None:
        self._write("run-close", {"cause": cause, "write_failures": self.write_failures})

    def attempt_open(
        self,
        *,
        attempt_id: str,
        challenge_id: int | str,
        challenge_name: str,
        category: str,
        challenge_type: str,
        solves_at_open: int,
        tier: int,
        budget_s: int,
        attempt_sequence: int,
        instance_until: str | None,
        order_ranks: dict[str, int],
    ) -> None:
        """Open an Attempt, including where every unsolved Challenge stood when it was picked.

        `attempt_sequence` is this Attempt's number *for that Challenge*, and `order_ranks` is
        Order's rank for everything still unsolved — without the second, a Run that passed over
        sixty Challenges and one that only ever had fourteen read identically.
        """
        self._write(
            "attempt-open",
            {
                "attempt_id": attempt_id,
                "challenge_id": challenge_id,
                "challenge_name": challenge_name,
                "category": category,
                "challenge_type": challenge_type,
                "solves_at_open": solves_at_open,
                "tier": tier,
                "budget_s": budget_s,
                "attempt_sequence": attempt_sequence,
                "instance_until": instance_until,
                "order_ranks": order_ranks,
            },
        )

    def attempt_close(
        self,
        *,
        attempt_id: str,
        cause: str,
        approach_label: str,
        solves_at_close: int,
        extensions_granted: int,
        flag: str | None,
    ) -> None:
        """Close an Attempt with the **cause** that ended it, never an outcome.

        `no-flag` is the absence of a cause rather than one, and naming it hides which counter
        fired — which is the only thing calibration needs to know.
        """
        self._write(
            "attempt-close",
            {
                "attempt_id": attempt_id,
                "cause": cause,
                "approach_label": approach_label,
                "solves_at_close": solves_at_close,
                "extensions_granted": extensions_granted,
                "flag": flag,
            },
        )

    def step_begin(
        self, *, attempt_id: str, step_index: int, command_raw: str, command_normalised: str, tool: str
    ) -> Step:
        """Record that a command is in flight, and hand back the only route to its end.

        `command_raw` is kept beside `command_normalised` because normalisation *is* the repetition
        counter's rule: keep only the normalised form and no future rule can be applied to a past
        Run.
        """
        identity = {
            "attempt_id": attempt_id,
            "step_index": step_index,
            "command_raw": command_raw,
            "command_normalised": command_normalised,
            "tool": tool,
        }
        self._write("step-begin", identity)
        return Step(self, identity, self._mono())

    def _store(self, output: bytes) -> Observation:
        """Redact, keep the body whole on disk, and describe it for the line that will point at it.

        The digest is taken over the redacted bytes, so it can never become an oracle for a secret
        the redaction just removed.
        """
        body = self._redactor.redact(output)
        ref = f"observations/{self._seq + 1:06d}.out"
        self._attempt_twice(lambda: (self.run_dir / ref).write_bytes(body))
        return Observation(
            digest=digest_of(body),
            nbytes=len(body),
            ref=ref,
            shown=elide(body, self._observation_limit),
        )

    def _write(self, kind: str, fields: dict[str, Any]) -> None:
        self._seq += 1
        moment = self._now()
        record = _redacted(
            {
                "schema_version": SCHEMA_VERSION,
                "seq": self._seq,
                "ts": moment.isoformat(),
                "mono": self._mono(),
                "record": kind,
                "run_id": self.run_id,
                **fields,
            },
            self._redactor,
        )
        line = json.dumps(record) + "\n"
        self._attempt_twice(lambda: self._append(line))

    def _append(self, line: str) -> None:
        """One record, appended under an exclusive lock and flushed before the lock is released.

        The lock is what makes a record atomic against any other writer of this file; the flush is
        what makes a killed container lose at most the line it was writing.
        """
        with self.stream_path.open("a", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            stream.write(line)
            stream.flush()

    def _attempt_twice(self, write: Callable[[], Any]) -> None:
        """Retried once, then counted — never fatal and never silent.

        The Solver's job is Flags, and `/state` is deletable mid-Run without costing the ability to
        solve; but the count reaches the run-close record, so a Run whose evidence is holed says so.
        """
        for last in (False, True):
            try:
                write()
                return
            except OSError:
                if last:
                    self.write_failures += 1


def _redacted(value: Any, redactor: Redactor) -> Any:
    """Every string anywhere in a record, with the known credentials taken out of it.

    A command line is the standing case — a `curl -H "Authorization: …"` reaches the stream through
    `command_raw` and never through an Observation.
    """
    if isinstance(value, str):
        return redactor.redact(value).decode("utf-8", "replace")
    if isinstance(value, dict):
        return {key: item if key in NEVER_REDACTED else _redacted(item, redactor) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item, redactor) for item in value]
    return value


def _resume_after_a_crash(stream: Path) -> int:
    """Where an existing stream got to, so a restarted Run continues rather than collides.

    A Run survives a restart while a process does not, and the file is per Run, so counting from one
    again would put two records at the same address. The last line may be a crash's truncated one:
    it is skipped rather than repaired, and terminated so that appending onto it does not fuse the
    crash with the next record and cost two lines instead of the one JSONL was chosen to cost.
    """
    if not stream.exists():
        return 0
    written = stream.read_bytes()
    if written and not written.endswith(b"\n"):
        with stream.open("a", encoding="utf-8") as opened:
            fcntl.flock(opened.fileno(), fcntl.LOCK_EX)
            opened.write("\n")
    seqs = []
    for line in written.decode(errors="replace").splitlines():
        try:
            seqs.append(int(json.loads(line)["seq"]))
        except (ValueError, KeyError, TypeError):
            continue
    return max(seqs, default=0)
