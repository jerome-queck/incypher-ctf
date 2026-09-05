# A version is a capability set and its gate, never a date

> **Superseded by [ADR-0030](0030-the-roadmap-is-four-versions-and-the-practice-board-is-one-we-build.md),
> then [ADR-0033](0033-v2-is-the-complete-pre-final-solver-and-v3-is-only-the-official-delta.md).**
> The thesis in this record stands. **The roadmap table below is historical** — four of its five
> gates became unreachable as written and v2's venue binding no longer exists. ADR-0033 carries
> the live table and the mapping: old v2 and old v3 resolve to v2; old v4 and old v5 resolve to v3.
> Read this record for the reasoning and ADR-0033 for what is being built.

> **Superseded in part by [ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md).**
> The roadmap, the gates and the venue bindings below all stand. Only the Consequence that the ADK
> "integrates behind the same seam the Board sits behind" is overtaken: there are two seams, and the
> ADK sits behind the one that reaches a Challenge's service, never the one that talks to the Board.

> **Amended by [ADR-0026](0026-a-gate-clause-with-no-venue-is-pending-and-the-gate-closes-without-it.md).**
> The two verdicts below — a gate passes, or it fails and re-scopes the next version — gain a third.
> A clause no binding of its version could produce evidence for is **Pending**: the gate closes
> without it, the first Board that can prove it discharges it, and it expires at v5's gate. v1's
> gate is closed on exactly that basis, with its Instance clause Pending.
Five versions stand between an empty repository and a container that must solve capture-the-flag
challenges for five and a half hours with nobody watching. The obvious way to write that down is a
calendar — v1 this weekend, v2 the next — and the calendar is the part that broke first. The plan
this record replaces named **v1 · Brunner 22–23 Aug**, and on 22 August the Solver did not exist
while the board it was to be judged on had thirty-four hours left to run.

The decision: **a version is a capability set and the gate that closes it.** A date and a venue are
*bindings* to a version, re-bound whenever the world moves, and re-binding one is not a change to
the roadmap. What just happened is a rebind, and this record is written so the next one costs
nothing to absorb.

## What a gate is

A gate is a scheduled review with teeth, and it does two things.

**It reopens the prior version's decisions** — continue, change, or remove, feature by feature. v1
is a beta, not a foundation; its decisions are provisional by construction rather than by accident,
which is why [ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md) records the v1 → v2
gate in its own *Revisit when*.

**It does not block.** A failed gate never pauses the calendar, because the calendar is fixed by a
competition that will not wait: it re-scopes the *next* version's capability set, and the re-scope
is written down. The single exception is **v5's gate, which is a real go/no-go**, and even there the
only live decision is what to freeze.

A gate that could halt the schedule would halt it, once, on the first failure — and then every
version behind it would be late for a reason nobody chose. Diagnostic gates spend the same
information without spending the dates.

## The roadmap

| | Capability set | Gate | Bound to |
|---|---|---|---|
| **v1** | Intake, Triage, ReAct solve loop, flag verification, submission with rate-limit discipline, Instance deploy/terminate within an Attempt, telemetry, clean termination on an externally-derived stall signal | One unattended run, zero keystrokes: re-read and triage a live Board, complete Attempts on **≥5 distinct Challenges** including **≥1 Instance it deployed and terminated itself**, cut and requeue without deadlocking, submit **≥1 correct Flag**, terminate cleanly — the Instance clause is **Pending** ([ADR-0026](0026-a-gate-clause-with-no-venue-is-pending-and-the-gate-closes-without-it.md)) | BrunnerCTF Global if ready by 23 Aug 20:00 SGT, else COMPFEST 29–30 Aug |
| **v2** | PID-1 supervision, crash-restart, endurance, per-Challenge timeboxes, the anti-derailment suite, mana-aware Instance lifecycle | Survives a mid-run crash; completes **5.5h unattended**; never deadlocks on mana | A local CTFd fixture seeded from the real Brunner Board |
| **v3** | Reasoning/execution concurrency, Category tool inventory, model roster and routing, eval harness, scoreboard-driven Triage | Clears an easy Board fast under a hard deadline | PatriotCTF 11–13 Sep |
| **v4** | ADK behind the platform-adapter seam, PoW gate, healthcare-scenario knowledge, held-out eval | Solves real IN-CYPHER Challenges | IN-CYPHER online batch, opens 14 Sep |
| **v5** | Adapt day 1, freeze, go/no-go checklist, deadline-awareness from an absolute timestamp, final credential posture | Frozen, crash-proof, autonomous — **the only true go/no-go** | On-site, 21–22 Sep |

