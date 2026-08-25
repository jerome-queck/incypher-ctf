"""When an Attempt ends, and why — the orchestrator's call, never the model's.

[ADR-0005](../docs/adr/0005-the-stall-call-lives-outside-the-solving-model.md) is the whole design,
and it is a refusal before it is a mechanism: **the model never ends its own Attempt, and nothing
it says buys it more time.** EnIGMA gave a CTF agent an explicit `exit_forfeit` and it fired in
**0.5%** of runs while **63.1%** exhausted the cost cap instead; on failed trajectories BAGEN
measures models reporting feasibility **above 70% after 60% of the budget is spent**. A design that
let self-assessed progress buy time would buy it on very nearly every run that was going to fail.

So everything here reads Observations and nothing here reads prose — with one exception that runs
in one direction only, `said`, which may **shorten** a budget and can never lengthen one.

Three counters, whichever trips first, and one thing that buys time back:

- **Repetition** — a normalised command already seen this Attempt, answering exactly as it did.
- **Novelty** — N Steps since an Observation whose content hash was unseen this Attempt.
- **Step cliff** — a hard backstop, because the first two are uncalibrated and this one cannot be
  argued with.
- **A Checkpoint** — the same normalised command answering *differently* than it did. That is an
  environment state transition and the command that found it is its own replay, which is what
  ADR-0005 means by re-verifiable: "a route that was 403 and is now 200" is literally this shape.
  A first success is not one — a transition needs a before as well as an after, and treating every
  command that worked as progress would max the extension cap out inside a minute.

The Checkpoint rule is also what keeps repetition from being brutal: **a repeat that changed is
progress and a repeat that did not is repetition**, so the model may re-run `ls` as often as the
environment keeps moving under it.

Everything is derived and nothing is stored, per ADR-0009 — which is also why a Checkpoint is read
out of the Step stream rather than out of the working directory. A rule that reads the filesystem
is a rule an offline replay cannot recompute, and replaying stored streams at other thresholds is
the only calibration v1 will ever get.

Every threshold is a **parameter**. None of them has been calibrated against a Board, and a
constant is a number nobody can move on the day it turns out to be wrong.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from solver.record import (
    CRASHED,
    CUT_BUDGET,
    CUT_INSTANCE_EXPIRED,
    CUT_NOVELTY,
    CUT_REPETITION,
    CUT_SELF_REPORTED_IMPOSSIBLE,
    CUT_STEP_CLIFF,
)

# A line the Solver wrote about itself — `[codex] the turn failed`, `[recon] entropy over …`. It is
# an Observation like any other and counts for novelty and the cliff, but it is not a command
# anyone could replay, so it feeds neither repetition nor a Checkpoint. Two rungs of the credential
# chain reporting the same failure is not the model looping.
MARKED = re.compile(r"^\[[a-z][a-z-]*\] ")

# The wrapper the vendor puts round every command it runs — `/bin/zsh -lc 'cat note.txt'`. Two
# invocations that differ only in which shell was reached for are the same command to anyone
# reading them, and repetition is a claim about what was tried rather than about how it was spelt.
SHELL = re.compile(r"^\S*(?:sh|bash|zsh|dash)\s+-[a-z]*c\s+")

# Matched lowercased against what the model said, and deliberately tight. ADR-0005 expects this to
# fire rarely — MIRAGE-Bench finds agents fabricate an action 46–65% of the time in genuinely
# unachievable states rather than say so — and a loose matcher would turn "impossible to read
# without the password" into a cut Attempt. It is a parameter for the same reason every threshold
# here is one.
IMPOSSIBLE_SAYS = (
    "this is impossible",
    "this challenge is impossible",
    "cannot be solved",
    "unsolvable",
    "i give up",
)


def normalise(command: str) -> str:
    """The repetition counter's rule, and the reason `command_raw` is kept beside it in the record.

    Owned here rather than by whatever produced the Step, so a future rule can be applied to a past
    Run: the record stores both forms precisely so this function may change.
    """
    bare = SHELL.sub("", command.strip())
    if len(bare) > 1 and bare[0] == bare[-1] and bare[0] in "'\"":
        bare = bare[1:-1]
    return " ".join(bare.split()).rstrip(";").strip()


def replayable(command: str) -> bool:
    """Whether this is a command at all, or a line the Solver wrote about itself."""
    return bool(command.strip()) and not MARKED.match(command)


@dataclass(frozen=True)
class Thresholds:
    """Every number the stall call turns on, in one place and none of them calibrated.

    `repeats` is counted as repeats rather than appearances, so the default is ADR-0005's rule
    exactly as it is written: the moment a command answers the same way a second time.
    """

    repeats: int = 1
    novelty: int = 5
    cliff: int = 25
    # ADR-0005's small K. An approach that keeps yielding cheap Checkpoints still ends.
    extensions: int = 3
    extension_seconds: float = 120.0
    impossible: tuple[str, ...] = IMPOSSIBLE_SAYS


@dataclass
class Deadline:
    """The moment this Attempt is killed, and the two clocks that bound it.

    An object rather than a timestamp because a Checkpoint **moves** it: the vendor's agent takes
    its next turn without asking, so what progress buys is the kill deadline and never a grant of
    one more Step (ADR-0014). The adapter reads `at` on every pass of its loop, which is what makes
    an extension reach a child that is already running.

    Only the budget leg ever moves. An Instance's expiry is the Board's fact and not ours, so
    `at` taking the earlier of the two makes "an extension cannot outlive the Instance" structural
    rather than a rule someone has to remember.
    """

    budget: dt.datetime
    instance: dt.datetime | None = None
    granted: int = 0
    # Set by `shorten`, and the whole of "may shorten a budget and may never lengthen one": once the
    # model has volunteered that this is impossible, no later Checkpoint can hand the time back.
    sealed: bool = False

    @property
    def at(self) -> dt.datetime:
        return min(self.budget, self.instance) if self.instance else self.budget

    def left(self, now: dt.datetime) -> float:
        return (self.at - now).total_seconds()

    def spent(self, now: dt.datetime) -> str:
        """Which of the two clocks ran out, or the empty string while neither has.

        The Instance is named first because it is the more specific fact: an Attempt that ran out of
        Instance and one that ran out of budget want completely different responses.
        """
        if self.left(now) > 0:
            return ""
        if self.instance and self.instance <= self.budget:
            return CUT_INSTANCE_EXPIRED
        return CUT_BUDGET

    def extend(self, seconds: float, *, cap: int) -> bool:
        """Buy time with a state transition, up to `cap` times. Answers whether it moved."""
        if self.sealed or self.granted >= cap:
            return False
        self.budget += dt.timedelta(seconds=seconds)
        self.granted += 1
        return True

    def shorten(self, to: dt.datetime) -> None:
        """Bring the kill forward and seal it there. Never lengthens: an argument for more time
        arriving through the one door that only closes is how ADR-0005's exception stays narrow."""
        self.budget = min(self.budget, to)
        self.sealed = True


