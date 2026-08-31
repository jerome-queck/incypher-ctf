# The roadmap is four versions, and the practice board is one we build

> **Supersedes [ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md).**
> Its thesis stands and is restated here — a version is a capability set and its gate, a date and a
> venue are bindings, a gate does not block. Its *table* is replaced: four of five gates are now
> unreachable as written and one venue binding no longer exists. Read this record for the roadmap
> and ADR-0006 for the reasoning that produced the shape.

[ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md) planned five versions. v1 has landed
and its gate is closed, and this record merges two of the four that were left — so **the roadmap is
four versions and three of them are still ahead**. The plan for those three was written against a
practice calendar that has since been measured and found nearly empty. This record re-locks it and
makes two decisions ADR-0006 could not have made: **old v2 and old v3 merge**, because four of old
v2's six capabilities already had code in v1 — per-Challenge timeboxes, the anti-derailment suite,
crash-restart continuity, and a partial mana-aware Instance lifecycle — leaving only PID-1
supervision and `/state` bounding with none. **What shipped is not the same as what works**, which
is why three of those four reappear below as things v2's gate *measures* rather than assumes. The
second decision: **the venue v2 gates on is a board we build**, because the live calendar cannot
supply one.

## The renumber, and what it does not touch

Old v2 + old v3 become **v2**. Old v4 becomes **v3**. Old v5 becomes **v4**.

| Written before this record | Reads now |
|---|---|
| v2 (PID-1, crash-restart, endurance, timeboxes, anti-derailment, mana lifecycle) | **v2** |
| v3 (concurrency, tool inventory, roster, eval harness, scoreboard Triage) | **v2** |
| v4 (ADK, PoW, healthcare, held-out eval) | **v3** |
| v5 (adapt, freeze, go/no-go, deadline-awareness, credential posture) | **v4** |

**The ADRs are not rewritten.** A `v3` inside
[ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md),
[ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md),
[ADR-0009](0009-store-what-was-observed-derive-every-judgement.md),
[ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md) or
[ADR-0018](0018-the-container-is-the-only-sandbox-v1-has.md) is a record of what was decided *then*,
and editing it would make those records lie about their own moment. The table above is how a reader
resolves one. What *is* rewritten is prose describing work still to come — `README.md`, `AGENTS.md`,
`CONTEXT.md`, and the comments in `solver/` and `tests/` that name a version as the one that will
do something. Roughly fifteen edits rather than ninety-five, and no falsified record.

## The roadmap

| | Capability set | Gate | Bound to |
|---|---|---|---|
| **v1** | *(as shipped — see [ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md))* | **Closed**, with the Instance clause **Pending** ([ADR-0026](0026-a-gate-clause-with-no-venue-is-pending-and-the-gate-closes-without-it.md)) | — |
| **v2** | **The failsafes are right, and the solve rate holds up.** PID-1 supervision, `/state` bounding, the Attempt-boundary carry, a bounded submission path, reasoning/execution concurrency, per-Category tool inventory, the eval harness, and an Order that works on a board with no crowd | Five clauses, below | **BrunnerCTF Global** and **the local board** |
| **v3** | ADK behind `Target`, the PoW gate, healthcare-scenario knowledge, held-out eval | Three clauses, below | IN-CYPHER online batch, opens 14 Sep 10:00 SGT |
| **v4** | Adapt day 1, freeze, go/no-go checklist, deadline-awareness from an absolute timestamp, final credential posture | Every checklist item ticked or **consciously waived, with the waiver written down** — the checklist is v4's own deliverable, and the gate is that no item is silently unanswered. **The only true go/no-go** | On-site, 21–22 Sep |

### v2's gate, in a form that passes or fails

A 5.5-hour unattended Run on Brunner — the competition's own length, not an endurance stunt — plus
the Instance clauses on the local board, which Brunner structurally cannot ask.

1. **Zero intervention.** No keystroke, no restart by hand, no configuration touched mid-Run.
2. **No Instance leak and no mana deadlock**, exercised against the local board: deploy, renew,
   terminate, the Attempt-boundary sweep and a mana refusal, none of them an in-process fake.
3. **A cut Attempt records what it tried.** `approach_label` non-empty in **≥80% of cut Attempts**.
   Measured today at 5 of 40 — a cut kills the model before it writes the one field the next
   Attempt needs.
4. **The carry is bounded by construction.** No Attempt's carried context exceeds the ceiling
   declared in code. The ceiling's value belongs to the Attempt-boundary work; the gate asserts the
   bound holds, never a magic number.
