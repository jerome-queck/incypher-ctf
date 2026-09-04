# Autonomous runtime self-repair authority — 5 September 2026

## Question and standard

May a Recovery or Maintainer agent edit Solver source, build or replace the Solver image, or
otherwise repair a practice or scored Run without a human?

This note distinguishes four different claims:

- **expressly permitted** — the organisers name the action or its class;
- **compatible / not prohibited** — the published rules contain no contrary rule, but never name
  the action;
- **unresolved** — published wording supports competing readings;
- **penalised** — a published rule directly reaches the action.

The official rules span was re-fetched at **2026-09-05 00:14 SGT** with
[`scripts/check-rules-drift.sh`](../../scripts/check-rules-drift.sh); its normalized text was exactly
unchanged from the team's [verbatim official capture](../competitions/incypher-2026-hackathon.rules.txt).
Only the official [hackathon page][official], [agenda][agenda], [How to play][how-to-play], that
capture, and this repository's delivery contracts are used below.

## Result

**Practice-phase editing and rebuilding are expressly within the published build-and-tune phase;
replacing a practice execution with the rebuild is compatible but not named. Autonomous recovery
inside the submitted container during the scored Run is defensible. Arbitrary Solver source
patching is not specifically authorised, and building or replacing the container during the scored
Run is unresolved. Human repair during the scored Run is expressly penalised.**

The strongest scored-Run design is therefore one submitted container with a prebuilt in-image
supervisor and Recovery agent. It may diagnose immediately, replay durable state, restart a Worker,
switch among already-configured inference paths, back off, reconcile positively-owned resources,
and select prebuilt payloads or rollback modes. The current evidence does **not** justify treating a
host-side source editor, sibling Maintainer container, Docker-socket controller, or dynamically
built replacement image as organiser-approved.

## Official-rule reading

### Practice phase

The official surfaces expressly tell teams to change the agent before scoring:

- The [hackathon page][official] calls 14 September the online build phase and says to build and
  tune the agent remotely; it calls 21 September the adaptation phase.
- The [agenda][agenda] says 14–20 September is for developing and testing against live Challenges,
  then allocates 21 September to continued building.
- [How to play][how-to-play] says to build and tune against the first batch online and adapt the
  agent to the on-site setup on 21 September.

Therefore source edits, ordinary repository delivery, and image builds are **expressly within the
phase's purpose**. Replacing one practice execution with the rebuilt image is the compatible
operational consequence, but is not separately named. The human-intervention penalty is worded only
for the competition Run, so it does not prohibit human development during this phase. The pages do
not specifically discuss an already-running practice agent rewriting or rebuilding itself; that
narrower mechanism is merely **not prohibited**.

### Scored Run

The rules grant broad implementation freedom: the [official rules capture][rules-capture] says
there are no restrictions on how the autonomous agent is built and permits any LLM, tool, or
framework. That is express authority for an autonomous recovery mechanism as part of the agent.
The [agenda][agenda] also says agents continue running through lunch and tea, supporting an
unattended supervisor as part of the intended operating shape.

But two other sentences fix a boundary: the deliverable is a Docker container, and on competition
day the agent runs **inside that container** ([official page][official]; [capture][rules-capture]).
The sources never mention:

- modifying the submitted Solver's source after scoring begins;
- granting it access to the host checkout or Docker daemon;
- building a second image during the Run;
- replacing the submitted container;
- a host-side or sibling-container Maintainer counting as part of the submitted agent.

Consequently:

| Action during the scored Run | Published authority |
| --- | --- |
| In-image Worker restart, state replay, failover, backoff, reconciliation, or selection among prebuilt remedies | **Compatible and strongly defensible.** It is autonomous, uses permitted tools/frameworks, and remains inside the submitted container. The rules do not name self-repair specifically. |
| In-image edit of the copied Solver source, followed by a Worker respawn | **Not prohibited, not expressly permitted.** It remains in the container, but changes the submitted program after scoring begins and has no specific organiser ruling. |
| Build a new image or replace the running container autonomously | **Unresolved.** It is non-human automation, but appears to cross the statement that the agent runs inside the submitted container. |
| Host-side or sibling-container Maintainer | **Unresolved for the same reason.** The official pages do not define the execution environment or whether several cooperating containers count as the submitted agent. |
| Human edits, rebuilds, restarts, or repair | **Penalised.** The rule reaches any human intervention during the competition Run; it does not create a repair exception. |