@dataclass(frozen=True)
class Checkpoint:
    """An environment state transition, and the command that re-verifies it.

    `moved` is orchestrator-composed and factual — what the transition was, never what it means. A
    Checkpoint is never an interpretation: a confident wrong turn produces Claims in quantity and no
    Checkpoints at all.
    """

    moved: str
    replay: str


@dataclass(frozen=True)
class Tried:
    """A command this Attempt ran and what it exited with — information, never prohibition."""

    command: str
    exit_code: int | None


@dataclass
class Watch:
    """One Attempt's stall call: what it observed, what that bought it, and the cause that ends it.

    It cannot speak to the model. There is no method here that reaches a prompt or a running child,
    which is ADR-0005's silent reset made structural — telling a model mid-Attempt that it appears
    stuck puts the judgement back inside the narrative this whole module removes it from, and is
    close to an ideal prompt for inducing "impossible".

    `steps` is seeded rather than started at zero because recon **is** the opening of an Attempt and
    its probes are the Attempt's first Steps.
    """

    deadline: Deadline
    thresholds: Thresholds = Thresholds()
    steps: int = 0
    checkpoints: list[Checkpoint] = field(default_factory=list)
    tried: list[Tried] = field(default_factory=list)
    last: str = ""
    _answered: dict[str, tuple[int | None, str]] = field(default_factory=dict)
    _digests: set[str] = field(default_factory=set)
    _repeats: int = 0
    _stale: int = 0
    _impossible: bool = False

    def observed(self, command: str, *, exit_code: int | None, digest: str) -> Checkpoint | None:
        """One Step of this Attempt, as the record took it — and a Checkpoint if it moved anything.

        Handed the Steps that are the model working the Challenge, never the invocation's own Step:
        one spawn of the CLI per rung of the credential chain is the same command line every time,
        and a chain switch is not the model looping.
        """
        self.steps += 1
        self._novelty(digest)
        if not replayable(command):
            return None
        self.last = command
        key = normalise(command)
        answer = (exit_code, digest)
        before = self._answered.get(key)
        self._answered[key] = answer
        if before is None or before == answer:
            self._repeat(key, exit_code, repeated=before is not None)
            return None
        return self._moved(key, before[0], exit_code)

    def said(self, prose: str, *, now: dt.datetime) -> bool:
        """The one direction the model's prose is read in, and the reason it is read at all.

        The same self-report that is useless as a continue signal is a cheap stop signal: ADR-0005
        measures 28–64% of tokens saved on failed trajectories for 1.6–4.2 points of overall
        success. It shortens the budget and seals it; nothing here can hand the time back.
        """
        if not any(phrase in prose.lower() for phrase in self.thresholds.impossible):
            return False
        self._impossible = True
        self.deadline.shorten(now)
        return True

    def cause(self, now: dt.datetime) -> str:
        """Why this Attempt is over, or the empty string while it is not.

        The counters are read before the clock because a counter that has tripped ended the Attempt
        at the moment it tripped, and the deadline is only ever the cause when nothing else was.
        """
        if self._impossible:
            return CUT_SELF_REPORTED_IMPOSSIBLE
        if self._repeats >= self.thresholds.repeats:
            return CUT_REPETITION
        if self._stale >= self.thresholds.novelty:
            return CUT_NOVELTY
        if self.steps >= self.thresholds.cliff:
            return CUT_STEP_CLIFF
        return self.deadline.spent(now)

    def _novelty(self, digest: str) -> None:
        """Content hash, not command: the same command against a moving Target is novel and a
        different command scrolling the same file is not."""
        if digest and digest not in self._digests:
            self._digests.add(digest)
            self._stale = 0
            return
        self._stale += 1

    def _repeat(self, key: str, exit_code: int | None, *, repeated: bool) -> None:
        if repeated:
            self._repeats += 1
        self.tried = [entry for entry in self.tried if entry.command != key]
        self.tried.append(Tried(command=key, exit_code=exit_code))

    def _moved(self, key: str, before: int | None, exit_code: int | None) -> Checkpoint:
        """A command that answered differently than it did — so the environment moved under it.

        The tried list clears here rather than merely being appended to, because a command that
        failed before a Checkpoint may be exactly right after one; and the counters clear with it,
        since an Attempt that is moving the environment is by definition not stalled.
        """
        checkpoint = Checkpoint(
            moved=f"{key} answers differently than it did — exit {before} then {exit_code}",
            replay=key,
        )
        self.checkpoints.append(checkpoint)
        self.tried = []
        self._repeats = 0
        self._stale = 0
        self.deadline.extend(self.thresholds.extension_seconds, cap=self.thresholds.extensions)
        return checkpoint


