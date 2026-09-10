# One Order spends every scoreable second without slicing Attempts

> **The comparative objective and admission proof are resolved by
> [ADR-0055](0055-one-score-basis-qualifies-one-release-candidate-profile.md).** Final official Board
> score leads within the hard constraints, with a non-blocking score-basis fallback. Three fresh
> descriptive whole-profile pairs select the exact profile before its full-window Gate; this
> record's Attempt allocation and terminal-window lifecycle are what that evidence exercises.

> **The final-submission reserve's storage path is fixed by
> [ADR-0054](0054-authority-remains-writable-when-storage-is-exhausted.md).** Its sealed Candidate set
> reserves every worst-case authority, outcome, reconciliation, Incident and receipt write before
> the time reserve begins. A Candidate without that reservation is fenced before POST, and cleanup
> cannot borrow the terminal floor.

[When may a Challenge claim release productive
capacity?](https://github.com/jerome-queck/incypher-ctf/issues/245) resolves the idle-capacity branch
left by ADR-0043. **There is no parked Attempt, no hidden Challenge pool and no division of the
remaining clock by the number of Challenges. An open Attempt retains its Challenge claim and Lane;
an ordinary admission receives at least the sealed Attempt floor; and one bounded final-chance
round may spend the remaining scoreable time below that floor.**

The sequencer always selects the highest normal Order member that deterministic control can safely
admit now. A hard constraint may defer a higher-ranked Challenge without demoting or hiding it. The
Challenge is reconsidered at every later boundary. This keeps Order authoritative while preventing
one temporarily impossible resource shape from idling otherwise productive Lanes.

This forward-amends ADR-0015's final-stretch arithmetic, ADR-0032's pre-close 300-second tail,
ADR-0043's full-budget no-skip calendars and no-final-round rule, ADR-0049's closing proof,
ADR-0051's Core lifecycle and ADR-0052's tail-entry wording. The one sequencer, pure Order,
exclusive claims, frozen acquired Tier, protected control and Recovery reserves, at-most-once
submissions, closing barriers and post-map implementation handoff stand. This is a v2 planning
contract. Current v1 already has one 300-second admission floor and clips one selected budget to its
fixed tail, but it has neither this parallel admission contract nor the final-chance and
post-competition lifecycles.

## A Challenge is never parked

Every unsolved, deployable Challenge remains in Order. A Cut releases its Attempt's claim only after
the closing barrier has revoked effects and joined or killed owned descendants; a later selection
opens a fresh Attempt. There is no parked Attempt, queued owner or off-Lane claim. Releasing a Lane
therefore means closing its Attempt, not suspending an identity after the Run controller releases
the Lane.

An open Attempt keeps its Lane, claim, frozen Tier, budget and logical Solve Lead Engagement through
Turn gaps and Quota wait. Attempt-local Recovery may diagnose beside declared work; it closes the
Attempt only when safe continuation is no longer possible. Shared-authority Recovery freezes the
effects ADR-0046 requires but does not manufacture a capacity release. A Checkpoint proves progress,
resets its bounded stall epoch and earns only the existing bounded consequences; it never preempts
the Attempt. No wall-clock quantum is added beside the repetition, novelty, Step-cliff, Attempt,
Instance and Run bounds already settled.

Every child process remains owned by its Step, Engagement or Attempt. The Resource governor may end
optional work under pressure using ADR-0043's order, but an open Attempt cannot shed its executor or
background descendants into an ownerless service merely to make its Lane appear free.

## The clock buys Attempts, not seconds per Challenge

For one exact release candidate, the ordinary prospective budget remains:

```text
L(c) = max(L_min, L* × tier_weight(c))
```

`L_min` starts at 300 seconds. It is a release-candidate dial: controlled Gate evidence may change a
future candidate, the Release-candidate manifest seals the chosen value, and no live Run lowers it.
Tier supplies the normal target budget; neither the unsolved count nor the working-set size divides
that target. The mean prospective length may estimate how many complete Attempts the remaining clock
can buy, but that count is lookahead and reporting only. It never becomes `time / Challenges`.

At an ordinary free-Lane boundary, if the time to the final-submission cutoff is at least `L_min`,
hard admissibility first requires a safe Target and Lease through at least `L_min`. The sequencer
then admits its chosen Challenge for:

```text
min(normal Tier budget, time to final-submission cutoff, safe Instance deadline)
```

This deliberately rejects ADR-0043's requirement that the full normal Tier budget fit before any
admission. A six-minute scoreable window may buy one six-minute Attempt even where its normal Tier
budget is ten minutes. It may not buy twelve thirty-second Attempts. An Attempt already open when
the clock crosses `L_min` continues to its existing hard deadline; the threshold is an admission
floor, not a scheduled stop or preemption point.

## Hard admissibility filters; it never reranks

At each admission the choice is `argmax Order(c)` over the currently hard-admissible, unclaimed
Challenges. Deterministic control may defer a Challenge only when a positive current fact proves
that admitting it would be unsafe or impossible now:

- another open Attempt owns its Challenge claim;
- its measured Resource envelope cannot fit without touching protected control or Recovery reserves;
- no safe Target or Lease can exist through its work and submission deadlines; or
- Recovery has frozen the required effect authority.

Solved Challenges and those positively excluded by the existing deployability rules are not
admission candidates. A full normal Tier budget not fitting is not a hard-admissibility failure; the
budget clips as above. A model Claim, predicted difficulty, old failure, absent crowd signal or
temporary lower rank never makes a Challenge inadmissible.

Every deferment records the Challenge, Order rank, exact reason, evidence identity and next
reconsideration boundary. It changes no score and creates no stored order. Intake, released claims,
restored resources, reconciled Leases and Recovery authority changes all trigger the ordinary fresh
evaluation. If none is admissible, the Lane remains free until a relevant fact changes; the Solver
never pretends the Board is finished.

## One final-chance round breaks only the admission floor

When a Lane is free or becomes free with positive scoreable time below `L_min`, it receives one
final-chance entitlement. The final-chance round fills every free enabled Lane once. Each admission:

- recomputes normal Order and takes the highest hard-admissible unclaimed Challenge;
- uses the sealed normal model, effort, Inference route, Tool policy and Resource envelope;
- disables the long-horizon exploration share;
- may select the Challenge whose preceding Attempt just closed if Order still ranks it first; and
- receives exactly the time remaining to the final-submission cutoff, further bounded by its safe
  Instance deadline.

The floor exception changes no other guard. Stall, ownership, effect, Lease, submission and resource
bounds remain. No Checkpoint or extension may cross the cutoff. A Lane whose ordinary Attempt stays
open is not preempted to join the round. If no Challenge is currently admissible, its entitlement
remains unspent and is reconsidered after relevant Intake, resource or Recovery changes. Once the
Lane admits its final-chance Attempt, that entitlement is spent; an early Cut opens no replacement.
This one-round bound prevents the last minutes becoming a rapid series of fresh model-startup taxes.

## Score first; clean after the official end

The pre-close boundary is a **final-submission reserve**, not a generic cleanup tail. Its measured,
bounded duration covers the sealed set of last-call Candidate proposals, serial broker pacing,
request deadline and uncertainty margin. The Release-candidate manifest fixes it before the Run;
live optimism cannot shrink it. Attempt work may consume every earlier scoreable second, and
last-call submissions run before the Board's official closing instant because an unsubmitted
Candidate proposal cannot score.

A healthy Run starts Instance reclamation, evidence flush and process teardown only when the
competition officially ends. These jobs consume no scoring time. The local competition rig must
provide post-close operational grace and prove the cleanup completes. Whether an official runtime
survives its scoring boundary is later official compatibility evidence; its absence is reported and
cannot silently recreate a guessed five-minute pre-close cleanup tail.

If Recovery exceptionally proves before the official end that no sanctioned Boot can continue, the
Run is terminal and cleanup begins immediately. This is containment after productive capacity is
already lost, not an ordinary early-tail policy. Any such early terminality is a zero-tolerance Gate
failure.

## Consequences

The Run controller gains one deliberate exception to its ordinary floor and must persist per-Lane
entitlements, deferment evidence and two separate closing boundaries. A final-chance Attempt is less
likely to repay model startup than an ordinary Attempt, but the once-per-Lane bound makes that cost
finite while preserving a last opportunity to score. Post-close cleanup removes reclamation time
from the scoring window but makes runtime survival after official close an explicit compatibility
fact the rig can prove and the official environment may later contradict.

## Revisit when

Recalibrate a future sealed candidate when controlled receipts disprove the 300-second floor or the
final-submission reserve. Reopen the lifecycle if official rules or runtime evidence forbid work
after the scoring boundary; never reinterpret that evidence during a live Run. Reopen hard
admissibility only if replayable Gate evidence shows a deterministic filter systematically idles
capacity that a safe admission could use.

## Handoff and proof

Production follows the cleared map through `/to-spec` and `/to-tickets`. Canonical records must make
ordinary and final-chance admissions, floor/cutoff dials, hard deferments, entitlement spend,
Challenge claims, Lane ownership, final submissions and post-close cleanup replayable without
reconstructing them from prose.

Controlled scenarios cover one and two Lanes; an active Attempt crossing the floor; ordinary
six-minute clipping; a Cut inside the final five minutes; one final-chance round; an initially empty
round becoming admissible; the same Challenge winning Order twice; resource, Lease, Quota-wait and
Recovery deferments; pending submissions at the cutoff; process death around the official end; and
post-close cleanup. No scenario may divide time by Challenge count, park a claim, preempt confirmed
progress, repeat a final-chance admission, invade protected reserves, miss a scoreable submission or
leak an Instance.

[ADR-0055](0055-one-score-basis-qualifies-one-release-candidate-profile.md) fixes the comparative
objective and admission proof. This record supplies the Attempt-allocation policy it evaluates.
