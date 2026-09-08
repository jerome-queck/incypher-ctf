# One sequencer owns a measured rolling working set

[How many Attempts work at once, and who owns the working
set](https://github.com/jerome-queck/incypher-ctf/issues/188) decides how v2 becomes parallel-safe
without turning concurrency into competing schedulers, duplicated Board effects or an unmeasured
resource gamble. **One Run controller owns a rolling working set through one durable sequencer; each
open Attempt owns one Challenge claim and one Lane; every other capacity is globally admitted from
measured envelopes.**

The supported v2 surface is one or two Lanes and zero, one or two concurrent Specialists; any value
outside those ranges causes a pre-Run Refusal rather than a clamp. One Lane is enabled initially.
The v2 Gate may promote only configurations proved under the named `v2-macbook-m4pro-8c-24g`
profile. A scored Run may schedule below its approved maxima but never raise them or widen an
envelope from live observations.

This record composes [ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)'s one
Order and no queue, [ADR-0032](0032-a-run-survives-its-boots-and-recovery-owns-the-first-fault.md)'s
durable Run and global Lease ownership, [ADR-0037](0037-submission-is-speed-first-provenance-backed-and-serial.md)'s
serial submission authority, [ADR-0040](0040-one-owner-one-subscription-and-one-observed-limit-state.md)'s
shared-capacity policy, [ADR-0041](0041-one-container-separates-control-from-hostile-execution.md)'s
process boundary and [ADR-0042](0042-one-controller-owns-four-agent-roles-and-every-engagement.md)'s
Agent topology. It refines their single-Lane assumptions; it does not reopen them.

## One Challenge claim, one rolling working set

A **Lane** is Run-scoped capacity with a stable ordinal for coordination and telemetry. It is not a
work identity. At most one open Attempt occupies it, and a Specialist remains a child Engagement of
its parent Attempt rather than consuming another Lane.

A **Challenge claim** is the exclusive, durable association between one Challenge and one open
Attempt. The claim is acknowledged before any Attempt effect, survives Turn gaps and Quota wait,
and releases only after the Attempt's closing barrier and bounded child join complete. A Challenge
has at most one open claim at any instant; a later Attempt may acquire a fresh claim. Admission
candidates have none: there is no pending queue, parked Attempt pool or hidden backlog. A Lease
remains the separate durable hold on an Instance.

Every Attempt boundary rolls the working set forward. The controller recomputes the pure Order from
canonical state, protects every open claimed Attempt, and selects the largest Order prefix whose
open Attempts' frozen budgets and candidates' deterministic prospective budgets fit the remaining
Lane calendars. A prospective budget is the budget current Tier and policy would assign upon
admission. The controller never skips an unaffordable higher-ranked Challenge to admit a
lower-ranked one. Each free Lane receives the highest-ranked unclaimed member of that prefix. If
several Lanes become free together, the sequencer services them by ascending Lane ordinal and
recomputes after each durable admission. An acquired Attempt retains its Tier and budget; a new
release or changed Order never preempts it. If the clock-sized working set shrinks below its active
membership, the controller admits nothing.

Affordability uses deterministic per-Lane capacity calendars ending at the reserved-tail boundary.
Each active Attempt's remaining frozen commitment occupies its Lane; the current Order prefix is
packable only where those frozen and prospective budgets fit the remaining calendars. No two
admissions spend the same capacity and no calendar borrows the tail.

An open Attempt retains its Lane and Challenge claim through a controller-declared admission wait
or Quota wait. Those waits pause its spend budget, but never the Run clock, reserved tail, Instance
expiry or an external deadline. On resumption the remaining spend is clipped by those hard clocks.

## Admission and authority are durable before effects

One controller-owned sequencer performs admission a Lane at a time. It reads the replay-derived
snapshot, assigns a fresh Attempt identity and durably writes the Attempt open, Challenge claim,
Lane ordinal, acquired Tier and budget, hard deadlines, Order/policy versions and Resource envelope
before staging files, deploying an Instance, starting a Lead or causing any other effect. Only then
may the next Lane be considered. There is no intermediate reservation that masquerades as queued
work.

Concurrent producers write bounded, immutable, source-identified blobs with owner-local sequence;
they never append canonical control records. The one record writer accepts them and assigns global
sequence. Proposals ready in the same controller cycle are ordered by stable Attempt admission,
Engagement and owner-local sequence. Replay follows that global order exactly and rejects a gap,
duplicate or contradictory ownership transition. A reservation authorising an irreversible effect
is crash-durable before the effect and its observed outcome follows separately.

An Attempt-owned fault closes only that Attempt. A shared writer, controller, broker or ownership
failure stops new effects and enters Boot-level Recovery. No Lane continues from ambiguous
authority.

## One global governor admits every productive process

Lane count limits Attempts only. The same deterministic admission authority governs Lead,
Specialist, Triage and Recovery Turns and every owned process group against global CPU, RAM, PID,
disk/output, network/request, inference and remaining-clock envelopes. There is no per-Lane request,
submission, subscription or host-resource allowance.

A **Resource envelope** binds an owner, purpose and deadline to measured soft targets and hard
ceilings. The Supervisor, Run controller, sequencer, brokers, reserved tail and one global Recovery
reserve keep protected non-borrowable capacity. Productive work may borrow unused capacity only
inside the proved aggregate envelope. A configuration is admitted only when its measured
worst-compatible combination fits beside those reserves: touching protected headroom is a Gate or
admission failure, not ordinary scheduling.

Ready productive work is served in deterministic rounds. Every ready Solve Lead receives base
service before an Attempt receives an optional additional Engagement; ties follow Attempt admission
sequence, then its acquired Order. Residual capacity admits valid Specialist requests by declared
expected critical-path benefit and request sequence. No open Attempt is starved by another
Attempt's fan-out.

v2 supports a global Specialist limit from zero through two and a per-Attempt limit from zero
through two, with the global limit always binding. Each Specialist receives selected read-only
Evidence artifacts and a private output generation. The Gate chooses the enabled value; Lane and
Specialist increases are evaluated separately before their combined configuration.

If observed pressure nevertheless reaches an unproved safety boundary, the governor freezes new
admission, drops unstarted optional work, and closes active Specialists from lowest declared benefit
and newest request first. A positively attributed hard breach fails its owner locally. Unowned,
shared or persistent pressure stops effect admission and enters Boot-level Recovery. This is
emergency containment for a failed envelope, never a live capacity-discovery mechanism, and it
never evicts an Attempt merely because its Order fell.

## Effects, closing and results remain single-owner

Attempt executors never perform Board mutations or Instance lifecycle operations directly. Typed,
sequenced brokers own those effects; Board reads come through controller-owned Intake and Lease
paths. The one submission authority retains ADR-0037's account-wide reservations and pacing, with
no Lane-local allowance. Target traffic may proceed concurrently only through an identity-bound
per-Attempt proxy enforcing owner and global network/request limits. Inference remains globally
admitted under ADR-0040.

Attempt close begins with a durable **Closing barrier**. The controller revokes child effect
capabilities, bounded-joins descendants, kills what does not end, and only then releases the Lane
and Challenge claim. Results sequenced before the barrier may affect the Attempt. Later messages,
Candidates and effects are rejected; fully written bytes survive as inert quarantined evidence.

A completing Specialist returns a digest manifest from its private generation. Before the parent
barrier, the controller validates provenance and bounds, then atomically imports only selected,
conflict-free artifacts into the Challenge Working directory. Specialists never concurrently write
that shared directory. Later work may use quarantined output only after explicit revalidation.

Every command and process group retains ADR-0041's owner, purpose, deadline and lifecycle. A Step's
children end with the Step unless explicitly adopted as an Attempt service; an Engagement's
children end with it; every adopted service ends at the Attempt barrier.

## Replay closes interrupted work before replacement

A successor Boot replays the canonical global sequence before admitting anything. It reconciles an
unclosed Attempt's processes and external-effect reservations, durably closes that Attempt as
`crashed`, and releases its Challenge claim and Lane exactly once. Continuation, if Order selects
the Challenge again, uses a fresh Attempt identity and the preserved bounded carry and Working
directory. A Lane ordinal may be reused only after its former Attempt closes; it never resumes the
old work by identity.

No Lane performs an Instance sweep. An Attempt boundary reports its ownership transition to the
central Lease coordinator, which reconciles the whole Run and Board ledger. A Lease may remain
recoverable after its Attempt and Challenge claim end. Exact retention, grace and reattachment are
left to [Who owns an Instance across Lanes and
Boots](https://github.com/jerome-queck/incypher-ctf/issues/189); this record fixes the concurrency
boundary that decision receives.

## Measurement chooses the enabled configuration

v2's evidence venue is the named `v2-macbook-m4pro-8c-24g` profile: a 14-core M4 Pro MacBook Pro
with 48 GiB host memory, running the Colima CPU, memory and disk settings pinned by
`scripts/runtime.py` — Colima 0.10.3 and Docker 29.7.2 with 8 vCPUs, 24 GiB RAM and a 100 GiB disk.
Every heavy tool and role profile is
measured alone and in supported aggregate combinations, including long crypto, build, browser,
emulation and forensics work plus adversarial hangs, output, process trees and failures. Pilot Runs
estimate variance and set Resource envelopes with an explicit uncertainty margin. Reserve contact,
OOM, thrash, an escaped process, unbounded state growth or a missed tail fails the experiment
configuration and requires recalibration before confirmation.

The matched 5.5-hour matrix changes one axis at a time:

1. one Lane, no concurrent Specialist;
2. one Lane with one, then two concurrent Specialists;
3. one versus two Lanes while holding the promoted Specialist setting fixed; and
4. the maximum supported two-Lane/two-Specialist configuration as a safety stress configuration even
   when it is not promoted.

Cells use fresh paired Discovery holdouts with the rig, release schedule, injected faults, images,
policies and model menu frozen; only Gate-qualified solves enter the primary result. Pilot evidence
sets the paired analysis, repetition count, exclusions and stopping rules before fresh confirmatory
Runs begin.

Every promotion has zero tolerance for an ownership, effect, submission, replay, isolation,
resource-reserve or tail-safety failure. Gate-qualified Flags are primary. A candidate must have a
predeclared one-sided 95% paired bound showing no Flag loss and must additionally show either a
positive lower bound for more Flags or a positive one-sided 95% paired lower bound for improvement
in the verified-Flag timing curve. Inconclusive evidence and a statistical tie retain the lower,
simpler setting. Resource use is reported beside Flags and wall time but is not penalised while it
remains inside the proved envelope.

The maximum stress configuration exercises simultaneous Lane admission and completion,
duplicate-Challenge contention, crashes around reservations and effects, Closing-barrier races,
Quota wait, Instance expiry, concurrent Candidates, escaped children, resource pressure and
reserved-tail entry. Replay must recover unique identities, one authoritative working set, no
duplicated external effect, no leaked owner and the same deterministic next acquisition from the
recorded state.

At startup the Supervisor reads the cgroup limits and selects the highest approved
`v2-macbook-m4pro-8c-24g` configuration whose aggregate envelope and reserves fit. Missing capacity
scope disables the optional second Lane and Specialists; capacity below the proved one-Lane
baseline causes a pre-Run Refusal.
The selected profile fixes live Lane and Specialist maxima and every hard envelope. The governor
may schedule below them, reclaim elastic loans or downshift future optional admission; it never
promotes a configuration or widens a hard ceiling live. If the official release reveals different
competition hardware, its profile, build consequences and fresh calibration are an official delta
for v3 rather than evidence v2 pretends to hold.

## Consequences and handoff

- The present serial loop is evidence, not an implementation of this record. Production work follows
  the cleared map through `/to-spec` and `/to-tickets`.
- [What persists in `/state`, and how it stays
  bounded](https://github.com/jerome-queck/incypher-ctf/issues/175) receives the canonical events,
  private generations, quarantine and measured disk-growth requirements.
- [The Recovery Agent: early diagnosis, forced repair, and safe
  authority](https://github.com/jerome-queck/incypher-ctf/issues/191) receives the local-versus-shared
  failure boundary and emergency pressure path.
- [The local board: what it replicates, and what it must
  not](https://github.com/jerome-queck/incypher-ctf/issues/158) receives the matched matrix and fault
  scenarios; [What evidence makes v2 ready to
  freeze](https://github.com/jerome-queck/incypher-ctf/issues/196) and [Reconcile the v2 Gate after its
  evidence contract is settled](https://github.com/jerome-queck/incypher-ctf/issues/208) receive the
  promotion rule.
- No new Wayfinder ticket is needed: the remaining detail already belongs to those children.
