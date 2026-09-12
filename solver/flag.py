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

The reports rule is a body rule *and* a record rule, and it took a live Board to find out why —
`NOT_EVIDENCE` carries that story.

A fifth is skipped on the strength of the **command** rather than anything in its output: one that
reads the Run's own record. v1 does not take that record out of the model's reach and
[ADR-0028](../docs/adr/0028-the-runs-record-stays-reachable-and-the-evidence-is-refused-instead.md)
measures why each boundary costs more than it buys, so the access stays and `_reads_the_record`
refuses the evidence instead.

A fourth is skipped, and it is the one the Claim/Observation pair could not name
([ADR-0019](../docs/adr/0019-the-boards-statement-of-a-challenge-is-not-evidence.md)): a Challenge's
**description** and the flag-scan over it are recorded as Observations because the recon cascade
records everything it does uniformly, not because anything ran. Four of the nine Brunner
descriptions our Runs persisted end in a flag-format section, and **all four spell the wrapper
out** — so wherever that section exists the sweep finds a decoy, every time. The record says which
Steps those are — `source` — and this module reads it.

How strongly a candidate is known is the whole of the policy below, and there are seven answers:

- **reproduced** — the exact command that emitted it was replayed once and the same string came
  back. This is the only strength that may spend a Board's last attempt.
- **observed** — a real command's output carried it, and the replay did not confirm it: it differed,
  it failed, or the emitting command was the Solver's own probe and is not a shell command at all.
- **unverified** — no Observation carries it. The model said it and nothing else did. Recorded as
  what it is, which is a per-model number worth having.
- **guessed** — it *was* authorised by an Observation, and then the Instance that minted it expired
  underneath it. Known from our own clock rather than from a rejected Flag.
- **stated** — the Board's own prose carried it and the model repeated it. Positive evidence
  *against*, so it waits for the reserved tail on any Board at all (ADR-0021).
- **crowded** — one command emitted it alongside more Flags than a Challenge has. A Challenge has
  one, so the command was reading a list rather than solving, and the whole of what it emitted is
  demoted together (`_crowded`).
- **template** — the string is a Flag's shape rather than a Flag, a regular expression or a
  placeholder token, so no provenance can make it right. Held whether or not this is the reserved
  tail (`_describes`, ADR-0029).

Two submission branches follow, and only the first has ever been exercised on a real Board:

- **Unlimited attempts** — a candidate the Solver's own work produced is submitted the moment it is
  reproduced, because speed wins and holding a Flag back is the *no-sandbagging* rule Brunner bans
  outright. The two bounds here are the ones a Board with no budget of its own does not supply:
  everything below `observed` waits for the reserved tail (`NEEDS_THE_SOLVERS_OWN_WORK`), and an
  Attempt stops once the Board has graded it wrong `WRONG_CEILING` times.
- **Limited, or unknown** — the full gate applies and the last attempt is reserved for a reproduced
  candidate. The Board's stated maximum is itself the bound on a wrong-Flag loop, so neither of the
  two above is repeated here. Brunner reported `max_attempts` 0 on all 74 Challenges, so unknown is
  treated as limited and this branch is covered by a test rather than discovered live.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import time
import unicodedata
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath

from solver.board import Board, Verdict
from solver.carry import DERIVED
from solver.candidate_template import describes_template
from solver.instance import Instances, Lease, submission_shape
from solver.record import NO_MODEL, SOURCE_SOLVER, Recorder
from solver.shell import run
from solver.stall import replayable
from solver.wrapper import compiled, found_in
from solver.write_reservation import Capacity, EffectIdentity, ReservedEffect, RetentionPolicy

# Every line this module writes about itself opens with this, for the reason `solver/recon.py`
# gives: a reader of a stream can tell what the Solver said from what a tool said, and never takes
# one of these for a shell command they could replay.
MARK = "[flag]"

