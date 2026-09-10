# One container separates control from hostile execution

> **Isolation-profile admission and failure are sharpened by
> [ADR-0056](0056-a-v2-run-requires-the-strict-hostile-execution-profile.md).** One exact strict
> profile is sealed for a candidate claiming v2; failure to initialise it or pass its allow/deny
> preflight is a Refusal. A separately selected broker-only partial is explicitly non-v2, never an
> automatic fallback or isolation proof.

[The Worker, control, and credential trust
boundary](https://github.com/jerome-queck/incypher-ctf/issues/190) decides how v2 can execute a
hostile solving model and hostile Challenge content without handing either the authority that runs
the Solver. The public rules strongly imply one top-level Solver container but do not yet state an
exact container count or runtime flags; the 14 September ADK and demo remain the binding recheck.
v2 nevertheless designs for one top-level container, with separately enforced identities inside it,
rather than depending on a sidecar or Docker authority the competition has not promised.

The decision: **the submitted container is a small trusted control system around separately
confined Attempt executors. Authority crosses that boundary only through typed, identity-bound
capabilities; credentials, canonical state, broad networking and host/build authority never do.**
Both the model and every byte a Challenge supplies remain hostile even after a trusted process has
recorded them.

## The process and authority graph

PID 1 is the **Supervisor**. It owns Boot and child lifecycle, signal classification, resource
ownership and the immutable environment snapshot settled by ADR-0040. It does not expose a shell
or raw credentials. Long-running duties use separate processes and UIDs:

| Principal | Authority it owns | Authority it never receives |
| --- | --- | --- |
| Supervisor | Boot lifecycle, process ownership, isolation-profile selection and typed recovery actions | General solving tools, model-authored commands, Board or inference use outside fixed brokers |
| Run controller | Order, Lane and Attempt coordination; capability issuance; typed Board, Target, inference and submission requests | Raw credentials, general shell, direct canonical-record writes |
| Board broker | CTFd token and Team key; Board reads, deployment connection setup and serial submission under controller policy | Model prompts, Challenge workdirs, inference credentials |
| Target proxy | One Attempt's current Target connection, already attributed to its Run/Lane/Attempt identity | Board operations, other Targets, general egress, Team-key disclosure |
| Research proxy | Board-profile-governed public-web access and its independently recorded provenance | Board, private/control networks, sibling Targets, arbitrary non-research tunnels |
| Codex Control | Native `CODEX_HOME`, App Server pipe, account/model/limit state and pre-authorised reset operations | Attempt filesystem or direct tool execution |
| CPA service | Its separate persistent OAuth store, local client capability and minimal Responses Harness | Native Codex auth/control, Board credentials, shared management or public listener |
| Record writer | Sequencing, redaction, hashing and append-only canonical records | Solving tools, Board effects, model-selected identity fields |
| Recovery | Bounded incident evidence and typed diagnostic/remedy requests | Secrets, raw control-state mutation, filesystem search, general shell or host authority |
| Attempt executor | One Challenge workdir, isolated temporary storage, bounded CTF tools and the capabilities issued to that Attempt | Every raw credential, canonical state, sibling workdir, Board/control API, restart, reset, route, Lease or submission authority |

Each active Attempt executor has a distinct UID, process group and resource domain. A general shell
and Challenge tools exist only there. Native Codex and CPA stay inside the same top-level container
but outside that hostile identity: model-requested tools cross a narrow tool protocol and execute
inside the Attempt executor. The credential-owning services never expose a general shell on the
model's behalf.

Every control channel is a pathname Unix socket on controller-only container storage. The receiver
authenticates the kernel peer identity and binds every handle to the Run, Boot, Lane, Attempt and,
where applicable, Step selected by trusted control. An executor cannot gain authority by supplying
those fields itself. Inherited file descriptors are closed unless the fixed isolation profile
names them.

Docker necessarily injects the initial environment into PID 1. At Boot, the Supervisor is therefore
the transient bootstrap custodian ADR-0040 names: before any hostile child starts, it validates the
whole snapshot, transfers each secret through a sealed channel to its owning broker, constructs the
trusted writer's non-exporting redaction capability, and clears the inherited secret environment.
After bootstrap, only the Board broker may use Board credentials, only Codex Control may use native
auth, and only CPA may use CPA auth. The Supervisor retains authority over those services, not a
second usable copy or a generic read-secret operation.

## Files are split by authority

v2 presents four storage domains whose exact inventory, retention and disk bounds remain with
[What persists in `/state`, and how it stays
bounded](https://github.com/jerome-queck/incypher-ctf/issues/175):

- **Canonical control and evidence** are durable and writable only through the Record writer.
  Controllers consume projections; executors submit bounded bytes and never open the record they
  are judged from.
- **Challenge work** is durable per Board and Challenge. The executor sees its one directory as
  `/work`, may create and change files freely there, and carries them across Turns, Attempts and
  later Boots of the same Run. It cannot traverse to the physical parent or another Challenge.
- **Credential stores** are persistent, broker-specific and absent from shared Run state. Codex and
  CPA never share an auth directory; Board credentials never enter either.
- **Runtime IPC and temporary storage** are non-durable, identity-scoped and recreated on Boot or
  Attempt boundaries. Attempt temporary files disappear with the Attempt.

The practice Evaluator's corpus, oracle, network evidence and Isolation receipt remain outside both
Solver and Target write authority. A model-writable workdir may carry useful evidence forward but
can never establish identity, ownership, submission or canonical history.

## Network access is capability-shaped

The Attempt executor receives no direct Board or control route. Its network surface is three
mediated capabilities: the current Target proxy, its selected inference route, and public research
allowed by the Board profile. Scored profiles may permit brokered web research while denying the
Board, private/control ranges and sibling Targets. The offline local Gate permits Target and
inference only; its separate search-enabled rehearsal enables and records research access without
turning the result into Gate-qualified discovery evidence.

For raw-TCP Targets, the Board broker spends the Team key and hands the Target proxy an established
connection; the executor never learns the key. For web Targets, the unguessable address is itself
a capability and remains inside the Attempt-scoped proxy. Target, research and inference services
are separate so that one allowed destination cannot become a general tunnel to another.

## The boundary is a proved profile, not a favourite mechanism

The first profile to test is rootless user, mount, PID and network namespaces combined with separate
UIDs, cgroups, seccomp or equivalent syscall policy, filesystem restriction and broker admission.
Namespaces answer different questions: filesystem visibility, process visibility and network
reachability; cgroups answer CPU, memory, PID and whole-tree teardown. No one mechanism substitutes
for the rights matrix above.

The current Colima measurements do not prove that nested namespaces work inside the ordinary Solver
container. If rootless creation is unavailable, a tiny one-shot launcher may receive only the
specific outer capabilities an actual-runtime prototype proves necessary. It creates the fixed
boundary, irrevocably drops every capability from the executor, closes unintended descriptors and
exits. Neither the executor nor any long-running process retains `SYS_ADMIN` or `NET_ADMIN`, and
the container never uses `--privileged`, host PID/network/IPC, a Docker socket or an unrestricted
host mount.

An alternative pretested profile is admissible only if the same allow/deny matrix and resource
teardown pass. Failure to initialise an admitted profile is a pre-Run Refusal; UID separation or a
process group alone is never a silent fallback.

Every command starts inside the owning Step's process group and Attempt resource domain. Descendants
are removed at the Step boundary unless the executor explicitly requests adoption through the
Supervisor. An adopted **Attempt service** keeps its attributed identity, sockets and resource
bounds, may outlive its spawning Step, and is removed at Attempt end. A child cannot adopt itself or
escape by creating a new session.

## Recovery and repair never inherit hostile authority

In-image Recovery receives bounded, read-only incident evidence and requests fixed probes or
remedies. Any command it needs runs in a separate confined diagnostic executor under a typed
profile; the Recovery model never acquires filesystem-search, secret or control-state authority.
[ADR-0046](0046-recovery-contains-the-smallest-safe-scope-and-changes-before-retrying.md) fixes
fingerprints, remedies, timing and probation within this ceiling.

Practice adds a host Recovery Controller outside the Solver. A repair model may inspect labelled,
digested, quarantined Challenge-derived evidence and propose a patch in a disposable checkout, but
it owns no Docker socket, production checkout, GitHub credential or promotion action. Separate
deterministic brokers build in isolation, run the required checks and adversarial Gate, launch a
probationary image, roll back, and deliver a persistent change through the repository's issue and
pull-request trail. Scored mode has no host launch, restart, rebuild or image-replacement capability
unless later written organiser authority creates one; prebuilt in-image Recovery remains the only
scored repair path.

Quarantine does not make content trusted. Deterministic control parses fixed schemas, verifies
lengths and identities, and never executes instructions found in prose, Artefacts, Target responses,
Observations or model output. Models that read those bytes possess proposal authority only; every
irreversible effect is re-authorised by deterministic state and a typed capability.

## Proof and handoff

Every Run performs a lightweight allow/deny preflight before spending Board or inference capacity.
Practice additionally attacks the boundary from hostile prose, Artefacts, executors and Targets.
The full suite runs for every release candidate and whenever the Solver image, isolation profile,
runtime, mount, network, capability or practice-rig digest changes. Unexpected access or
inconclusive evidence causes Refusal or Gate failure, never a reduced denominator.

The post-map specification must encode the principal/capability matrix, storage visibility,
network routes, process adoption, credential custody, profile negotiation and failure semantics.
ADR-0045, ADR-0039/0040, ADR-0042, ADR-0043, ADR-0044, ADR-0046 and ADR-0050 respectively narrow
retention, CPA proof, Agent routing, concurrency, Lease, Recovery and Observer behaviour; none
widens this authority ceiling.

This decision makes ADR-0034's practice boundary enforceable, supplies ADR-0032's Supervisor and
Recovery ceiling, and concretises ADR-0040's capability-not-credential rule. It does not claim that
the unreleased official runtime supports a particular kernel mechanism. The live organiser wording
and mechanism evidence are recorded in
[`IN-CYPHER container and nested-namespace boundary`](../research/2026-09-07-in-cypher-container-and-nested-namespace-boundary.md).
