# Authority remains writable when storage is exhausted

[Which facts survive storage
exhaustion?](https://github.com/jerome-queck/incypher-ctf/issues/246) closes the finite-storage
contradiction left by ADR-0045 and ADR-0046. **Canonical ownership and external-effect facts, plus
the capacity to contain or end the Run honestly, outrank new evidence, continued work and forensic
detail. Every producer write and external effect is admitted against a storage reservation before
it begins; open-Incident evidence is bounded before it is pinned; and no storage
fault may turn missing authority into permission to continue.**

This forward-amends ADR-0032's non-fatal ordinary Observation-write policy, ADR-0045's unbounded
open-Incident pin and hard-pressure wording, ADR-0046's Incident evidence and terminal boundary,
ADR-0049's disk proof, ADR-0052's submission admission and ADR-0053's final-submission lifecycle.
Their single-writer, reachability, minimum-safe-blast-radius, at-most-once, full-window and
post-official-end cleanup decisions stand. This is a v2 planning contract; the current v1 recorder
does not implement it.

## Truth outranks throughput

Storage pressure keeps this order:

1. the verified canonical stream, restart-required private broker state, ownership and effect
   fencing, and enough capacity to sequence containment or terminality;
2. already admitted non-reconstructible Solve receipts, selected Evidence artifacts and exact
   Candidate-vault state;
3. new evidence or continued work only where the complete write or effect transaction has already
   reserved its worst-case storage; and
4. raw, duplicate, reconstructible or otherwise optional forensic detail.

The first two classes never lose reachability to make the Solver appear productive. The third may
continue only inside its reservation. The fourth is shed first. No model Turn, diagnostic body or
unreserved Flag path may borrow authority capacity; stopping safely is better than continuing with
a history which cannot prove who owned or caused an effect.

Canonical records remain append-only. Storage pressure changes whether a new body is admitted and
which disposable references remain live; it never compacts, rewrites or silently skips canonical
authority. A missing body before its recorded retirement is damage and fails closed.

## The Control write reserve is admission authority

The **Control write reserve** is a release-candidate-sealed pool of storage reservations which
ordinary writers cannot consume. One storage reservation accounts separately for worst-case bytes;
filesystem objects such as inodes or directory entries; and the create, append, rename, unlink and
durability operations the pinned filesystem must still complete. The storage profile fixes:

- the absolute writable Run-state envelope, host/filesystem safety floor and soft and hard
  thresholds over the same reservation dimensions;
- ordinary producer envelopes and the maximum reservation of every authority or external-effect
  transaction;
- a non-borrowable **terminal floor** for exactly one worst-case terminal transaction;
- a non-borrowable **Recovery floor** for one minimal storage Incident from detection through
  containment, one retirement transaction, the semantic durability probe, probation and outcome;
- the remaining shared authority pool, including submission and reconciliation transactions; and
- per-Incident and aggregate Incident-evidence limits.

ADR-0049's broader machine and rig allocations do not substitute for this writer-level profile.
The exact values are candidate facts, not permanent ADR constants: controlled hostile trials set
them, and the Release-candidate manifest seals them before a Run.

The shared authority pool is sized for the sum of every mandatory authority and external-effect
transaction which the sealed maximum Lane, broker and control concurrency may hold at once. That
aggregate stands beside, and never includes, the terminal or Recovery floor. A candidate which
cannot prove the whole concurrent set refuses before a Run.

The sequencer atomically grants a storage reservation before a producer writes a blob or before any
external effect is admitted. A producer reservation covers its body, seal, canonical reference
and possible governed retirement. An effect reservation covers the pre-effect authority record,
outcome, worst-case reconciliation and epoch transition, Incident transition, and any bounded
receipt records the effect can require. Two Lanes, brokers or Recovery paths cannot observe the
same headroom and overcommit it independently.

A reservation is released only by a crash-durable outcome, cancellation before the admitted
boundary, or retirement completion. Failure to obtain the whole reservation refuses the write or
effect before it begins. A free-space reading, expected compression, pending eviction, process-local
counter or promise to clean up later is not a reservation.

The terminal and Recovery floors are never borrowed by ordinary authority. The terminal floor is
spent only by the bounded transaction which fences effects and records an irrecoverable Run. An
ordinary allocation crossing into either floor, a reservation-accounting contradiction, or loss of
the filesystem guarantees behind the profile opens a Run-shared resource Incident immediately.

## Incident evidence is bounded before it is pinned

Every Incident keeps its compact canonical lifecycle and authority facts: detection, classification,
Failure fingerprint, affected owners and effects, deadlines, containment, probe and Remedy
decisions, probation, links and final outcome. Its evidence manifest may additionally retain only:

- one bounded trigger snapshot;
- bounded probe, action, termination, cleanup and probation results; and
- bounded redacted head and tail projections of raw inputs, with source identity, full byte length,
  digest and an explicit truncation, refusal or loss reason.

Each item reserves storage before capture and is charged to both its per-Incident and aggregate
limits. A probe already has a timeout, byte bound and redaction rule under ADR-0046; its storage
reservation is now part of admission. A model summary is a Claim and never the sole surviving
evidence. Where complete hostile or oversized bytes do not fit, the governor streams only the
bounded projection and digest where safely obtainable, then records what was not retained. It does
not first write an unbounded body and hope to classify it later.

An open Incident pins this bounded admitted evidence set, not every byte transitively mentioned by
diagnosis. Selected solve evidence remains protected by its own class and cannot be charged against
the Incident budget to hide its growth. Recurrence with the same open Failure fingerprint shares the
existing bounded set; a materially different fingerprint may open a linked Incident only after the
aggregate reservation succeeds. When it cannot, the minimal canonical Incident transition still
records the refused evidence while effects remain contained.

## Retirement is ordered and crash-replayable

Only the trusted storage governor may retire material. At soft pressure it proceeds in order:

1. ephemeral temporary files, caches, duplicates and reconstructible derivations;
2. failed or unselected Work-generation bulk after its closing barrier and reachability checks;
3. raw high-rate telemetry, tool bodies and private noncanonical logs outside selected Evidence and
   bounded open-Incident manifests; then
4. closed-Incident detail with no receipt, Promotion, carry or dependent-evaluation reachability.

The governor never pressure-retires canonical authority, restart-required private broker state,
Candidate-vault state, Solve receipts, selected Evidence or a bounded open-Incident evidence set.
Intake originals and any other class keep ADR-0045's own reachability rules.

Retirement is one replayable two-phase transaction. Before deletion, the sequencer appends a
crash-durable tombstone naming the exact path, class, digest, length, reason and released reference;
that changes reachability but credits no physical capacity. The governor deletes only after that
record, then appends completion before the freed bytes or inode may fund another reservation. On
replay, present bytes behind a valid tombstone are cleanup still to retry; absent bytes without
completion cause the sequencer to append completion before reuse. Bytes absent without a tombstone
are damage. A failed deletion merely leaves harmless extra bytes and no falsely reusable capacity.

## Hard pressure contains before it destroys

Hard pressure opens a resource-plane Incident and blocks new bulk work and optional admission. No
new Attempt, Specialist, Intake download or model Turn begins. Already admitted work may continue
only inside its complete outstanding reservation; work without one stops at its safe boundary. An
owned producer which exceeds its envelope is joined, isolated or terminated after the bounded
Incident projection is preserved.

Deterministic diagnosis, canonical containment and a submission whose complete effect transaction
is already reserved may continue. Read-only or in-memory work is permitted only where its owner and
bounded result path remain proved; it cannot become a route around admission. Ordinary bodies never
consume the Control write reserve; the sequencer spends only the authority allocation already made
for them. This is the smallest safe blast radius: unrelated capacity is not discarded merely
because one producer filled its envelope, but no work is called unrelated unless its future writes
and effects are already covered.

## Contained and terminal have exact storage meanings

A storage Incident may remain `contained` only while all of these hold:

- the canonical prefix, single writer, owners and external-effect boundaries are verified;
- the Control write reserve and its terminal and Recovery floors remain intact;
- unsafe effects and unreserved producers are fenced; and
- an admissible eviction, producer containment, filesystem recovery or other bounded Remedy remains.

Solving resumes only after bytes, inodes and metadata headroom exceed the candidate's proved floor;
a semantic write, durability and replay probe succeeds; every retirement and authority projection
replays consistently; affected owners and effects reconcile; the offending producer is closed or
re-admitted under a smaller envelope; and ADR-0046 probation passes. One free-space sample cannot
resolve the Incident.

The Incident is `terminal` when mandatory canonical authority or the Control write reserve cannot
be restored and no admissible Remedy remains. Effects freeze first; the terminal transaction then
uses its non-borrowable floor to close or classify every owner it safely can and record the honest
Run outcome. Irrecoverable early terminality remains ADR-0053's zero-tolerance Gate failure.

If the filesystem is read-only, returns durability errors or otherwise cannot accept even the
terminal transaction, the Solver fabricates no `run-close`. The Supervisor remains quiescent and
admits no later Boot while durability is unavailable. If durability returns, one successor begins
from the last verified prefix, records the lost interval and storage verdict, and terminates without
reopening work. If it never returns, the unclosed Run is external evidence of Gate failure, never a
canonical claim that cleanup or safe closure occurred.

## Final submissions reserve their complete storage path

Before the time-based Final-submission reserve begins, the sequencer reserves the worst-case storage
for every Candidate proposal in its sealed last-call set: reservation, wire-start, outcome,
reconciliation cycles, `unknown-and-spent`, Submission-epoch or breaker transitions, Incident facts
and accepted receipt records. A Candidate proposal without that complete reservation is fenced
before its POST. Evidence flush and post-competition cleanup cannot consume this storage; healthy
cleanup still starts only after the official Board close.

This rule changes no ADR-0052 deadline, at-most-once identity or minimum-safe-blast-radius boundary.
It ensures the final opportunity to score cannot create an effect whose outcome the Run lacks room
to retain.

## The Gate proves exhaustion rather than estimating it

Controlled proofs cover byte exhaustion, inode exhaustion, metadata exhaustion, read-only and I/O
failure; two Lanes racing a threshold; a blob which fits while its reference does not; a reserved
submission whose outcome takes the worst branch; oversized hostile Incident evidence; pressure
during every retirement crash point; producer termination; reserve-accounting damage; restart and
probation; and the official-close path under hard pressure.

Every writable controlled scenario asserts bounded growth, single admission, no unrecorded
deletion, no missing authority, no duplicate or stale-epoch effect, an intact terminal floor, and
one honest `resolved`, `contained` or `terminal` Incident outcome. A permanently read-only or
durability-failed scenario instead passes only when no `run-close`, later Boot or external effect is
admitted and independently observed Supervisor/Evaluator evidence names the Run as unclosed; it
must not invent the canonical outcome storage made impossible. Fault injection may intentionally
enter the Control write reserve to prove containment. The qualifying 5.5-hour Run may not touch its
terminal floor, lose an authority fact, admit an unreserved effect or exceed its sealed profile.

The requirement ledger records the exact profile, the worst-case concurrent-set proof, peak and
remaining reservation dimensions, every reservation and release, pressure transition, tombstone,
evidence truncation, semantic durability probe, containment, probation and canonical outcome or
external unclosed verdict. Production storage, Supervisor, sequencer, governor, record formats and
fault fixtures remain the post-map `/to-spec` and `/to-tickets` handoff; this ADR does not claim the
current v1 runtime implements them.
