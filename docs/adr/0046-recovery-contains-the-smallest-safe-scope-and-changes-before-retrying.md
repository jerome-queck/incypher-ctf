# Recovery contains the smallest safe scope and changes before retrying

> **Indeterminate Flag-submission containment is narrowed by
> [ADR-0052](0052-one-unknown-submission-fences-flags-for-sixty-seconds.md).** One unknown Candidate
> proposal freezes Flag POSTs for at most sixty seconds, not all external effects. A broader
> `run-shared` freeze still applies when broker ownership, the canonical writer or durable effect
> recording is ambiguous.

> **Capability admission is amended by
> [ADR-0051](0051-v2-has-a-finite-core-and-proof-earned-capability-packs.md).** Deterministic
> detection, containment, fixed remedies, restart and safe no-inference operation remain Core;
> model-assisted novel Recovery and external practice candidate-image repair are Capability Packs.

[The Recovery Agent: early diagnosis, forced repair, and safe
authority](https://github.com/jerome-queck/incypher-ctf/issues/191) operationalises
[ADR-0032](0032-a-run-survives-its-boots-and-recovery-owns-the-first-fault.md). Recovery is an
event-triggered control path, not a permanent model watcher: deterministic control detects and
contains a fault, preserves the smallest safe blast radius, and opens one bounded **Incident**.
When deterministic handling cannot resolve it, a fresh Recovery Agent interprets bounded evidence
and requests typed probes or prebuilt remedies; it never grants authority or executes arbitrary
repair.

## One Incident owns one fault lifecycle

An Incident is durably identified before diagnosis and has three orthogonal classifications:

- `scope`: `owner-local`, `run-shared` or `external`;
- `plane`: `process`, `resource`, `state`, `authority`, `board-target`, `inference`, `security` or
  `host`; and
- `mode`: `hard`, `inactive`, `degraded` or `indeterminate`.

The component may add an open reason code without widening those control classifications. Its
**Failure fingerprint** normalises the scope, plane, mode, component, operation, failure class,
relevant authority state and image/configuration identity. Timestamps, process ids and free prose
never enter the fingerprint. A matching fingerprint coalesces only while that Incident remains
open. Any recurrence after a final outcome or any changed fingerprint opens a linked successor
rather than rewriting the first Incident's identity.

The Incident's open states are `open`, `diagnosing`, `remedy-authorized` and `probation`. Detection,
containment, probes and recurrence are sequenced events rather than extra states. Exactly one final
outcome closes it:

- `resolved`: the required probation passed;
- `contained`: safe degraded operation remains and the affected capability stays isolated; or
- `terminal`: mandatory Run authority cannot be restored by any admissible action.

The canonical stream records every transition. No model-authored summary becomes the current state
or repairs history in place.

## Detection owns deadlines, not waiting periods

The deterministic Resource governor observes process ownership, declared deadlines, heartbeats,
output, CPU, RAM, PIDs, disk, network, requests and typed progress probes. Every declared
long-running command or Attempt service names its owner, purpose, hard deadline, Resource envelope
and progress-probe cadence before launch. A heartbeat proves life, not progress; age alone proves
neither a hang nor safety. Long cryptography, compilation, emulation and scans may continue while
Recovery diagnoses beside them.

ADR-0032's `15 / 60 / 120 / 180` seconds are upper deadlines:

- a hard fault has an Incident owner and containment within 15 seconds;
- 60 seconds of unexplained inactivity seals a diagnostic snapshot;
- the condition is rechecked by 120 seconds; and
- an unresolved condition has a Recovery Agent active by 180 seconds.

Critical, run-shared, unknown and repeated faults start the Agent immediately after containment;
the schedule never delays an already-useful diagnosis until 180 seconds. Incident deadlines are
crash-durable. A successor Boot reconstructs them, and an elapsed interval that cannot be proved is
treated as already expired so restart never buys a fresh silence budget.

A known fingerprint may take one allowlisted deterministic Remedy before an Agent only when its
preconditions and expected delta are already proved. Failure to resolve, recurrence or remedy
resistance starts the fresh Recovery Engagement immediately. This fast path never refreshes the
blind-retry allowance or weakens the 180-second deadline.

## Containment preserves the minimum safe blast radius

Recovery does not stop the Solver by default.

Where safety permits, deterministic control seals the triggering evidence before containment or a
Remedy changes state. A hard resource or isolation breach may force immediate termination first;
the lost pre-containment evidence interval is then recorded explicitly.

| Fault scope | Immediate containment | Work that continues |
| --- | --- | --- |
| `owner-local` | Revoke that owner's effects; join, isolate or kill only its process group when required | Unaffected Lanes, Intake, deterministic control and unrelated local work |
| `external` | Pause only operations dependent on the unavailable Board, Target, backend or capacity | Independent local analysis, safe deterministic work and unrelated effects |
| `run-shared` | Freeze new and irreversible effects whose ownership or durable recording is ambiguous | Read-only diagnosis, evidence sealing, replay, containment and work proved independent of the ambiguity |

An unowned process, corrupt writer, ambiguous control owner or shared broker fault that loses
effect ownership or durable outcome stops external effects across Lanes and enters Boot-level
Recovery. A shared service availability fault without that ambiguity pauses only its dependent
operations. Replacing a Boot closes its open Attempts as `crashed`; successors receive fresh
identities and never resume half-observed effects.

An ambiguous Candidate or missing selected solve evidence blocks that Candidate's submission,
Solve receipt and Promotion; it does not stop unrelated work. Damage shared by several Candidates
or the canonical provenance path is `run-shared` and freezes those effects until the evidence
boundary is safe again.

Recovery capacity is a protected global reserve. Pressure drops unstarted optional work and closes
the lowest-benefit Specialists before it denies healthy Solve Leads. Recovery may therefore run
beside unaffected solving without turning self-repair into a whole-Run outage.

## Novel or resistant faults receive one bounded Agent

One fresh, incident-scoped Recovery Engagement receives a bounded, redacted evidence manifest and
typed capability surface. It may author only:

- a classification and Failure fingerprint with cited evidence;
- requests for fixed Recovery probes;
- at most one Remedy request across the whole Engagement and its expected material delta;
- stop conditions; and
- unresolved questions.

After that request, any further Remedy comes only from deterministic escalation or a linked
successor Incident; the Agent cannot revise its request into a sequence of model-driven retries.

It receives no credential, general shell, filesystem search, canonical-state write, deletion,
submission, Lease, reset, route, restart, build or promotion authority. Hidden reasoning is never a
record; typed proposals and their cited evidence are.

An isolated fault uses Luna max. A whole-Run or critical-path fault uses Daybreak medium/high and
may spend the protected Recovery reserve. The Gate selects Daybreak's effective effort by
successful recovery, healthy-work disruption, wall time and subscription use rather than model
price.

A fault in Recovery opens one linked child Incident under deterministic Supervisor ownership; it
does not recursively spawn Recovery Agents. A positively classified native-local Harness failure
may start or continue Recovery through the CPA Harness. A CPA-local fault may use healthy native
Codex. A shared backend failure, account restriction or Quota wait changes neither route nor model;
deterministic polling, pre-authorised reset handling and the Run continue under ADR-0040. If no
selected model is available, the parent remains safely contained while deterministic escalation
applies this policy.

## Probes and remedies are closed authority surfaces

A **Recovery probe** is a fixed, typed, read-only diagnostic with an owner, authority scope,
timeout, byte bound and redaction rule. v2's probe families cover process trees and descendants,
resource envelopes, canonical streams and sealed blobs, brokers/routes/limit state,
Board/Target/Lease state, storage ownership and modes, and isolation controls. A state-changing
probe is a Remedy and must use the stronger contract below.

A **Remedy** has a versioned id, applicable fingerprints, typed inputs, deterministic
preconditions, bounded executor, expected material delta, postconditions, rollback, authority mode
and maximum uses. Its **Remedy context** is the exact tuple of typed inputs and applicable
authority, image, configuration, Resource-envelope and isolation-profile versions checked at
authorisation. The initial catalogue permits:

- joining, reaping, isolating or restarting an owned process or service;
- replacing a Boot only after its predecessor and descendants are proved dead;
- replaying a verified stream prefix, quarantining corrupt bytes, linking a successor segment and
  rebuilding projections without mutating the damaged evidence;
- retrying safe reads and reconciling, reattaching or terminating a Lease only under ADR-0044;
- probing or refreshing a credential through its broker without revealing it;
- selecting CPA only for a classified native-local fault;
- entering Quota wait or requesting one journalled, pre-authorised reset under ADR-0040;
- shedding optional work or applying governed ADR-0045 storage eviction; and
- selecting a pretested rollback configuration or isolation profile.

Practice adds candidate-image replacement through its separate controller. Scored mode exposes
only the fixed in-image catalogue.

ADR-0032's blind replacement allowance remains at most three replacement Boots in a rolling ten
minutes, delayed `5 / 30 / 120` seconds, but diagnosis starts on the first fault. An identical
Solver-owned failure is never blindly attempted twice. After diagnosis, at most one probation is
admitted for a unique `(Failure fingerprint, Remedy version, Remedy context)` tuple. That tuple
cannot repeat. The finite catalogue and remaining safe Run window bound distinct attempts; a retry
counter alone never declares the Run terminal.

## Probation proves a material change

A Remedy succeeds only after probation proves:

- the predecessor and affected descendants are dead or safely joined;
- canonical replay and the single writer are valid;
- required brokers and protected control services are healthy;
- identity, submission and Lease authority are reconciled;
- a trustworthy Intake cycle completed where Board reads are required;
- the active Resource envelope and protected reserves remain safe; and
- the original Failure fingerprint does not recur for at least 120 seconds or the longer recurrence
  horizon established by that fault's injection test.

An owner-local Remedy may satisfy the same invariants for its owner without replacing the Boot.
A probationary Boot remains visibly probationary until the shared invariants pass; it cannot admit
unsafe external effects merely because its process stayed alive.

`terminal` is available only when predecessor ownership, canonical truth, credential authority,
submission ambiguity, Lease safety, isolation or protected reserves cannot be restored and no
admissible Remedy remains. Shared quota, a backend outage, an unavailable Board or exhausted blind
replacement count is not alone terminal: the Run remains contained or in Quota wait until capacity
returns, the window reaches its reserved tail, or a mandatory safety invariant genuinely fails.

## Evidence is durable, bounded and visible

The canonical Incident record binds:

- detection source and time, classifications, fingerprint and affected owners;
- process/service adoption, anomaly, trigger, containment and every timer transition;
- sealed snapshot and evidence references;
- every probe request, authorisation, result and bound;
- Recovery Engagement, model, effort, Harness, prompt/schema and usage identity;
- Remedy request, deterministic authorisation or rejection, execution and observed delta;
- termination request/result, descendant reconciliation and cleanup outcome;
- probation checks, recurrence, child/successor links and final outcome; and
- process ownership through Run, Boot, Lane, Attempt, Engagement, Turn and Step where applicable.

Raw tool bodies, hostile bytes and high-rate telemetry remain sealed or quarantined under
ADR-0045. The Run capsule retains compact lifecycle facts and only selected Evidence artifacts.
An open Incident pins its dependent evidence beyond ordinary retention; the trusted governor
records any later eviction.

The Observer CLI receives sanitized typed notices: severity, scope, affected work, Incident state,
elapsed deadlines, selected action and outcome. It remains read-only in scored mode and Recovery
never waits for a human response.

## Practice repairs images; scored Recovery does not

The practice Recovery Controller may give a repair model labelled, digested, quarantined evidence
in a disposable checkout. The model proposes a patch but owns no production checkout, Docker
socket, credential, build, launch, rollback, GitHub or promotion authority. Deterministic brokers
run the repository checks, build in isolation, execute the adversarial fault suite, identify the
candidate image by digest, launch it as a probationary Boot and roll back to the last proved image
after safely closing and reconciling the candidate. A durable repair still enters the repository
through its issue, checks and pull request.

The scored image cannot patch source, build, launch a sibling, restart the container or replace
itself. Written organiser authority and a later v3 decision are required to widen that boundary.

## The Gate attacks every boundary

The local Board and practice rig inject every combination needed to exercise the three scopes and
the process, resource, state, authority, Board/Target, inference, security and host planes. The
matrix includes owner death and stalls, declared long Runs and commands, escaped descendants,
resource leaks, corrupt and missing state, every crash-durable reservation boundary, Board-read
failure, Target failure, Instance deployment/expiry/termination failure, indeterminate submissions,
Lease ambiguity, Candidate-provenance ambiguity, missing selected solve evidence, native- and
CPA-local faults, shared outage, Quota wait/reset outcomes, probation failure, candidate-image
repair and rollback.

The Gate proves the `15 / 60 / 120 / 180` deadlines, exact fingerprint coalescing, no unchanged
retry, no duplicate effect, bounded/redacted evidence, healthy-Lane continuity for local faults,
global effect freeze only when shared authority requires it, deterministic child-Incident handling,
successful rollback and one honest final outcome for every Incident. Happy-path Runs establish none
of these properties.

This resolves planning only. The current v1 process remains PID 1 and implements none of this
contract. Production records, governor, Supervisor, probe/remedy catalogue, recovery prompt,
practice controller, local fault injection and Gate follow the cleared map through `/to-spec` and
`/to-tickets`.