5. **Solve rate is reviewed as a direction, not a threshold.** *Is Attempt 90 as good as Attempt 3?*
   — the first third of Attempts against the last third, recorded and read at the gate. It is
   deliberately not pass/fail: one venue yields too few samples for a threshold to mean anything,
   and a noisy fail would re-scope v3 for no reason.

Clauses 1–4 pass or fail. Clause 5 informs.

### v3's gate, in the same form

1. **The ADK is integrated behind `Target`**, not behind `Board` — ADR-0008's boundary, asserted by
   a Run that reaches a Challenge's service through it.
2. **The real PoW scheme is cleared on at least one Challenge.** Not a scheme we invented: the
   algorithm is unpublished until 14 September, and anything built before it is our own fiction.
3. **At least one Flag from an Instance the Solver deployed and terminated itself.** This is also
   what discharges v1's Pending clause, on the first Board that can ask it.

A clause here can still come back **Pending** if the batch turns out not to contain the Challenge
shape it needs — that mechanism is ADR-0026's and this gate inherits it unchanged.

## Why the practice board is one we build

This is the reversal. ADR-0006 bound v2 to *"a local CTFd fixture seeded from the real Brunner
Board"*, the wayfinder map then ruled that fixture out of scope in favour of Brunner's own board,
and this record puts a local board back — but a different and larger one, and for a reason neither
document had.

**The calendar was measured on 29 August 2026, and it is nearly empty.** Two read-only sweeps
covered 42 events running 30 August to 20 September: 23 probed, 19 disqualified on platform family
without probing (fourteen on Hack The Box, three on CyberTalents, two on FlagYard). **Three were
CTFd at all** — about 13% of what was probed. The rest are bespoke Next.js single-page apps, bespoke
Laravel, rCTF, and in-house Flask. Of the three, one is the COMPFEST mirror, which ran before the
window opened and now answers `/api/v1/challenges` with a Cloudflare Managed Challenge; one is CSAW,
whose board host does not resolve; and one is WATCHLIST, which is clean and is 19–20 September —
three days before the scored run, inside the week already committed to the ADK.

So practice scarcity is **structural, not bad luck**: expect roughly one CTFd-and-clean board per
three-week window. A roadmap that gates two versions on the calendar is a roadmap betting on a
coin-flip, twice.

**A board we build removes the bet, and buys three things the calendar cannot sell.** It runs
`ctfd-chall-manager`, which Brunner does not and never will, so the Instance path stops being
covered only by in-process fakes. It is available on a Tuesday. And it yields **reproducible
baselines** — which matters more than it sounds, because the measurements this roadmap's thresholds
are calibrated against are already gone: no COMPFEST Run was ever promoted to `runs/`, and the local
`state/runs/compfest-2026-seg2/` no longer exists on the machine that produced it.

**It is a replica, and a replica is our own dictionary wearing the Board's clothes.** That is
[ADR-0007](0007-truth-about-an-instance-lives-on-the-board.md)'s warning and
[ADR-0026](0026-a-gate-clause-with-no-venue-is-pending-and-the-gate-closes-without-it.md)'s, and it
is why Brunner is bound alongside it rather than replaced by it. Brunner is the control: a real
board, real grading, real rate limits, real files, on an account that has solved none of its 74
Challenges. What the local board proves is *mechanism*; what Brunner proves is that the mechanism
survives contact with something we did not write. **Neither the local board nor any rig can
discharge v1's Pending Instance clause** — ADR-0026 is explicit that a fake is exactly what the
clause exists to distrust, and only a Board can discharge it.

