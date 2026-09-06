# The exploration share and solve velocity are reinstated

> **The v2 form of both mechanisms is replaced by
> [ADR-0038](0038-one-order-reads-crowd-quality-and-tier-recomputes.md).** Solve velocity remains
> preferred once two timed observations exist, but only qualified crowd affects ranking;
> deterministic exploration now reaches the best provisional or unavailable Challenge rather than
> depending on a literal zero-solve count.

> **The accepted restart loss expires at v2 under
> [ADR-0032](0032-a-run-survives-its-boots-and-recovery-owns-the-first-fault.md).** A later Boot
> replays the Run's recorded Intake and ordering facts, including solve-velocity baselines and
> exploration cadence; a process restart may no longer silently reset them.

Order gets back the two ordering mechanisms
[#15](https://github.com/jerome-queck/incypher-ctf/issues/15) decided and
[ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md) does not contain: a
**reserved exploration share** of roughly one Attempt in four, spent on a Challenge nobody has
solved regardless of rank; and **solve velocity in preference to solve count** as the `w_solves`
input, once two samples exist. Everything else in ADR-0015 stands unchanged — the function, the
working set, Tier's one-way movement, and every alternative it rejected.

This is a **partial correction** rather than a supersession. ADR-0015 is still the record of how
the Solver chooses what to work; this one names two terms inside it.

## Why this is a correction and not a new idea

The audit of [spec #63](https://github.com/jerome-queck/incypher-ctf/issues/63) read all twelve
wayfinder tickets against the spec and found this as *"one silent conflict"*: #15 decided both
mechanisms, [#47](https://github.com/jerome-queck/incypher-ctf/issues/47)'s grill never raised
either, and ADR-0015 was written without them. Nothing rejected them. That is a drop, and the
choice in front of
[#73](https://github.com/jerome-queck/incypher-ctf/issues/73) was to reinstate them or to record
them as deliberately dead. They are reinstated, because both survive ADR-0015's own tests — each is
cheap, each is deterministic, and neither needs a signal Intake does not already hold.

### The exploration share

`+ w_solves × solves_norm` weights **high**-solve Challenges up. That is deliberate and right —
*"the crowd says it is tractable"* is the strongest free signal on a Board — but it is exactly the
opposite pressure to the one #15 reserved the share for. Without a share, a zero-solve Challenge is
**systematically last**, in every ranking, for the whole Run: not because it was judged and passed
over, but because the one term that dominates a fresh Board's ranking cannot say anything about it
except *nobody has*.

That is starvation by construction, and it is the failure mode a fixed clock makes permanent — a
Challenge that is never top of Order is never worked at all, and the Run ends with the unsolved set
untouched and no record of why. #15's phrase is the whole argument: *"so the unsolved set is never
starved by construction."*

So every fourth Attempt goes to the top of Order **restricted to Challenges nobody has solved**,
regardless of where they rank. Three properties keep this from being a second scheduler:

- **It reads Order rather than replacing it.** The share picks the *best* of the unsolved set by
  the same function, so the spend penalty still rotates it: a zero-solve Challenge attempted three
  times sinks below the zero-solve Challenges that have not been.
- **It is deterministic**, and therefore replayable. The turn is counted, not sampled — the fourth,
  the eighth, the twelfth. A random share would be cheaper to write and would make an offline
  replay a second opinion rather than a baseline.
- **It is never spent on nothing.** Where no Challenge qualifies — a Board where everything has
  solves — the turn falls through to Order's top. The share protects against a Board we might meet;
  it does not tax the Boards where the question does not arise.

**A quarter, and it is a dial.** One in four is #15's number and has no more source than any other
number in v1. What the share costs is one Attempt in four spent below Order's estimate of the best
available; what it buys is that the unsolved set is reachable at all. `explore_every` and what
counts as unsolved are both parameters, and the first Run that produces a Step stream is what fits
them.

### Velocity in preference to count

`solves` is a **cumulative** count, and a cumulative count at t=0 is uniformly zero. That is the
cold start #15 named: on a fresh Board every Challenge reads the same number and the term separates
nothing at all, so the ranking falls entirely to `value` on the one day it matters most.

Two hours in, the count has the opposite problem. It credits a Challenge for the rush that happened
before we arrived and cannot tell a Challenge the field is currently solving from one the field
finished with at 11:00. **The difference between two samples can.** #15's own phrasing is the test:
*"no movement while its neighbours gained 40"* separates *nobody can* from *nobody has yet*, and no
snapshot of a running total contains it.

Intake already stores what this needs. ADR-0015 has every cycle record each Challenge's
`(solves, value)` pair — for fitting the scoring curve — so velocity is a subtraction over data the
stream already carries. The baseline is stated rather than left implied, because it is what a
replay has to agree with: **the first Snapshot a Run ranked that carried the Challenge**, which is
the first Intake cycle that found it, since Order recomputes at every Attempt boundary and Intake
runs on a cycle underneath. A Run that somehow ranked nothing for a whole cycle would drift from
its own replay by one sample; nothing in v1 does that, and naming the baseline is what makes it
checkable if something later does.

Two fallbacks, and each answers a way this could be worse than the count:

- **Fewer than two samples on a Challenge falls back to its own count.** A Challenge released at
  13:00 has had no chance to move, and reading one sample as a velocity of zero would bury every
  mid-Run arrival — which is the population Intake exists for.
- **A Board where nothing moved between samples falls back to the count for the whole set.** A
  quiet Board has not told us anything, and reading every Challenge as stalled would switch the
  term off for the rest of the Run.

## What was rejected

**Amending ADR-0015 to record both as deliberately dropped.** The honest option, and the one this
record would have been if either mechanism had failed a test ADR-0015 sets. Neither does: both are
deterministic, both are total, both are recomputed rather than stored, and both read signals Intake
already syncs. Dropping a decision because a later grill did not re-raise it is not a decision.

**Reweighting instead of reserving.** A large enough `w_solves` penalty for *high* solve counts
would push some zero-solve Challenges up without a share. It was rejected because it inverts a term
that is otherwise the most reliable signal available, and it turns one guaranteed property — *the
unsolved set is reached* — into a property that holds only where the weights happen to be tuned
right for that Board's distribution.

**A random exploration share.** Cheaper, and it destroys the offline replay ADR-0009's shadow mode
is built on. A deterministic counter costs one modulo.

**Velocity as a separate seventh term.** It would double-count: velocity and count are the same
signal differentiated, so weighting both means tuning two numbers against one another with a single
Run's data. It is the `w_solves` **input**, and the weight stays one number.

## Consequences

- **A quarter of the Run's Attempts are not Order's top pick.** That is the cost, stated plainly.
  It is recorded per pick, so whether the share ever banked a Flag is a question the stream answers
  rather than one the next version argues about.
- **`w_solves` now has two meanings across one Run**, count early and velocity later. The switch is
  per Challenge and is derived rather than stored, so a replay reproduces it — but a weight fitted
  over a whole Run is fitted over both, which is a calibration note the eval queries have to carry.
- **The velocity samples are Run state and do not survive a restart.** A restarted Solver falls
  back to counts until it has seen two cycles again. That is the same trade the ledger makes
  everywhere — the window is the one thing read back off disk
  ([ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md), `CONTEXT.md`,
  *Run state*) — and it degrades to the behaviour ADR-0015 specified rather than to nothing.
- **`CONTEXT.md`'s *Order* entry is corrected.** It described v1's Order as *"tractability, payoff,
  progress, a live Lease, and a monotone penalty"* and nothing else chose the pick. The share is a
  second chooser, and a glossary that does not say so describes a scheduler nobody wrote — which is
  the mistake ADR-0015 named when it cut `strength(category)` rather than stubbing it.

## Revisit when

- **A practice Run has produced a Step stream.** Whether the share ever reached a Flag, and whether
  velocity separated anything the count did not, are both back-testable the moment one Run exists —
  and both are cheap to switch off, because each is one dial.
- **A Board is met whose `solves` do not move at all** — an unranked or freshly-opened Board, or one
  whose scoreboard is hidden. Velocity is inert there by design, and the fallback is what carries
  it; if that turns out to be the common case, the switch is not worth its complexity.
- **The v1 → v2 version gate**, which reopens the prior version's decisions by design
  ([ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md)).
