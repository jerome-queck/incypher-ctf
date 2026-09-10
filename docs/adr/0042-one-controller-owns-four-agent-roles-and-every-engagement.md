# One controller owns four Agent roles and every Engagement

> **Route-primary selection and Pack admission are amended by
> [ADR-0051](0051-v2-has-a-finite-core-and-proof-earned-capability-packs.md).** Release-candidate
> qualification selects native or CPA as primary; Lead, Specialist, Triage and adaptive-routing
> support remain Core, while model-assisted novel Recovery is a Capability Pack.

[The v2 agent topology and routing
policy](https://github.com/jerome-queck/incypher-ctf/issues/187) closes the model topology around four
roles: **Triage Judge, Solve Lead, Specialist and Recovery Agent**. Everything that decides what may
happen remains deterministic. The Run controller admits, routes, interrupts and closes bounded
**Engagements**; Agents interpret evidence and propose work, Candidates or remedies but never grant
themselves authority.

The decision has four joined parts. **One persistent Solve Lead owns an Attempt's reasoning;
Specialists are controller-mediated children whose semantic brief comes from that Lead; Triage and
Recovery are fresh, bounded Engagements over exact evidence; and every prompt is a deterministic,
versioned contract around only the semantic fields an Agent is allowed to author.** The result
optimises verified Flags and wall-clock time first, then subscription usage and coordination cost,
without adding a permanent model watcher.

This ADR supersedes ADR-0014 and ADR-0023 only where they reject a persistent Codex thread, and the
fresh-Turn premises repeated by ADR-0009, ADR-0024 and ADR-0025. Their Attempt boundary, Turn,
workdir, evidence and deterministic-control decisions stand. ADR-0039's two Harnesses, ADR-0040's
one subscription and ADR-0041's authority ceiling also stand.

## Four roles, one deterministic owner

An **Engagement** is one controller-owned use of an Agent role. It binds the role, owner, parent,
context, capabilities, model, Harness, budget and deadline from admission through durable close,
and may contain one or more Turns.

| Role | Engagement boundary | What it may decide | What stays deterministic |
| --- | --- | --- | --- |
| Triage Judge | One exact Intake/Crowd snapshot batch | Ambiguous per-Challenge judgement, rationale and uncertainty | Snapshot admission, deterministic priors, applicability, replay and Tier/Order update |
| Solve Lead | One Attempt | Hypotheses, plan/status, next discriminating action, Candidate proposals and Specialist requests | Attempt ownership, deadline, capabilities, tool effects, Checkpoints, submission and Cut |
| Specialist | One Lead-authored investigation | Scoped Claims, Evidence artifact references, Candidate proposals and unresolved questions | Admission, capability ceiling, deadline, delivery, cancellation and acceptance |
| Recovery Agent | One incident | Failure classification and typed diagnostic or remedy proposals | Incident opening, evidence bundle, remedy authority, execution, probation and terminal Run decision |

Intake, Order, Board and Target operations, verification, submission, resource governance, record
writing and lifecycle ownership are functions or trusted principals, never Agent roles. A Candidate
becomes a Flag only through the serial Board authority; a model-authored derivation is a Claim even
when it cites strong visual, semantic, OSINT or multi-source Evidence artifacts.

Every credentialed operation is capability-mediated. An Agent may ask to deploy, connect, search or
submit through the typed surface it was issued; it never receives a Board token, Team key, inference
credential or raw secret. If future official evidence makes raw disclosure unavoidable, that is a
new trust-boundary decision rather than an implicit widening of this one.

The Solve Lead's initial context contains the exact Challenge and recon snapshot, Board rules,
durable carry and its one workdir. Its handles permit only the current Target, profile-governed
research, selected inference route and Candidate proposals; it cannot mutate the Board, read raw
credentials or write canonical state. Recovery receives bounded incident evidence and typed
diagnostic or remedy requests. It receives no general shell, secret, filesystem search or raw
control-state mutation; any admitted probe executes through the confined diagnostic boundary in
ADR-0041.

## The Lead persists; its children do not own the Attempt

One Solve Lead thread persists across the Turns of an Attempt, with one fixed selected model. An
early terminal Turn with budget remaining resumes that thread inside the same Attempt; Order is not
consulted, the Attempt deadline and stall state do not reset, and the controller supplies only a
bounded delta of new Observations, accepted Specialist reports, capability changes and remaining
time. The controller interrupts an active Turn only for a hard deadline, a safety or resource
boundary, superseded Target state or a classified failure.

A Boot failure ends the active Attempt as `crashed`; a later Boot opens a new Attempt and a fresh
Lead. A positively classified native-Harness-local failure is narrower: the controller may keep the
same Engagement and Attempt while opening a fresh CPA thread segment from durable context, but only
where the selected effective model remains available. The segment inherits the Attempt's deadline,
spend and stall state, never private native thread state. If the selected effective model cannot be
preserved, the Attempt closes `crashed`. An unclassified failure invokes Recovery without changing
route. Shared account
exhaustion enters Quota wait and changes neither route nor model.

Native Codex is the default Harness for every role. A Recovery Engagement for a positively
classified native-local incident starts on CPA so the diagnosis does not share the failed Harness.
After any other positively classified native-local failure, Triage, Specialist and Recovery may
continue their current Engagement through a fresh CPA thread segment only while their snapshot or
parent, deadline and selected effective model remain current. While that classified fault remains,
new Engagements with an available selected model start on CPA rather than reproving the same native
failure. They carry durable records, never private native context. If the model is unavailable,
Triage closes without an applicable judgement, Specialist closes failed and hands control back to
the Lead, and Recovery hands the unresolved incident to the deterministic escalation owned by its
policy. No role selects CPA for quota, capacity or an unclassified failure.

The Solve Lead may request a Specialist; it never spawns one. Its typed semantic brief states:

```text
goal
question_or_hypothesis
selected_evidence_artifact_refs[]
expected_critical_path_benefit
requested_tool_profile
suggested_approach?
success_evidence
stop_conditions[]
```

The controller admits, defers or rejects that Claim using separability, expected critical-path
saving, verification cost and current shared resources. It rejects missing Evidence artifacts, unavailable
tools, authority expansion, an over-broad goal or a deadline outside the parent Attempt. A rejected
brief may be refined. An admitted Specialist receives only the selected Evidence artifacts, profile,
capabilities and deadline; its conclusion remains a Claim and its source-identified outputs remain
Evidence artifacts. Its result may nominate a Checkpoint, but deterministic replay alone confirms
one. No Specialist implicitly replaces the Lead. Exact concurrent Attempt and
Specialist admission limits belong to [How many Attempts work at once, and who owns the working
set](https://github.com/jerome-queck/incypher-ctf/issues/188).

Attempt closure durably cancels every child Specialist and removes its effect authority. Fully
written, provenance-valid Evidence artifacts remain inspectable; a late conclusion is not injected into the
active Lead and cannot trigger a control effect. Later work may use it only after revalidation.

## Triage overlaps only when the choice is stable

Triage receives stated difficulty, Board metadata, manifest/prose and Crowd observations, never a
Challenge workdir, Target, shell or mutable Board capability. One model call batches every newly
seen Challenge that deterministic evidence cannot settle. A completed judgement is bound to exact
Intake, Crowd, policy, prompt-bundle, schema, requested/effective model and catalog digests, and is
durable before Tier or Order reads it. Later Boots replay it without another call.

Deterministic Intake, preparation and a Triage call may overlap. The first Lane starts before the
batch completes only where the controller proves the selected Challenge cannot change under any
admissible pending judgement. Otherwise it waits. Later ambiguous releases coalesce by snapshot;
active Attempts continue. An incomplete call has no applicable partial answer and may retry only
while its exact snapshot remains current. A stale result is recorded as superseded but never applied
or used to interrupt an Attempt.

## Prompts are prepared contracts with one bounded authored region

Primary OpenAI and CTF evidence is captured by [How each v2 model should be instructed for CTF
work](https://github.com/jerome-queck/incypher-ctf/issues/230) and [its reviewed research pull
request](https://github.com/jerome-queck/incypher-ctf/pull/231). It supports a versioned prompt
bundle, ordered from stable to volatile:

1. common authorization, authority, source-trust, evidence and terminal-state contract;
2. role objective, capabilities, result schema and checkable completion criteria;
3. effective-model overlay;
4. sharply triggered pointers to versioned Category, tool and task playbooks; and
5. typed live snapshot, Evidence artifact references, deadline, budget and delta.

The controller chooses and hashes every component, labels Challenge and retrieved content as
untrusted data, and keeps the stable prefix and tool schemas ahead of live content. Capability
brokers, not prompt prose, enforce authority. Triage Judge, initial Solve Lead and Recovery prompts
are deterministic. For a Specialist only, the Lead authors or refines the semantic brief above;
the deterministic builder validates it, selects the reviewed overlays and playbooks, and wraps the
immutable authority, provenance, capability, tool, budget and completion contract. There is no
runtime Prompt Agent and no reason for a live model to research how to prompt another.

The Lead's base prompt carries one sharply worded pointer to a versioned brief-authoring guide. The
pointer loads that prepared guidance only when the Lead considers delegation, so it can refine the
semantic fields without carrying the prompt research on every Turn or rediscovering it during the
Run.

Luna, Terra and Sol begin with the one published GPT-5.6-family overlay. Astra receives only its
documented behaviour corrections. Daybreak composes the authorization/refusal overlay with the
current Sol overlay while requested alias and effective model remain separate facts. The four
effective families remain available to evaluation even when one is absent from the current routing
menu. An alias or material model-identity change invalidates the affected overlay and tested cell;
unsupported per-model or per-effort advice remains absent rather than becoming folklore.

The current candidate routing cells from [Calibrate model, effort, and shared-capacity routing by
Agent role](https://github.com/jerome-queck/incypher-ctf/issues/224) remain candidates rather than
hard-coded topology: Luna/Daybreak for Triage, Daybreak/Terra for Leads, Luna/Daybreak plus a tiny
Astra screen for Specialists, and Luna/Daybreak for Recovery. Ultra remains excluded. The selected
model is fixed for a Lead's Attempt; effort changes only between Turns after a verified Checkpoint
or concrete blocker. An active Turn never silently downgrades.

## One envelope records every handoff

Every Engagement durably records:

- identity, role, controller owner and parent Engagement;
- Run, Boot, Lane, Attempt, snapshot or incident identity where applicable;
- context, Evidence artifact, prompt-bundle, playbook, tool-schema and capability digests;
- Harness, requested route, catalog and effective model or `unknown`, and requested/effective effort;
- admitted budget, deadline, start, Turns and controller-measured usage;
- outputs and Evidence artifact references, Checkpoint and Candidate provenance; and
- terminal outcome, cancellation, supersession, failure, refusal, route-change and retry reasons.

Role-specific result schemas sit inside that common envelope. Agents author semantic Claims;
trusted processes author identity, capability, timing, usage and lifecycle facts. A malformed or
partial result has no effect authority. The deterministic envelope classifies schema-invalid
results, tool failure or timeout, Agent deadline, Harness crash, cancellation, partial Triage and
model refusal before deciding what happens next.

No Engagement retries unchanged. A retry names a falsifiable delta: repaired tool or route, narrower
brief, new Evidence artifacts, current snapshot, corrected authorization context or policy-selected
model/effort. Shared quota waits; an unclassified failure opens Recovery. The controller is the only
authority that admits, reroutes, cancels, resumes, supersedes or closes an Engagement. Agent
requests are Claims; Observations, Checkpoints, deadlines, resource state, snapshot versions and
classified failures are the facts that policy may read.

## Evaluation chooses useful complexity

The local Board compares the persistent Lead alone against controller-mediated Specialists over
matched reproducible 5.5-hour Runs. Prompt development and held-out Gate Challenges are separate;
one prompt component, model, effort or topology changes per cell, with repetitions and stopping
rules declared before results are read.

Verified Flags lead the objective. Time to first and total Flag, Checkpoint conversion, wall time,
calls, cached and uncached usage, subscription-limit impact, startup delay, coordination and
verification cost, cancellations, refusals, failure/Recovery outcomes and injection attempts are
reported beside them. The prompt matrix covers Luna, Terra, Sol and Astra, plus Daybreak as a route,
and tests deterministic rendering, stable prefixes, source labels, hostile content, alias drift and
historical replay. A safety regression rejects a cell; a statistical tie selects the Lead-only
topology or smaller prompt. Numeric promotion thresholds belong to the v2 Gate rather than this
planning decision.

Production prompt templates, Engagement records and controller logic follow the cleared map through
`/to-spec` and `/to-tickets`. This ADR decides their contract; it does not implement them in v1.

## Consequences

- Persistent Lead context can preserve a productive line of reasoning, but it can also preserve a
  wrong one; external stall state, bounded deltas, hard Attempt deadlines and matched Lead-only
  evaluation remain load-bearing.
- Specialist breadth costs subscription capacity, process capacity and verification time even when
  its own answer is good. Admission therefore stays global and a tie removes the delegation path.
- Prompt guidance becomes versioned production material with provenance, digests and held-out tests,
  rather than prose hidden in one adapter or improvised during a Run.
- The v1 fresh-invocation implementation now differs deliberately from the v2 contract. Production
  changes wait for the cleared map's specification and tickets rather than landing piecemeal here.

## Revisit when

- Persistent Lead Runs solve fewer Flags, take materially longer or amplify stale hypotheses against
  the Lead-only or fresh-context comparator.
- A native or CPA Harness cannot preserve the declared Engagement identity, deterministic record or
  capability ceiling across a Turn boundary.
- Official model guidance, effective alias identity, CTF evidence or matched Runs establish a
  repeatable model-specific prompt delta.
- Written competition rules or the official ADK make capability-mediated credential use impossible;
  reopen the trust boundary explicitly before any raw value reaches an Agent.