What the local board must replicate, what it must deliberately not, and what seeds it are
[#158](https://github.com/jerome-queck/incypher-ctf/issues/158)'s to settle. **What seeds it is in
better shape than this repository has been recording.** Measured 31 Aug 2026 against
`jerome-queck/ctf-workspace`: **398 challenges across nine competitions, 271 of them carrying a real
flag** in `meta.yml` — every one of the 110 empty ones is `status: stuck`, which is to say never
solved rather than never recorded. An earlier audit called that repository *"not a challenge-content
source"* on the strength of a sample that happened to be stuck challenges, and that claim is
withdrawn here so it stops being repeated. **Handouts are linked rather than committed** — 259 of
412 challenge READMEs carry a URL — so the open question for the rig is whether those links still
resolve now that the competitions are over, not whether content exists.

## External CTFs are opportunities, never gates

No version gates on an event we do not run. An external board is entered when the suitability check
clears it and the calendar allows, its Run is evidence like any other, and **it cannot fail a
version**. This retires ADR-0006's *"v3 is the version that loses its venue if anything upstream
slips"* outright: no version has a venue left to lose.

The suitability check is what the sweeps taught, and one rule of it is load-bearing enough to state
here: **a status code is not a platform fingerprint.** Nine hosts across the two sweeps answered
HTTP 200 on both `/api/v1/challenges` and `/api/v1/plugins/ctfd-chall-manager/mana` with a
single-page-app catch-all serving HTML. A status-only check reports all nine as CTFd with
chall-manager installed. Content-type, plus the body compared against the landing page, is the test.

**WATCHLIST (19–20 Sep, `ctf.xposedornot.com`) is the one board that clears every bar** — a CTFd
Cloud tenant with no edge screening on API paths at all, and AI permitted in writing on both its
rules and its FAQ. It carries one precondition and it is ours, not theirs: its rules make
*"automated brute-force at scale"* an immediate permanent ban, and one COMPFEST segment emitted 51
wrong Flags, 43 of them arriving `reproduced`, from single commands emitting many. **The bounded
submission path is a v2 capability for that reason** — not to enter WATCHLIST, but because a Solver
that can be mistaken for a brute-forcer is one venue-ban away from having no board at all.

## What is struck from the roadmap

- **Model roster and routing.** Discharged by the Codex-only decision: there has never been an
  Anthropic code path in the shipped image, the only inference seam is `codex exec --json`, and a
  Claude adapter is permanently off the roadmap. One vendor and one seam leave nothing to route.
- **Scoreboard-driven Triage.** Not deferred — *replaced*. It assumed a crowd. `solves` carries the
  ordering wherever a Board states no difficulty, and on a thin board every Challenge sits near zero
  solves, so the signal stops discriminating exactly when it is relied on. Order and Tier read the
  same inputs oppositely, so both degrade together. v2 carries an Order that works without a crowd
  instead.
- **The seeded local CTFd uptime fixture.** ADR-0006's own version of a local venue — CTFd on SQLite
  with no Docker — is superseded by the local board rather than revived. A fixture with no Docker
  cannot run chall-manager, which is the whole reason to have a board of our own.

## Consequences

- **ADR-0026's Pending clause now expires at v4's gate**, which is the same gate under a new number:
  the final go/no-go, on-site, 21–22 September. Its discharge rule is unchanged and its discharge is
  now *expected* rather than hoped for — `ctfd-chall-manager` is confirmed installed on the IN-CYPHER
  board, so v3's binding is the first that can prove the clause, and it will discharge there in the
  ordinary course rather than reaching the expiry.
- **A gate still does not block.** Three gates and twenty-two days is less slack than ADR-0006 had,
  which is an argument for the rule and not against it: a gate that could halt the schedule would
  halt it once, and every version behind it would be late for a reason nobody chose.
- **v2 is where the Flags currently being lost are recovered.** Survivability and solve rate are the
  same work here, because the Solver loses Flags to its own Attempt-boundary mechanics rather than
  to being outsolved — one Challenge took 5 Attempts and 44% of all attempt-minutes for none.
- **The Solver's Board adapter stays narrow on purpose.** Kaspersky{CTF} is CTFd underneath and
  still unreadable by us, because it is served under `/public-api` with session-cookie auth while
  our adapter's base path and auth scheme are constants. Generalising them would buy practice
  venues, not Flags, and IN-CYPHER is confirmed vanilla CTFd. The narrow adapter is right for the
  deliverable and is the reason practice is scarce; both are true.

## What this record does not settle

The **stall thresholds** remain [ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md)'s.
**What the local board replicates** is [#158](https://github.com/jerome-queck/incypher-ctf/issues/158).
**What a failed Attempt hands forward** is
[#155](https://github.com/jerome-queck/incypher-ctf/issues/155), and the carry ceiling clause 4
asserts is its number to choose. **Whether Triage's model judge earns its cost** still has no
evidence either way — it has never fired in any Run.

## Revisit when

- **Each gate**, by construction.
- **14 September**, when the ADK lands, the PoW scheme is published, and v3's binding is either
  cheap or wrong.
- **A Board the Solver enters is found to run chall-manager** — ADR-0026's trigger, unchanged.
