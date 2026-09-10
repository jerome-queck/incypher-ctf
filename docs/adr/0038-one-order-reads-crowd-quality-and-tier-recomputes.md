# One Order reads crowd quality and Tier recomputes

> **The payoff term and its missingness boundary are amended by
> [ADR-0055](0055-one-score-basis-qualifies-one-release-candidate-profile.md).** Order consumes
> Challenge value without calling its scalar score: any absent or unsettled value makes the whole
> coherent snapshot use unit value until the next Attempt boundary. Crowd qualification, neutral
> missing evidence, deterministic recomputation and Tier rules stand.

[Order and Tier when the board has no
crowd](https://github.com/jerome-queck/incypher-ctf/issues/163) found that a solve-count field is
not a crowd: IN-CYPHER served canned empty collections beside old per-Challenge solve rows, while
Brunner's nine distinct unresolved counts never moved across seventeen post-event Snapshots. We
decide to keep **one deterministic Order over every Board state, make unavailable evidence neutral,
recompute Base Tier from qualified observations, and tune the declared policy through controlled
practice rather than treating field presence or one historical Run as calibration**. This is a
planning decision whose production changes and proofs belong to wayfinder map 153's `/to-spec` ->
`/to-tickets` handoff.

## There is one Order, not a crowd mode and a crowd-less mode

Every Challenge receives the same total function. A **Crowd observation** is the raw population
evidence read at one trusted time; a **Crowd state** is the derived quality of that evidence for one
Challenge: `unavailable`, `provisional` or `qualified`. The starting crowd component is

```text
crowd(c) = 0.5 + reliability(c) x (tractability_percentile(c) - 0.5)

reliability(qualified)   = 1
reliability(provisional) = 0.25
reliability(unavailable) = 0
```

The starting Order is

```text
order(c) = + 1.00 x crowd(c)
           + 0.50 x value_norm(c)
           + 0.75 x lease_alive(c)
           + 1.25 x checkpoints_norm(c)
           + 1.00 x category_strength_norm(c)
           - 1.50 x spend_norm(c)
```

The numbers are versioned starting dials, not claims of optimality. A Challenge with no still-trusted
Crowd observation, including one whose source is missing, unreadable past its age limit or positively
synthetic, receives the same neutral midpoint instead of being sunk for what we do not know. An
unseen or under-sampled Category does the same. Trusted point value remains the current payoff even
where dynamic scoring correlates it with crowd evidence; an unsettled value is neutral until a
trusted read settles it. Lease, Checkpoint and spend terms retain their meanings from ADR-0015.
ADR-0031's removal of a self-reported-impossible penalty stands.

Every unsolved Challenge remains in Order. A positive failure of an acquisition precondition defers
that side effect while Intake or Recovery settles it; it never removes the Challenge or adds a rank
penalty. Board position and then Challenge id remain the total tie-break. A reserved exploration
Attempt, initially every fourth, chooses the best provisional or unavailable Challenge using Order
without its crowd component. When none qualifies it falls through to Order's top. The share and the
monotone time-or-Attempt spend penalty make uncertain Challenges reachable without inventing an
exclusion.

This fallback helps by construction in three bounded senses: an unqualified crowd cannot reverse a
ranking, the remaining trusted payoff, Lease, progress, Category and spend terms still discriminate,
and exploration plus a total tie-break makes every unsolved Challenge reachable while none is
banned. Controlled Runs must still show that those properties improve Flags or wall-clock against
the present policy before implementation passes the Gate; the historical evidence is too sparse to
claim an empirical gain now.

## Qualification proves trust, activity and discrimination

Field presence and a successful status are not qualification. The initial qualified state requires
all of the following:

- a structurally settled source that has not positively exhibited a synthetic response;
- two observations at least five minutes apart;
- trusted Board activity within the preceding thirty minutes;
- at least five new solves across three Challenges, or five distinct recent solvers;
- at least three distinct tractability values; and
- no single tied bucket containing 75 percent or more of the eligible set.

These are versioned dials, calibrated through the local Board and its hidden Evaluator. Two
consecutive qualifying observations enter `qualified`; two consecutive failures return it to
`provisional`. Positive proof
of a canned or synthetic source makes it `unavailable` immediately: examples include the same body
across semantically incompatible endpoints, an invalid control returning the same data, malformed
identity or schema, and authenticated contradiction. An ordinary read failure proves none of those.
Unreadability itself contributes no term: under ADR-0036 the last trusted observation may continue
at its derived reliability for an initial maximum age of thirty minutes, after which Crowd state is
`unavailable` and neutral. The age is a versioned dial, and observation time makes the transition
replayable.

Tractability is rolling solve velocity over thirty minutes once two timed samples exist, and solve
count percentile before then. Scoreboard and team activity validate freshness; they never rank a
Challenge. A freshly released Challenge stays provisional until it has had the observation window
and population activity needed to say that zero movement means anything.

The Challenge LIST is the cheapest coherent crowd read: it supplies every Challenge's solves,
value, position and solved state in one response. Updating one Challenge would compare a new value
against old peers, so it is forbidden. Immediately before acquiring an Attempt, Intake refreshes
the LIST unless another Lane produced a trusted one within sixty seconds. It atomically updates the
whole cross-section, fetches newly arrived Challenges, and then Order chooses from that coherent
state. The selected Challenge's detail and changed files refresh before any side effect; an
eligibility change reranks before acquisition.

The complete Intake sync starts at fifteen-minute intervals, shortened by a published release
cadence or measured Board limit and backed off on read failure. It refreshes all details, compatible
scoreboard and activity sources, changed files and unsettled reads; unsolved Challenges are served
before solved ones when work is constrained. Where LIST movement cannot settle freshness, the sync
may inspect recent solve histories for a deterministic sample of unsolved, new or provisional
Challenges bounded by

```text
min(unsolved, max(2, ceil(sqrt(unsolved))))
```

The resource control owned by the topology, concurrency and Recovery tickets may lower that cap
under Board faults or measured pressure and never silently raises it. On the current stable seam a
complete sync costs `N + 2` requests for `N` Challenges;
extra compatible sources, changed Artefacts and unsettled reads are explicit incremental cost.
Insufficient evidence leaves the crowd provisional.

## Base Tier may move; an active Attempt may not

**Base Tier** is the recomputed evidence-derived effort band before Solver-specific Category
weakness and Checkpoint uplift. Four reverse tractability quartiles map qualified crowd to four
bands: the most tractable quarter receives Base Tier 1 and the least tractable or zero-velocity
quarter receives Base Tier 4. Qualified empirical crowd evidence may replace either an author-stated
difficulty or the durable model judgement. Otherwise Triage uses recognised stated difficulty,
then one durable batched model judgement, then the honest unknown floor of Tier 2.

Losing freshness does not resurrect an older judgement: the last qualified Base Tier remains until
new qualified crowd changes it. A newly seen ambiguous release may join one bounded model call; an
existing durable answer is replayed across Boots and is not re-asked merely because crowd state
changed.

Category strength is a Solver-specific reward-per-minute estimate, seeded from controlled practice
and shrunk toward the global rate. An accepted Flag is positive evidence, replay-confirmed
Checkpoints receive a bounded partial credit, and no-Flag minutes are censored exposure rather than
proof of failure. After at least three completed Attempts and twenty Attempt-minutes in a Category,
the reliably estimated bottom strength quartile earns at most one extra Tier rung. Unseen or
under-sampled Categories remain neutral. Checkpoints then add their monotone uplift, and the final
Tier is clamped to 1 through 4.

The effective Tier may therefore fall when new qualified external evidence lowers its Base Tier;
failed Attempts and model Claims still cannot lower it. Acquisition records the final Tier and
freezes that Attempt's budget. Observations and learning from a closing Attempt affect only the next
acquisition.

## Live adaptation is arithmetic; model fitting is offline

The formula's structure, feature meanings and safety envelopes are fixed for a Run. Its declared
dials may adapt through predeclared deterministic updates over accepted Flags, time-to-Flag,
Checkpoints, Attempt spend and Cut, Board request latency and faults, and CPU, RAM and disk pressure.
Statistics update after each Attempt closes, changes are bounded per update, and the serial control
authority persists every calibration event before a later acquisition reads it. A replacement Boot
replays the same value. A model's confidence or unverified Claim is never training evidence, and no
model improvises a new formula during a scored Run.

Initial offline calibration searches the constrained transparent formula directly. Weights keep
their signs, Order and Tier are tuned separately, search is seeded, and reports show plateaus and
sensitivity rather than presenting one noisy optimum as truth. Replays expose policy sensitivity;
they cannot manufacture outcomes for Challenges the historical policy never attempted. Paired
controlled Runs and deterministic exploration supply that counterfactual coverage.

The practice pipeline is automatic after every sealed Run: it updates the dataset registry, reports
which eligibility conditions remain unmet, and starts formula and model trials when the corpus earns
them. Calibration Challenges are separate from rotating untouched Discovery holdouts. A reset of
the same Challenge is regression evidence, not a fresh test; once a result affects selection, that
partition is no longer untouched.

The original ticket left model contribution to a separate prototype. The grilling expanded only
the later offline calibration path: the prototype still decides whether a model judgement exists,
and any trained candidate below must enter through this deterministic, bounded control contract.

Machine learning begins only after at least three independent Board or event groups permit separate
training, tuning and untouched testing, every validation fold carries several accepted Flags, and
the learning curve is stable. The first candidate is regularised logistic regression for the
probability of a verified Flag within a fixed horizon from evidence available at acquisition. Its
probability is one bounded Order input and never emits Tier. Training runs host-side after practice,
outside the Solver and Board containers; the shipped path needs only reviewed coefficients and
normalisation data, not a scientific-Python runtime.

Eligibility automatically starts a candidate trial, not a promotion. Promotion requires repeated
untouched local-Board Runs to improve verified Flags and wall-clock without worsening resource
envelopes or worst-case Runs. Accuracy or AUC alone is irrelevant. Every candidate binds the data
manifest, feature schema, model and policy hashes, metrics and rollback target. Failure retains the
transparent champion.

## Records make the adaptation replayable

Every Crowd observation retains its source, read outcome, observation time, response digest and raw
values. The observation window retains sample times, population activity, solve movement, solver
identities where available, dispersion, tie fraction and positive synthetic controls. Derived Crowd
state records the policy version, dials, verdict and reasons.

Every acquisition records all Order components, Base Tier sources, Category and Checkpoint uplift,
final Tier and budget, chosen Challenge, Intake identities and calibration state. Offline replay
recomputes the decision and treats disagreement with the recorded acquisition as a failed invariant,
not a second opinion.

## Handoffs

[The local board: what it replicates, and what it must
not](https://github.com/jerome-queck/incypher-ctf/issues/158) now owns the dynamic population,
release, scoring, fault and sealed-corpus rig that supplies controlled evidence. [How many Attempts
work at once, and who owns the working
set](https://github.com/jerome-queck/incypher-ctf/issues/188) owns atomic acquisition across Lanes.
[The v2 agent topology and routing
policy](https://github.com/jerome-queck/incypher-ctf/issues/187), [The Worker, control, and credential
trust boundary](https://github.com/jerome-queck/incypher-ctf/issues/190), [The Recovery Agent: early
diagnosis, forced repair, and safe
authority](https://github.com/jerome-queck/incypher-ctf/issues/191), [What persists in /state, and how
it stays bounded](https://github.com/jerome-queck/incypher-ctf/issues/175), and [What the Observer CLI
reveals, and when it may control](https://github.com/jerome-queck/incypher-ctf/issues/198) own the
resource control, process ownership, bounded incident evidence and display.

[What evidence makes v2 ready to
freeze](https://github.com/jerome-queck/incypher-ctf/issues/196) proves both the policy and its cost
through accelerated iterations and genuine 5.5-hour Runs. The local Board is therefore the primary
v2 Gate instrument, not a convenience. Its hidden Evaluator controls simulated teams, releases,
changing values, faults and oracles through an authority unavailable to the Solver. Local proof
never claims compatibility with unpublished official PoW, ADK or Target behaviour; evidenced
official deltas enter v2 until its Gate passes and v3 thereafter.

## Consequences

Order remains available on an empty or deceptive Board, but qualification adds observations,
request cost, durable provenance and hysteresis that implementation must keep coherent across
Lanes and Boots. Initial thresholds and weights are declared hypotheses, not evidence-backed
optima. Holding the last trusted observation through a brief fault avoids thrash but permits bounded
staleness; the age limit and reduced provisional reliability expose that trade rather than hiding it.

## Revisit when

Reopen the thresholds, weights or cadence when controlled local-Board Runs show worse Flags,
wall-clock, request load or resource tails than the present policy; when official Board evidence
changes the available sources or limits; or when enough independent positive Runs make the offline
learning gate pass. Preserve one formula, neutral missing evidence, no ban and replayable acquisition
unless evidence directly falsifies those invariants.