# How a candidate is known, strongest first — which is also the order candidates are submitted in.
REPRODUCED = "reproduced"
OBSERVED = "observed"
UNVERIFIED = "unverified"
GUESSED = "guessed"
# The Board's own prose carried it and the model repeated it, and it is the only strength there is
# positive evidence *against*: a Flag-shaped string in a Challenge's statement is the Board showing
# the wrapper's shape far more often than it is the Flag (ADR-0021).
STATED = "stated"
# One command emitted this alongside more Flags than a Challenge has. A Challenge has exactly one,
# so N of them from one command are N-1 wrong at best and the command was reading something that is
# not this Challenge's answer — a repository the model cloned, the Run's own record, a wordlist.
# Second-last because `stated` is at least one string the Board really did print, where this is
# a set that has disproved itself.
CROWDED = "crowded"
# The candidate is not a value at all: it is a Flag's *shape*, a regular expression or a placeholder
# token, so no evidence about where it was seen can make it right. `compfest-2026-seg2` submitted
# both `COMPFEST18{[A-z0-9_-]+}` — CTFd's submission-box `placeholder=` attribute, swept off a
# challenge-detail page — and `COMPFEST18{FAKE_FLAG}` — the string a handout ships where the Flag
# will be. Both were `observed` off a genuine command, which is why every other rule here waved
# them through: the provenance was real and the string still described a Flag rather than being one
# (#150). Weakest of the seven, and the only one refused at `last_call` too — a template is wrong
# whenever it is sent, where every other held candidate is merely unproven.
TEMPLATE = "template"
STRENGTHS = (REPRODUCED, OBSERVED, UNVERIFIED, GUESSED, STATED, CROWDED, TEMPLATE)

# How many distinct Flags one command may emit before everything it emitted is `crowded`.
#
# Set against what a *right* command looks like rather than against what the bad ones did: the Flag
# appears in one command's output as many times as it likes and dedups to one, a Challenge that also
# ships a decoy makes two, and beyond that the command is describing its input rather than the
# Challenge. The single commands that cost `compfest-2026-seg1` emitted 27 and 47.
CROWD_LIMIT = 3

# What an **unlimited** Board will not spend a slot on before the reserved tail: everything the
# Solver's own work did not produce.
#
# The confidence gate, and it lives in that branch for the same reason the ceiling does. Where a
# Board states a maximum, the maximum *is* the bound on a wrong-Flag loop and the reserve already
# keeps the last slot for a reproduced candidate. Where it states none, nothing here ever said stop
# — submitting was free, so the gate waved everything through, and *free to submit* was read as
# *free to be wrong*. COMPFEST prices the second one: a wrong Flag is a wrong Flag under a rule it
# states beside DDoS.
#
# Nothing is dropped — the tail still spends all three, at the one moment holding them costs nobody
# anything, which is what keeps this a confidence gate rather than the sandbagging Brunner bans.
NEEDS_THE_SOLVERS_OWN_WORK = (UNVERIFIED, GUESSED, CROWDED)

# Words a handout puts where the Flag will go. A candidate whose wrapped body is nothing but these,
# joined by separators, is a placeholder rather than a value — `FAKE_FLAG`, `your_flag_here`,
# `REDACTED`. A heuristic and a parameter like every threshold here: a real Flag themed on one of
# these words (`…{fake_solved_it}`) keeps its other segments, so it is the *only* content being one
# of these that trips the rule, never a mention of one.
# The three tools this module spends Steps on. The first and the last are the Solver talking *about*
# candidates rather than a command producing one, so the sweep never reads their bodies; the replay
# is a real command and its output is an Observation like any other.
SWEEP = "flag-sweep"
REPLAY = "flag-replay"
SUBMIT = "flag-submit"
NEVER_SWEPT = (SWEEP, SUBMIT)

# The same two reports again, as a rule about a **body line** rather than about a record — because
# `NEVER_SWEPT` only reaches this module's reports where they arrive as their own Step, and the
# Solver's record is on the writable mount the solving model reads. A model grepping
# `/state/runs/*/stream.jsonl` for session material gets every earlier `[flag] submit` line back in
# its own output, which is a real command's Observation in the current Attempt: the sweep harvests
# the strings, the replay re-runs the grep and the file answers identically, and a rejected string
# arrives `reproduced`. That is the strength this module reserves for a Board's last attempt, so
# the loop upgrades junk rather than merely repeating it (#141).
NOT_EVIDENCE = (DERIVED.encode(), MARK.encode())

