"""Flag verification and submission: the Solver's own work authorises, nothing it was told does.

The point of the whole exercise, and where the Claim/Observation split earns its keep. **A Flag the
model states is a Claim. The same string in real command output is an Observation, and the
Observation is what authorises a submit.** MIRAGE-Bench measures agents in unachievable states
fabricating an action **46–65%** of the time, so the distinction is not theoretical: a Solver that
believed what it was told would spend its submission slots on strings nothing ever produced.

The sweep therefore runs over the Step stream and reads `observations/` alone (`solver/record.py`).
That is structural rather than careful: a Claim's record carries `claim_ref` and no
`observation_ref`, so the field this module reads does not exist on the half of the record the
model wrote. Two more Observations are skipped for the same reason — a body line carrying
`carry.DERIVED`, which holds a model-authored approach label the model can print back out, and this
module's own reports, since a sweep that read its last report would authorise a candidate on the
strength of having mentioned it. Both are rules about a **body**: the model's own prose goes through
neither, because a Flag that appeared only there is `unverified` already and refusing it would drop a
nomination rather than protect anything.

A fourth is skipped, and it is the one the Claim/Observation pair could not name
([ADR-0019](../docs/adr/0019-the-boards-statement-of-a-challenge-is-not-evidence.md)): a Challenge's
**description** and the flag-scan over it are recorded as Observations because the recon cascade
records everything it does uniformly, not because anything ran. Four of the nine Brunner
descriptions our Runs persisted end in a flag-format section, and **all four spell the wrapper
out** — so wherever that section exists the sweep finds a decoy, every time. The record says which
Steps those are — `source` — and this module reads it.

How strongly a candidate is known is the whole of the policy below, and there are five answers:

- **reproduced** — the exact command that emitted it was replayed once and the same string came
  back. This is the only strength that may spend a Board's last attempt.
- **observed** — a real command's output carried it, and the replay did not confirm it: it differed,
  it failed, or the emitting command was the Solver's own probe and is not a shell command at all.
- **unverified** — no Observation carries it. The model said it and nothing else did. Still
  submitted while attempts remain, and recorded as what it is — a per-model number worth having.
- **guessed** — it *was* authorised by an Observation, and then the Instance that minted it expired
  underneath it. Known from our own clock rather than from a rejected Flag.
- **stated** — the Board's own prose carried it and the model repeated it. The only strength with
  positive evidence *against* it, so it is the only one an unlimited Board does not wave through: it
  waits for the reserved tail, where the slot it spends has nothing else to be spent on (ADR-0021).

Two submission branches follow, and only the first has ever been exercised on a real Board:

- **Unlimited attempts** — submit as soon as a candidate is reproduced. Speed wins, and holding a
  Flag back is the *no-sandbagging* rule Brunner bans outright.
- **Limited, or unknown** — the full gate applies and the last attempt is reserved for a reproduced
  candidate. Brunner reported `max_attempts` 0 on all 74 Challenges, so unknown is treated as
  limited and this branch is covered by a test rather than discovered live.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from solver.board import Board, Verdict
from solver.carry import DERIVED
from solver.instance import Instances, Lease, submission_shape
from solver.record import NO_MODEL, SOURCE_SOLVER, Recorder
from solver.shell import run
from solver.stall import replayable
from solver.wrapper import compiled, found_in

# Every line this module writes about itself opens with this, for the reason `solver/recon.py`
# gives: a reader of a stream can tell what the Solver said from what a tool said, and never takes
# one of these for a shell command they could replay.
MARK = "[flag]"

# How a candidate is known, strongest first — which is also the order candidates are submitted in.
REPRODUCED = "reproduced"
OBSERVED = "observed"
UNVERIFIED = "unverified"
GUESSED = "guessed"
# The Board's own prose carried it and the model repeated it. Weakest of the five, because it is the
# only one there is positive evidence *against*: a Flag-shaped string in a Challenge's statement is
# the Board showing the wrapper's shape far more often than it is the Flag (ADR-0021).
STATED = "stated"
STRENGTHS = (REPRODUCED, OBSERVED, UNVERIFIED, GUESSED, STATED)

# The three tools this module spends Steps on. The first and the last are the Solver talking *about*
# candidates rather than a command producing one, so the sweep never reads their bodies; the replay
# is a real command and its output is an Observation like any other.
SWEEP = "flag-sweep"
REPLAY = "flag-replay"
SUBMIT = "flag-submit"
NEVER_SWEPT = (SWEEP, SUBMIT)

# Read in blocks so an Observation nothing bounded — `aggregated_output` from the vendor's stream is
# whatever a command wrote — is never held whole in memory. A partial line is carried across the
# boundary so a Flag straddling one is still matched, and the carry is bounded because output with
# no newline in it is a stream rather than a line, and a Flag longer than this is not a Flag.
SCAN_BLOCK_BYTES = 1 << 20
LINE_CARRY_BYTES = 1 << 16

# Characters that carry no width at all, so a Flag holding one looks exactly like the Flag that
# grades. They reach a candidate through a copy out of a rendered page as readily as through malice.
INVISIBLE = "​‌‍⁠﻿ ᠎"

# Letters that *are* another script's and are drawn the same. NFKD folding reaches fullwidth and
# mathematical alphabets and never reaches these — Cyrillic а and Latin a are unrelated codepoints
# with identical glyphs — so the ones a Flag body could plausibly hold are named here.
LOOKALIKES = {
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O", "Р": "P", "С": "C",
    "Т": "T", "Х": "X", "ѕ": "s", "α": "a", "ο": "o", "ρ": "p", "ν": "v", "Α": "A", "Β": "B",
    "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P",
    "Τ": "T", "Χ": "X", "Ⅰ": "I", "ℓ": "l", "‐": "-", "‑": "-", "–": "-", "—": "-",
}  # fmt: skip


@dataclass(frozen=True)
class ReplayLimits:
    """What one replay may spend, as parameters rather than constants like every other number here.

    The output cap is far larger than the recon cascade's, and for a different question: recon caps
    what a model is shown, where a replay has to see whether one exact string came back. A cap that
    landed before the Flag did would answer *not reproduced* about the cap rather than about the
    command.
    """

    seconds: float = 20.0
    output_bytes: int = 1 << 20


# The replay's one edge to a process: it is handed the command exactly as the vendor's agent ran it,
# the directory it was run in, and the caps — and answers as a Step's own tuple. Injectable because
# that is the honest place to stand a test: above it is this module's rule, below it is a fork.
Runner = Callable[[str, Path, ReplayLimits], tuple[int | None, bytes]]


@dataclass(frozen=True)
class Candidate:
    """One string that might be this Challenge's Flag, and what is known about how it got here.

    `command` and `ref` are an Observation's and are empty on an unverified candidate, which is the
    Claim/Observation split made structural in the same way `codex.Taken` makes it: a candidate the
    model merely stated carries nothing that points at output, so nothing that reads output can
    accidentally read it as authorised.
    """

    text: str
    strength: str
    command: str = ""
    ref: str = ""
    # The replay is spent once per candidate, so a command that is slow or destructive is not run
    # again by a second pass over the same candidates.
    replayed: bool = False

    @property
    def authorised(self) -> bool:
        """Whether an Observation carried it — which is what a submission is allowed to rest on."""
        return self.strength in (REPRODUCED, OBSERVED)

    @property
    def rank(self) -> int:
        """Where it sorts against the others: strongest first, which is the order slots are spent
        in and the reason `STRENGTHS` is written as a sequence rather than as five constants."""
        return STRENGTHS.index(self.strength)


@dataclass(frozen=True)
class Slots:
    """This Challenge's submission budget as the Board states it.

    CTFd writes *unlimited* as `max_attempts` 0 and `spent` is our own count held server-side, so it
    survives a container restart where a local tally would not. **Absent is unknown and unknown is
    treated as limited**: a Board that does not say cannot be assumed generous.
    """

    max_attempts: int | None = None
    spent: int = 0

    @property
    def unlimited(self) -> bool:
        return self.max_attempts == 0

    @property
    def left(self) -> int | None:
        """Attempts remaining, or `None` where the Board never stated a maximum. Read only where
        `unlimited` is false, so the `None` here is always the unknown one."""
        if self.max_attempts is None or self.unlimited:
            return None
        return max(self.max_attempts - self.spent, 0)


@dataclass
class Pace:
    """The Board-wide incorrect-submissions-per-minute limit, kept to on our side of it.

    The setting is `incorrect_submissions_per_min` and is **unreadable to a non-admin**, so CTFd's
    own default is assumed and the number is a parameter like every other uncalibrated one. Only a
    Flag the Board graded *wrong* counts against it, which is what CTFd itself counts — so a Run
    that is solving things is never paced by this at all.

    It is Board-wide rather than per-Challenge, which is the whole reason it is held here and handed
    in: one Challenge burning the limit is one Challenge spending every other Challenge's slots.
    """

    per_minute: int = 10
    window_seconds: float = 60.0
    wrong: list[dt.datetime] = field(default_factory=list)

    def wait(self, now: dt.datetime) -> float:
        """How long to hold before the next submission, in seconds — zero while there is room."""
        self.wrong = [moment for moment in self.wrong if (now - moment).total_seconds() < self.window_seconds]
        if len(self.wrong) < self.per_minute:
            return 0.0
        oldest = min(self.wrong)
        return max(self.window_seconds - (now - oldest).total_seconds(), 0.0)

    def graded(self, verdict: Verdict, at: dt.datetime) -> None:
        if not verdict.solved:
            self.wrong.append(at)


@dataclass(frozen=True)
class Graded:
    """One candidate the Board answered about: what it said, and the shape its message named.

    A late Flag and a chall-manager that was down at submit both grade `incorrect` through an
    identical status, so the shape comes from `instance.submission_shape` and never from the code.
    """

    candidate: Candidate
    verdict: Verdict
    shape: str = ""


@dataclass(frozen=True)
class Outcome:
    """What one pass over a Challenge's candidates did.

    `solved` is *there is nothing left to win here* and `flag` is *this string is the Flag*, which
    are two different facts: a Board answering `already_solved` says the first and says nothing at
    all about the string it was sent, so it leaves `flag` empty rather than recording a junk string
    as the one that solved a Challenge.

    `held` is every candidate the gate refused a slot to — the reserve, a spent budget, or the
    confusable guard. It is what the Run's reserved tail comes back for with `last_call`, and it is
    reported rather than dropped because a candidate nobody submitted and nobody recorded is
    indistinguishable afterwards from one that was never found.
    """

    solved: bool = False
    flag: str = ""
    graded: tuple[Graded, ...] = ()
    held: tuple[Candidate, ...] = ()


def confusables(candidate: str) -> tuple[str, ...]:
    """Every character in a candidate that is drawn like an ASCII one it is not.

    Run before a submission, because a homoglyph spends a slot to be told `incorrect` about a Flag
    that was right. It names what it found rather than repairing it: a repaired Flag is a *different
    string* from the one an Observation carried, and this module submits nothing an Observation did
    not carry.

    Deliberately not "anything non-ASCII", and deliberately not "anything that folds to ASCII"
    either — the second would refuse `zephyr{caf\u00e9}` for the whole Run, which is this guard
    costing a solve rather than saving one. What it looks for is a character *substituted* for an
    ASCII one: another script's letter drawn identically, a compatibility form of an ASCII letter,
    or something with no width at all. An accent is none of those, and neither is an emoji.
    """
    named = []
    for at, character in enumerate(candidate):
        looks_like = LOOKALIKES.get(character) or _folded(character)
        if character in INVISIBLE:
            named.append(f"invisible U+{ord(character):04X} at {at}")
        elif looks_like and looks_like != character:
            named.append(f"{character!r} U+{ord(character):04X} at {at} is drawn like {looks_like!r}")
    return tuple(named)


class Flags:
    """This Run's Flag candidates, and the submissions they do or do not authorise.

    Deliberately stateless about what has been found: `candidates` is a pure read of the record, so
    the same Attempt swept twice answers the same way, and an offline replay of a stored stream
    recomputes exactly what the Solver saw (ADR-0009). What it *is* stateful about is the pace,
    which is a fact about the Board rather than about a Challenge and so outlives every Attempt.
    """

    def __init__(
        self,
        board: Board,
        recorder: Recorder,
        *,
        flag_wrappers: Sequence[str],
        instances: Instances | None = None,
        pace: Pace | None = None,
        limits: ReplayLimits = ReplayLimits(),
        runner: Runner | None = None,
        now: Callable[[], dt.datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        step_numbers: Callable[[], int] | None = None,
    ) -> None:
        self._board = board
        self._recorder = recorder
        self._wrappers = tuple(flag_wrappers)
        self._instances = instances
        self._pace = pace or Pace()
        self._limits = limits
        self._runner = runner or _shell
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._sleep = sleep
        # A sweep, a replay and a submission are Steps of the Attempt they belong to, so the numbers
        # come from whoever owns it — two counters would put two Steps at the same address.
        self._step_numbers = step_numbers or _from_one()

    def candidates(self, *, attempt_id: str, said: Sequence[str] = ()) -> tuple[Candidate, ...]:
        """Everything that could be this Challenge's Flag, and what authorises each one.

        `said` is the model's own prose, and passing it is not a hole in the rule: it *nominates* a
        candidate and never authorises one. A string that also appears in an Observation comes back
        `observed` because the Observations are read first; a string that appears nowhere else comes
        back `unverified` and is submitted as that.
        """
        shapes = ", ".join(self._wrappers)
        matchers, broken = compiled(self._wrappers)
        if not matchers:
            told = f"{MARK} {'; '.join(broken) or 'the Board states no Flag wrapper'} — nothing was swept"
            self._record(SWEEP, f"{MARK} sweep {shapes}", told.encode(), attempt_id, ok=False)
            return ()
        found: dict[str, Candidate] = {}
        by_the_board: set[str] = set()
        swept = 0
        stated = 0
        for command, ref, source in _bodies_of(self._recorder.stream_path, attempt_id):
            # An allowlist rather than a refusal of `SOURCE_BOARD`, so a source nobody has
            # thought of yet arrives non-authorising (ADR-0019).
            if source != SOURCE_SOLVER:
                stated += 1
                # Read rather than skipped, because *which* strings the Board stated is what tells a
                # model repeating one from a model that found it (ADR-0021).
                by_the_board.update(_in_body(self._recorder.run_dir / ref, matchers))
                continue
            swept += 1
            for text in _in_body(self._recorder.run_dir / ref, matchers):
                found.setdefault(text, Candidate(text, OBSERVED, command=command, ref=ref))
        for text in (match for prose in said for match in _matches(prose.encode(), matchers)):
            # A string a command produced is already here and outranks both — the Board having also
            # stated it says nothing about whether the Solver later found it.
            found.setdefault(text, Candidate(text, STATED if text in by_the_board else UNVERIFIED))
        ordered = tuple(sorted(found.values(), key=lambda candidate: candidate.rank))
        self._record(
            SWEEP,
            f"{MARK} sweep {shapes}",
            (
                f"{MARK} swept {swept} observation(s), passed over {stated} the Board stated, and no claim"
                + "".join(f"\n{MARK} {one}" for one in broken)
                + f"\n{_listed(ordered)}"
            ).encode(),
            attempt_id,
            ok=True,
        )
        return ordered

    def submit(
        self,
        candidates: Sequence[Candidate],
        *,
        attempt_id: str,
        challenge_id: int | str,
        slots: Slots,
        workdir: Path,
        lease: Lease | None = None,
        last_call: bool = False,
    ) -> Outcome:
        """Work the candidates in order of strength until one grades or the gate holds the rest.

        Each candidate is replayed once, checked against the Instance's own deadline, put past the
        confusable guard and then either submitted or held — in that order, because every step of it
        can change what the next one is entitled to do.

        `last_call` is the Run's reserved tail releasing the reserve: there is no later Attempt for
        the last attempt to be reserved *for*, so a held candidate is submitted rather than carried
        out of the Run unsubmitted.
        """
        graded: list[Graded] = []
        held: list[Candidate] = []
        spent_here = 0
        for found in candidates:
            candidate = self._degraded(self._reproduced(found, attempt_id=attempt_id, workdir=workdir), lease)
            if refusal := _refuses(candidate, slots, spent_here, last_call=last_call):
                held.append(candidate)
                self._record(
                    SUBMIT, f"{MARK} hold {candidate.text}", f"{MARK} {refusal}".encode(), attempt_id, ok=False
                )
                continue
            answer = self._graded(candidate, attempt_id=attempt_id, challenge_id=challenge_id)
            graded.append(answer)
            spent_here += 1
            if answer.verdict.solved:
                self._release(challenge_id, lease, attempt_id=attempt_id)
                return Outcome(True, candidate.text if answer.verdict.correct else "", tuple(graded), tuple(held))
        return Outcome(False, "", tuple(graded), tuple(held))

    def _reproduced(self, candidate: Candidate, *, attempt_id: str, workdir: Path) -> Candidate:
        """Replay the exact command that emitted the candidate, **once**, and see it come back.

        Reproduction is what separates *it appeared* from *it is real*: a string that showed up in
        one command's output and never again is as likely to be an artefact of that command as a
        Flag. The replay runs the model's own command line outside the vendor's sandbox, which is
        the same trust the container itself already extends to challenge-supplied code — and the
        environment allowlist covers it, as it covers every child (`solver/credentials.py`).
        """
        if candidate.strength != OBSERVED or candidate.replayed:
            return candidate
        spent = replace(candidate, replayed=True)
        if not replayable(candidate.command):
            told = (
                f"{MARK} not replayed — {candidate.command} is the Solver's own probe rather than a "
                f"shell command, so this candidate stays {OBSERVED}"
            )
            self._record(REPLAY, f"{MARK} replay — {candidate.command}", told.encode(), attempt_id, ok=False)
            return spent
        exit_code, output = self._runner(candidate.command, workdir, self._limits)
        came_back = candidate.text in output.decode("utf-8", "replace")
        self._record(REPLAY, f"{MARK} replay — {candidate.command}", output, attempt_id, ok=came_back)
        return replace(spent, strength=REPRODUCED) if came_back else spent

    def _degraded(self, candidate: Candidate, lease: Lease | None) -> Candidate:
        """A candidate an Observation authorised, once the Instance that minted it has expired.

        An Isolated Challenge's Flag belongs to the Instance it was deployed for, so when the TTL
        wins the string stays what it was and stops being a Flag. The Solver learns that from its
        own clock — the deploy's `until` against now — rather than from a Board rejecting a Flag it
        had every reason to believe in.
        """
        if not candidate.authorised or lease is None or lease.until is None or self._now() < lease.until:
            return candidate
        return replace(candidate, strength=GUESSED)

    def _graded(self, candidate: Candidate, *, attempt_id: str, challenge_id: int | str) -> Graded:
        """Spend one submission slot, paced, and read the verdict out of the body.

        The pacing is inside this method rather than beside it because the wait is part of what the
        submission cost, and a Run that spent forty seconds holding for the limiter should say so on
        the Step that spent them.
        """
        waited = self._paced()
        verdict = self._board.submit(challenge_id, candidate.text)
        at = self._now()
        self._pace.graded(verdict, at)
        shape = submission_shape({"message": verdict.message})
        told = (
            f"{MARK} submitted a {candidate.strength} candidate and the Board graded it "
            f"{verdict.outcome!r} (HTTP {verdict.http_status}): {verdict.message or 'no message'}"
            + (f"\n{MARK} {shape}" if shape else "")
            + (f"\n{MARK} held {waited:.1f}s first, under {self._pace.per_minute} wrong/min" if waited else "")
        )
        self._record(SUBMIT, f"{MARK} submit {candidate.text}", told.encode(), attempt_id, ok=verdict.solved)
        return Graded(candidate, verdict, shape)

    def _paced(self) -> float:
        """Hold until the Board-wide limiter has room. Never longer than its own window."""
        waited = self._pace.wait(self._now())
        if waited > 0:
            self._sleep(waited)
        return waited

    def _release(self, challenge_id: int | str, lease: Lease | None, *, attempt_id: str) -> None:
        """A correct Flag releases the Instance it was found on, and **a 404 is success** — the
        Challenge's own `destroy_on_flag` may have got there first. The whole rule lives in
        `Instances.terminate`; what is here is that a solve is one of the moments it runs.

        The Lease says *whether* one is held and the caller says *which Challenge*, so the id that
        was submitted against is the id that is released — two sources for one Challenge is a
        mismatched pair somebody eventually expresses.
        """
        if lease is not None and self._instances is not None:
            self._instances.terminate(challenge_id, attempt_id=attempt_id, after_flag=True)

    def _record(self, tool: str, command: str, output: bytes, attempt_id: str, *, ok: bool) -> str:
        """One Step per thing this module did, as a `step-begin` / `step-end` pair like any other."""
        step = self._recorder.step_begin(
            attempt_id=attempt_id,
            step_index=self._step_numbers(),
            command_raw=command,
            command_normalised=" ".join(command.split()),
            tool=tool,
        )
        return step.end(exit_code=0 if ok else 1, output=output, usage=NO_MODEL).shown


def _refuses(candidate: Candidate, slots: Slots, spent_here: int, *, last_call: bool) -> str:
    """Why this candidate may not be submitted right now, or the empty string where it may.

    The whole submission policy, in the order the checks have to run in. The guard is first because
    it is the one refusal that is about the candidate rather than about the budget: a homoglyph is
    wrong however many attempts remain. It is also the one refusal `last_call` overrides outright —
    a homoglyph submitted at the end of a Run spends a slot nothing else will ever use, where one
    held back is a Flag the Solver found and never submitted.

    A `stated` candidate is refused next, and **above the unlimited branch**, which is the one place
    this module does not let speed win. An unlimited Board still has the Board-wide
    `incorrect_submissions_per_min` limiter, and that is every *other* Challenge's budget — so a
    string the Board itself showed us waits for the tail, where the slot has nothing else to be spent
    on. It is not sandbagging: the candidate is submitted, at the one moment holding it costs nobody
    anything (ADR-0021).
    """
    if not last_call and (wrong := confusables(candidate.text)):
        return f"the confusable-character guard held it back — {'; '.join(wrong)}"
    if not last_call and candidate.strength == STATED:
        return (
            "the Board stated this string itself and the model repeated it, so it waits for the "
            "reserved tail, where the slot it spends has nothing else to be spent on"
        )
    if slots.unlimited:
        return ""
    left = slots.left
    if left is not None and left - spent_here <= 0:
        return f"the Board states {slots.max_attempts} attempt(s) and every one is spent"
    if candidate.strength == REPRODUCED or last_call:
        return ""
    if left is None:
        return (
            f"max_attempts is unknown, which is treated as limited — so every attempt could be the "
            f"last, and this candidate is {candidate.strength} rather than {REPRODUCED}"
        )
    if left - spent_here <= 1:
        return f"the last attempt is reserved for a {REPRODUCED} candidate and this one is {candidate.strength}"
    return ""


def _bodies_of(stream: Path, attempt_id: str) -> Iterator[tuple[str, str, str]]:
    """Every Observation this Attempt produced: the command that emitted it, where its body is, and
    where its bytes came from. What that last one *entitles* a candidate to is the caller's rule.

    Reading the record rather than the live Steps is what makes verification recomputable offline
    from a stored stream, which is ADR-0009's whole trade. It is also what makes *never sweeping a
    Claim* structural: a Claim's record has no `observation_ref` at all, so the field this reads
    does not exist on the half of the record the model wrote.

    Access is by name with a default, per the record's third stability rule — a line a later schema
    wrote, or a crash's truncated last one, is skipped rather than fatal. An absent source is the
    Solver's own work, so a Run promoted before that field existed replays exactly as it behaved.
    """
    if not stream.exists():
        return
    for line in stream.read_text(errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict) or record.get("attempt_id") != attempt_id:
            continue
        if record.get("tool") in NEVER_SWEPT or not (ref := record.get("observation_ref")):
            continue
        yield str(record.get("command_raw", "")), str(ref), str(record.get("source", SOURCE_SOLVER))


def _in_body(body: Path, matchers: Sequence[re.Pattern[bytes]]) -> Iterator[str]:
    """Every wrapper match in one Observation's body, minus any derived record the body carries.

    `carry.DERIVED` marks the one line per Attempt holding a model-authored approach label. That
    label crosses into the next Attempt's prompt, so the model can print it back out — into a note
    file it was invited to leave in the working directory, say — and a Flag it had written into its
    own label would then be swept in as `observed`, replayed to `reproduced` off that same `cat`,
    and spend the attempt the gate reserves.

    **It is a body rule, and lives here rather than in `_matches`**, which the model's own prose
    also goes through. A Flag that appeared only in prose is `unverified` by construction — the
    body loop runs first — and `unverified` is exactly the answer this rule wants. Refusing to
    match it there protects nothing and drops the nomination instead, on a Board reporting
    `max_attempts` 0 where an unverified candidate would have been submitted.
    """
    try:
        for line in _lines(body):
            if DERIVED.encode() not in line:
                yield from _matches(line, matchers)
    except OSError:
        # A body file that has been deleted under us costs the candidates it held and nothing more:
        # `/state` is deletable mid-Run by design, and a Run that died sweeping is the worse trade.
        return


def _matches(text: bytes, matchers: Sequence[re.Pattern[bytes]]) -> Iterator[str]:
    """Every wrapper's matches in some bytes — one line of a body, or one whole model message.

    Every shape the Board states is matched separately, never as one alternation built out of them:
    an alternative that starts earlier eats the bytes a later one would have matched
    (`solver/wrapper.py`).
    """
    for match in found_in(text, matchers):
        yield match.decode("utf-8", "replace")


def _lines(body: Path) -> Iterator[bytes]:
    carried = b""
    with body.open("rb") as reading:
        while block := reading.read(SCAN_BLOCK_BYTES):
            *whole, carried = (carried + block).split(b"\n")
            yield from whole
            carried = carried[-LINE_CARRY_BYTES:]
    if carried:
        yield carried


def _listed(candidates: Sequence[Candidate]) -> str:
    if not candidates:
        return f"{MARK} no candidate matched the Board's wrapper"
    return "\n".join(
        f"{MARK} {candidate.strength}: {candidate.text}"
        + (f" — from {candidate.ref} ({candidate.command})" if candidate.ref else "")
        for candidate in candidates
    )


def _folded(character: str) -> str:
    """The ASCII an ASCII character was *written as*, or the empty string where it is its own letter.

    A compatibility decomposition is the substitution — fullwidth, mathematical, script, a Roman
    numeral — and a canonical one is a letter with an accent on it, which is a character in its own
    right and not a disguise for the letter underneath. So the two normal forms are compared: where
    they agree, the fold is canonical and this is not a lookalike.
    """
    compatibility = unicodedata.normalize("NFKD", character)
    if compatibility == unicodedata.normalize("NFD", character):
        return ""
    folded = compatibility.encode("ascii", "ignore").decode()
    return folded if folded.strip() else ""


def _from_one() -> Callable[[], int]:
    numbers = iter(range(1, 1 << 30))
    return lambda: next(numbers)


def _shell(command: str, workdir: Path, limits: ReplayLimits) -> tuple[int | None, bytes]:
    """The real replay: the model's own command line, in the directory it was run in.

    `sh -c` rather than a parse of the command: what the vendor recorded is a shell line — its own
    `/bin/zsh -lc '…'` wrapper included — and a Solver that re-quoted it would be replaying
    something other than the exact command, which is the one thing reproduction turns on.
    """
    return run(
        ("/bin/sh", "-c", command),
        budget=limits.seconds,
        cap=limits.output_bytes,
        mark=MARK,
        cwd=workdir,
    )
