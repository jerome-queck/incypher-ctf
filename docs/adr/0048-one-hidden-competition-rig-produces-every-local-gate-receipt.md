# One hidden competition rig produces every local Gate receipt

> **Candidate scope is amended by
> [ADR-0051](0051-v2-has-a-finite-core-and-proof-earned-capability-packs.md).** The rig remains
> wholly external evidence infrastructure with zero Solver or official-backend-implementation
> dependency. Synthetic populations,
> automatic datasets/fitting/promotion and elaborate repair orchestration are optional supporting
> work, not Solver Core; external candidate-image repair is a Capability Pack.

[The local board: what it replicates, and what it must not](https://github.com/jerome-queck/incypher-ctf/issues/158)
turns the local Board promised by
[ADR-0033](0033-v2-is-the-complete-pre-final-solver-and-v3-is-only-the-official-delta.md)
into the primary v2 validation instrument. **A private sibling competition rig runs a pinned,
single-replica CTFd plus chall-manager semantic baseline, real isolated Targets and a hidden
Evaluator. It drives sealed dynamic scenarios, qualifying corpora, fault injection and matched
full-window Runs while exposing only ordinary Board, Target and inference surfaces to the Solver.**
It proves local mechanisms and controlled outcomes. It never claims unpublished IN-CYPHER Target,
PoW, ADK, human-crowd or production-load compatibility.

The rig is a separate product at `jerome-queck/incypher-practice-rig`, checked out beneath the
operator's home directory so Colima can mount it. It contains Board and Target definitions,
scenarios, Evaluator, private oracle and corpus metadata, but no Solver code. This repository pins
the rig commit, exact images and promoted receipts. Neither repository is mounted into a Run.

## The baseline is exact and the official profile is honest

The reproducible baseline is CTFd 3.8.5, `ctfd-chall-manager` 0.10.1 and `chall-manager` 0.6.5 with
MariaDB, Redis, a private registry and the upstream janitor. Docker Compose profiles and a small
host CLI are sufficient; Kubernetes would add an unobserved production shape rather than fidelity.
Custom images are multi-architecture. Full Runs use native arm64; amd64 must pass built-image and
smoke proofs rather than slow emulated full Runs.

Pins advance only after a clean build, schema/behaviour compatibility suite and representative Run.
A required upstream change is a minimal extension where possible. A fork additionally records its
upstream commit, patch digest, reason and compatibility tests. The janitor is not hidden behind the
stack: scenarios exercise it healthy, disabled and failing, and the Solver never relies on it for
Lease correctness.

Before every release candidate, a host-side authenticated probe captures the official landing,
guide and confirmed Solver-facing API surfaces. The committed sanitized compatibility lock contains
URLs, status, content type, normalized schemas and headers, asset and body digests, behavioural
results, version bounds and explicit gaps. Encrypted content-addressed raw captures live outside
both repositories and never retain credentials, cookies, Flags or answer material. An exact
official identity replaces the baseline only when evidenced and compatible; otherwise the profile
states its supported range. The release-candidate build consumes the sanitized lock offline.
Missing, incompatible or unexpectedly changed capture evidence blocks promotion of an
official-compatible release candidate; it may not be resolved by guessing an exact version.

Nine observed hosts have shown why status alone is not a fingerprint: an SPA catch-all can return
HTTP 200 for both CTFd and chall-manager-looking paths. Compatibility therefore compares content
type, schema and body with the landing page. Unknown official PoW, ADK and Target behaviour remains
parameterised or absent. It is never invented for a green local result.

## A sealed scenario owns every controlled change

Every Run has a unique Run ID and a versioned declarative **Scenario manifest**. Before Boot it
materialises and seals:

- the random seed, population profile and Challenge family/revisions;
- Board, plugin, Instance-manager, janitor, database, cache, registry, Evaluator, Target and Solver
  image digests;
- corpus roles and splits, release/crowd schedule, Target TTLs and expected observations;
- fault schedule, resource profile, model route and effective identity, effort, prompt/tool/policy
  hashes and supported Lane/Specialist configuration; and
- wall-clock, monotonic and accelerated-scenario time mapping.

Acceleration changes declared release intervals and Target TTLs, never the host or container system
clock.

The Evaluator is a host-side, manifest-driven controller on a private control network. It schedules
events, holds oracles and seals receipts; it never solves a Challenge. Solver and Targets have no
route, credential or mount to the Evaluator, registry, oracle, controller, container socket or host
filesystem. There is no interactive dashboard. [The Observer
CLI](https://github.com/jerome-queck/incypher-ctf/issues/198) may present canonical receipts but does
not become their owner.

The Evaluator reconciles three records for every scheduled change: the intended event, the actual
Board/Target state and the canonical Observation the Solver retained. A mismatch stays visible and
cannot be converted into an expected result after the Run.

Planned and actual events enter an append-only hash chain carrying Run ID and manifest hash. The
final receipt binds all manifest, image, result, isolation and dataset digests and is signed by a
host-held key. That signature establishes integrity, not independent authorship. A controller crash
may resume only after exact reconciliation proves no event was missed or duplicated; ambiguity
invalidates the Run and preserves it for diagnosis.

A reset is transactional. First finalize and verify Promotion; then stop the Solver, destroy the
Run's Targets, namespaces, volumes and networks, prove they are absent, recreate from pinned images,
seed fresh identities and data, and pass isolation preflight. CTFd identities and credentials are
generated per Run into restricted untracked files. Only the separately isolated Codex authentication
broker may persist.

## The Board changes like a competition, not a fixture list

The semantic stack is single-replica because Solver-observable behaviour, not invented high
availability, is the goal. Its scenario suite covers:

- initial/full/incremental Intake; Challenge addition, release, hint, replacement, metadata and
  point change, Target-endpoint rotation and removal;
- list/detail divergence, pagination reorder and duplication, stale caches, source disagreement,
  malformed JSON, an HTML catch-all, authentication refusal, rate limiting, latency and bounded
  failures;
- absent, unreadable, stale, tied, non-discriminating and deliberately canned Crowd observations;
  and
- accepted, rejected, duplicate, delayed and indeterminate submissions, including a lost response,
  retry across Boot, accepted-then-teardown failure and stale-epoch output.

Every submission receipt asserts that serial admission, durable intent and reconciliation produced
no duplicate Board effect and opened no brute-force path.

Board-native simulated users, teams and Solves create the ordinary public views. A rig-only CTFd
extension uses CTFd model/service hooks rather than raw SQL, recalculates dynamic values, clears
caches and verifies list, detail, solves and scoreboard agree. It never creates fake-team Instances.
Solver-visible responses carry no special header, label or endpoint revealing scenario provenance.

The Evaluator privately records that a population is simulated. That is distinct from the Solver's
source fact: the observation came through an authentic Board surface. The three initial
**Population profiles** are deliberately broad hypotheses:

| Profile | Registered | Active |
| --- | ---: | ---: |
| Sparse | 5–20 | 0–5 |
| Typical | 21–60 | 5–25 |
| Busy | 61–149 | 20–80 |

Active never exceeds registered. A hidden schedule derives solving from difficulty, team skill,
Category and activity, independently of the Solver's Tier and Order. Edge scenarios separately
cover empty, one-team, all-tied, burst, long-quiet, late-release and scoreboard-heavy states. These
profiles test reaction under controlled distributions; they do not model human strategy or prove
official load. Separate canned/proxy responses positively exhibit synthetic behaviour and must make
Crowd unavailable or neutral under ADR-0038.

`active` means scheduled to emit at least one public activity signal during the scenario interval.
Conditional sampling enforces the registered/active bounds, and the exact team identities, solve
times and Challenge assignments are materialised before the Run rather than selected in response to
Solver behaviour.

Scenarios cross ADR-0038's exact Crowd qualification boundary: two observations at least five
minutes apart, recent activity, sufficient solve movement or recent solvers, three distinct values,
tie-fraction control, freshness and hysteresis. They also exercise coherent LIST snapshots,
acquisition refresh, changed artifacts and the complete 15-minute Intake/request-accounting path.

## Instance behaviour is real and exhaustively reconciled

The rig uses the upstream Instance stack and isolated HTTP and raw-TCP Targets, plus static handouts.
Other transports enter only after official evidence or a real corpus need. The first passing receipt
must contain the first real non-short-circuited deploy/read/renew/terminate path; the historical 31
deploy short-circuits establish only that the path had never executed.

The matrix distinguishes authenticated-empty, owned non-empty, unreadable, malformed and `not ours`
ledger states. Team mode keys ownership by `teamId`; users mode keys it by `userId`. Cases include
lazy identity fallback, transient identity-read failure, truncation, unexpected JSON, plugin
absence, hidden/deleted Challenge rows and anonymously cached pages. It covers disabled Mana,
sufficient capacity and refusal; refusal creates neither an Instance nor a Lease and cannot block
static work. It proves create, trustworthy read,
late renewal, computed expiry, delete, accepted-Flag teardown, Cut teardown, Run-close sweep,
repeated termination/404 success, Target-unavailable causal reads and Target recovery.

Two independent Leases are held across separate Lanes and a replacement Boot. Their epoch-fenced
coordinator must reject stale effects, reattach only after Order and reconciliation permit it, and
prevent either Lane from terminating the other's Instance. Faults cover death before and after
deploy intent, lost deploy/renew/terminate responses, paused and Quota-wait Attempts, simultaneous
Lane completion, ambiguous same-Challenge rows, cached reads, natural expiry, earlier-Run residue
and reserved-tail entry. No receipt may contain overlapping ownership, an unsafe destroy or an
Instance falsely classified closed.

An unattributed row is never destroyed. Orphan classification requires durable provenance and two
healthy authenticated ledger reads at least 15 seconds apart. Natural expiry requires successful
DELETE/404 or ledger absence plus per-Challenge absence after the 60-second cache horizon.

Each Target has its own network and storage boundary, cannot reach sibling Targets or control
services and exposes only its declared transport. Solver access is Attempt-scoped and mediated by
the Target proxy; unguessable endpoints and Team keys never become general network authority.

## The catalogue is small, qualified and unreachable

The **Practice catalogue** remains a minimum-cover instrument, not an archive. Its coverage ledger
admits reproducible material only when it has provenance, licence or explicit private-use authority,
Category and capability metadata, complete local handouts or source, independent answer/reference
verification, reset, private oracle and artifact/Target/scenario digests. It rejects incomplete,
dead-link, redundant, answerless and live-third-party-dependent candidates.

The pinned `ctf-workspace` commit
`463105313f82817d74cc0df67a648b3e698d3b10` is a candidate pool, not automatic admission: many
entries retain useful evidence without complete organiser source or licensing. Material from
competitions the Owner joined is eligible for private practice only where terms permit and the rig
can reconstruct it independently. Private access is not a redistribution licence. Prior solutions
and writeups remain oracle-side and unreachable.

Coverage spans the Board's evidenced `web`, `pwn`, `crypto`, `rev` and `forensics` Categories while
keeping Category an open string; low and high organiser difficulty; static, isolated HTTP and
isolated raw-TCP delivery; and the required capability families from ADR-0047. Independent Challenge
families populate calibration, validation and rotating untouched Discovery roles. A related family
or generator lineage cannot cross those boundaries. Selection stops when cells and necessary
redundancy are covered, not at a Challenge count. Missing cells remain honestly `pending`.

Each Scenario manifest selects and seals only the subset needed by that Run; no Run receives the
whole Practice catalogue merely because it exists.

Purpose-built microfixtures test Board, Instance, submission, state and Recovery mechanisms. They
never count as Category discovery or machine-learning generalisation. Once a holdout affects a
model, prompt, tool, topology, Tier, Order weight or other dial, it retires permanently to
calibration/regression evidence. Resetting the same Challenge does not make it fresh.

During the offline Gate, an Attempt executor may reach only its Target and selected inference
route. Public DNS and arbitrary internet are blocked. A separate search-enabled rehearsal records
research provenance and may test real research-enabled Board profiles, but its solves never become
Gate-qualified Discovery evidence. Any route to the oracle, prior solutions, sibling work or
answer-bearing external material makes the solve Retrieval-contaminated or Unqualified under
ADR-0034 and fails closed where breach scope is unknown.

Every Run performs hostile-position isolation preflight, continuous route/effect collection and a
post-Run audit. Every release candidate—and any relevant image, runtime or policy digest change—runs
the full adversarial isolation suite. Unexpected access or inconclusive audit evidence fails the
whole Gate Run.

## Training is automatic between Runs and frozen within one

The Evaluator owns an append-only dataset registry outside Solver authority. Each immutable,
content-addressed **Dataset snapshot** binds Run, Attempt, Challenge revision and family, corpus
role, time-causal feature row, complete typed outcome, provenance and receipt, and feature-schema,
policy and image hashes. Outcomes include accepted Flag, no Flag, Cut, timeout, refusal and
classified failure; censored records stay censored. Corrections create a superseding record; history
is never rewritten.

Schemas, split manifests and hashes are committed. Immutable capsules and derived columnar datasets
live in a private content-addressed artifact store; a query index is rebuildable and never the sole
truth. Feature changes create a new schema version. A historical value is backfilled only when
sealed evidence reproduces it exactly; otherwise it remains explicitly missing.

A feature row may use only evidence available at its recorded acquisition time. Later solves,
final Crowd state, oracle data, synthetic-origin metadata and retrospective labels never reappear as
features. Compact typed features and receipt links enter the registry. Raw failed-output bulk,
secrets, private answers and scenario provenance do not. Selected evidence remains in its immutable
capsule under ADR-0045's retention contract.

After every promoted sealed Run, the host pipeline automatically validates and ingests eligible
evidence, rebuilds dataset snapshots, reports unmet eligibility, recalibrates the transparent
formula and starts eligible model trials. Invalid, contaminated, schema-incompatible or
insufficiently independent evidence fails ingestion. Tuning uses seeded predeclared search spaces,
fixed CPU/time budgets and retained trial records; failure of corpus or stability conditions stops
the search.

ADR-0038's first **Model candidate** remains regularised logistic regression for the probability of a
verified Flag within its fixed horizon. Training begins only after at least three independent
Board/event groups permit training, tuning and untouched testing, each validation fold has several
accepted Flags and the learning curve is stable. A Model candidate binds dataset, features,
coefficients, normalisation, policy, metrics and rollback target.

Automatic training is not automatic trust. A Model candidate may replace the reviewed **Model
champion** only
after repeated untouched matched Runs satisfy the versioned Gate without worsening Flag yield,
wall-clock, resource tails or worst-case Runs. The Model champion, prior Model champion and
transparent fallback ship together. Runtime incompatibility or an envelope breach quarantines the
model and falls back; it never triggers fitting during the Run.

Only predeclared deterministic dials may adapt live over accepted Flags, time-to-Flag, Checkpoints,
Attempt spend/Cut, Board latency/faults and resource pressure. Each bounded change is durably
recorded before later acquisition reads it. Learned coefficients, normalisation, feature meaning and
safety envelopes remain fixed for the sealed Run. There is no live self-training.

## Faults are controlled, attributable and fail closed

Fault injection prefers out-of-band network proxies/netem, container pause/kill/restart, cgroup
limits, volume pressure and service toggles. A rig hook exists only where external control cannot
produce the semantic condition. Schedules are seeded, predeclared and hidden from the Solver; the
receipt records actual injection/clear times and deviations. Solver state is corrupted or truncated
only while its writer is stopped, with the original snapshot preserved.

Every fault is proved alone before it enters a compound stress scenario. The minimum matrix covers
Board/API, database/cache, Instance manager/janitor, Target, network, disk, CPU/RAM/PID, Solver child
process, whole-Boot replacement, inference route, shared quota and Repair Agent authority. Each case
declares expected containment, Recovery action and the applicable `15 / 60 / 120 / 180`-second
deadline from ADR-0046.

Workload fixtures include legitimate long crypto, compilation, browser, emulation and forensics
work; silent hangs; runaway output; TERM-ignoring and double-forked children; child escape and orphan
accumulation; memory/disk/output exhaustion; slow/failing Board reads; corrupt/missing state; and
malicious archives, traversal, symlinks and hostile prompt/tool content. They attempt access to
credentials, Board/control routes, public network, sibling Targets and host authority.

Every process and resource sample resolves, where applicable, to Run, Boot, Run controller, Triage,
Board broker, Target/research proxy, Codex Control, native/CPA Harness, writer, storage governor,
reserved tail, adopted service, Lane, Attempt, Engagement, Turn and Step. Samples distinguish
Solver, inference broker and rig infrastructure. Five-second sampling bursts to one second around
faults. The receipt retains requests and latency, image/state/cache growth, token classes and
reserves, censored Cuts, completion distributions, failures and instrumentation overhead rather
than means alone.

Inference attribution records requested, catalogued and effective model and effort; cached,
uncached, input, output, reasoning and unknown token classes where reported; route refusals,
shared-limit movement, reset state and reserve impact. Unsupported usage stays `unknown` rather than
estimated.

The host preflight reserves the declared Solver, broker, rig and safety headroom in separate cgroup
and storage profiles. If background contention or rig demand breaches the declared host floor, the
Run remains diagnostic evidence but its Solver Resource-envelope conclusions are invalid.

The intervention order is soft snapshot, fresh incident-scoped Recovery diagnosis and a changed
Remedy before a hard safety cut. Legitimate owned work survives while safe; positively unowned work
is removed. The rig proves Attempt-local, Lane and global-authority containment, healthy-Lane
continuity, deterministic effect freezes, native- and CPA-local faults, shared outage, Quota
wait/reset outcomes and candidate-image diagnose/build/test/probation/promotion/rollback. It never
gives the scored image patch, build, launch or replacement authority.

## One ledger closes every prior-ticket handoff

The rig maintains a machine-readable requirement ledger. Every local Gate obligation from an ADR or
ticket names its source, scenario, fixture, observable, metric, expected containment, receipt and
status. A prose claim or green exit cannot close a row. The ledger covers:

- Intake, Triage, Order, Tier, Crowd, releases and Board-read settlement;
- Attempt, Cut, carry, Checkpoint epochs, reserved tail and bounded submission;
- model, effort, prompt, tool, Harness, topology, Lane and Specialist comparisons;
- Lease, Instance, Boot and restart safety;
- state Promotion, retention, pressure, reconstruction and fresh-Run separation;
- Recovery scope, inference faults, quota/reset and Repair Agent probation;
- image/tool proof, isolation, resources, hostile content and stray processes; and
- Observer evidence and every confirmed Board/Target compatibility seam.

Proof modes are deterministic mechanism suites, accelerated Runs, matched genuine 5.5-hour Runs,
search-enabled non-qualifying rehearsals and Repair Agent probation Runs. Comparisons freeze scenario,
corpus, images, releases/faults, model identity, effort, tools, prompts, policy and resources; one
causal axis changes per cell except a declared safety stress matrix. Pilot Runs set repetitions,
exclusions and stopping rules before confirmatory evidence is read.

Representative suites explicitly exercise no/weak Crowd Triage, Order/Tier recomputation,
Cut/carry/Checkpoint behaviour, Challenge re-entry, indeterminate submission, model refusal and
malformed events, native/CPA route-local failure, shared exhaustion and every pre-authorised reset
outcome. They exercise all required capability families and architecture claims, state Promotion
and reconstruction, and Recovery's local/Lane/global scopes.

Verified Flags and wall time lead comparisons. Receipts also report time to first Flag, Flags/hour,
tokens/Flag, usage and shared-capacity observations, Checkpoint conversion, request load,
CPU/RAM/PID/disk/network tails, state and image cost, coordination/idle overhead, failure/Recovery
outcomes and isolation. Every safety boundary has zero tolerance. Numeric thresholds, repetition
counts, aggregation and the v2 verdict remain
[#196](https://github.com/jerome-queck/incypher-ctf/issues/196); final Gate reconciliation remains
[#208](https://github.com/jerome-queck/incypher-ctf/issues/208).

The mandatory governance-cost comparison holds the same seed, corpus, releases, faults, model,
effort, tools, inference route and safety boundary while comparing full v2 with the lean persistent
Solve Lead. It reports verified Flags, wall time, Board requests, CPU/RAM/disk/network tails and
instrumentation overhead; a simpler baseline is not allowed to drop mandatory isolation or effect
safety to look cheaper.

## Consequences

- The practice rig is a mandatory v2 deliverable and the only controllable full-Board Gate venue.
- It can reject or recalibrate every local v2 decision without pretending its own implementation is
  independent official evidence.
- The Solver cannot distinguish ordinary simulated population from ordinary Board population, while
  the Evaluator can still exclude simulation origin from model features and qualify the claim.
- Automatic training becomes reproducible and useful without allowing a scored Run to silently
  change its learned policy.
- Resource ceilings stay evidence-calibrated; rig overhead and inference capacity are measured
  separately from Solver demand.
- A compatibility-lock or dependency change invalidates affected evidence and triggers targeted
  reruns, not an automatic full reset of unrelated receipts.
- v2 remains planning-only until the cleared map passes through `/to-spec` and `/to-tickets`; this
  ADR does not claim that the rig or production Solver already exists.

## Revisit when

- authenticated official evidence identifies a different CTFd/chall-manager contract, transport,
  PoW, ADK or Target behaviour;
- a corpus family lacks licence, reproducibility, independent oracle or required coverage;
- synthetic population or fault injection becomes Solver-detectable outside its declared adversarial
  scenario;
- dataset lineage cannot reproduce a Model candidate or reveals leakage across families or time; or
- [#196](https://github.com/jerome-queck/incypher-ctf/issues/196) or
  [#208](https://github.com/jerome-queck/incypher-ctf/issues/208) changes the receipt or aggregation
  contract.
