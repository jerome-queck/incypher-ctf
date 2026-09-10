# A Run survives its Boots, and Recovery owns the first fault

> **The native-first route clause and unbounded v2 scope are superseded by
> [ADR-0051](0051-v2-has-a-finite-core-and-proof-earned-capability-packs.md).** Both native and CPA
> are Core and candidate qualification selects the primary route; model-assisted novel Recovery is
> a Capability Pack. Deterministic fault ownership, containment and safe no-inference operation
> stand.

> **[ADR-0046](0046-recovery-contains-the-smallest-safe-scope-and-changes-before-retrying.md)
> resolves the Recovery policy left open here.** It defines Incident identity and fingerprints,
> minimum-safe-blast-radius containment, typed probes and remedies, forced Agent escalation,
> probation, practice repair, durable evidence and the fault-injection Gate.

> **The inference credential, environment and shared-exhaustion mechanism are resolved by
> [ADR-0040](0040-one-owner-one-subscription-and-one-observed-limit-state.md).** Codex Control reads
> authoritative account-limit state when available, spends only pre-authorised journalled resets
> and holds a live Quota wait instead of routing or closing the Run.

> **The open secondary-Harness clause is resolved by
> [ADR-0039](0039-native-codex-leads-and-one-small-loop-owns-the-cpa-route.md).** Native Codex remains
> primary; the CPA route uses a separate minimal Solver-owned Responses loop.

> **[ADR-0043](0043-one-sequencer-owns-a-measured-rolling-working-set.md) resolves Lane admission and
> measured concurrency.** “Exactly one worker” below means one live Boot-level Run control root and
> predecessor, not one Attempt executor; several Lane-owned executors remain under that one writer.

> **[ADR-0044](0044-one-coordinator-fences-every-instance-lease.md) resolves Instance ownership.**
> The central coordinator owns every Lease; Attempts receive epoch-fenced bindings; Lanes never own
> Instances; and Lease phases, Board reconciliation verdicts and terminal causes stay separate.

