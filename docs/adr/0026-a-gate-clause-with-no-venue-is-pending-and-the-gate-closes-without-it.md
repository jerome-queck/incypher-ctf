# A gate clause with no venue is Pending, and the gate closes without it

> **Amended by [ADR-0033](0033-v2-is-the-complete-pre-final-solver-and-v3-is-only-the-official-delta.md).**
> The discharge rule below is unchanged. The local v2 Board proves our mechanism but cannot
> discharge the real-Board clause; Pending now expires at v3's final go/no-go, the old v5 then v4
> Gate under the current mapping.

> **The two `docs/competitions/compfest-2026.md` citations below now point at a file that is
> gone**, removed by [#171](https://github.com/jerome-queck/incypher-ctf/issues/171). COMPFEST is
> dead as a venue — a Cloudflare Managed Challenge on every path to every client shape, and the
> board closed 2026-08-31T00:00:00Z — and wayfinder map
> [#153](https://github.com/jerome-queck/incypher-ctf/issues/153) rules it out of scope, so its
> three tracked files came out of `docs/competitions/`. **Nothing in this record's reasoning
> depends on them.** The chall-manager question it credits that file with stating is stated in
> *this record's own sentence* — check the plugin's asset URL and whether any Challenge carries a
> `dynamic_iac` type — so the citation was attribution and never the only copy.
>
> Two things a later reader should know. The files are recoverable at **a1580d8**, the last
> commit that held them, which `git log --diff-filter=D -- docs/competitions/` also finds — and
> that matters beyond this record, because ADR-0027's premise that *COMPFEST names a wrong-Flag
> retry loop beside DDoS* is sourced from the deleted `compfest-2026.rules.txt` without citing its
> path. And the detection method above has since been overtaken: ADR-0030 records that **a status
> code is not a platform fingerprint**, because nine hosts answered 200 on both
> `/api/v1/challenges` and the chall-manager `mana` endpoint while serving HTML — content-type,
> and the body compared against the landing page, are the test.

**A gate clause that no binding of its version could produce evidence for is *Pending*: the gate
closes, the clause is carried, and it is discharged by the first Board that can prove it.** This
amends [ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md), which has two verdicts and
needed a third, and it answers
[#134](https://github.com/jerome-queck/incypher-ctf/issues/134). Applied immediately: **v1's gate
is closed with one clause Pending** — *"≥1 Instance it deployed and terminated itself"*.

The alternative was to move the clause to v2, which is what the ticket proposed and what the
evidence below rules out. It is argued against rather than dropped, because it is the obvious
answer and somebody will reach for it again.

## The clause was never under test

v1's bindings are *"BrunnerCTF Global if ready by 23 Aug 20:00 SGT, else COMPFEST 29–30 Aug"*, and
an Instance needs a Board running `ctfd-chall-manager`. Neither binding has one, and both were
checked rather than assumed:

- **Brunner runs no chall-manager.** Measured 26 August 2026 and again on the 28th: a detail GET
  carries no `shared`, `timeout`, `destroy_on_flag` or `mana_cost` key, and the only `type` values
  across all 74 Challenges are `dynamic` and `flightops` — neither is the instanced type.
  `docs/competitions/brunnerctf-2026-global.md` records it, and `scripts/ctfd_probe.py` reports
  `no dynamic_iac challenge on this board yet` against the live Board.
- **COMPFEST was unverified when v1 was tagged**, and `docs/competitions/compfest-2026.md` says so
  under a heading it already owns — *What this Board cannot prove*.

So the Solver did not fail this clause. **It was never asked it**, at either venue, because no
Board it was pointed at could ask. That is a different fact from every other clause in v1's gate,
and the record had no way to say it.

## Why not "failed"

[ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md) has exactly two outcomes: a gate
passes, or *"a failed gate re-scopes the next version's capability set, and the re-scope is written
down."* Calling this failed invokes that mechanism, and the mechanism has nowhere to put the clause.

**v2 cannot prove it either.** v2 gates on *"a local CTFd fixture seeded from the real Brunner
Board"*, built as *"CTFd standalone on SQLite with no Docker, no MySQL and no Redis"* — and
chall-manager provisions Instances through infrastructure-as-code. A fixture with no Docker cannot
run one. Re-scoping v2 to hold this clause would move it from a venue that cannot prove it to
another venue that cannot prove it, and ADR-0006 already says why that is worthless: v1 gates on a
real Board *"because the two things it must prove … are exactly what a fixture would fake."*

"Failed" also mis-describes what happened. v1 met every other clause and the evidence is committed
under `runs/`.

## Why not "passed"

Because the clause would disappear. A gate whose unmet clauses can be absorbed by calling the gate
passed is a gate that stops constraining anything, and the capability at stake is the one ADR-0006
itself names as *"v1's real cost … the two things with no prior art"*.

What makes silence tempting here is that the path is not untested: `tests/test_instance_path.py`
drives `Board` and `Instances` through a fake transport across roughly thirty tests — deploy, the
HTTP 200 that did not deploy, the three-armed 403, the per-team lock, renewal, liveness, terminate,
terminate-after-flag, the Attempt-boundary sweep and the Run-close sweep. **Tested against a fake
is exactly what the clause exists to distrust.** ADR-0007 puts the truth about an Instance on the
Board precisely because our own dictionary can be wrong, and a fake transport is our dictionary
wearing the Board's clothes.

## Pending

A clause is **Pending** when no binding of its version could produce evidence for it. It is not a
softer word for failed and it is not available for a clause that was testable and untested — a
venue that had a chall-manager and a Run that never reached it is a failure, and this record does
not cover it.

Two rules make Pending mean something rather than defer something:

**Discharge is Board-driven, not version-driven.** A Pending clause is proven by the first Board
the Solver enters that can prove it, whatever version is current. That is already what the venue
documents say — *"it is proven on the first board we enter that runs one"* — and it is the rule
that keeps evidence from being wasted: COMPFEST opening with a chall-manager discharges v1's clause
on the spot, and no ceremony is needed to let it. The alternative, attaching the clause to v4
because IN-CYPHER is the first binding *known* to run one
(`docs/competitions/incypher-2026-hackathon.md`: *"chall-manager is live"*), would leave a clause
sitting unproven while a Board in front of us could close it.

**Pending expires at v5's gate.** ADR-0006 makes v5's *"the only true go/no-go"*, and a clause
still Pending there is a capability we ship unproven. That is a decision someone must take with
their eyes open, which is what that gate is for. Without an expiry, Pending is how a clause quietly
stops being a clause.

## Consequences

- **v1's gate is closed**, and its verdict is a sentence rather than a word: every clause met and
  evidenced under `runs/`, with *"≥1 Instance it deployed and terminated itself"* Pending. The v1
  spec ([#63](https://github.com/jerome-queck/incypher-ctf/issues/63)) closes on that basis and
  `README.md` says so, because a gate moving is what that section tracks.
- **v2 is not re-scoped by this.** Its capability set already carries *"mana-aware Instance
  lifecycle"*, which is the concurrency work and not this clause. Nothing downstream moves.
- **The first authenticated read of any new Board now has a question to answer** — whether it runs
  chall-manager — and `docs/competitions/compfest-2026.md` already states how to ask it: check the
  plugin's asset URL and whether any Challenge carries a `dynamic_iac` type. A Board profile
  records `chall_manager` at every Run open, so the answer lands in the stream without anyone
  remembering to look.
- **A Pending clause is not a licence to stop building.** The Instance path ships in v1 complete
  and swept at every Attempt boundary; what is Pending is the proof, not the code.
- **This record is the precedent, not the exception.** Any version whose bindings cannot produce
  evidence for a clause now has a verdict to reach for, and the same two rules bound it.

## Revisit when

**A Board the Solver enters is found to run chall-manager.** That Run discharges the clause or
fails it for a reason the record can finally state, and either way this is the record that says
what happens next. If that Board is COMPFEST, it happens the day after this was written.