# How many *wrong* answers one Attempt may spend before the rest of its candidates are held.
#
# The gate below is otherwise a budget rule, and an unlimited Board has no budget — so a sweep that
# nominated 36 candidates submitted 36, which is the wrong-Flag retry loop COMPFEST names beside
# DDoS. This bounds it without deciding *which* candidate is wrong: past the ceiling the Attempt has
# been answering wrongly for long enough that its whole candidate set is suspect, and a Challenge is
# better re-approached than exhausted. A correct Flag before the ceiling still grades and still ends
# the Attempt, so the bound costs a solve only where the Solver was wrong that many times first.
WRONG_CEILING = 5

# An absolute path as a command spells one, ending where the shell would end it. Quotes are
# terminators rather than content because a quoted path is still the path — `"/state/runs"` names
# the same directory as `/state/runs` — and the separators are what keep `rg X /state | head` from
# reading as one long path token.
PATH = re.compile(r"/[^\s'\"$;|&()<>]*")

# Read in blocks so an Observation nothing bounded — `aggregated_output` from the vendor's stream is
# whatever a command wrote — is never held whole in memory. A partial line is carried across the
# boundary so a Flag straddling one is still matched, and the carry is bounded because output with
# no newline in it is a stream rather than a line, and a Flag longer than this is not a Flag.
SCAN_BLOCK_BYTES = 1 << 20
LINE_CARRY_BYTES = 1 << 16