[#157](https://github.com/jerome-queck/incypher-ctf/issues/157) found that restarting today's
Solver would preserve its wall-clock and stream sequence while losing nearly every control that
makes the Run safe: Attempt identity, spend, carry, pending Flags, submission pacing, breaker
history, Intake safeguards and Lease ownership. It would also spend the final tail and write
`run-close` before trying to continue. v2 adds concurrent Lanes, so process lifetime can no longer
stand in for either Run lifetime or global ownership.

The decision: **a Run survives one or more Boots; its canonical stream is replayed before work;
Recovery owns the first fault rather than waiting for a restart limit; and no side effect occurs
from identity or ownership the Solver could not recover durably.**

## One Run, one writer, many Boots

A **Boot** is one uninterrupted Solver-process incarnation inside a Run. The `run_id`, absolute
window and accumulated control state do not move when the process does. A Run writes one
`run-open`; each Boot is represented by `boot-open` and `boot-close`; the Run writes one terminal
`run-close`. A Boot cannot close itself after SIGKILL or host loss, so the supervisor or successor
reconciliation durably closes the unclosed Boot and any unclosed Attempt exactly once. No synthetic
Attempt is coined when none was open. Exactly one worker and stream writer may be live for the Run.
A successor starts only after its predecessor and descendants are dead and reaped.

A Boot failure is not a Run close. It does not spend the reserved tail, emit a normal final
`run-close`, reset the window or claim the Board is finished. If an Attempt was active, the failure
ends it as crashed, preserving its spend, Observations, Checkpoints, approach, tried commands,
wrong submissions and working directory. Recovery then durably burns the next per-Challenge
sequence and reserves an Attempt identity unique within the Run **before** staging, deploying or
writing anything under it. A reserved sequence remains burned even if its Attempt never opens; the
global address is `(run_id, attempt_id)`. Half-observed work is never resumed under, or merged into,
the old identity. Flag sweep and every stream reader select exactly that address, so no Observation
from the crashed Attempt can become a candidate or Step of its successor.

Where a hard death prevents a final timing record, replay charges the unclosed Attempt
conservatively: its work is never free or zeroed, while host-reboot downtime remains distinct from
active Attempt time. The exact estimator belongs to the recovery specification; preserving the
anti-livelock penalty does not justify pretending an unknowable timestamp is exact.

Only a true terminal transition may close the Run, and it does so once. Window closure or deliberate
shutdown performs the final tail; a Refusal is not restarted; a Recovery verdict that no sanctioned,
safe Boot can be produced records a diagnosed crash rather than pretending the tail ran. A clean
exit, Refusal and crash therefore remain different signals to the supervisor.

## The stream is the recovery truth

The append-only Run stream remains canonical. A later Boot replays it into projections for:

- Scheduler spend, Attempt counts, Tier progress, exploration cadence and solve-velocity baselines;
- Attempt-boundary carry, work already tried and breaker history;
- pending and previously offered Flag candidates, wrong-submission ceilings and Board-wide pacing;
- Intake cycles, last trustworthy Snapshot and sticky safety failures;
- Boot, Attempt, Lane and Lease ownership; and
- evidence continuity and accumulated write-failure accounting.

No mutable scheduler snapshot becomes a second truth. Facts already observable stay derived.
Facts required for recovery but absent from the stream gain recovery-grade lifecycle or control
records. One sequencer/writer serialises concurrent Lanes, the supervisor and recovery; `flock`
around independently allocated sequence numbers is insufficient. Boot and Attempt identities,
Lease claims and releases, submission/pacing changes and any other reservation that authorises a
side effect receive an fsync-equivalent crash-durable acknowledgement **before** that side effect.
Failure to persist one is fail-closed. Ordinary Observation writes may retain ADR-0009's retry,
count and continue policy where their absence cannot authorise unsafe work.

This makes the Run's durable state input to later Boots. Once a Run opens, deleting or losing that
state is no longer harmless degradation. On missing, truncated or contradictory records, recovery
may reconstruct control authority only from surviving crash-durable records or a trusted,
corroborated Board read. A Challenge working directory may recover artefacts and carry after
verification; model-writable files never corroborate an identity, submission or Lease owner. If
ambiguity could duplicate a submission, overlap work or destroy an Instance, no worker starts
until it is resolved or the Run receives a diagnosed recovery verdict.

Damaged evidence is evidence too. Recovery preserves and quarantines the original bytes; it never
truncates, rewrites or “repairs” a corrupt stream in place. Diagnosis and any safe continuation are
written separately with provenance that identifies the damaged segment they follow.

## Board truth plus global Lease ownership

The Board remains authoritative about whether an Instance exists; the Solver's stream is
authoritative about why it may use or destroy it. Neither alone proves an orphan. One central Lease
coordinator owns Run-unique Leases and hands Attempts epoch-fenced bindings; Lanes never own them.
Its open phases are `reserved`, `attempt-bound` and `recoverable`. `orphaned` and `unattributed` are
reconciliation verdicts, while `expired`, `terminated` and `never-deployed` are terminal causes.

Only a positively attributed Instance that remains durably ownerless after at least fifteen
seconds and two reconciliations becomes orphaned and eligible for termination. An unattributed
Instance is never adopted or destroyed. Empty ledger pages cannot prove absence: expiry requires a
successful DELETE/404 or ledger absence corroborated by the per-Challenge endpoint after its cache
horizon. Reattachment never preserves an Attempt identity or bypasses Order. ADR-0044 holds the
full reservation, startup, fencing, reattachment and termination contract.

v2 must be parallel-safe and ship concurrent Attempt execution, but start with one enabled Lane
until measured gates justify raising it. One Lane as the default is calibration, not permission to
build single-Lane ownership. The eventual Lane count, routing and scheduling policy remain separate
Wayfinder decisions.

## Supervision starts Recovery early

The submitted v2 image must have an in-image PID-1 supervisor—an implementation role, not a new
domain object—which owns worker Boots, descendants, signals and exit classification. Docker
`unless-stopped` must restore the whole container after daemon or host loss. Once the Run finishes
or refuses, PID 1 stays quiescent; Docker restart is not allowed to turn a terminal state into
another Boot.

Recovery begins on the first operational fault:

- a hard failure is owned within 15 seconds;
- 60 seconds of unexplained inactivity captures a diagnostic snapshot;
- the condition is rechecked by 120 seconds; and
- an unresolved condition has a recovery agent active by 180 seconds.

A declared long-running command may continue while Recovery diagnoses beside it. An identical
Solver-owned failure is never attempted blindly twice. The ordinary replacement budget is at most
three **replacement** Boots in a rolling ten minutes, delayed 5, 30 and 120 seconds and
reconstructed from durable Boot records. Exhausting it freezes blind restarts; it does **not**
abandon the Run or begin the diagnosis late. A recovery agent must select a prebuilt remedy, and
may authorise one probationary Boot only after the environment, configuration or remedy changed
materially. Further probation after a distinct remedy/fingerprint belongs to the recovery-policy
ticket; a counter alone can never close the Run.

Prebuilt in-image remedies may reconcile state, back off, change an already-authorised inference
path or select a tested rollback mode. They may not invent credentials, bypass Board/account rules,
delete evidence or patch arbitrary source. Unknown failures remain actively diagnosed; safe
continuation, not optimism, authorises another worker.

SIGTERM reaches the worker once, suppresses ordinary replacement and begins the true final tail.
The tail is a durable, restartable phase: one serial submission authority writes crash-durable
intent before each POST, then reconciles its outcome with the Board. A response lost after POST is
an indeterminate side effect, not an idempotent request; it is never blindly repeated and is
at-most-once where the Board cannot prove it was not consumed. Lease termination may retry because
an already-absent Instance is success. A worker failure resumes only safe unfinished tail work
within the same absolute budget. The tail has a hard 300-second deadline; `run-close` follows once;
and the container gets 330 seconds of stop grace. Thus the reserve has time to run without allowing
shutdown to become an unbounded hang or repeating ambiguous side effects.

## Repair may change the practice Solver image, not yet the scored one

Practice adds a separate host recovery controller and isolated repair agent able to diagnose,
patch, build, verify, launch a probationary image and roll back. It starts early enough to repair
rather than merely explain hours of silence, and persistent changes still enter the repository
through its issue, checks and pull-request trail.

For the scored Run, dynamic source patching, a Docker-socket controller, sibling maintainer or image
replacement is **not authorised by this record**. Published rules support autonomous prebuilt
recovery inside the submitted container but do not settle replacement of that container. Those
actions activate only after written organiser confirmation and a fresh review of the Starter Pack,
scoring/runtime rules, isolation boundary and provenance contract. Until then only the in-image
supervisor/recovery path may control the scored Run. A host or sibling repair agent may read and
diagnose where rules permit, but cannot launch, restart or replace it. This decision must be
revisited when that new information arrives.

## The inference set is Codex-only

The admissible inference set is closed to **native Codex first and one private, single-owner
CLIProxyAPI-backed Codex path second**. Claude, every local model and a metered OpenAI API key are
removed. CPA is alternate plumbing—and, if paired with an independent tool loop, possible harness
diversity—not extra capacity: both routes spend the same account allowance and reach the same Codex
backend. Quota exhaustion, a fraud restriction or backend failure therefore causes a declared wait
or use of a separately pre-authorised saved reset; it never causes proxy rotation around the limit.

Native `codex exec` remains primary. Whether CPA sits behind that CLI or a second tool-running
harness, and whether Hermes, oh-my-pi, OpenCode or a small purpose-built loop can preserve the
Solver's Step/deadline/sandbox contract efficiently, remain research and prototype decisions.
Official OpenAI model, prompting, agent and tool guidance are inputs to those decisions. CPA does
not enter the scored critical path until its topology, containment, failure classification and
tool-loop behaviour are proved. The evidence boundaries are recorded in
[`CLIProxyAPI as a Codex-subscription fallback`](../research/2026-09-05-cliproxyapi-codex-subscription-fallback.md).

## What this moves

- [ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md)'s v1 choice that the
  Solver itself is PID 1 expires exactly where it predicted: v2's supervisor now owns PID 1. The
  one-image and Board/Target seam decisions stand.
- [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md)'s append-only stream and
  store-facts/derive-judgements rule stand. Its claims that all failed writes are nonfatal and that
  `/state` may disappear mid-Run without costing ability are superseded for recovery-critical
  records. Replay is not a mutable judgement store.
- [ADR-0017](0017-the-exploration-share-and-solve-velocity-are-reinstated.md)'s accepted loss of
  velocity state on restart is superseded; the already-recorded Intake facts now rebuild it.
- [ADR-0007](0007-truth-about-an-instance-lives-on-the-board.md)'s Board-authoritative existence
  stands. Its boundary sweep becomes global and ownership-aware: Board presence alone cannot prove
  orphanhood.
- [ADR-0023](0023-an-attempt-holds-the-turn-loop-and-order-is-not-asked-between-turns.md)'s ordinary
  Turn loop stands. A Boot failure is the new explicit reason an Attempt ends before a successor
  opens with another identity.
- [ADR-0014](0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)'s Codex-driven
  tool-loop seam stands. Its Claude adapter/credential branch is removed.
- [ADR-0021](0021-a-string-the-board-stated-waits-for-the-reserved-tail.md)'s placement of held
  candidates in the final tail stands; losing them merely because a Boot failed does not.
- [ADR-0013](0013-filevault-is-off-and-the-reboot-recovers-unattended.md)'s measured host-reboot
  path is historical evidence, not current readiness; this record supplies the Run semantics it
  lacked, and v2's gate must re-prove the whole path.
- [ADR-0010](0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)'s
  subscription-first, unattended and quota-is-a-stall principles stand. Its Claude socket,
  automatic metered-key fallback and metered overlay are removed.
- [ADR-0011](0011-the-sanctioned-path-is-the-only-path.md)'s absolute proxy ban now has exactly one
  later exception: isolated, one-owner CPA use under the evidence and gates above. No account
  pooling, sharing, hosted relay or limit circumvention is admitted.

The scored-repair authority behind that boundary is recorded in
[`Autonomous runtime self-repair authority`](../research/2026-09-05-autonomous-runtime-self-repair-authority.md).
The live official surfaces that trigger its next review are tracked in
[`IN-CYPHER live-surface recheck`](../research/2026-09-05-in-cypher-live-surface-recheck.md).

## Delivery and proof obligations

Restart safety lands before the supervisor. The implementation must first add crash-durable
reservations, replay, reader/Flag isolation and composition tests; only their passing proof permits
PID 1 to change.

The proof runs two fresh composition roots under one `RUN_ID` and injects death after identity
reservation, `attempt-open`, deploy, candidate hold, wrong submission and during the final tail. It
must show: no Attempt identity or `(attempt_id, step_index)` reuse; no dead-Attempt Observation in a
successor's Flag sweep or reader projection; preserved spend/carry/breaker/pacing/Intake state;
correct global Lease reconciliation; one `run-open`, one final tail and one terminal `run-close`;
and no overlapping predecessor/successor process tree. Fault-injection also proves the 15/60/120/180
deadlines, replacement-budget reconstruction in a fresh root, clean/Refused/terminal quiescence,
fingerprint deduplication and fail-closed recovery from corrupt control records.

The same delivery deliberately **amends**, never deletes, the exec-form ENTRYPOINT assertion in
`tests/test_no_secret_reaches_a_layer.py` so it expects the PID-1 supervisor. It adds ADR-0008's
forward amendment that its v1 scope expired; adds forward amendments where ADR-0007, ADR-0009 and
the credential records now read historically; reconciles `CONTEXT.md`'s Run-state, Refusal,
Reserved-tail and Credential-chain entries—including that CPA shares quota and exhaustion is a
wait/reset rather than failover; replaces README's incompatible `--rm` lifecycle; and re-proves
`unless-stopped`, Colima host reboot, Codex reauthentication, signal delivery and the 300/330-second
tail in the v2 gate.

## What remains to decide

This is a recovery contract, not its implementation spec. Separate Wayfinder tickets still own the
recovery remedy catalogue and fingerprinting; repair-controller isolation and authority; Lane
count, agent routing and global scheduling; CPA topology and secondary harness; and context/loop
engineering grounded in official OpenAI model, agent and tool guidance plus current CTF evidence.
No supervisor or parallel worker is safe to ship until those tickets preserve the invariants above.