“No restrictions on how” is broad implementation freedom, but it is not a specific answer to the
container-custody question. Likewise, absence of a self-repair ban is not affirmative permission to
replace the scored artifact. Full scoring remains unannounced in the [official rules
capture][rules-capture], so a later scoring or runtime rule could narrow this conclusion.

## Repository delivery contract and current capability

The repository currently chooses a narrower, provenance-preserving boundary than the official
rules require:

- Every code change starts with an issue, reaches an open pull request, never goes directly to
  `main`, and stops on red checks ([`CONTRIBUTING.md`][contributing]). This is the durable path for a
  practice-discovered repair.
- A core improvement found during practice goes to a separate pull request to `main`
  ([ADR-0008][adr-0008-practice]).
- Everything needed at Run start is baked into one image; `/state` is for produced state, not a code
  source; gate Runs use no checkout/source mount ([ADR-0008][adr-0008-image]).
- The Solver does not invoke Git or commit mid-Run; evidence promotion happens after the container
  is dead ([ADR-0009][adr-0009]). `git` exists for Challenge work, but an AST test still prevents
  Solver modules from invoking it ([ADR-0024][adr-0024]).
- The declared run command mounts only `state` ([`AGENTS.md`][agents-run]). The image copies
  `solver/` and Board profiles into `/opt/solver`, then launches Python; it declares no host source
  mount or Docker socket ([`Dockerfile`][dockerfile]). Thus the shipped shape has no declared path
  to build or replace its own image.

There is an important capability/policy distinction. Today the model runs as root with
`danger-full-access`, and the container is the only sandbox ([ADR-0018][adr-0018]). Therefore a
process can technically mutate the copied files in `/opt/solver`; the filesystem does not enforce
the repository's intended one-way `/state` discipline. Such a patch is nevertheless ephemeral,
not reviewable, absent from `main`, lost when the container is replaced, and indistinguishable at
the filesystem-authority level from hostile Challenge code modifying the orchestrator.

Exposing the host checkout or Docker socket to make self-replacement possible would also collapse
the boundary ADR-0018 relies on: Challenge-supplied code currently shares the model's root authority
inside the container. This is not a small implementation detail; it requires a new isolation and
delivery decision before it can be considered safe.

## Decision consequence

For v2, separate **fast autonomous recovery** from **artifact mutation**:

1. Start diagnosis on the first qualifying failure; do not wait through minutes of blind retries.
2. Keep ordinary and probationary recovery inside the submitted container and use prebuilt,
   independently testable remedies.
3. During the practice phase, let a Maintainer agent propose persistent fixes through the normal
   issue/PR/check/build path and start a fresh gate Run from the resulting image.
4. Do not make arbitrary Solver source edits, Docker-socket access, a sibling Maintainer, or image
   replacement part of the scored-Run guarantee unless organisers answer that exact question in
   writing and the repository records a new isolation/provenance contract.
5. No architecture can promise that every unknown failure is repairable. “Never wait silently” is
   enforceable; “never fail” is not. The final autonomous response may keep diagnosing and preserve
   evidence, but it cannot convert an unproved repair into a safe Worker restart.

## Exact organiser question still owed

> During the scored Run, may the submitted Docker container autonomously modify its own Solver
> code, access the host Docker daemon, build a replacement image, or replace/restart itself under an
> autonomous supervisor? If several containers cooperate without human input, do they collectively
> count as the submitted agent?

Until answered, **in-container prebuilt recovery is the rule-safe line; dynamic image replacement
is an unresolved contingency, not an authorised fallback.** Re-run the drift check when scoring
details or the Starter Pack land.

[official]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/
[agenda]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/hackathon-agenda/
[how-to-play]: https://hackathon.in-cypher.com/how-to-play
[rules-capture]: ../competitions/incypher-2026-hackathon.rules.txt#L14-L23
[contributing]: ../../CONTRIBUTING.md#L26-L72
[adr-0008-practice]: ../adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md#L44-L62
[adr-0008-image]: ../adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md#L105-L141
[adr-0009]: ../adr/0009-store-what-was-observed-derive-every-judgement.md#L198-L214
[adr-0024]: ../adr/0024-the-image-carries-what-a-run-reached-for-and-a-picture-is-attached.md#L76-L89
[agents-run]: ../../AGENTS.md#L22-L23
[dockerfile]: ../../Dockerfile#L295-L313
[adr-0018]: ../adr/0018-the-container-is-the-only-sandbox-v1-has.md#L1-L56
