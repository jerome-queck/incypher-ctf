# Canonical state is sealed, classified and governed by reachability

> **Selection evidence and retirement are bounded by
> [ADR-0055](0055-one-score-basis-qualifies-one-release-candidate-profile.md).** Exposure or influence
> retires an entire Selection-holdout family permanently; resetting, reflagging or renaming restores
> no freshness. The canonical stream, content-addressed artifacts, classifications and governed
> reachability below stand.

> **The finite-storage edge is bounded by
> [ADR-0054](0054-authority-remains-writable-when-storage-is-exhausted.md).** Open-Incident evidence
> is bounded before it is pinned; every write and effect reserves bytes, inodes and metadata first;
> governed retirement uses a replayable tombstone; and a non-borrowable Control write reserve keeps
> containment and terminality recordable. Authority outranks throughput.

> **The Candidate proposal uncertainty lifecycle is bounded by
> [ADR-0052](0052-one-unknown-submission-fences-flags-for-sixty-seconds.md).** After sixty seconds an
> unresolved Candidate proposal becomes durably `unknown-and-spent`: it remains fail-closed against
> resend, while a successor Submission epoch may admit unrelated Flags and late exact evidence may
> append a historical correction.

[What persists in `/state`, and how it stays bounded](https://github.com/jerome-queck/incypher-ctf/issues/175)
decides the durable shape behind ADR-0032's restartable Run. **One sequenced append-only stream is
the authority; immutable blobs supply its bytes; every other structure is a rebuildable projection,
private support state or disposable work. Every file is classified at creation, and only a trusted
storage governor may retire it under a Gate-approved profile.**

This record composes ADR-0032's restart authority, ADR-0034's practice isolation, ADR-0037's
submission provenance, ADR-0040's two inference routes, ADR-0041's execution boundary, ADR-0043's
one sequencer and ADR-0044's Lease ledger. It specifies v2; current v1 directories and record
formats do not satisfy it.

## Four storage authorities never collapse into one directory

The production mount layout has four distinct ownership and reachability domains:

1. **trusted Run state** holds the canonical stream, sealed blobs, Intake originals, receipts,
   telemetry and quarantine. Only the controller, sequencer, Supervisor and narrow read-only
   projections reach it;
2. **Challenge work** holds generation-scoped model work. An Attempt executor reaches only its
   assigned generation and explicitly supplied read-only inputs;
3. **private inference stores** hold native Codex and CPA credentials, checkpoints, logs and vendor
   rollouts in separate trusted-owner mounts outside shared `/state`; and
4. **ephemeral execution state** holds IPC, pipes, process-local buffers and temporary executor
   files and disappears with its owner or Boot.

The image contains code and tools, never Run input. No executable, package, instruction or tool is
loaded from Run state, Challenge work, quarantine, a capsule or an old corpus. Private stores start
under `umask 077`; startup and every resume verify owner, type and mode before inference is enabled.
CPA's forced prompt/error logging stays disabled. Native and CPA stores never share a token,
checkpoint, log path or cleanup rule. `CODEX_HOME_METERED` has no v2 meaning and is removed rather
than retained as an alias.

## The stream names facts; sealed blobs carry bytes

Only the controller-owned sequencer appends canonical records and assigns their global sequence.
A producer writes a bounded immutable blob in its own staging area, closes it, verifies its length
and digest, atomically seals it and makes it durable before proposing a reference. The sequencer
validates owner, source, sensitivity and bound, then records the reference. Producers never reserve
sequence numbers or append around the sequencer.

Records which create authority or precede an effect are crash-durable before that effect: Run and
Boot lifecycle, Attempt/Lane/Challenge claims, Engagement and process ownership, Candidate and
submission reservations and outcomes, Lease intents and outcomes, closing barriers, recovery
decisions, artifact import or disposition, pressure transitions, eviction and terminal Run state.
Ordinary high-volume measurements may be buffered through the same sequencer and flushed at a
bounded interval; they cannot authorize an effect. A record cannot truthfully refer to bytes which
were not already sealed.

A Triage judgement batch has a durable idempotency key over its input-snapshot digest, unresolved
Challenge revisions, crowd-evidence classification, prompt/schema version and requested model
identity. Its lifecycle distinguishes admitted, invocation-open, complete-reusable, interrupted,
superseded and damaged. It seals the included Challenge ids, bounded raw answer, parsed Tier/Order
contribution, timing, usage and completion status. A later Boot reuses an exact complete batch and
never pays for it again. A Challenge released before invocation extends the admitted batch; one
released after invocation creates a successor containing only newly unresolved revisions while
settled contributions remain projections of their earlier complete batches. Interrupted work may
be reinvoked under a fresh Engagement; damaged or contradictory evidence enters Recovery rather
than silently buying a replacement answer.

Replay verifies the segment manifest, sequence, record schema, referenced length and digest and
rebuilds all control projections. A mutable scheduler snapshot, database, index, trend table,
working directory or capsule is never a second truth. A duplicate, gap, impossible transition,
missing authority blob or conflicting owner stops effects and enters Boot-level Recovery.

Damage is preserved, not repaired in place. Replay ends at the last verified record, quarantines
the corrupt bytes, and a crash-durable link may open a successor segment. Facts whose authority is
unambiguous may be replayed; an uncertain submission, Lease, Candidate or owner remains unsettled
and fails closed. Missing selected solve evidence fails the Gate even when control recovery is
otherwise possible.

## Every state class has one lifecycle

`Durable` below means the sanitized capsule copy remains indefinitely; the redundant live copy may
leave only after verified Promotion. `Eval + 30d` is the normal retention through every declared
dependent evaluation and then thirty days, extended while an Incident remains open; ADR-0054 may
shorten it under pressure only after releasing every protected dependency. No age rule overrides a
pin, and ADR-0054 bounds every live Incident pin before admission.

| Class | Writer and reader | Restart, sensitivity and executor reach | Promotion, retention and cleanup |
| --- | --- | --- | --- |
| Canonical records and manifests | sequencer writes; controller, Supervisor, Recovery and offline projections read | mandatory authority; sanitized but may reference private blobs; never executor-reachable | sanitized form is Durable; live duplicate only after verified capsule; governor records deletion |
| Sealed content-addressed blobs | trusted producer seals; sequencer admits; named consumers read | required exactly when a live record reaches it; sensitivity is explicit; executors receive only capability-scoped copies | selected blobs enter capsule; unselected raw bodies are Eval + 30d normally, or ADR-0054 pressure-retirement once unreferenced |
| Intake originals and Board statements | Intake writes; Attempt staging and replay read | immutable Run input; untrusted Board data; assigned Challenge only | retained while reachable by a selected artifact or active Practice entry; otherwise copies retire only after capsule and automated reconstruction succeed |
| Work generations | one Attempt or Specialist writes; controller validates selected output | fresh per owner; model-reachable and untrusted; never authority | selected files change class; sealed unselected generation may retire after capsule; interrupted remainder becomes quarantine |
| Quarantine | controller moves sealed late, corrupt or interrupted bytes; Recovery reads | never silently re-enters work or authority; sensitivity inherited; no executor reach | Eval + 30d; an unresolved Incident pins only its bounded admitted evidence set; only explicit revalidation changes class |
| Solve receipts and selected Evidence | controller seals; replay, Gate and writeup tooling read | mandatory, immutable, sanitized; no executor write | Durable in capsule; never pressure-evicted |
| Exact Candidate vault | submission authority writes; Supervisor and submission path read | encrypted, exact Candidate across Boots; stream holds keyed digest, provenance and disposition; no executor browse | purge only after the event and verified Promotion; capsule keeps no recoverable Candidate, including an accepted Flag |
| Raw Observation/Claim bodies and tool chunks | recorder seals; current Turn projection, Recovery and evaluation read | potentially secret and adversarial; only bounded selected projections reach an Agent | excluded from capsule unless selected as Evidence; Eval + 30d normally, or ADR-0054 pressure-retirement once unreferenced |
| Native rollouts and CPA logs | their isolated harness writes; trusted diagnosis reads | private, possibly credential-bearing, noncanonical; never executor- or capsule-reachable | Eval + 30d normally; the private-store governor may pressure-retire unreferenced detail outside a bounded Incident pin |
| Native and CPA credentials/checkpoints | separate brokers write/read | secret, restart-required capability state; owner-only; no `/state` or executor reach | never promoted; broker-owned rotation and deletion |
| Cache, dependencies, derived extraction and generated bulk | scoped tool or controller writes; owner reads | reconstructible and untrusted; no authority | first pressure class; delete after reachability check; no age promise |
| Resource/process telemetry | Supervisor and brokers measure; governor, Recovery and Gate read | compact owner-linked evidence; executor cannot write canonical samples | lifecycle facts and compact samples enter capsule; raw high-rate samples are Eval + 30d normally, or ADR-0054 pressure-retirement once unreferenced |
| Ephemeral IPC and temporary execution | owning process writes/reads | Boot- or Step-local; sensitive by default | never promoted; owner close removes it |
| Evaluator catalogue, oracle and corpus splits | Evaluator writes/reads outside Solver authority | restart-required for the rig; answer material is unreachable during isolated Runs | catalogue manifest and Isolation receipt are durable Gate evidence; oracle never enters Solver state |
| Attempt corpus and derived trends | offline tooling builds from capsules | reproducible projection, never control input during its source Run | capsule inputs are Durable; views may be regenerated |

Failed work retains compact lifecycle facts—Attempt, Turn and Step identities, timings, model/tool
identity, cuts, errors, checkpoints, submissions and recovery—without retaining its arbitrary bulk
or vendor context forever. Historical retention never implies that later model context contains it.
The already deleted COMPFEST material remains historical and unqualified: it supports no new
decision until the required evidence is reproduced under this contract.

Every process group is durably bound to its Run, Boot, Lane, Attempt, Engagement, Turn and Step as
applicable, plus executable digest, purpose, owner and deadline. Canonical lifecycle records name
spawn, explicit adoption as an Attempt service, bounded heartbeats and resources, soft anomaly,
Recovery trigger, termination request and result, descendant reconciliation and cleanup outcome.
Compact lifecycle facts enter the capsule; raw high-rate samples follow `Eval + 30d`.

## Content identity, generations and disposition bound work

Each downloaded original is stored once by digest. Name, source, Challenge revision and acquisition
remain manifest metadata. Splits, decoding, extraction and other derivations record parent digest,
tool and parameters; derived bytes are disposable unless selected. Identical downloads share one
content-addressed object rather than multiplying storage.

Each Attempt gets a fresh Work generation assembled from immutable Board inputs and a bounded carry
manifest over sealed earlier artifacts. Each Specialist gets a private generation. Files begin
`ephemeral`; before close the model may nominate `carry`, `solve-evidence`, `training-evidence` or
`discard`. Trusted control—not the model—validates existence, digest, ownership, sensitivity, count
and bytes, applies the disposition and sequences the result. Unclassified is not a state: an
interruption maps remaining ephemeral material to `quarantine`.

The carry contains selected artifacts and typed Checkpoints, not an old directory. Its absolute
context ceiling remains ADR-0031's 16 KiB until the Gate promotes a lower target. A closing barrier
seals the generation. Late bytes stay inert in quarantine. After an accepted Board verdict no
extra model Turn writes a summary: the controller seals the receipt and generation, moves selected
material outside the active tree, releases ownership and continues the Run.

Tool output streams through byte-ceiling chunks. The model receives a bounded projection; full
bytes remain addressable only when policy permits and selected full bytes are pinned. Compaction
may replace no canonical record and may not destroy the artifact cited by a receipt.

## A Solve receipt reconstructs the solve, not a narrative

For every accepted Flag the controller seals one receipt which binds:

- Board identity and Challenge revision, accepted verdict, Candidate disposition and submission
  reservation/outcome;
- Run, Boot, Lane, Attempt, Engagement, Turn and Step identities and every relevant Lease,
  Instance and Target identity or deadline;
- the final exact credential-free commands or scripts, required inputs, selected output excerpts or
  full bytes, and cited visual, OSINT, web and tool Evidence with content digests and derivation
  lineage;
- exact Solver/Target/Evaluator images, model route and effective identity, reasoning effort,
  prompt-component digests, context/schema version, tool profile and tool versions; and
- timing, known token classes, resource envelope, checkpoints, recovery and the closing outcome.

The capability boundary keeps credentials out of command and script bytes. The selection keeps the
final exploit or script, required inputs, evidence necessary to warrant the
Candidate and enough output to reproduce it. It excludes dependency caches, duplicate downloads,
routine extraction, abandoned experiments and unrelated bulk. The receipt contains no narrative,
credential, private rollout, raw CPA log or hidden chain of thought. Post-event tooling authors a
writeup from the receipt and reports any missing evidence rather than inventing it.

Every Candidate proposal and Board verdict is keyed to the exact Board identity and Challenge
revision. A rejection suppresses only that revision; replacement, rerelease or material Target
change invalidates the disposition rather than inheriting a stale wrong answer.

## Timing is first-class evidence with an honest measurement boundary

Primary solve time runs from Attempt admission to the accepted Board verdict. Candidate discovery
and post-verdict sealing are separate spans. The capsule also retains cumulative Challenge active
time, explicit waits, wall span across Attempts, per-Attempt/Engagement/Turn/Step spans, Board and
Target latency, tool process time, pacing, quota waits, Recovery and restart downtime. Cut and
unsolved Attempts retain their budget and Cut cause as censored observations rather than zeroes or
omissions.

Controller transitions use a monotonic clock plus wall timestamp. Process/cgroup counters are
sampled at transitions and every five seconds, bursting to one second during a fault. Existing
record additions are preferred over new events. Ordinary samples are buffered; authority records
alone force durability. A full 5.5-hour Run is expected to add roughly 0.8 MiB at five-second
sampling, versus roughly 4 MiB at one second, before compression; the Gate measures CPU, I/O and
latency cost rather than treating those estimates as proof.

The controller can measure Turn wall time, event arrival gaps, tool time and its own overhead. It
cannot separate vendor queueing, transport and hidden reasoning. `Turn wall - observed tool and
controller time` is labelled a harness/transport/model residual, never reasoning time. Reported
reasoning tokens are retained when present; interrupted or unsupported usage remains
`usage_known=false` and is never estimated.

Every timing row binds the exact prompt bundle, model/effort, context and tool profile. Timing and
errors may reveal a poor tool or instruction as a hypothesis; only a matched held-out trial can
attribute cause. v3 prompt personalization therefore enters only when a predeclared matched trial
beats the simpler v2 bundle; a tie keeps v2.

Gate-approved controlled completion distributions may improve Tier budgets only with their
uncertainty and comparable Category, model, tool and artifact-shape scope attached. A cut Attempt is
censored evidence, and Tier both affects and observes duration, so elapsed time alone never becomes
difficulty truth. Sparse or out-of-scope samples fall back to the deterministic remaining-clock
formula rather than extrapolating confidence.

## Reachability and pressure, not age alone, govern deletion

Only the trusted storage governor deletes Run or work material. Before deletion it proves the
object is not protected by open authority, bounded unresolved-Recovery evidence, a carry manifest,
a receipt, selected Evidence, an unpromoted capsule or a dependent evaluation. Agent shell commands
and cleanup prose have no deletion authority.

[ADR-0054](0054-authority-remains-writable-when-storage-is-exhausted.md) owns the storage profile,
admission reservations, bounded Incident pin, retirement transaction and order, Control write
reserve, hard-pressure containment, final-submission storage and terminal boundary. A missing or
unproved profile causes pre-Run Refusal; v2 does not invent a byte limit from today's 22 GiB legacy
tree or corrupt reachable truth to keep solving.

## Promotion is a fail-closed publication transaction

A Run capsule contains the sanitized canonical stream, configuration and provenance manifests,
Solve receipts and their selected Evidence. It excludes raw bodies not selected as Evidence, raw
rollouts, auth, CPA state/logs, general work, quarantine, caches and exact rejected Candidates. A
manifest binds every path, length and digest. Promotion reads a terminal stable Run, builds the
whole capsule, scans it, copies it to independent durable storage, verifies the destination and only
then starts local retention clocks. Metrics and trend tables are reproducible views over capsules.

The publication scanner examines raw bytes and a lossless decoded representation of every
structured key and value, while preserving malformed bytes, plus ordinary secret rules. A
host-Keychain-backed scanner vault retains every current and prior exact declared secret needed to
scan all affected Runs through Promotion; a hash
alone cannot detect disclosure. If provenance says credential history is incomplete, scanning
fails, or a stream is live, shrinking, malformed or missing reachable bytes, Promotion refuses the
entire package. Historical secrets leave the scanner vault only after no unpromoted Run can contain
them. Raw Codex rollouts remain private precisely because a model may have read a credential; their
recorder redaction is never evidence of cleanliness.

## Practice data is curated, partitioned and generated by the landed system

The initial Practice catalogue is curated primarily from the public `ctf-workspace` repository at
pinned commit `463105313f82817d74cc0df67a648b3e698d3b10` plus the local Board's purpose-built
fixtures. Admission requires reproducible material, licence/provenance, Category and coverage
metadata and an independently verified answer or reference result. Stuck, incomplete, dead-link,
redundant and answerless entries are rejected.
The old workspace and its solve details are never mounted into a Run.

The Evaluator partitions admitted entries into calibration, validation and rotating untouched
Discovery holdouts. Once a holdout influences a model, prompt, tool, Tier or dial, it retires from
Discovery and becomes calibration/regression evidence. The Practice catalogue may name unattempted
Challenges; the Attempt corpus grows only from compact capsules when the Solver actually exercises
one. Online availability may be checked before a Run, but answer-bearing retrieval during an
offline isolated Run makes the solve retrieval-contaminated. The Evaluator holds the answer before
admission and the Solver never does.

Pre-v2 instrumentation and test training may shape implementation but are explicitly
non-qualifying and never enter the official corpus or v2 Gate baseline. Official
calibration/training evidence begins only with the landed v2 commit and exact built image, complete
local rig and evaluator, fresh Run state and declared split. Practice Runs start with fresh
history, cache, configuration and Challenge work; only the separately persistent auth broker
survives. Policies freeze within a Run and may be promoted only between Runs through a versioned
Gate—there is no live self-training.

After the actual competition, ordinary internet-enabled Codex may help reconstruct a writeup only
after the applicable embargo ends. It receives the sanitized receipt, never competition
credentials or private inference stores, and every new external source remains distinct from the
Run's original Evidence.

The Gate compares full v2 with a simple persistent Solve Lead under the same seeds, model, effort,
tools and mandatory safety boundary. It reports verified Flags, time to first Flag, Flags/hour,
tokens/Flag, completion distributions, failures and measured infrastructure cost. Inconclusive
evidence or a tie keeps the simpler topology.

## Migration and proof are implementation obligations

Current state is legacy evidence, not a production corpus. Migration first moves it intact to a
cold archive outside both the repository and future `/state`, writes a path/size/digest manifest and
proves the move. That restores the production layout but deliberately does not claim disk recovery.
An explicit audit may then retain the tiny promoted v1 streams/profile and delete bulky Brunner
work—including the roughly 21 GiB `Baked In` tree—only after ownership, uniqueness and dependent
evidence are resolved. A generated resource-stress fixture replaces that archive as the disk test.

The storage implementation ticket must run the complete local-Board lifecycle: migration; fresh
landed-v2 Run; restart and damaged-segment replay; work generations and late quarantine; accepted
solve to receipt; fail-closed secret scanning; capsule Promotion; reachability cleanup; and trend
recomposition. Full 5.5-hour trials measure state growth and timing overhead and calibrate each
profile's numerical ceiling. Faults cover torn writes, missing selected blobs, rotated secrets,
mode drift, forced CPA logging, cache/output exhaustion, interrupted generations and open
incidents. No current directory layout, short test or successful process exit is proof of this
decision.
