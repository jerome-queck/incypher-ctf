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
  and a Run that produced gigabytes is still cheap to parse. **Claims get their own directory**
  beside them, because Flag verification sweeps one of the two and a Claim swept as an Observation
  is what would authorise a fabricated Flag.
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

# The two body channels, and the whole reason there are two. An Observation is real output from a
# real command; a Claim is anything the model *said*. Flag verification sweeps the first and never
# the second, so a Flag the model asserted can never be swept back in as evidence that it observed
# one (ADR-0014). They are separate directories rather than one directory with a field, because a
# grep that has to read a field to know what it is holding is a grep that will one day forget to.
OBSERVATIONS = "observations"
CLAIMS = "claims"

# Where a Step's bytes came from, and the third thing the two channels above cannot say. A Challenge
# **description** reaches `observations/` because the recon cascade records everything it does
# uniformly — not because anything ran — so the Board stating the shape of a Flag would otherwise be
# read as the Solver having found one ([ADR-0019](../docs/adr/0019-the-boards-statement-of-a-challenge-is-not-evidence.md)).
# It is one directory with a field rather than a third channel, for the reason the two above are
# not: a Claim and an Observation have different *bodies*, where these have the same body and
# differ only in who produced it.
#
# The fact, never the judgement — "may this authorise a submission" is a policy in `solver/flag.py`
# and is expected to change, so it is derived from this and never frozen into the record.
SOURCE_SOLVER = "solver"
SOURCE_BOARD = "board"

# Why an Attempt ended, and the whole of it — the vocabulary `CONTEXT.md` closes, given one home
# here because this is where a cause becomes permanent. `cut:novely` reaching the stream is a typo
# nothing goes red for, and it breaks the eval query that counts which counter fired.
#
# **Declared, never enforced.** ADR-0009 makes the record never fatal and never silent, so an
# unrecognised cause at 14:00 is a bug at the call site rather than a reason to end a Run. What one
# home buys is that the vocabulary cannot be invented twice.
#
# There is no `no-flag`: that is the *absence* of a cause rather than one, and naming it hides which
# counter fired — the only thing calibration needs to know.
FLAG = "flag"
CUT_REPETITION = "cut:repetition"
CUT_NOVELTY = "cut:novelty"
CUT_STEP_CLIFF = "cut:step-cliff"
CUT_BUDGET = "cut:budget"
CUT_INSTANCE_EXPIRED = "cut:instance-expired"
# ADR-0005's single narrow exception, in the list **because the aim is for it never to fire**: a
# cause nobody records is a defect nobody can watch trending to zero.
CUT_SELF_REPORTED_IMPOSSIBLE = "cut:self-reported-impossible"
CRASHED = "crashed"

CAUSES = (
    FLAG,
    CUT_REPETITION,
    CUT_NOVELTY,
    CUT_STEP_CLIFF,
    CUT_BUDGET,
    CUT_INSTANCE_EXPIRED,
    CUT_SELF_REPORTED_IMPOSSIBLE,
    CRASHED,
)


@dataclass(frozen=True)
class Usage:
    """What one Step cost, in tokens and never in money.

    Cost is tokens times a price table that lives outside the record and changes underneath it, so
    computing it at analysis time keeps the record true when prices move. Context size per Step
    needs no field either — it is `tokens_in + cache_read`.

    `known` is the difference between **nothing was spent** and **nobody said what was spent**, and
    it defaults true because every Step but one knows: a Step that ran no model spent nothing, and
    a command inside a turn spends nothing of its own, since the vendor meters the turn and its
    tokens ride the invocation's Step. The exception is a turn killed before the vendor reported
    it, whose zeros are an absent measurement rather than an absent spend — and a zero standing in
    for both is a record that is blindest about the Attempts that cost the most (#104).
    """

    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    cache_read: int = 0
    cache_write: int = 0
    known: bool = True


# A Step that invoked no model still names one, and the empty name is the fact rather than a
# placeholder — the deterministic cascade and the Instance path both spend Steps and no tokens.
NO_MODEL = Usage(model="")


