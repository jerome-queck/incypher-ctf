# One sanitized Observer projects one Run and owns nothing

[What the Observer CLI reveals, and when it may
control](https://github.com/jerome-queck/incypher-ctf/issues/198) fixes the host-side operational
view promised by ADR-0033. One **Observer CLI** projects one explicitly selected Run from existing
authority, remains useful live and after the container exits, and never becomes another monitor,
state store, Evaluator, Recovery Agent or control plane.

## One authority profile fixes the ceiling

Every Run seals one **Run authority profile** before it begins: `development`, `rehearsal` or
`scored`. An Observer invocation may narrow that ceiling but cannot widen it. Scored Runs are
read-only: a control attempt is refused before request creation, the refusal is recorded, and no
bypass flag exists. Development and rehearsal expose the same explicit request surface, but any
accepted human-initiated rehearsal control marks the Run intervened and not Gate-qualified.
Predeclared Evaluator actions remain a separate evidence plane and authority.

Observer owns no operational logic. Where the profile permits it, `request` submits a typed,
durable and idempotent request to the authority-owning broker. A dangerous request is confirmed in
two phases against its Run, target, canonical sequence, expected state, parameters and request
digest; state drift invalidates confirmation. Concurrent duplicates replay one request identity or
conflict. The request surface is:

- `snapshot`;
- `attempt pause|resume|cut`;
- `recovery open|probe|remedy`;
- `boot restart`;
- `run stop`; and
- `repair start|rollback` through the separate practice controller.

Observer never patches, builds, launches, promotes or rolls back an image itself. It only requests
and displays every practice-controller transition: sanitized exact patch diff, checks and their
verdicts, build, candidate digest, tests and their verdicts, probation, Promotion and rollback. A
durable repair still follows repository issue, pull-request and promotion governance.

## Every fact keeps its source and uncertainty

Observer reads the sequenced canonical Run stream, sealed manifests and blobs, and trusted
broker-recorded observations. It never independently inspects the Board, process table, container
or filesystem to invent a second truth. A fresh Board read is never implicit; where permitted, an
explicit refresh request goes through the Board broker and becomes a canonical, rate-accounted
event. Scored Runs normally refuse it.

Canonical Solver facts and separately labelled Evaluator-attested practice evidence may share an
interface but never a plane: Evaluator evidence cannot fill, override or lend authority to a Solver
fact. Every field retains provenance and freshness. Missing evidence is `unknown`, `stale` or
`unsettled`, never a healthy zero. Projection stops at the verified prefix on an ownership,
permission, schema, sequence, digest, reachability or seal failure and reports the break rather than
continuing best-effort. Postmortem live-only fields keep their last observation and timestamp while
saying that they are final or stale.

The view permanently redacts credentials, exact Candidates including accepted Flags, model
context, exploit material, private raw bodies, hostile bytes and native or CPA logs. Candidate
correlation uses Run-scoped keyed digests and dispositions, never cross-Run identity. Secret
metadata has no value or hash: only class, availability, provenance class, expiry or rotation
posture, and access outcome. Fixed-schema summaries neutralize control characters and terminal
escapes. TTY and versioned JSON expose identical facts and redactions; privileged forensic access
is a different tool.

## A small read surface answers the operational questions

The stable repository-owned host entrypoint is `python3 scripts/observer.py`. It requires an
explicit Run id except where exactly one exists. Versioned JSON consumed by host operations
automation is the primary interface; concise human TTY rendering is secondary. It offers:

- `runs` for sanitized discovery—id, start/end, authority profile and completeness, not comparison;
- `status` for a bounded snapshot ordered as identity/profile/clock, completeness, Board settlement,
  solve progress, active ownership, open Incidents and resource/inference/storage exceptions;
- `watch` for canonical-sequence transitions, anomalies and material threshold changes, with raw
  sanitized samples only when explicitly requested;
- `show <kind> <id>` for Run, Boot, Board, Challenge, Challenge claim, Lane, Attempt, Lease,
  Engagement, Turn, Step, process group, Incident, Remedy, Candidate, submission and Promotion;
- `timeline` in canonical sequence order, filterable by owner chain, entity, event class, severity
  and sequence/time bounds, with cursor pagination; and
- `check` for projection integrity, freshness and declared Run safety invariants, never a Gate
  verdict or solve-quality judgement.

Read commands fail only when observation is incomplete or invalid; an unhealthy Run remains a
successful observation carrying unhealthy state. `check` alone maps its declared conditions to
automation-facing exit codes. Every JSON envelope carries schema version, Run id, authority
profile, canonical sequence/as-of, evidence plane, completeness, redaction policy, data, warnings
and next cursor. Observer never migrates or mutates evidence: it reads declared supported schemas
or reports the compatible version required. It keeps no persistent projection cache or private
export. Colour never carries meaning; canonical timestamps are UTC and optional local rendering is
presentation only.

The projection makes typed relationships explicit rather than forcing a false tree. Every entity
shows its applicable Run/Boot/Lane/Challenge/Attempt/Lease/Engagement/Turn/Step/process ownership.
Challenge views carry revision, release and settlement, Category, score, Tier and Order, Challenge
claim, Attempt, Checkpoints, Candidate dispositions, submission verdict and verified-solve state.
Timing uses wall timestamps plus monotonic durations and identifies remaining Run/tail time,
deadlines, queue/model/tool/Board/Recovery spans and residual or unknown time. Resource views show
current, peak, limit percentage and recent trend for CPU, RAM, PIDs, disk, I/O, network and Board
requests at each available ownership scope. Inference views show requested/catalogued/effective model and
effort, route and Harness, admission or refusal, Turn outcome, reported token classes, unknown
usage, shared-limit snapshots, reset state and reserve impact. Incident views show severity,
scope, fault plane, Run authority profile, redacted fingerprint, affected owners, state/deadlines,
probes, Remedies, observed delta, probation, recurrence links and outcome.

Every active process group and Engagement additionally shows owner, purpose, age, declared deadline,
adopted-background status, soft anomaly, linked Incident and hard Resource-governor action.
Engagement views show controller ownership; a Specialist Engagement also names the Solve Lead
Engagement whose typed request admitted it. They show the model/effort routing cell's recorded
status plus, when it is `needs-recheck`, the material model-identity change that caused that status.
Storage views show the state envelope and pressure posture, class and reachability counts,
Promotion status and Triage-batch reuse.

The adaptive Triage Judge is an accountable control input rather than an aggregate token row. Its
view distinguishes fresh, replayed, interrupted and superseded calls; counts them by Run, Boot,
Intake cycle and release batch; names included, deterministically settled and unresolved Challenge
ids; and shows crowd-adequacy classification/provenance, queue/start/finish time, first-pick delay,
overlap, token classes, subscription-usage evidence, model, resulting Tier/Order changes and later
verified-Flag outcomes. Repeating an unchanged batch is visibly anomalous.

## Observation is on demand; Recovery is automatic

Observer is not auto-started for an ordinary or scored Run and no resident monitoring LLM reads
it. The deterministic Resource governor already monitors the Run and invokes a fresh,
incident-scoped Recovery Agent when needed; Observer never duplicates or triggers that mechanism.
An explicit development or Gate orchestrator may own a non-LLM `watch` child. It exits at terminal
Run state, lost input or parent death and has no detached daemon or independent restart loop.

Setup gives its caller a machine-readable handoff containing Run id, state root, authority profile
and exact Observer invocation. Live canonical state remains under the host-mounted `/state/runs/`;
after shutdown the same `--state <root> --run <id>` reads it. Verified Promotion returns and
canonically records the sanitized capsule path and digest, after which `--capsule <path>` reads the
independently durable result. The solving Agents never reach Observer or raw Run records. A later
Codex maintenance agent learns the interface from `AGENTS.md`, the Observer runbook, `--help`, the
setup handoff and tracked versioned schemas, then invokes Observer rather than private paths.

Observer requires only OS-owner access to sanitized Run records and the broker socket. It needs no
Run credential, never opens credential or private-inference-store paths, and treats inability to
reach an allowed source as missing evidence rather than a reason to widen access.

The caller resumes `watch --json` from its last canonical cursor. A gap or caller restart causes an
explicit `check --json` before watching resumes; bounded polling is only a fallback when the stream
cannot be followed.

During a scored Run, a caller may only record and surface the sanitized condition. It does not
advise the Solver, feed observations back, submit a request or invite notification-driven human
intervention unless later written organiser authority changes the profile. The runbook names the
allowed response to every uncertainty, corruption, open Incident, terminal state and scored refusal
so an Agent never improvises authority.

## The Gate proves usefulness without distortion

Golden TTY and JSON projections, schema/property tests, gaps, corruption, staleness, hostile
terminal payloads, redaction canaries, request races, all authority profiles and every injected
Recovery plane exercise the contract. Full-window trials measure snapshot latency and optional
watch CPU, RAM and I/O separately from the Solver envelope, including instrumentation distortion.
The implementation must use bounded memory, incremental reads and zero inference; an Observer that
materially changes Solver or rig evidence fails its Gate row.

Cross-Run comparisons and Gate verdicts remain Evaluator/report work. Exact numeric overhead
thresholds remain Gate-calibrated rather than asserted before the implementation exists.

This record settles v2 planning only. The current v1 Solver has legacy live `/state/runs/` records
but no Observer CLI, Run authority profile or v2 classified-state implementation; those follow
through the cleared-map build route.

## Consequences

- The Run gains no second watcher or model expense: fault detection remains with the Resource
  governor and incident-scoped Recovery, so Observer cannot rescue their absence or failure.
- A caller receives stable, sanitized operational facts, but raw forensic work needs a separate
  privileged path and cross-Run judgement remains with Evaluator reports.
- Versioned schemas, broker requests, runbooks, compatibility errors and full-window overhead tests
  become implementation and Gate obligations.
- Observer may be less convenient than reading raw files or attaching a permanent monitoring Agent;
  that inconvenience is the cost of preserving one truth, one recovery path and one authority
  boundary.

## Revisit when

- Written organiser authority permits scored control or human intervention.
- Fault evidence shows the Resource governor and incident-scoped Recovery leave a detection gap.
- A required operational fact cannot be projected safely from canonical records and brokered
  observations.
- Schema evolution makes the declared fail-closed compatibility policy impractical.
- Measured Observer overhead materially distorts Solver or rig evidence.
