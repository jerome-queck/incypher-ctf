# A practice solve qualifies by its evidence boundary

An accepted Flag on a public Challenge does not prove the Solver discovered the solution. The
v1 Gate contains a successful Flag fetched from a public writeup, and an exact Challenge can also
be recalled from model training after live retrieval is removed. v2 therefore qualifies practice
evidence by the corpus, isolation and Challenge-derived trace that produced it, never by the Board's
verdict alone. Training-data recall remains an explicit limit: the Gate can resist and expose
retrieval contamination; it cannot prove recall absent.

This resolves [How we know a practice solve is a solve, when the writeups are public and the Solver
can search](https://github.com/jerome-queck/incypher-ctf/issues/166).

## Three corpus roles

Every practice Challenge has exactly one role in an Evaluator-owned manifest:

| Role | Required difference from public material | What it may prove |
|---|---|---|
| **Known-answer regression** | None. Exact copies, re-flagged copies, and any variant whose public exploit still works unchanged belong here. | Deterministic startup, lifecycle, tool, submission, timing and resource regressions; approximate historical calibration. Never discovery solve rate. |
| **Execution holdout** | Fresh secrets or constants make the unchanged reference exploit fail; an adapted exploit must produce target evidence. | Execution, adaptation and Recovery, reported separately from discovery. |
| **Discovery holdout** | A private derived or original Challenge changes solution-critical structure; its fresh generator, oracle and reference exploit are unreachable; the old exploit fails. | Discovery solve rate, subject to the qualification boundary below. |

Changing only a Flag changes none of the solution path and is a Known-answer regression. An accepted Flag
does not promote one role into another. The manifest is frozen before a Run, and the Solver is not
told a Challenge's role, expected tools, solution, reference exploit or oracle.

## The corpus is small and coverage-driven

The local Board is a minimum-cover instrument, not a challenge archive. Its manifest covers the
officially evidenced web, pwn, crypto, reversing and forensics Categories while keeping Category an
open Board string; low and high organiser difficulty in each, with a middle Challenge only where it
adds a distinct capability; static handouts, isolated web Targets and isolated raw-TCP Targets; and
representative integrated tool families. One Challenge may cover several cells. Selection stops
when the cells and necessary redundancy are covered, not at an arbitrary large count.

Every candidate names its official source repository and commit, licence or private-use boundary,
artifact hashes, organiser difficulty, historical Tier separately from difficulty, corpus role,
delivery and transport, target and scenario digests, expected capability families, external
dependencies, reset, private oracle, reference result, unchanged-exploit result and covered cells.
Only complete local handouts or reproducible organiser source qualify. Public facts, OSINT, CAPTCHA,
Discord, live third-party APIs, external bots and other web-dependent answers are excluded from the
offline Gate. They may run in a search-enabled rehearsal whose solves are never added to the
offline denominator.

Tool probes and Challenges answer different questions. Deterministic image probes prove every
declared tool is installed and starts. Challenges prove that the Solver selects tools productively:
the Gate reports Challenge-derived solve, tool sequence, Checkpoints, failed or irrelevant calls, wall
time, Steps, tokens and resources. No prescribed tool is mandatory when a better path solves the
Challenge.

## The practice rig is a separate product

The Board, Challenge sources, target images, scenarios, generators, oracles, solutions and
containment tests live in a sibling practice-rig repository, not this repository and not the Solver
image. This repository keeps the Solver, Gate contract and exact practice-rig commit plus target and
scenario digests. Neither repository is mounted during a Run; promoted audit evidence lives outside
Solver-writable storage.

The rig's **Evaluator** owns the frozen manifest, private oracles, isolation policy, network evidence
and Gate receipts outside both Solver and Target write authority. It observes practice; it is never
part of a scored Run, never solves a Challenge and never lends its authority to the Observer CLI.

The rig follows chall-manager's real architecture: the plugin is installed in CTFd; chall-manager,
its janitor, registry and data/control services are separate; and a `dynamic_iac` Challenge points at
an OCI scenario which deploys its target image or images and returns their connection information.
A single-container Target behind the generic scenario is the default. A multi-container Challenge
is admitted only when it uniquely covers a necessary capability or failure path.

The old CTF workspace is an input to curation, never a mount or wholesale seed. Each selected
handout or Target source is copied explicitly from a pinned organiser or workspace revision,
re-built and digest-pinned. Writeups, findings, Flags, solutions, reference corpora, caches and prior
Run state do not cross into the Solver-visible corpus. Evaluator-only generators, solutions and
oracles remain on the other side of the runtime boundary.

No old-workspace file seeds the Board or control infrastructure. The rig builds that infrastructure
from pinned upstream CTFd and chall-manager sources plus new rig-owned configuration and tests. The
old workspace may inform the decision through its recorded evidence; it does not donate platform,
sandbox, agent, RAG, tool-install or orchestration code.

## The offline Gate is retrieval-sealed

The same immutable v2 image runs practice and scored work. The offline Gate's Board profile disables
the vendor web-search tool, and deployment policy blocks arbitrary shell egress and public DNS.
Scored Board profiles retain legitimate web research where their rules permit it. A separate
search-enabled practice rehearsal tests that path but produces no Gate-qualified discovery evidence.

For that rehearsal, the inference broker records each vendor search query, result URL, retrieval
time, provider event identity and returned-content digest outside the Attempt executor's write authority.
The Evaluator retains enough bounded content to classify direct writeups, known answer repositories
and exposed Flags against the frozen manifest. A result that cannot be retained and classified makes
the solve Unqualified; a legitimate reference remains `external-retrieval` and visible in the
rehearsal report. Scored web research remains governed by its Board profile and is never represented
as offline Gate evidence.

The trusted Run controller and hostile Attempt executor have separately enforced process and network
identities. A Board broker gives only the Run controller the Board operations it owns. An
Attempt-scoped Target proxy gives the Attempt executor only the currently assigned Target and binds
every returned byte to that Attempt. A constrained inference broker exposes the inference route
without becoming a general HTTP tunnel. Withholding a URL or credential in the environment is not
an access boundary.

Every offline Run receives fresh Run state and fresh Codex session, history, cache and configuration
state. Those may survive later Boots of that same Run and no further. Authentication is a separate
read-only capability or broker which carries no earlier Run's work. The hostile Attempt executor sees no
repository, historical Run, other Challenge working directory, solution material, Docker socket,
host filesystem, registry, database or chall-manager control API. A Target may answer traffic through
its Attempt proxy; it cannot initiate or reach the Attempt executor's control interfaces, inference route,
host, Board control plane, sibling Targets or public internet.

The solving model and untrusted Challenge code are both hostile actors. Prompts enforce nothing.
Container, filesystem, identity and network boundaries do: no privileged, host-network, host-PID or
host-IPC mode; no repository or Docker-socket mount; read-only roots with narrowly scoped writable
storage; separate identities and volumes; capabilities dropped by default and individually justified
where tooling needs one; and bounded CPU, memory, processes, disk and time.

## Qualification, audit and failure

A **Gate-qualified solve** is an accepted Flag with an Evaluator-attested Target response or an
Evaluator-replayed deterministic derivation, a frozen Discovery holdout role, fresh state, and a
complete passing Isolation receipt.
A **Retrieval-contaminated solve** observed answer-bearing external or prior material. An
**Unqualified solve** lacks a complete provenance or isolation claim. Neither of the latter two
enters discovery solve rate.

Every practice Run begins with probes from both hostile positions and the trusted Run controller.
The Run controller must reach its Board broker; the Attempt executor must reach its fresh workspace,
assigned Target proxy and constrained inference broker. From the Attempt executor, direct Board
access, public DNS and IP,
host gateway, other Targets, control services, Docker socket, repositories, solution material and
old state must fail. From a Target, only responses through declared Challenge communication work;
new connections to the Attempt executor, other Targets, control plane, host, inference route and public
internet fail. Probe unavailability or an unexpected success is a pre-Run Refusal.

The lightweight preflight and a post-Run audit run every time. Denied and allowed network flows are
recorded continuously. The full adversarial suite runs for every release candidate and whenever a
Solver, Target, scenario or practice-rig digest, network or mount policy, capability set, Docker or
Colima version changes. Its receipt binds those exact values, the fresh-state identity and every
probe result. The Evaluator collects flows outside the authority of the Attempt executor and Target, writes a monotonic
content-addressed record, and integrity-binds its final receipt with a key neither hostile actor can
read. Command text, destination labels and self-reported Observations from the Attempt executor are Claims until joined
to that record.

A denied retrieval attempt during solving is recorded as `retrieval-attempted`: the boundary held,
so it does not contaminate the solve, but it counts against tool effectiveness. Any protected bytes
returned make the affected evidence Unqualified and fail the whole Gate Run because the breach's
scope is unknown. An inconclusive audit fails closed in the same way; it never silently removes a
Challenge from the denominator.

The Evaluator's canonical practice record carries the evidence policy, manifest and rig digests,
Isolation receipt and corpus role beside each Challenge. It joins Steps from the Attempt executor to proxy-observed
destination, allow/deny outcome, Target identity and returned bytes rather than trusting command
text. Flag evidence names the attested Target response or Evaluator-replayed deterministic
derivation it depends on. The Observer consumes these receipts; it does not infer them from
Solver-authored labels. Gate queries report Known-answer regression, Execution holdout and Discovery
holdout separately; only qualified Discovery holdouts form discovery solve rate, and every frozen
unsolved holdout remains in its denominator. Every accepted but Retrieval-contaminated or
Unqualified Flag remains visible with its corpus role, exclusion reason and count; an excluded
Discovery holdout stays in the denominator as unsolved.

## Consequences

- [The local board: what it replicates, and what it must not](https://github.com/jerome-queck/incypher-ctf/issues/158)
  chooses the exact minimum-cover corpus and builds the separate rig under this contract.
- [The Worker, control, and credential trust boundary](https://github.com/jerome-queck/incypher-ctf/issues/190)
  must make the filesystem, identity and inference-route boundary enforceable by the production image.
- [What evidence makes v2 ready to freeze](https://github.com/jerome-queck/incypher-ctf/issues/196)
  chooses thresholds and Gate aggregation without merging the three corpus roles.
- Official Board compatibility and web-dependent solving remain later evidence. Local isolation can
  prove the v2 mechanism, never what the unreleased IN-CYPHER environment will permit or expose.

## Revisit when

- The selected inference route cannot expose an independently recordable vendor-search event stream.
- The pinned runtime cannot enforce separate identities for the Run controller and Attempt executor.
- Official rules or infrastructure change the scored Run's web-search or isolation boundary.
- Evidence shows a corpus role, qualification verdict or minimum-cover cell predicts the wrong thing.