class Step:
    """A Step in flight — the handle a `step-begin` returns, and the only route to its `step-end`.

    It carries the begin's clock and nothing else. The pair cannot be mismatched, because the end is
    reachable only through the begin — and the duration comes off that clock rather than from a
    caller, so no Step can report time it did not spend.
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
        """Everything here is a fact about what happened. The duration is not one a caller can
        report, so it is measured from the clock the begin took."""
        return self._recorder._close_step(self._identity, self._began_mono, exit_code, output, usage, checkpoint)


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
        for channel in (OBSERVATIONS, CLAIMS):
            (self.run_dir / channel).mkdir(parents=True, exist_ok=True)
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

    def intake(
        self,
        *,
        cycle: int,
        challenges: list[dict[str, Any]],
        scoreboard: list[dict[str, Any]],
        mana: dict[str, Any] | None,
        outcome: str,
        detail: str,
    ) -> None:
        """One Intake cycle, as a record of what the Board said rather than of what we concluded.

        `challenges` carries each Challenge's `(solves, value)` pair, which is the whole reason this
        is written every cycle: the scoring curve is fitted from the stream afterwards, so a later
        version needs no new Solver code to have the data (ADR-0015). The same line carries what was
        fetched and what was refused, because a Challenge whose attachment was over the cap is one
        every later judgement is made without.

        `outcome` names how the cycle ended and `detail` carries the sentence behind it. A failed
        sync is a **record**, never an exception, because a Board that could not be read is not a
        Board that emptied and a Run never ends on the Board looking finished — and the two ways of
        failing are named apart because spec #63 wants opposite responses to them.
        """
        self._write(
            "intake",
            {
                "cycle": cycle,
                "challenges": challenges,
                "scoreboard": scoreboard,
                "mana": mana,
                "outcome": outcome,
                "detail": detail,
            },
        )

    def triage(self, *, tiers: list[dict[str, Any]]) -> None:
        """The Tier each Challenge was given, **with the provenance that produced it**.

        The provenance is the point of the record rather than a decoration on it: a Tier extracted
        from a stated difficulty, one inferred from `solves` and one a model judged are three
        different qualities of evidence, and an analysis that cannot tell them apart cannot say
        whether the model's judgement was ever worth asking for — which is the one thing measured
        near zero and the reason Triage extracts rather than predicts (ADR-0006).

        There are no token counts on this line, and that is not an omission: a judge that spent any
        is an invocation that wrote its own Steps, and the same tokens counted twice would be worse
        than a line that leaves the counting to the records that observed it.
        """
        self._write("triage", {"tiers": tiers})

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
        exploring: bool = False,
    ) -> None:
        """Open an Attempt, including where every unsolved Challenge stood when it was picked.

        `attempt_sequence` is this Attempt's number *for that Challenge*, and `order_ranks` is
        Order's rank for everything still unsolved — without the second, a Run that passed over
        sixty Challenges and one that only ever had fourteen read identically.

        `exploring` says the reserved exploration share chose this pick rather than Order's top
        (ADR-0017). It is the whole measurement of whether the share earns its quarter of the Run,
        and it cannot be derived from the rank: an exploration turn that landed on Order's top is
        still one. It defaults because a reader defaults what is missing, which is the rule that
        lets this schema gain a field without invalidating a stream written before it.
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
                "exploring": exploring,
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

        `cause` is one of `CAUSES` above, and passing a string that is not one is a bug at the call
        site rather than a reason to end a Run: this refuses nothing, because ADR-0009 makes the
        record never fatal. `cut:novely` is the mistake the constants exist to stop.

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

    def claim(self, *, attempt_id: str, text: bytes) -> str:
        """Record something the model **said**, in the channel no check ever greps.

        A Claim is not a Step and is never written as one: it earns no `step-end`, and the line
        left behind carries a pointer, a length and a digest — never a word of the prose. That is
        what makes the Observation log orchestrator-append-only in the only sense that matters. The
        model still writes into the record, because a Run that lost the model's reasoning could not
        be read afterwards; what it cannot do is write into the half that authorises a Flag.

        Answers with the prose as the orchestrator will read it, so a caller that needs the approach
        label or a candidate Flag takes it from here rather than from the file.
        """
        ref, digest, nbytes, shown = self._body(text, CLAIMS, ".txt")
        self._write(
            "claim",
            {"attempt_id": attempt_id, "claim_ref": ref, "claim_digest": digest, "claim_bytes": nbytes},
        )
        return shown

    def step_begin(
        self,
        *,
        attempt_id: str,
        step_index: int,
        command_raw: str,
        command_normalised: str,
        tool: str,
        source: str = SOURCE_SOLVER,
    ) -> Step:
        """Record that a command is in flight, and hand back the only route to its end.

        `command_raw` is kept beside `command_normalised` because normalisation *is* the repetition
        counter's rule: keep only the normalised form and no future rule can be applied to a past
        Run.

        `source` defaults to the Solver's own work, which is what all but two Steps of an Attempt
        are — so the caller that has to say otherwise is the one probing what the Board merely
        stated.
        """
        identity = {
            "attempt_id": attempt_id,
            "step_index": step_index,
            "command_raw": command_raw,
            "command_normalised": command_normalised,
            "tool": tool,
            "source": source,
        }
        self._write("step-begin", identity)
        return Step(self, identity, self._mono())

    def _close_step(
        self,
        identity: dict[str, Any],
        began_mono: float,
        exit_code: int | None,
        output: bytes,
        usage: Usage,
        checkpoint: str | None,
    ) -> Observation:
        observation = self._store(output)
        self._write(
            "step-end",
            {
                **identity,
                "exit_code": exit_code,
                "duration_ms": round((self._mono() - began_mono) * 1000),
                "observation_digest": observation.digest,
                "observation_bytes": observation.nbytes,
                "observation_ref": observation.ref,
                "checkpoint": checkpoint,
                "model": usage.model,
                "tokens_in": usage.tokens_in,
                "tokens_out": usage.tokens_out,
                "cache_read": usage.cache_read,
                "cache_write": usage.cache_write,
                "usage_known": usage.known,
            },
        )
        return observation

    def _store(self, output: bytes) -> Observation:
        """Redact, keep the body whole on disk, and describe it for the line that will point at it.

        The digest is taken over the redacted bytes, so it can never become an oracle for a secret
        the redaction just removed.
        """
        ref, digest, nbytes, shown = self._body(output, OBSERVATIONS, ".out")
        return Observation(digest=digest, nbytes=nbytes, ref=ref, shown=shown)

    def _body(self, output: bytes, folder: str, suffix: str) -> tuple[str, str, int, str]:
        """One body file, whole on disk under the redaction, described for the line pointing at it.

        The number is the sequence the record about to be written will carry, so a body file and
        its line share an address and neither channel needs a second counter.
        """
        body = self._redactor.redact(output)
        ref = f"{folder}/{self._seq + 1:06d}{suffix}"
        self._attempt_twice(lambda: (self.run_dir / ref).write_bytes(body))
        return ref, digest_of(body), len(body), elide(body, self._observation_limit)

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
        self._attempt_twice(lambda: _locked_append(self.stream_path, line))

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


def _locked_append(path: Path, text: str) -> None:
    """The lock is what makes a record atomic against any other writer of this file; the flush is
    what makes a killed container lose at most the line it was writing."""
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        stream.write(text)
        stream.flush()


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
        _locked_append(stream, "\n")
    return max((seq for line in written.decode(errors="replace").splitlines() if (seq := _seq_in(line))), default=0)


def _seq_in(line: str) -> int | None:
    """This module's one reader, holding the third stability rule: access by name, with a default.

    A line that cannot answer is the crash's truncated one, or a record written by a schema this
    reader has never met. Neither is a reason to stop reading the rest.
    """
    try:
        return int(json.loads(line).get("seq"))
    except (ValueError, TypeError, AttributeError):
        return None