# Complete path for one representative Board effect: durable admission, bounded verdict Observation,
# and terminal effect classification. Candidate-profile trials replace this conservative slice value.
SUBMISSION_WRITE_NEED = Capacity(bytes=32 * 1_024, objects=1, operations=3)

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
    """This Challenge's submission budget, as the Board states it and never more generous than
    that (`floored`).

    CTFd writes *unlimited* as `max_attempts` 0 and `spent` is our own count held server-side, so it
    survives a container restart where a local tally would not. What it does not survive is the gap
    between two readings — it is re-read once an Intake cycle and spent once a turn — so a caller
    spending slots inside that gap holds the only record of them and floors the stated count with it.
    **Absent is unknown and unknown is treated as limited**: a Board that does not say cannot be
    assumed generous.
    """

    max_attempts: int | None = None
    spent: int = 0

    def floored(self, spent: int) -> Slots:
        """The same budget, holding the higher of the Board's spent count and one the caller knows.

        The larger rather than the later, because the two move for different reasons: the Board's
        count includes a teammate's submissions and the caller's includes the ones the Board has not
        been re-read since. Taking the larger is what makes the number a floor under the Board's
        real count — over-counting refuses a candidate a slot the Board would have taken, and
        under-counting spends the reserve on a Challenge that has none left.
        """
        return replace(self, spent=max(self.spent, spent))

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
    own default is assumed and the number is a parameter like every other uncalibrated one. Every
    answer but a solve counts against it, so a Run that is solving things is never paced by this at
    all.

    That is a **wider** set than the per-Challenge budget spends (`Verdict.spent_a_slot`), and
    deliberately: the two are wrong in opposite directions. A `ratelimited` answer is the Board
    saying its own count of our wrong Flags is ahead of ours, which is the last answer a pacer
    should ignore, and pacing on one too many costs seconds — where counting one too many against a
    Challenge's budget refuses a reproduced candidate a slot the Board would have taken, and no
    `last_call` releases *the budget is spent*.

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
        # How many times each Attempt has been graded wrong, which is the one thing the ceiling is
        # about and the one scope `submit` cannot see: `solver/run.py` calls it **once a turn**, so
        # a count local to the call resets with the turn and an Attempt of N sweeps spends N
        # ceilings. Keyed by Attempt because that is what the bound is a fact about.
        self._wrong: dict[str, int] = {}

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
        recited = 0
        for command, ref, source, tool in _bodies_of(self._recorder.stream_path, attempt_id):
            # An allowlist rather than a refusal of `SOURCE_BOARD`, so a source nobody has
            # thought of yet arrives non-authorising (ADR-0019).
            if source != SOURCE_SOLVER:
                stated += 1
                # Read rather than skipped, because *which* strings the Board stated is what tells a
                # model repeating one from a model that found it (ADR-0021).
                by_the_board.update(_in_body(self._recorder.run_dir / ref, matchers))
                continue
            # A command that went to the Run's own record brings back what the Solver already
            # submitted, never what this Challenge's Flag is. Named on the **command** because that
            # is what survives the shape the body rule cannot see: `rg -o` emits bare matches with
            # no `MARK` on the line, and still has to name the path it reads (ADR-0028).
            if _reads_the_record(command, self._recorder.run_dir.parent):
                recited += 1
                continue
            swept += 1
            # Distinct and in order, because *how many different Flags this one command emitted* is
            # the question `_crowded` asks and a Flag repeated down a file is still one Flag.
            emitted = tuple(dict.fromkeys(_in_body(self._recorder.run_dir / ref, matchers)))
            # A replay is exempt because it re-runs a command the sweep already read: everything in
            # its output was counted once at the command that first emitted it, and counting it
            # again would let one candidate's confirmation nominate a crowd. Where that first
            # command really was reading a list, the demotion has already happened there.
            crowd = CROWDED if tool != REPLAY and _crowded(emitted) else OBSERVED
            for text in emitted:
                # A template outranks even a crowd's demotion downward: `_describes` reads the
                # string alone, so a candidate that is a Flag's shape is one wherever it was seen
                # and no clean second sighting can promote it.
                candidate = Candidate(text, TEMPLATE if describes_template(text) else crowd, command=command, ref=ref)
                # A later command that emitted it alone outranks an earlier one that emitted it in a
                # crowd: the crowd says the *command* was reading a list, never that the string is
                # wrong, so one clean sighting is still the Solver's own work finding it.
                if (already := found.get(text)) is None or candidate.rank < already.rank:
                    found[text] = candidate
        for text in (match for prose in said for match in _matches(prose.encode(), matchers)):
            # A string a command produced is already here and outranks both — the Board having also
            # stated it says nothing about whether the Solver later found it.
            nominated = TEMPLATE if describes_template(text) else (STATED if text in by_the_board else UNVERIFIED)
            found.setdefault(text, Candidate(text, nominated))
        ordered = tuple(sorted(found.values(), key=lambda candidate: candidate.rank))
        self._record(
            SWEEP,
            f"{MARK} sweep {shapes}",
            (
                f"{MARK} swept {swept} observation(s), passed over {stated} the Board stated, "
                f"{recited} that read the Run's own record, and no claim"
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

        `slots` is the budget as the gate has to see it **now**, which is not always as the Board
        last stated it: the caller spends a slot a turn and the Board is re-read once an Intake
        cycle, so a caller with submissions in that gap hands one floored by them (`Slots.floored`).

        `last_call` is the Run's reserved tail releasing the reserve: there is no later Attempt for
        the last attempt to be reserved *for*, so a held candidate is submitted rather than carried
        out of the Run unsubmitted.
        """
        graded: list[Graded] = []
        held: list[Candidate] = []
        spent_here = 0
        for found in candidates:
            candidate = self._degraded(self._reproduced(found, attempt_id=attempt_id, workdir=workdir), lease)
            if refusal := _refuses(candidate, slots, spent_here, self._wrong.get(attempt_id, 0), last_call=last_call):
                held.append(candidate)
                self._record(
                    SUBMIT, f"{MARK} hold {candidate.text}", f"{MARK} {refusal}".encode(), attempt_id, ok=False
                )
                continue
            answer = self._graded(candidate, attempt_id=attempt_id, challenge_id=challenge_id)
            graded.append(answer)
            spent_here += 1
            self._wrong[attempt_id] = self._wrong.get(attempt_id, 0) + (1 if answer.verdict.incorrect else 0)
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
        candidate_digest = hashlib.sha256(candidate.text.encode()).hexdigest()
        key = f"board-submit:{challenge_id}:{candidate_digest}"

        def observe(verdict: Verdict) -> None:
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

        verdict = ReservedEffect(self._recorder.write_authority).execute(
            key,
            EffectIdentity("board.submit", str(challenge_id), candidate_digest),
            SUBMISSION_WRITE_NEED,
            lambda: self._board.submit(challenge_id, candidate.text),
            encode=lambda answer: {
                "outcome": answer.outcome,
                "message": answer.message.replace(candidate.text, "[submitted-candidate]"),
                "http_status": answer.http_status,
            },
            decode=lambda answer: Verdict(
                outcome=str(answer["outcome"]),
                message=str(answer["message"]),
                http_status=int(answer["http_status"]),
            ),
            observe=observe,
            retention=RetentionPolicy.RECEIPT,
        )
        shape = submission_shape({"message": verdict.message})
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
            self._instances.terminate(
                challenge_id,
                attempt_id=attempt_id,
                after_flag=True,
                generation_id=lease.generation_id,
            )

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


def _reads_the_record(command: str, records: Path) -> bool:
    """Whether this command went to the Run's own record for its output.

    The Solver's record sits on the same mount the model works in, and v1 does not take it out of
    reach — `/state` is virtiofs, which does not enforce file modes, and the namespace that would
    mask it needs a capability worth less than it costs (ADR-0028). So the access stays and the
    *evidence* is refused instead.

    Every earlier Attempt's submissions are in there, which is the one thing a Flag sweep must never
    read: it is the Solver quoting itself back. Matched on the command rather than in the body
    because a command has to name the path it reads however it formats what it found — which is the
    case `MARK` on a body line misses, since `rg -o` prints the matches and nothing else.

    Any Run's record, not just this one: `compfest-2026-seg1` read `ctfd-probe`'s stream beside its
    own, and a Flag from a Run we finished last week is no more this Challenge's answer.

    **In either direction along the branch**, which is #150 and the first thing this rule met after
    it landed. It was a substring test, so it caught a command naming the record or something under
    it and missed `rg -n "Phantom Ledger|PhantomVault" /state` — an *ancestor*, which reads every
    byte of the record and names none of it. That command brought back the previous segment's copy
    of a challenge-detail page and the sweep took CTFd's submission-box placeholder off it as a
    candidate. So the question is whether the two paths lie on one branch, and `/` is a legitimate
    answer to it: a Flag sweep over `grep -r … /` is reading this record along with everything else.
    """
    return any(_overlaps(found.group(), records) for found in PATH.finditer(command))


def _overlaps(named: str, records: Path) -> bool:
    """Whether one of the two paths contains the other, so reading one can read the record.

    Lexical, over the string the command spelled — nothing is resolved against the filesystem. A
    resolve would follow symlinks and answer about the machine the sweep runs on rather than about
    the command, and the sweep also runs offline over a stored stream (ADR-0009), where the paths a
    finished Run named no longer exist.
    """
    where = PurePosixPath(named.rstrip("/") or "/")
    root = PurePosixPath(records)
    return where == root or where in root.parents or root in where.parents


def _crowded(emitted: Sequence[str]) -> bool:
    """Whether one command emitted so many different Flags that the command is what it says
    something about.

    The rule the incident argues for. A Challenge has exactly one Flag, so a command answering with
    a dozen has read a list of them rather than solved anything — and that is the exact shape of
    both leak paths that cost `compfest-2026-seg1`: one `rg` over a cloned repository's example
    Flags, one `rg` over the Run's own record. Every per-candidate rule is defeated by them, because
    each individual string is well-evidenced; it is only together that they disprove each other.

    Per **command**, not per sweep: an Attempt that works for twenty turns legitimately accumulates
    Flag-shaped strings across them — a decoding tried, a decoy found, a guess printed — and one
    each from twenty commands is a model working, where twenty from one command is a model grepping.

    Demotion rather than refusal, because the set is untrustworthy and not proven wrong: `crowded`
    sits in `NEEDS_THE_SOLVERS_OWN_WORK`, so the tail still spends them. It is also not
    `authorised`, so the
    replay that would have promoted it to `reproduced` off a second read of the same file — which is
    how junk reached the strength this module reserves for a Board's last attempt — never runs.
    """
    return len(emitted) > CROWD_LIMIT


def _refuses(candidate: Candidate, slots: Slots, spent_here: int, wrong_here: int, *, last_call: bool) -> str:
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

        The two rules inside the unlimited branch are `NEEDS_THE_SOLVERS_OWN_WORK` and `WRONG_CEILING`,
        and they are there rather than here because a Board that states a maximum has already bounded
        the loop they exist to bound.

        The ceiling sits above even the guard, and is the one refusal `last_call` does **not** override.
        Every other rule here asks what this candidate is worth; the ceiling asks whether the Attempt is
        still answering rather than guessing, and an Attempt the Board has said no to `WRONG_CEILING`
        times is guessing whatever the next candidate's strength says. The tail argument does not rescue
        it: a slot nothing else will spend is still a wrong Flag on a Board that prohibits the loop, and
        the candidates left at that point come from a set already shown to be bad (#141).

        A `template` is refused above all of it, and is the one refusal `last_call` shares with the
        ceiling. Every other rule here asks whether *now* is the moment to spend a slot on a real
        string; a template is not a string to spend a slot on at any moment, because it describes a
        Flag rather than being one. The tail argument that rescues a held candidate — a slot nothing
        else will use — does not apply, since the slot would be spent being wrong on a Board that
        prices it (#150).
    """
    if candidate.strength == TEMPLATE:
        return (
            "this candidate is a Flag's shape rather than a Flag — a pattern or a placeholder the "
            "Board or a handout showed — so it is held whether or not this is the reserved tail"
        )
    if not last_call and (wrong := confusables(candidate.text)):
        return f"the confusable-character guard held it back — {'; '.join(wrong)}"
    if not last_call and candidate.strength == STATED:
        return (
            "the Board stated this string itself and the model repeated it, so it waits for the "
            "reserved tail, where the slot it spends has nothing else to be spent on"
        )
    if slots.unlimited:
        # The ceiling belongs to this branch alone. Every refusal below it is the Board's own budget
        # doing the bounding, and a Board that states a maximum has already said how long a
        # wrong-Flag loop may run; it is only where the Board states *no* limit that nothing else
        # here ever says stop. Not overridden by `last_call`: a slot nothing else will spend is
        # still a wrong Flag under a rule that names the loop beside DDoS (#141).
        if wrong_here >= WRONG_CEILING:
            return (
                f"this Attempt has already been graded incorrect {wrong_here} time(s), which is the "
                f"ceiling — its remaining candidates are held rather than spent on a wrong-Flag loop"
            )
        if not last_call and candidate.strength in NEEDS_THE_SOLVERS_OWN_WORK:
            return (
                f"a {candidate.strength} candidate is not the Solver's own work saying so, and this "
                f"Board states no budget to spend it against — so it waits for the reserved tail"
            )
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
        yield (
            str(record.get("command_raw", "")),
            str(ref),
            str(record.get("source", SOURCE_SOLVER)),
            str(record.get("tool", "")),
        )


def _in_body(body: Path, matchers: Sequence[re.Pattern[bytes]]) -> Iterator[str]:
    """Every wrapper match in one Observation's body, minus any line the Solver itself wrote.

    Both markers in `NOT_EVIDENCE` are here for the same reason: a body line carrying one is the
    Solver talking rather than a command producing, and a sweep that read it would authorise a
    candidate on the strength of having mentioned it.

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
            if not any(marker in line for marker in NOT_EVIDENCE):
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