**v3 and v4 collide by one day** and this is known rather than overlooked: PatriotCTF runs 11–13
September and the IN-CYPHER batch opens on the 14th. v3 is therefore the version that loses its
venue if anything upstream slips, and its fallback binding is the IN-CYPHER practice arena.

### Why the venues are what they are

Eight practice events sit in the window. **Two of them run CTFd** — COMPFEST (board at
`mirror-ctf.compfest.id`, not the URL CTFtime lists) and PatriotCTF. ASIS Quals is a bespoke Laravel
platform, NNS is rCTF behind a SvelteKit front end, BlackHat MEA is FlagYard, and TFC and K17 had no
board deployed at all when this was written. So the practice calendar for a CTFd Solver is two
weekends, which is the reason gates were decoupled from event weekends in the first place: a gate
you can only attempt on a Saturday is a gate you get four attempts at before the competition.

**v2 gates on a local fixture rather than an event** because its gate is survivability, not solving —
it needs N Challenges and uptime, not real Flags. CTFd runs standalone on SQLite with no Docker, no
MySQL and no Redis, and seeds hundreds of Challenges in seconds, so the fixture is hours of work and
is available on a Tuesday. Seeding it from the Brunner Board the Solver has already enumerated buys
a realistic Category mix and real descriptions for free.

**v1 gates on a real Board** because the two things it must prove — that a Board's own rate limits
and Flag grading behave as expected, and that an Instance can be deployed and torn down — are
exactly what a fixture would fake.

## Consequences

- **Slipping a version costs its binding, not its identity.** v1 did not become "late" on 22 August;
  its venue binding moved. Nothing downstream re-plans.
- **The 5.5-hour clause belongs to v2, not v1.** It is the competition's condition, not a
  development gate, and v1 has no crash-restart for it to exercise. Attaching it to v1 would make
  every gate attempt cost 5.5 hours of wall clock to learn something v1 was not built to do.
- **Deadline-awareness is a v1/v2 cross-cut, not a v5 line item.** On a fixed unattended window,
  remaining wall-clock is the scarce resource that ordering, Attempt budgets and the extension cap
  all consume. Building it last means building all three of them twice. This is a correction to the
  earlier plan, which held deadline-awareness until v5.
- **The ADK is an adapter, never a foundation.** It is released 14 September, eight days before the
  scored run. v1–v3 are built without it, v4 integrates it behind the same seam the Board sits
  behind, and **v5 must ship if it turns out to be unusable**. The fallback is thinner than it
  looks: a proof-of-work client for whatever scheme IN-CYPHER actually uses is new code, not an
  adaptation of an existing one.
- **v1's real cost is the two things with no prior art**: the Instance deploy/terminate path against
  `ctfd-chall-manager`, and the Step/Observation/Checkpoint records. ADR-0005's three counters, v1's
  Flag verification and its clean termination all sit on that substrate, and none of it can be
  lifted from anywhere. It is v1's spine and should be scheduled first.
- **Triage extracts; it does not predict.** Asking a model to estimate difficulty from a description
  is measured at near-zero correlation, and going coarse does not rescue it — a 3-tier task scored
  37.75% while mislabelling 83% of Hard problems as Easy. So a stated difficulty is *parsed* where a
  Board gives one, `solves` carries the ordering where it does not, and a model judges only what is
  left, with the file manifest in front of it, because artifacts beyond the description are the one
  condition under which that judgement beats noise.
- **Tier is an effort budget; Order is a separate sort.** They are computed from the same inputs and
  they weight them oppositely — Order attacks where the Solver is strongest first, to bank Flags
  early; Tier spends longest where it is weakest, because that is where budget changes an outcome.
  Merging them is the failure mode this names in order to prevent.

## What this record does not settle

The **stall thresholds** remain ADR-0005's, calibrated by shadow mode, and this roadmap does not
move them. **Whether the Solver is told its remaining wall-clock** stays open there too — pulling
deadline-awareness forward is a statement about the *orchestrator* holding the clock, not about
showing it to the model.

## Revisit when

- **Each gate**, by construction — that is what a gate is for.
- **A venue binding fails**, which costs a line in this table and nothing else.
- **14 September**, when the ADK lands and the adapter assumption above is either cheap or wrong.
