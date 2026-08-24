# There is no queue, and the clock chooses a working set

[#47](https://github.com/jerome-queck/incypher-ctf/issues/47) was chartered to settle where a cut
Challenge lands and what recomputes the sequence. It inherited its own framing from
[ADR-0009](0009-store-what-was-observed-derive-every-judgement.md) and
[#16](https://github.com/jerome-queck/incypher-ctf/issues/16), both of which say a cut Challenge
goes *"to the back of the queue"*.

There is no queue. `CONTEXT.md` had already committed to the opposite — **Order** is *"recomputed
from the Board's own signals every time it picks, never fixed at the start"* — and the two
vocabularies had been running side by side unnoticed since #16. This record settles the question in
the glossary's terms and corrects the other two.

The decision has three parts:

**Order is a pure, deterministic, total function over Board state and Run state, recomputed at every
Attempt boundary.** Nothing is stored, spliced or repaired, so "requeue" means only that a Challenge
is eligible again — which it is the moment its Attempt closes.

**The clock chooses a working set, not a slice per Challenge.** The remaining time buys a number of
Attempts, and those Attempts are spent on the top of Order.

**Nothing is ever banned, and nothing is ever given less time for having failed.** A barren Attempt
costs a Challenge its place in line, never its budget.

This record **corrects [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md)** in three
places and **extends [ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md)** and
[ADR-0007](0007-truth-about-an-instance-lives-on-the-board.md) in one each.

## Why a function rather than a queue

A stored order answers the state that was live when it was written, and the Board does not hold
still — **Intake** exists precisely because Challenges are released mid-event, hints are added and
files are replaced. A queue would need repairing on every Intake, every solve and every release:
three mutation paths where a function has none, and three places for the ordering to drift from the
Board it claims to describe.

This is [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md)'s own argument in a
second setting. A stored roll-up answers exactly one threshold — the one live when it was written —
and a stored order answers exactly one Board state. Both are recomputed from what was observed.

The cost is real and small: Order is evaluated over every unsolved Challenge at every Attempt
boundary. It touches no model, reads Intake's local copy rather than the wire, and Brunner's 74
Challenges are the largest Board we have measured.

## The function

Every term normalised to 0–1, weights in a versioned config block:

```
order(c) = + w_solves     × solves_norm(c)            # the crowd says it is tractable
           + w_value      × value_norm(c)             # the setter's own estimate, and the payoff
           + w_lease      × lease_alive(c)            # mana already spent, TTL already burning
           + w_progress   × checkpoints_norm(c)       # it moved the environment last time
           − w_spend      × spend_norm(c)             # monotone, and the anti-livelock guarantee
           − w_impossible × self_reported(c)          # the volunteered "impossible"

  demoted below every un-demoted Challenge:  past its f × T_total spend ceiling
  ineligible:                                solved · undeployable by us
  tie-break:                                 the Board's own position, then id
```

`spend_norm(c)` is **`max(time_norm, attempts_norm)`**, and that `max` is load-bearing rather than
defensive. A monotone penalty on *time* alone does not bound a degenerate Attempt: an adapter that
fails to launch, or a Target that refuses instantly, produces an Attempt of two seconds and near-zero
spend, so the Challenge stays at the top of Order and is picked again immediately. The loop is tight,
every counter reads *"almost no spend"*, and at ~22k input tokens of preamble per invocation
(measured, below) it is a quota fire as well as a wasted Run. Counting Attempts as well as seconds
closes it, and restores the guarantee: **any Challenge picked repeatedly without producing
Checkpoints eventually ranks below every un-attempted one**, so the Board gets covered and a
livelock is impossible by construction rather than by a threshold someone tuned.

Deadlock is impossible for a duller reason: there is nothing to block on. While any unsolved
Challenge exists the function returns a top element.

### What is deliberately absent

**`strength(category)`.** Both `CONTEXT.md` entries were written around it — Order *"goes where the
Solver is strongest"*, Tier *"is deliberately biased toward the Categories the Solver is weakest
at"* — and v1 cannot have it. **Category is an open string read from the Board**, verified across
IN-CYPHER's `(Practice) forensics` and Brunner's `flightops`, and the competition batch is unseen by
construction ([#12](https://github.com/jerome-queck/incypher-ctf/issues/12),
[#18](https://github.com/jerome-queck/incypher-ctf/issues/18)) — so a table keyed by category name
has no entry for the categories that will actually be scored.

Seeding it uniformly was considered and rejected as worse than cutting it: a term that is present,
inert, and described in the glossary as though it worked is exactly `ctf-workspace`'s `final_tier`,
which its own ADR-0033 records parking **32 of 33** challenges at the default. What is left is
entirely measured — Order runs on solves, value, progress, lease and spend; Tier runs on the
difficulty a Board **states**, which #18 found in 22 of 25 sampled Brunner descriptions. Both
glossary entries are corrected to say the weighting is deferred rather than to describe a scheduler
nobody wrote.

## The clock

The naive allocation divides the remaining time by the number of unsolved Challenges. On Brunner
that is 74 Challenges into 5.5 hours — four minutes each — and it solves nothing.

The sharp reason is not "plan for failure", though that is the consequence.
[#15](https://github.com/jerome-queck/incypher-ctf/issues/15) measures that **most solves land in
the first ~20 Steps** and that **spend anti-correlates with success**, which describes a solve
probability that rises steeply and then flattens. There is a knee, `L*`, past which more time buys
almost nothing — and *below* which almost nothing is bought at all. Dividing by the Challenge count
puts every Attempt in the flat near-zero region.

```
L(c) = L* × tier_weight(c)                    # Tier is a multiplier on the knee
K    = (T_remaining − reserve) / L̄            # Attempts still affordable
                                              # working set = top K of Order
```

Failure is planned for because a Challenge takes `A > 1` Attempts, so distinct Challenges reached is
`K / A` — and `A` is **measured** from practice Runs rather than assumed to be 1. `L*`, the reserve
and `A` are the three dials, and all three are calibrated from the Step stream ADR-0009 already
writes.

**The final stretch is not a mode.** As `T_remaining` falls, `K` falls, and the working set narrows
to the top few of Order on its own. An explicit late-run policy was rejected because it is a second
thing to calibrate that fires exactly once per Run — the worst possible place for untested code.

There is no deploy-specific late-run gate either. It was proposed and cut: ADR-0007's
`min(budget, TTL)` already prevents an Attempt outliving its Instance, and a fresh deploy's TTL is
generous, so gating deploys would forbid Attempts that were perfectly viable. The rule that survives
is cause-neutral — **do not start an Attempt shorter than `L_min`**, whether or not it needs a
deploy.

### The ~22k-token floor

Measured against `codex-cli` 0.147.0 on 2026-08-24: `codex exec --json` with the prompt *"Reply with
exactly: ok"* and a five-token reply billed **`input_tokens: 21995`**. That is the system prompt and
tool schemas, paid fresh on every invocation, and since ADR-0014 makes each Attempt a fresh
`codex exec` it is a fixed per-Attempt tax. It is an independent floor under `L_min` and a second
argument against spreading the clock thin.

## Tier moves on evidence, and only upward

Triage's Tier is a **prior**, and #18 established how weak a prior it is: an LLM asked for difficulty
from a description alone returns correlations *consistently near zero*. Attempting a Challenge is the
only real evidence of its difficulty we ever get, so a Tier that never moves is a guess defended for
5.5 hours.

What moves it is **Checkpoints** — the evidence ADR-0005 already trusts to buy time *inside* an
Attempt, extended across them. `tier = prior + min(K, checkpoints_across_attempts)`, the same small
cap ADR-0005 uses for in-Attempt extensions.

**Nothing lowers a Tier.** Symmetry is the obvious design and it is a livelock: a Challenge that
fails twice gets less time, so it fails again faster, so it gets less time — never terminal in the
letter of ADR-0005 and effectively terminal by hour three. It is also the wrong axis. A barren
Attempt is evidence about **whether** to come back, which is Order's question and which
`spend_norm` already answers; it is not evidence that the Challenge got easier.

The retry is not a re-roll of the same dice, which is what makes this coherent. ADR-0014 established
that **the workdir is the memory** — a Checkpoint is a state transition, so the extracted archive,
the shell that answers and the route that went 403→200 are still on disk when the next Attempt
opens. What is discarded is the model's theory; what survives is the environment it moved.

### Attempt quality already has a name

A second quality grade was proposed for the case where an Attempt runs a few Steps, finds nothing
new, and stops. It was rejected: that case is **zero Checkpoints with non-zero Steps**, and it is
already handled three times over.

| | Checkpoints > 0 | Checkpoints = 0 |
|---|---|---|
| **Steps > 0** | productive — Tier rises, `w_progress` boosts Order | cut on `repetition`/`novelty`, no Tier rise, `spend_norm` sinks it |
| **Steps = 0** | *(impossible)* | broken — circuit breaker, `crashed`, backoff |

A second grade would either be a function of Checkpoints, or it would read the model's prose — which
ADR-0005 forbids as stall input, and which is precisely what `ctf-workspace`'s `stall-guard` did
wrong. If Checkpoints prove too coarse, that is a threshold to calibrate in shadow mode, not a
mechanism to add.

## Consequences

- **`cut:self-reported-impossible` becomes a large, non-decaying penalty, and never an exclusion.**
  Large, because ADR-0005 admits the signal for measuring **28–64% of tokens saved** on failed
  trajectories. Non-decaying, because a model that called something impossible will call it
  impossible again, and a decaying penalty re-buys the same refusal every hour. Never excluding,
  because ADR-0009 keeps this cause **as an alarm the aim is for it never to fire** — and a cause
  that silently deletes Challenges is an alarm nobody can watch trending to zero.
- **A Challenge past its `f × T_total` spend ceiling is demoted, not excluded** — it ranks below
  every Challenge not past its own, so it returns only when the whole Board is past its ceiling. The
  ceiling exists for the *seductive* case, which is the harder one: ADR-0014 makes consecutive
  Attempts ordinary, a live Lease boosts Order, and Checkpoints raise Tier — three pressures pushing
  the same way, and one comparison caps their compounding rather than tuning each.
- **A degenerate-Attempt circuit breaker, and Run-level backoff.** N consecutive zero-Step Attempts
  on one Challenge is evidence about *us*, not the Challenge — hard-demote and record `crashed`. If
  the last M Attempts across *different* Challenges are all zero-Step, the Solver is broken and must
  not spin: exponential backoff, capped. It cannot fix itself and nobody is coming (ADR-0010 makes
  exhaustion a stall, not an ending), but it can fail slowly enough that the window it is burning
  stays recoverable. **This is a safeguard, not a proof** — it bounds a bug's blast radius and does
  not detect the bug; ADR-0009's stream detects it, after the Run.
- **`value` is an input, and #18's finding is narrowed.** #18 measured `value` as a strict function
  of `solves` and concluded *"one difficulty signal, not two"*. That was **measured on Brunner and is
  not a CTFd law**: dynamic value is computed from solves with *per-challenge* `initial`, `decay` and
  `minimum`, so two Challenges at equal solves differ in value exactly when their setters seeded them
  differently. `value` therefore carries the setter's own difficulty estimate, which `solves` cannot
  contain. The two remain correlated, so their weights cannot be tuned independently from a single
  Run — a calibration note for the eval queries, not a reason to drop either.
- **The scoreboard is recorded from v1 and acted on at v3.** `GET /api/v1/scoreboard` answers **200
  unauthenticated** on Brunner — verified 2026-08-24, and notably *more* readable than
  `/api/v1/challenges`, which is 403 without a token. So logging the top-N and every Challenge's
  `(solves, value)` pair at each Intake costs one GET and no model. That makes the placing-aware
  objective **back-testable against real Runs** instead of designed blind, which is ADR-0009's
  principle exactly. Two facts it will need: CTFd exposes none of `initial`/`decay`/`minimum` to a
  non-admin, so the curve is **fitted from our own logged pairs** rather than assumed — robust to
  which of CTFd 3.7+'s two decay forms a Board uses — and ADR-0009 already found that **CTFd
  recomputes value retroactively for every solver**, so our own solve lowers the Challenge's worth to
  us and a last-minutes race needs `f(solves + 1)`, never the current `value`.
- **Triage runs on new arrivals before Order sees them.** A Challenge released mid-Run has no Tier;
  `CONTEXT.md` already makes Triage *"one callable thing rather than a stage of a pipeline"*, so it
  runs at the Attempt boundary following an Intake that found something, and Order never handles a
  null Tier.
- **The leak sweep runs at Run close as well as at every Attempt boundary**, extending ADR-0007.
  Nothing may be left held when the process exits — chall-manager never evicts, so a Lease abandoned
  at 16:00 costs capacity nobody reclaims.
- **`concurrency` and `reasoning_effort` are config parameters pinned to constants.** v1 varies
  neither — ADR-0014 gives it one brain per Run, switching on exhaustion alone — but the slots exist
  so v3 tunes a number rather than reshaping the scheduler.
- **Order is deterministic and replayable.** Total, tie-broken on the Board's `position` then `id`,
  both stable across a Run. This is not tidiness: ADR-0009's shadow mode replays offline over the
  stored stream, and a non-deterministic Order cannot be replayed at all — *"would these weights have
  banked Flags faster"* is unanswerable without a reproducible baseline. `ctf-workspace` has a pure
  total-order sort key that ports near-verbatim (#18).

## Corrections to ADR-0009

1. **"A cut Challenge goes to the back of the queue" is wrong** — there is no queue. It becomes the
   `w_impossible` penalty above.
2. **Order's rank per Challenge is recorded at each pick.** ADR-0009 records nothing about a
   Challenge that was never attempted, so a Run that ignored 60 Challenges and a Run that only had 14
   are indistinguishable in the stream — and eval question 5, *"Is Order banking Flags early?"*,
   cannot be answered without knowing what Order passed over.
3. **Run-open and each Intake record the scoreboard top-N and every Challenge's `(solves, value)`
   pair**, per the consequence above.

## What this record does not settle

**Acting on observed waste.** This record grades an Attempt after the fact and steers the *next*
pick; it does nothing to stop the Solver re-entering a wasteful shape it has already paid for twice.
Learning across Attempts belongs with the full anti-derailment subsystem at v2, and is on the map's
fog beside the semantic relevance check and the remaining-wall-clock question.

**The placing-aware objective.** Which Flag moves us past a particular team — v3, and the only part
of the scheduler that would reason about opponents. v1 records what it needs and reasons about none
of it.

**Every number here.** `L*`, `L_min`, the reserve, `A`, `f`, `K`, the circuit-breaker counts and all
six weights are parameters, and not one has a source. The map's standing fact holds: there is **no
academic work on allocating a fixed budget across a Board** — every benchmark caps per-Challenge
independently. These are ours to measure on day 1, exactly like ADR-0005's stall thresholds, and the
shadow-mode replay is the instrument for both.

## Revisit when

- **A practice Run has produced a Step stream long enough to fit `L*` and `A`.** Every dial above is
  currently a guess with an argument attached; one Run replaces the arguments with a distribution.
- **The scoring function becomes known**, which would move `w_value` off a default and make the v3
  placing objective specifiable rather than sketched.
- **The v1 → v2 version gate**, which reopens the prior version's decisions by design
  ([ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md)).
