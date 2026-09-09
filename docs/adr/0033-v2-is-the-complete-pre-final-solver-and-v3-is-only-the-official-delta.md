# v2 is the complete pre-final Solver, and v3 is only the official delta

> **Supersedes [ADR-0030](0030-the-roadmap-is-four-versions-and-the-practice-board-is-one-we-build.md).**
> ADR-0030's practice-board decision and historical mapping stand. Its forward split between v3
> integration and v4 freeze does not: both now belong to v3.
>
> **Amended by [ADR-0049](0049-one-passing-full-window-run-closes-v2-after-controlled-proofs.md).**
> Brunner ended before v2 and cannot supply fresh release-candidate evidence. Exhaustive controlled
> local proofs plus one passing 5.5-hour local-Board Run replace the stale required real-Board Run;
> official compatibility and the real-Instance clause remain v3/Pending.

The roadmap has exactly three Versions: landed v1, **v2: Complete pre-final Solver**, and
**v3: Official competition delta and freeze**. Map
[Wayfinder map: specify the complete pre-final Solver](https://github.com/jerome-queck/incypher-ctf/issues/153) decides v2
only. It must clear early enough for `/to-spec` → `/to-tickets`, implementation and the v2 Gate
before 14 September; v3 gets a separate map only after official evidence exists.

## The mapping

Historical records keep the version number true at the time they were written. Resolve them here
rather than rewriting them:

| When written | Historical name | Current name |
|---|---|---|
| Before ADR-0030 | old v2 or old v3 | **v2** |
| Before ADR-0030 | old v4 or old v5 | **v3** |
| From ADR-0030 until this record | v2 | **v2** |
| From ADR-0030 until this record | v3 or v4 | **v3** |

## v2: Complete pre-final Solver

**Everything specifiable and testable before the official release belongs to v2.** Time does not
cut its capability set, and unfinished v2 work is never renamed v3. v2 includes the complete tool
image; OpenAI-only inference and its proved secondary harness; productive agent topology and
routing; parallel-safe Lanes, Order, Leases and persistent state; bounded submission; safe Board
and Instance reads; restart, Recovery and practice self-repair;
[Gate-qualified practice evidence](0034-a-practice-solve-qualifies-by-its-evidence-boundary.md); the
local CTFd plus chall-manager Board; and every generic seam needed to accept later official input.
One Lane may remain enabled until evidence permits more, but the design is concurrent from birth.

Missing live facts narrow configuration, never architecture. v2 therefore ships generic `Board`
and `Target` seams, an open Category surface, a PoW boundary and the means to ingest an ADK. It does
not invent the organiser's ADK, PoW algorithm, unpublished rules or Challenge details.

v2 also provides a host-side **Observer CLI**. It derives Run, Boot, Lane, Attempt, Lease, fault,
Recovery, solve, timing, token/usage and resource views from canonical records, trusted Board reads
and Evaluator-attested practice receipts; it owns no parallel state. Scored mode is read-only.
Development and rehearsal may expose explicit control commands, but scored configuration refuses
them. Credentials, candidate Flags, model context and exploit material are redacted by default.

Recovery may make bounded same-image operational repairs where the rules permit. Practice also
rehearses diagnose → patch → build → test → probation → promote or rollback using a traceable
candidate image; it never mutates the running image in place. Scored image replacement remains
disabled unless later written organiser authority permits it.

### v2's Gate

One immutable image must pass the repository, image and tool probes; the local-Board lifecycle,
submission, concurrency, restart and Recovery proofs; the controlled suites and one passing
5.5-hour local-Board Run; and every threshold decided by
[What evidence makes v2 ready to freeze](https://github.com/jerome-queck/incypher-ctf/issues/196).
No known v2 criterion may remain incomplete. A green process exit is not a Gate.

A failed v2 Gate leaves v2 open and records why; it never moves a knowable capability into v3.
The calendar still advances, so official evidence may be captured concurrently after 14 September,
but capture is not a pass and the official delta cannot freeze on an incomplete v2 foundation.

The local Board must exercise deploy, trustworthy ledger reads, renewal, expiry, termination, mana
refusal, unreadable reads, restart reconciliation and parallel-safe Lease ownership. This proves
our mechanism, not compatibility with the organiser's deployment. v1's real-Instance clause stays
Pending until an official Board can prove it.

Rules, How-to-play, landing and API surfaces, ADK/demo links and other official inputs are checked
throughout v2 work, at each build-session start, at the 14 September release boundary, on
21 September morning and before the scored Run. A newly knowable, implementable fact discovered
before v2 passes becomes v2 input. Missing the date makes v2 late; it does not make v2 smaller.

## v3: Official competition delta and freeze

v3 admits only a change that cites the official artifact, live observation, organiser ruling,
first-batch fact or venue rehearsal that made it unknowable during v2. Its expected surface is the
real ADK and PoW, live `Board`/`Target`/rules/auth/scoring/submission differences, batch-specific
tools or healthcare knowledge, defects uniquely exposed by those facts, rule-permitted optional
human input or scored-image repair, day-2 adaptation, freeze and go/no-go. Ordinary features and
known defects remain v2.

The first official Run that can do so must deploy, solve, submit and terminate a real Instance,
discharging v1's Pending clause. Aim to prove that on 21 September. A 22 September morning test is
usable only if the organisers affirmatively provide the surface before the 10:30 SGT scored Run;
otherwise the clause reaches the final go/no-go as Pending. Automation is not permission: any
self-modification, external control or human-input path fails closed until written rules authorise
its exact authority.

Every v3 change names its trigger, official integration tests pass, every rule and authority item
is answered or consciously waived, the scored image and configuration are frozen, and no
unrecorded last-minute change enters. The later v3 map owns the exact Gate after the 14 September
capture; this record supplies its boundary, not guessed tickets.

## Consequences

- ADR-0026's Pending clause expires at v3's final go/no-go, the old v5 then v4 Gate under the new
  mapping. A local replica cannot discharge it.
- ADR-0030 remains the source for why the practice Board is one we build and external events are
  opportunities rather than Gates.
- `README.md`, `AGENTS.md`, `CONTEXT.md`, ADR-0006 and ADR-0026 point to this live roadmap.
  ADR-0030 and ADR-0031 receive supersession notes; their historical reasoning is not rewritten.
- Current code comments use v3 where they mean future analysis. `MAP.md` changes no term or layout.
- The 14 September ingest, day-2 adaptation, optional runtime human authority and scored-image
  replacement are scaffolds for the later v3 map, not fog that can keep the v2 map open.

## Revisit when

- v2's Gate reviews every prior decision.
- The official release lands on 14 September and the v3 map is charted from captured evidence.
- Written rules change the authority available to Recovery, an Observer or a human.
