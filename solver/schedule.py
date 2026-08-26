"""The scheduler — what to work next, and how long to work it, from one authority.

`acquire()` answers a Challenge **and** its budget; `release(outcome)` gives it back. The two are
one surface on purpose: a design where something else decides how long an Attempt gets is a design
where the paralleliser v3 wants is a rewrite rather than *"hold N instead of one"*. v1 holds
exactly one, and asking for a second while one is out is refused rather than quietly allowed.

**There is no queue.** [ADR-0015](../docs/adr/0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)
makes Order a pure, deterministic, total function over Intake's local copy of the Board and the
Run's own ledger, recomputed at every Attempt boundary. Nothing is stored, spliced or repaired, so
a cut Challenge is not *placed* anywhere — it is eligible again the moment its Attempt closes, and
where it next ranks falls out of the function. Determinism is not tidiness: shadow mode replays a
stored stream offline at other weights, and a ranking that cannot be reproduced has no baseline to
be compared against.

    order(c) = + w_solves     × tractability(c)   the crowd says it is tractable
               + w_value      × value_norm(c)     the setter's own estimate, and the payoff
               + w_lease      × lease_alive(c)    mana already spent, TTL already burning
               + w_progress   × progress(c)       it moved the environment last time
               − w_spend      × spend_norm(c)     monotone, and the anti-livelock guarantee
               − w_impossible × impossible(c)     the volunteered "impossible"

`spend_norm` is `max(time_norm, attempts_norm)`, and that `max` is load-bearing rather than
defensive: a penalty on *time* alone does not bound an Attempt that dies in two seconds, so the
Challenge would stay top of Order and spin at ~22k input tokens a go. Counting Attempts as well as
seconds restores the guarantee — **any Challenge picked repeatedly without producing Checkpoints
eventually ranks below every un-attempted one**, so the Board gets covered by construction rather
than by a threshold somebody tuned.

**Nothing is ever banned.** Only two things are ineligible, and both are facts rather than
judgements: a Challenge already solved, and one undeployable by us. Everything else that fails only
ever loses its *place in line* — a Challenge past its `f × T_total` spend ceiling is demoted below
every un-demoted Challenge and returns when the whole Board is past its own, and a volunteered
"impossible" is a large non-decaying penalty because a model that said it will say it again.

**The clock buys a number of Attempts, never a slice per Challenge.** Dividing the remaining hours
by the unsolved count puts every Attempt below the length at which anything is ever solved, so
`L(c) = L* × tier_weight(c)` and `K = (T_remaining − tail) / L̄` — the working set is the top K of
Order and narrows on its own as the Run burns down. The final stretch is a consequence of that
arithmetic and never a mode with rules of its own.

Two things this deliberately does not own. The **circuit breaker** is `stall.Breaker`'s: it counts
zero-Step Attempts and this module only honours its verdict, because a counter kept in two places
is a counter that will disagree. And the **per-submission 60 s** held inside an Instance's own
deadline is `instance.Reserves`' — a second reserve, kept separate from the run-level tail below,
because one bounds a Run and the other bounds a Lease and merging them would silently apply
whichever was larger to both.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from solver.codex import Invocation
from solver.intake import Sighting, Snapshot
from solver.record import CRASHED, CUT_SELF_REPORTED_IMPOSSIBLE, Recorder
from solver.triage import FLOOR, Judge, triage, unasked

MARK = "[schedule]"

# Where the Run's own clock lives, beside the stream it belongs to. A file rather than an argument
# because the thing being defended against is a *restart* re-deriving it.
WINDOW = "window.json"


@dataclass(frozen=True)
class Dials:
    """Every number the scheduler turns on, in one versioned place, and not one of them calibrated.

    **Ours rather than the Board's**, which is why this is not part of the Board profile: how long
    we spend on a Challenge and how we weigh a solve count are facts about us, so two Runs at the
    same Board may legitimately differ here. `version` is what makes a replayed Run comparable —
    shadow mode re-ranks a stored stream at other weights, and a ranking with no version on it
    cannot be told from the one that actually ran.

    ADR-0015 is explicit that not one of these has a source: there is no prior work on allocating a
    fixed budget *across* a Board, so every value below is a guess with an argument attached, to be
    replaced by a distribution after one practice Run.
    """

    version: int = 1

    # The six terms of Order. Their relative size is the whole of the policy: progress outweighs
    # the crowd because a Checkpoint is our own evidence and a solve count is somebody else's;
    # spend outweighs both because the anti-livelock guarantee is that it eventually does.
    w_solves: float = 1.0
    w_value: float = 0.5
    w_lease: float = 0.75
    w_progress: float = 1.25
    w_spend: float = 1.5
    # Large enough to sink a Challenge under every other term at once, and never large enough to be
    # an exclusion in disguise: on a Board where every Challenge has been called impossible, the
    # least-spent of them is still picked (ADR-0009 keeps this cause as an alarm, and a cause that
    # silently deletes Challenges is an alarm nobody can watch trending to zero).
    w_impossible: float = 5.0

    # The knee, `L*`. Seeded from #15's measurement that most solves land in the first ~20 Steps
    # and that spend anti-correlates with success — a solve probability that rises steeply and then
    # flattens, with a per-Attempt band around 8–12 minutes.
    knee_seconds: float = 600.0
    # `L_min` — no Attempt is started shorter than this, whether or not it needs a deploy. There is
    # no deploy-specific late-run gate: `min(budget, TTL)` already stops an Attempt outliving its
    # Instance, and a fresh deploy's TTL is generous, so gating deploys would forbid Attempts that
    # were perfectly viable.
    floor_seconds: float = 300.0
    # The run-level tail, with four named jobs: submit pending candidates, destroy Instances, flush
    # telemetry, exit clean. Not the same reserve as `instance.Reserves.submission_seconds`.
    tail_seconds: float = 300.0
    # `tier_weight(t) = 1 + (t − FLOOR) × tier_step`, so Triage's floor is exactly `L*` and a Tier
    # is a multiplier on the knee rather than a table nobody can move.
    tier_step: float = 0.25
    # ADR-0005's small K, the same cap it uses for in-Attempt extensions: `tier = prior + min(K,
    # checkpoints across Attempts)`, and nothing ever lowers a Tier.
    tier_cap: int = 3

    # `f` — the share of the whole window one Challenge may hold before it is demoted below every
    # Challenge not past its own. One comparison caps the compounding of three pressures that all
    # push the same way: consecutive Attempts are ordinary, a live Lease boosts Order, and
    # Checkpoints raise Tier.
    ceiling_fraction: float = 0.2
    # The count leg of `spend_norm`: the number of Attempts at which a Challenge reads fully spent
    # however little wall-clock it burned. This is what bounds the degenerate Attempt.
    attempts_full: int = 6
    # Checkpoints at which `w_progress` reads full. The same shape as `attempts_full` and for the
    # same reason — a term with no denominator is not normalised to 0–1.
    checkpoints_full: int = 3

    # The reserved exploration share (ADR-0017): roughly one Attempt in four goes to a Challenge
    # nobody has solved, regardless of rank, so the unsolved set is never starved by construction.
    explore_every: int = 4
    # What "unsolved by anyone" means, as a dial rather than a literal zero — a Board whose easiest
    # Challenge is on three solves has still told us nothing about the rest of it.
    explore_at_most_solves: int = 0

    # How many Attempts may be held at once — read by `acquire`, and pinned at one so that v3
    # tunes this number rather than reshaping the scheduler. What v3 still has to settle is what a
    # second concurrent hold means for the ranking; the cap is what is settled here.
    concurrency: int = 1
    # The strength every Attempt is worked at. Taken from `codex.Invocation` rather than retyped,
    # so there is one default rather than two that drift apart — the value is a fact about how we
    # spend a window, which is why it is a dial, and the adapter is what actually spends it. v1
    # varies it never: ADR-0014 gives a Run one brain, switching on exhaustion alone.
    reasoning_effort: str = Invocation.reasoning_effort


@dataclass(frozen=True)
class Window:
    """The Run's clock, as two absolute moments written to disk at Run start.

    Absolute rather than process-relative because **a restart must not extend the window**. A
    duration counted from process start is reset by every restart, and the Solver is unattended: a
    crash at 14:00 would hand it a fresh 5.5 hours and it would still be working at 19:30 with the
    competition long closed and nobody there to notice.

    This is the one thing the Solver reads back out of `/state`, and the exception is narrow by
    design. Run state is output and never input (`CONTEXT.md`, *Run state*) — the ledger below is
    rebuilt from nothing after a restart and that is correct, because losing what we spent costs
    accuracy. Losing *when we stop* costs the window itself, which is why the moment is the one
    fact that survives.

    A window file that cannot be read is refused rather than replaced. Writing a new one over an
    unreadable one is precisely the silent extension this exists to prevent.
    """

    opened_at: dt.datetime
    ends_at: dt.datetime
    # Whether this window was inherited from a file rather than opened fresh — the difference
    # between a Run starting and a Run resuming, which is otherwise invisible from inside.
    restarted: bool = False

    @classmethod
    def opened(cls, run_dir: Path, *, lasting: float, now: dt.datetime) -> Window:
        """Open the Run's window, or answer with the one already open at `run_dir`.

        `lasting` is consulted **only** when there is no window on disk. A restart re-reads its own
        stamp and is handed exactly the time that is left, which is the whole point.
        """
        path = Path(run_dir) / WINDOW
        if path.exists():
            return replace(_written(path), restarted=True)
        window = cls(opened_at=now, ends_at=now + dt.timedelta(seconds=lasting))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"opened_at": window.opened_at.isoformat(), "ends_at": window.ends_at.isoformat()}) + "\n"
        )
        return window

    @property
    def total_seconds(self) -> float:
        """The whole window, which is what the `f × T_total` spend ceiling is a share of."""
        return (self.ends_at - self.opened_at).total_seconds()

    def left(self, now: dt.datetime) -> float:
        return (self.ends_at - now).total_seconds()


@dataclass(frozen=True)
class Ranked:
    """One Challenge's standing in Order at one Attempt boundary.

    `rank` is 1-based over every unsolved, eligible Challenge — recorded at each pick, because
    without it a Run that passed over sixty Challenges and a Run that only ever had fourteen read
    identically in the stream.
    """

    challenge_id: int | str
    name: str
    rank: int
    score: float
    tier: int
    budget_s: int
    solves: int
    demoted: bool
    committed: bool


@dataclass(frozen=True)
class Pick:
    """What to work, and how long — the two halves that make this one authority.

    `order_ranks` is keyed by id as text because that is how it reaches the record, and it carries
    **everything Order ranked** rather than the working set: a never-reached Challenge and a
    reached-and-cut one are different facts and the stream has to be able to tell them apart. A
    Challenge that is ineligible is absent rather than ranked last, because it was never something
    Order passed over.
    """

    challenge: Sighting
    budget_s: int
    deadline: dt.datetime
    tier: int
    attempt_sequence: int
    order_ranks: dict[str, int]
    working_set: tuple[int | str, ...]
    # Whether the reserved exploration share chose this rather than Order's top (ADR-0017). Worth
    # recording because a Run whose Flags all came from explored picks and one where none did are
    # the measurement that says whether the share is worth its quarter.
    exploring: bool = False


@dataclass(frozen=True)
class Ended:
    """How one Attempt finished, as the scheduler needs to hear it.

    The **cause** rather than an outcome — the same closed vocabulary the record uses, because two
    of the causes mean something here: `cut:self-reported-impossible` is a standing penalty, and
    `crashed` is `stall.Breaker`'s verdict that the Solver is broken at its own end, which
    hard-demotes rather than merely costing a place.

    `seconds` and `checkpoints` are what the ledger is made of, and neither is a judgement: one is
    a clock reading and the other is a count of environment state transitions.
    """

    challenge_id: int | str
    cause: str
    seconds: float
    checkpoints: int = 0


@dataclass
class _Spent:
    """What a Run has put into one Challenge. Monotone in every field — nothing here ever falls."""

    attempts: int = 0
    seconds: float = 0.0
    checkpoints: int = 0
    impossible: bool = False
    crashed: bool = False


class Scheduler:
    """One authority over what to work and how long, for the length of a Run.

    Holds the Run's ledger and nothing about the Board: every `acquire` is handed the current
    Snapshot and ranks it from scratch, so a Challenge released at 13:00 is ranked at 13:00 and a
    Board that moved underneath us is never something a stored order has to be repaired against.
    """

    def __init__(
        self,
        window: Window,
        recorder: Recorder,
        *,
        dials: Dials = Dials(),
        judge: Judge = unasked,
        now: Callable[[], dt.datetime] | None = None,
    ) -> None:
        self.window = window
        self.dials = dials
        self._recorder = recorder
        self._judge = judge
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._priors: dict[int | str, int] = {}
        self._spent: dict[int | str, _Spent] = {}
        self._first_seen: dict[int | str, tuple[dt.datetime, int]] = {}
        self._taken = 0
        self._held: dict[int | str, Pick] = {}

    def acquire(self, snapshot: Snapshot, *, leased: Collection[int | str] = ()) -> Pick | None:
        """The next Challenge and its budget, or `None` where the clock can no longer buy an Attempt.

        `None` has two causes and they want **opposite** responses, so a caller reads
        `out_of_time()` to tell them apart rather than assuming: the window being spent runs the
        tail and ends the Run, and nothing being eligible right now waits for the next Intake.
        Challenges drop mid-event, so "nothing left to work" is not a conclusion the Solver is ever
        allowed to reach — a Run ends when the window closes and **never because the Board looks
        finished** (`CONTEXT.md`, *Run*).
        """
        if len(self._held) >= self.dials.concurrency:
            holding = ", ".join(repr(one) for one in self._held)
            raise ValueError(
                f"{MARK} this Run holds {self.dials.concurrency} Attempt(s) at a time and is "
                f"holding {holding} — release one before acquiring another"
            )
        now = self._now()
        affordable = self.window.left(now) - self.dials.tail_seconds
        if self.out_of_time():
            return None
        ranked = self.order(snapshot, leased=leased)
        if not ranked:
            return None
        # What is already being worked is not picked again. A no-op while `concurrency` is one,
        # and the difference between a second hold and a second Attempt at the same Challenge for
        # any v3 that raises it.
        available = [one for one in ranked if one.challenge_id not in self._held]
        if not available:
            return None
        chosen, exploring = self._chosen(available)
        found = next(one for one in snapshot.unsolved if one.challenge_id == chosen.challenge_id)
        budget = int(min(chosen.budget_s, affordable))
        self._taken += 1
        self._held[chosen.challenge_id] = Pick(
            challenge=found,
            budget_s=budget,
            deadline=now + dt.timedelta(seconds=budget),
            tier=chosen.tier,
            attempt_sequence=self._spent.get(chosen.challenge_id, _Spent()).attempts + 1,
            order_ranks={str(one.challenge_id): one.rank for one in ranked},
            working_set=tuple(one.challenge_id for one in ranked if one.committed),
            exploring=exploring,
        )
        return self._held[chosen.challenge_id]

    def out_of_time(self) -> bool:
        """Whether the clock can no longer buy an Attempt over the tail — the **one** thing that
        ends a Run.

        Public because `acquire` answering `None` is otherwise ambiguous, and the ambiguity is
        dangerous in one direction: a caller that read *nothing is eligible* as *the window is
        spent* would end a Run at 11:00 on a Board that had simply gone quiet, which is precisely
        the failure the "never because the Board looks finished" rule exists for.

        Asked here rather than recomputed at the call site, because the tail and `L_min` are this
        module's dials and a rule kept in two places is a rule that will disagree.
        """
        return self.window.left(self._now()) - self.dials.tail_seconds < self.dials.floor_seconds

    def release(self, outcome: Ended) -> None:
        """Give the Attempt back, and count what it cost.

        Counting is unconditional and the ledger only ever grows: an Attempt whose spend went
        unrecorded is an Attempt that bought its Challenge a free place at the top of Order, which
        is the livelock `spend_norm` exists to make impossible.
        """
        self._held.pop(outcome.challenge_id, None)
        spent = self._spent.setdefault(outcome.challenge_id, _Spent())
        spent.attempts += 1
        spent.seconds += max(0.0, outcome.seconds)
        spent.checkpoints += max(0, outcome.checkpoints)
        spent.impossible = spent.impossible or outcome.cause == CUT_SELF_REPORTED_IMPOSSIBLE
        spent.crashed = spent.crashed or outcome.cause == CRASHED

    def order(self, snapshot: Snapshot, *, leased: Collection[int | str] = ()) -> tuple[Ranked, ...]:
        """Rank every eligible Challenge, best first — the function itself, recomputed from scratch.

        Total and deterministic: every eligible Challenge appears exactly once, ties break on the
        Board's own `position` and then on id, and both are stable for the length of a Run. Any
        Challenge with no Tier yet is triaged here, which is what stops a Challenge released
        mid-Run from reaching the ranking with a null Tier.

        *Pure* is ADR-0015's sense — **no order is stored**, so there is nothing to splice or
        repair — rather than a claim to touch no state. Two things a first sight of a Challenge
        does write: its Tier, which never changes again, and the solve sample velocity is measured
        from. Both make this **idempotent** and not free of effect: the second call over the same
        Snapshot writes nothing and answers identically, which is the property a replay needs.
        """
        eligible = [one for one in snapshot.unsolved if not _undeployable(one)]
        if not eligible:
            return ()
        self._triage_arrivals(eligible)
        tractability = self._tractability(eligible, snapshot)
        value = _scaled({one.challenge_id: float(one.value) for one in eligible})
        scored = [
            Ranked(
                challenge_id=one.challenge_id,
                name=one.name,
                rank=0,
                score=self._score(one, tractability[one.challenge_id], value[one.challenge_id], leased),
                tier=self._tier(one.challenge_id),
                budget_s=self._length(one.challenge_id),
                solves=one.solves,
                demoted=self._demoted(one.challenge_id),
                committed=False,
            )
            for one in eligible
        ]
        places = {one.challenge_id: (one.position, str(one.challenge_id)) for one in eligible}
        scored.sort(key=lambda one: (one.demoted, -one.score, places[one.challenge_id]))
        affordable = self.window.left(self._now()) - self.dials.tail_seconds
        affordable_attempts = _attempts_affordable(
            affordable, [one.budget_s for one in scored], floor=self.dials.floor_seconds
        )
        return tuple(
            replace(one, rank=place, committed=place <= affordable_attempts)
            for place, one in enumerate(scored, start=1)
        )

    def _chosen(self, ranked: Sequence[Ranked]) -> tuple[Ranked, bool]:
        """Order's top, except on the reserved exploration turn — and whether this *was* one.

        The exploration pick is taken from the whole of Order rather than from the working set —
        *"regardless of rank"* is the mechanism, and a share restricted to the top K would be no
        share at all on the Board it is there to protect us from. Where nothing qualifies the turn
        falls through to Order's top rather than being spent on nothing (ADR-0017).

        The flag is returned rather than inferred from *"is this Order's top"*, because an
        exploration turn that lands on Order's top is still an exploration turn — and a Run where
        the two coincided every time is exactly the measurement that would say the share is
        redundant.
        """
        if self._taken % self.dials.explore_every != self.dials.explore_every - 1:
            return ranked[0], False
        unsolved_by_anyone = (one for one in ranked if one.solves <= self.dials.explore_at_most_solves)
        explored = next(unsolved_by_anyone, None)
        return (explored, True) if explored else (ranked[0], False)

    def _triage_arrivals(self, eligible: Sequence[Sighting]) -> None:
        """Give a Tier to everything that has none, and never revisit one that has.

        Only the arrivals are triaged, which is what ADR-0015 asks for and which costs something
        worth naming: Triage's `solves` band is computed over the set it is handed, so a lone
        arrival has nothing to be ranked against and falls through to the judge or to the floor.
        Re-triaging the Board every cycle would fix that and would re-ask a model about every
        Challenge nothing states a difficulty for, every cycle, for a prior we then never lower.
        """
        arrived = [one for one in eligible if one.challenge_id not in self._priors]
        if not arrived:
            return
        for judgement in triage(arrived, recorder=self._recorder, judge=self._judge):
            self._priors[judgement.challenge_id] = judgement.tier

    def _tractability(self, eligible: Sequence[Sighting], snapshot: Snapshot) -> dict[int | str, float]:
        """The crowd's verdict on a Challenge: **velocity where two samples exist, the count before**.

        At t=0 every Challenge on a fresh Board reads zero solves and the count separates nothing.
        Two hours in, *"no movement while its neighbours gained 40"* is what tells `nobody can` from
        `nobody has yet`, and the count cannot carry that — it keeps crediting a Challenge for the
        rush that happened before we arrived (ADR-0017).

        The fallback is per Board rather than per Challenge in one direction only: where **nothing**
        on the Board has moved between samples the count carries the whole set, because a Board
        that is simply quiet has not told us anything and reading every Challenge as stalled would
        put the term to sleep. Where the Board is moving, a Challenge with a single sample falls
        back to its own count — which is what stops a Challenge released at 13:00 from being ranked
        as though the field had passed it by.

        The first sample is taken here rather than kept by the caller, because *when we first saw
        this Challenge and on how many solves* is a fact only this term ever reads. The baseline is
        therefore **the first Snapshot this Run ranked that carried the Challenge** — which is the
        first Intake cycle that found it, since Order is recomputed at every Attempt boundary and
        Intake runs on a cycle underneath. An offline replay reads the same baseline off the intake
        records, and would drift from a Run that somehow ranked nothing for a cycle.
        """
        counts = _scaled({one.challenge_id: float(one.solves) for one in eligible})
        speeds = {}
        for one in eligible:
            first_at, first_solves = self._first_seen.setdefault(one.challenge_id, (snapshot.at, one.solves))
            elapsed = (snapshot.at - first_at).total_seconds()
            if elapsed > 0:
                speeds[one.challenge_id] = max(0.0, (one.solves - first_solves) / elapsed)
        if not any(speeds.values()):
            return counts
        moving = _scaled(speeds)
        return {one.challenge_id: moving.get(one.challenge_id, counts[one.challenge_id]) for one in eligible}

    def _score(
        self,
        sighting: Sighting,
        tractability: float,
        value: float,
        leased: Collection[int | str],
    ) -> float:
        spent = self._spent.get(sighting.challenge_id, _Spent())
        dials = self.dials
        return (
            dials.w_solves * tractability
            + dials.w_value * value
            + dials.w_lease * (1.0 if sighting.challenge_id in leased else 0.0)
            + dials.w_progress * min(1.0, spent.checkpoints / dials.checkpoints_full)
            - dials.w_spend * self._spend_norm(spent)
            - dials.w_impossible * (1.0 if spent.impossible else 0.0)
        )

    def _spend_norm(self, spent: _Spent) -> float:
        """`max(time_norm, attempts_norm)`, and the `max` is the whole of the anti-livelock claim.

        An adapter that fails to launch, or a Target that refuses instantly, produces an Attempt of
        two seconds. Every clock reads *almost no spend*, so a penalty on time alone leaves the
        Challenge top of Order and it is picked again immediately — a tight loop paying ~22k input
        tokens a turn. The count leg has no such blind spot: six of those and the Challenge reads
        fully spent.
        """
        by_time = spent.seconds / (self.dials.ceiling_fraction * self.window.total_seconds)
        by_attempts = spent.attempts / self.dials.attempts_full
        return min(1.0, max(by_time, by_attempts))

    def _demoted(self, challenge_id: int | str) -> bool:
        """Below every un-demoted Challenge — and back in play once the whole Board is past its own.

        Two causes, and they are the same shape: a Challenge that has held more than `f` of the
        whole window, and one `stall.Breaker` has ruled the Solver's own fault. Neither is an
        exclusion, because a Board where every Challenge is demoted still hands out its best one.
        """
        spent = self._spent.get(challenge_id, _Spent())
        return spent.crashed or spent.seconds >= self.dials.ceiling_fraction * self.window.total_seconds

    def _tier(self, challenge_id: int | str) -> int:
        """`prior + min(K, checkpoints across Attempts)`, and nothing lowers it.

        Symmetry is the obvious design and it is a livelock: a Challenge that fails twice would get
        less time, so it would fail again faster, so it would get less time. It is also the wrong
        axis — a barren Attempt is evidence about *whether* to come back, which is Order's question
        and which `spend_norm` already answers.
        """
        spent = self._spent.get(challenge_id, _Spent())
        return self._priors.get(challenge_id, FLOOR) + min(self.dials.tier_cap, spent.checkpoints)

    def _length(self, challenge_id: int | str) -> int:
        """`L(c) = L* × tier_weight(c)`, never shorter than `L_min`."""
        weight = 1.0 + (self._tier(challenge_id) - FLOOR) * self.dials.tier_step
        return int(max(self.dials.floor_seconds, self.dials.knee_seconds * weight))


def render(ranked: Sequence[Ranked]) -> str:
    """Order as a reader sees it — the rank vector a dry run prints and a Run is replayed against."""
    return "\n".join(
        f"{MARK} {one.rank:>3}. {one.score:+.3f} · tier {one.tier} · {one.budget_s:>4}s "
        f"· {one.solves:>4} solves · {'demoted  ' if one.demoted else ''}"
        f"{'working set' if one.committed else 'not reached'} · {one.challenge_id} {one.name!r}"
        for one in ranked
    )


def _undeployable(sighting: Sighting) -> bool:
    """A `shared` Isolated Challenge is undeployable by us entirely — POST, PATCH *and* DELETE all
    fail, and only an admin ever deploys one (`solver/instance.py`). It is the one thing besides
    being solved that takes a Challenge out of the eligible set, and it is a fact about the Board
    rather than a judgement about the Challenge."""
    return sighting.terms.instanced and sighting.terms.shared


def _scaled(values: Mapping[int | str, float]) -> dict[int | str, float]:
    """0–1 against the largest in the set, and 0 for everything where the largest is not positive.

    Scaled over the eligible set rather than against a constant, because every input here is a
    Board's own units: 500 points is generous on one Board and the floor on another.
    """
    top = max(values.values(), default=0.0)
    return {key: max(0.0, value) / top if top > 0 else 0.0 for key, value in values.items()}


def _attempts_affordable(seconds: float, lengths: Sequence[int], *, floor: float) -> int:
    """`K = (T_remaining − tail) / L̄` — how many Attempts the clock can still buy.

    The mean rather than each Challenge's own length, because the working set is a count of
    Attempts and not a slice per Challenge: what is being asked is *how many more times can we do
    this*, and the answer narrows on its own as the Run burns down.

    Time that can still buy an Attempt buys at least one, whatever the mean says. A window with
    nine minutes left over a ten-minute mean is a window with one short Attempt in it, and a `K` of
    zero there would report a working set nothing is in while an Attempt was being worked.
    """
    mean = sum(lengths) / len(lengths) if lengths else 0.0
    if seconds < floor or mean <= 0:
        return 0
    return max(1, int(seconds // mean))


def _written(path: Path) -> Window:
    """The window already on disk. A file that cannot be read is refused, never replaced — writing
    a fresh window over an unreadable one is exactly the silent extension the stamp exists to
    prevent."""
    try:
        stamped = json.loads(path.read_text())
        return Window(
            opened_at=dt.datetime.fromisoformat(stamped["opened_at"]),
            ends_at=dt.datetime.fromisoformat(stamped["ends_at"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as unreadable:
        raise ValueError(f"{MARK} the Run's window at {path} cannot be read — {unreadable}") from unreadable