@dataclass
class Breaker:
    """A Solver broken at its own end, told apart from a Challenge that is merely hard.

    An Attempt that spent no Steps observed nothing, so it is evidence about us — a dead adapter, a
    Target that never came up, a credential chain with no rung left. Two different failures hide in
    that shape and they want opposite responses, which is why there are two counters:

    - **The same Challenge, barren again and again** is a circuit breaker, and the Attempt that
      trips it is recorded `crashed` rather than cut. A Cut would say the Challenge stopped this
      Attempt, and it did not.
    - **Barren across *different* Challenges** is the Solver itself, and the response is to slow
      down. A broken Solver spinning at full speed burns a quota window nobody is there to notice;
      backing off exponentially is what keeps that window recoverable.
    """

    limit: int = 3
    base_seconds: float = 30.0
    cap_seconds: float = 900.0
    _barren: dict[str, int] = field(default_factory=dict)
    _streak: list[str] = field(default_factory=list)

    def closed(self, challenge: str, *, steps: int) -> str:
        """One Attempt's outcome as this breaker reads it: `crashed` where it tripped, else ""."""
        if steps:
            self._barren.pop(challenge, None)
            self._streak.clear()
            return ""
        self._streak.append(challenge)
        self._barren[challenge] = self._barren.get(challenge, 0) + 1
        return CRASHED if self._barren[challenge] >= self.limit else ""

    def backoff(self) -> float:
        """How long to wait before the next Attempt — zero until the barren streak has crossed from
        one Challenge to another, which is what separates a broken Solver from a dead Target."""
        spread = len(set(self._streak))
        if spread < 2:
            return 0.0
        return min(self.cap_seconds, self.base_seconds * 2 ** (spread - 2))
