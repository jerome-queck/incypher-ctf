# An Attempt holds the turn loop, and Order is not asked between turns

**When the vendor's agent ends a turn with budget left, the orchestrator re-invokes *inside the same
Attempt*. The re-invocation is a new Turn, not a new Attempt: Order is not consulted, no second
budget is granted, and `attempt_sequence` does not move.**

This supersedes [ADR-0014](0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)'s
section *An early stop is a new Attempt, not a give-up* — its mechanism and its refusal to coin a
concept, and those only. **Everything else in ADR-0014 stands**: the vendor's agent drives its own
loop, the workdir is the memory, `codex exec resume` is still rejected, and an early stop still must
not become the give-up button ADR-0005 removed. What changes is where the re-invocation lands.

## What was decided, and what runs

ADR-0014:

> the orchestrator **re-invokes**, and the re-invocation **is a new Attempt**: Order simply ranks the
> same Challenge first again and takes it back to back. **No new concept is coined for it.**

`solver/run.py`, between one `attempt_open` and its `attempt_close`:

```python
while not self._turn(held, challenge):
    self._renew(held)
```

The concept was coined anyway — `Turn` in `scripts/stream.py`. And the Runs settle which shape ran:
four Attempts across the four gate Runs hold more than one turn, and **`v1-gate/94-2` is one Attempt
holding eight** — seven that ended themselves and one killed at the deadline, with Order never asked
in between. No Run has ever executed the mechanism ADR-0014 decided.

## Why the loop wins, rather than the record

Two reasons, and neither is "the code is already written."

**Nothing would catch a repeated early stop.** `_Held` is per-Attempt and its `counted` starts at
zero, so under ADR-0014's mechanism a re-invocation gets a fresh `_Held` *and* a fresh `Watch` —
every stall counter resets, the step cliff included. A model that stops early forever would be
re-invoked forever, and each pass would look like the first. ADR-0014 says the opposite in its own
consequences:

> A re-invocation producing no new Checkpoint feeds the novelty counter rather than being a free
> retry, so repeated early stops trip `cut:novelty` on their own

That is false under the mechanism the same record decided, and it is the load-bearing claim: it is
why ADR-0014 concluded Way B needed no new Cut cause. Under the turn loop it is half-true — `Watch`
is seeded with the Attempt's model Steps, so the **step cliff** carries across turns and does the
catching. It is what ended every multi-turn Attempt in the gate Runs, `v1-gate/94-2` among them.

**Order would not have done what ADR-0014 assumed.**
[ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md) subtracts an anti-livelock
term, `w_spend × max(time_norm, attempts_norm)`, and `attempts_norm` is `spent.attempts /
attempts_full` — 6 by default, at weight 1.5. So "Order simply ranks the same Challenge first again"
holds for one or two re-invocations and then stops holding: by the sixth, the Challenge carries the
maximum spend penalty and Order ranks it *away*. ADR-0014 was written before ADR-0015 and assumed a
ranking function that no longer exists. The two records do not compose, and this is where they part.

Under the turn loop they compose cleanly: `seconds` carries the anti-livelock load for a long
trajectory, and `attempts` counts what Order itself did.

## What is given up

Real, and worth naming rather than discovering later:

- **Order cannot take a Challenge away mid-Attempt.** Once an Attempt opens, the working set is
  fixed until it closes. A Challenge that drops onto the Board mid-Attempt waits for the boundary.
- **The Attempt's budget is the only clock over the whole trajectory.** Eight turns share one
  deadline, so a turn that ends early hands its remainder to the next turn rather than back to
  Order. That is the point — the workdir is the memory and re-entering Order would spend the
  continuity — but it means `L*`, the per-Attempt cap, is doing more work than ADR-0014 imagined.
- **Repetition and novelty still reset at every turn boundary**, so a multi-turn Attempt is governed
  by the step cliff and the clock alone. This is true under *both* mechanisms and is therefore not
  a reason to prefer either; whether those two counters should see across turns belongs with the
  thresholds themselves ([#105](https://github.com/jerome-queck/incypher-ctf/issues/105)).

## What this changes in the record: nothing written, one thing read

No field moves and no stream is rewritten. What moves is what two numbers *mean*, and both were
being read as if ADR-0014 had run:

- **`attempt_sequence` counts Order's picks, not re-invocations.** It is written at `attempt-open`
  and read by `solver/schedule.py` as `_Spent.attempts`, the anti-livelock term above. An Attempt
  that ran eight turns increments it once, which is correct under this record and would have been an
  under-count under ADR-0014.
- **An Attempt contributes one cause, not one per turn.** The five queries that read
  `Attempt.cause` — budget, tier, alarm, thresholds, context — see `v1-gate/94-2` as a single
  `cut:step-cliff`, and that is the right grain: the Cut ended the Attempt, and the seven turns
  before it ended themselves.

**Eval question 5 is untouched by any of this.** It plots cumulative Flags against elapsed Run time
from `run-open` and reads no per-Attempt field at all, so the loop cannot move its curve. It is
named here because it was wrongly named as affected when this divergence was first written up
([#115](https://github.com/jerome-queck/incypher-ctf/issues/115)), and a record that leaves the
wrong claim standing is how it gets re-derived.

## Lease keeps its word, on a different ground

[ADR-0007](0007-truth-about-an-instance-lives-on-the-board.md)'s amendment coined **Lease** for one
reason: the hold spanning several consecutive Attempts. Under this record it spans exactly one —
`solver/run.py`'s `_end_lease` terminates at the close of every Attempt — so that ground is gone.

The word stays anyway, and not from inertia: a hold is a **resource** and an Attempt is **work**.
The hold costs Mana whether or not anyone is working it, the boundary sweep looks for a hold nothing
is using, and "the Instance expired", "the Attempt was cut" and "we let the Lease go" remain three
separate facts with three separate responses. One-to-one in span is not one-to-one in meaning, which
is the test the glossary already applies to Run and Attempt.

## Consequences

- **ADR-0014 keeps its reasoning and loses its mechanism.** Its section gains a forward pointer;
  ADR-0007's amendment, which quotes that mechanism as the reason a hold outlives an Attempt, is
  corrected in the same change.
- **Two code comments stop describing a mechanism that never ran** — `solver/codex.py`'s note beside
  `STOPPED` and `solver/instance.py`'s `Lease` docstring. Comments, not logic: nothing here changes
  what the Solver does.
- **`CONTEXT.md` already describes this**, from [#114](https://github.com/jerome-queck/incypher-ctf/issues/114).
  The glossary reached the tree first and this record catches the decisions up to it.
- **The four promoted gate Runs are readable exactly as they stand.** They were written under the
  loop, so nothing about them was mis-recorded — only mis-explained.

## Revisit when

- **A Run shows an Attempt whose turns keep going nowhere.** The step cliff is the only counter
  holding a multi-turn Attempt to account, and if it proves too loose the answer is either a
  cross-turn novelty counter or a turn cap — both #105's territory, and both cheaper to argue with a
  Run in hand.
- **v3 brings concurrency.** Order not being asked between turns is affordable while one Attempt
  runs at a time; with several in flight, a working set that cannot be revised mid-Attempt is a
  different proposition.
- **A Board starts dropping Challenges mid-event often enough to notice.** That is the case where
  Order being locked out until the Attempt boundary costs something measurable rather than
  theoretical.
