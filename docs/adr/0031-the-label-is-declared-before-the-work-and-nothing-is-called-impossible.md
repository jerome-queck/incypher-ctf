# The label is declared before the work, and nothing is called impossible

**The approach label is written at the *start* of a turn as what the model is about to try, not at
the end as what it tried — because the Attempts that most need to hand something forward are the
ones a cut stops before they can. Its accuracy is never checked. `w_impossible` is deleted: a
Challenge that failed repeatedly gets less priority through the spend ledger, and is never declared
out of reach. And the carry gets a ceiling it currently only claims to have.**

It amends one clause each in [ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md),
[ADR-0009](0009-store-what-was-observed-derive-every-judgement.md),
[ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md) and
[ADR-0030](0030-the-roadmap-is-four-versions-and-the-practice-board-is-one-we-build.md), and leaves
the rest of all four standing. Per ADR-0030's rule, the older records are not rewritten: what they
say is what was decided then.

Decided on [#155](https://github.com/jerome-queck/incypher-ctf/issues/155), under wayfinder map
[#153](https://github.com/jerome-queck/incypher-ctf/issues/153).

## What the stream said

Every number here is from the four promoted Runs — `runs/*.jsonl`, 27 `attempt-open` and 26
`attempt-close` records, all against `brunnerctf-2026-global`. The `compfest-2026-seg2` figures the
ticket quoted survive only in issue bodies and are **not** restated as fact.

- **`approach_label` is empty in 22 of 26 Attempts**, and in **22 of 25** that did not end in a
  Flag. The model was not silenced: every one of those Attempts emitted between 3 and 28 Claims.
  `solver/prompt.py` asks for the label under *"Before you stop"* — and a `cut:step-cliff`, which
  ended 20 of the 26, is precisely the ending the model is never told is coming.
- **The model already writes the sentence.** Read out of `state/runs/*/claims`, each turn opens
  with an unprompted future-tense intent line. This decision puts a marker on a sentence that
  exists rather than asking for new behaviour.
- **Those sentences are 95–210 bytes, median 154.** `LABEL_LIMIT = 80` would chop every one of them
  mid-clause.
- **Checkpoints are rare, not absent: 3 across 27 Attempts, in 2 of them**, at ≤0.006 per thousand
  tokens. Counting the `checkpoint` field on `step-end` gives zero, but that field is never filled
  by design — a Checkpoint is a judgement, derived at read time (ADR-0009), so the honest count
  comes from `scripts/eval_context.py`. A measurement off the stored field measures a recording gap.
- **The carry's own bound is not enforced.** `solver/carry.py` states *"One line, enforced rather
  than intended, so K Attempts cost K lines"*, but `Boundary.closed()` is called at
  `solver/run.py:446`, inside `_turn`, under the `while not self._turn(...)` loop at
  `solver/run.py:359`. It fires **per turn**: 40 lines across 27 Attempts, up to **8 for a single
  Attempt**, each rendered `Attempt N` where N is a turn index.
- **The tried list reaches 12,059 bytes inside one Attempt**, with ~82% of commands distinct, so
  the exact-string dedup in `_merged` buys almost nothing.
- **`cut:self-reported-impossible` has never fired in a promoted Run.** The only two firings on
  record are seg2's, and both were wrong — Challenges other teams had solved 58 and 31 times.
  [#149](https://github.com/jerome-queck/incypher-ctf/issues/149) fixed the trigger; the 5.0 blast
  radius was never revisited.

## The decision

### 1. The label is declared before the work

The model writes it at the top of every turn as *what I am about to try*. The prompt is already
recomposed per turn (`solver/run.py:394` sits inside `_turn`) and `held.approach` is already sticky
across turns, so this costs no new machinery — only a moved instruction.

The alternatives were an orchestrator composition, which is deterministic but shallow and which
carry item 4 already does, and a monitoring model, which is accurate and non-deterministic — and
determinism is the property this seam most needs. A declaration is still an Observation of what the
model *said*, so [ADR-0022](0022-an-unmeasured-turn-is-marked-and-never-guessed.md)'s rule that a
judgement is never guessed is intact.

`LABEL_LIMIT` rises from 80 to **200**, above the measured maximum.

**The field is renamed.** ADR-0009's stability rule is that *"a field's meaning never changes once
written… its name is never reused"*, and 26 promoted records already carry `approach_label` under
the retrospective meaning. `attempt-close` therefore stops writing `approach_label` and starts
writing **`approach_declared`**; a reader of a v1 stream still finds the old field with the old
meaning, which is exactly what the rule protects.

### 2. Its accuracy is not checked

No divergence check, no monitor. ADR-0009 already frames the label's value as differential rather
than veridical — *"a label that changes is a new approach; a label that repeats is not"* — and
under that reading accuracy was never the property being bought. The deterministic ground truth
already rides in the same carried line (steps, checkpoints, cause, last command) and in the tried
list beside it. A semantic check would need either a model or a keyword heuristic, and both are
worse than the thing they would be checking.

### 3. Nothing is called impossible

`w_impossible` and the `- w_impossible × impossible(c)` term are deleted. `_Spent.impossible`
becomes dead state and goes with them.

`cut:self-reported-impossible`, `stall.py`'s `UNWINNABLE:` marker and its five matched phrases all
**stay**, unchanged. The cause is an Observation and an alarm the aim is for never to fire
(ADR-0009), and it still ends the Attempt. What it stops doing is changing a rank.

Repeated failure is a fact about our approach, not about the Challenge, and the priority reduction
it earns already exists in `spend_norm`. Two bounds are recorded rather than hidden:

- **`spend_norm` saturates.** `min(1.0, max(by_time, by_attempts))` caps the reduction at
  `w_spend × 1.0 = 1.5`, and past `attempts_full = 6` it stops separating two already-attempted
  Challenges at all. On a board small enough for everything to saturate, Order falls through to
  value and tractability.
- **That saturation is not something this decision introduces.** Driving the real `Scheduler` over a
  two-Challenge board with today's unmodified code and *nothing* declared impossible reproduces the
  same lock-in bit-identically. Deleting the weight stops exempting the declared Challenge from a
  property every other Challenge already has. On a 9-Challenge board — the Brunner residue the
  Solver actually faces — the declared Challenge moves from 1 pick in 24 to 2, and even fully
  saturated it takes only
  [ADR-0017](0017-the-exploration-share-and-solve-velocity-are-reinstated.md)'s reserved
  exploration quarter.

`solver/prompt.py`'s `UNWINNABLE` text tells the model that *"declaring it parks this Challenge and
spends the rest of the Run on the others"*. That sentence becomes false and is rewritten, because
the comment above it names the failure mode exactly: *"a model that thinks it is free will reach for
it the moment a Challenge is difficult."* What replaces it must still say the declaration ends the
Attempt — which remains true — without promising a standing penalty that no longer exists.

### 4. The ceiling gains the axis it was missing

`_spend_norm` uses `max(by_time, by_attempts)` and argues at length for why the count leg is
load-bearing. `_demoted`, twenty lines below, uses **time alone**. It gains the same `max`.

`ceiling_fraction` stays at **0.2**. The two legs are complementary rather than redundant at the
scored 5.5h window: against the observed budgets — 450s for 14 of 27 Attempts, 900s for 5, 750s for
5 — the count leg fires at ~45 minutes for a typical Challenge while the time leg backstops a
high-Tier one at 66. Moving 0.2 has no evidence behind it and would also move `_spend_norm`'s time
denominator. It is re-anchored at v2's gate, against 5.5 hours of real data.

### 5. The carry is bounded, and it counts Attempts again

- **`Boundary.closed()` moves out of `_turn` and onto the Attempt**, so `K Attempts cost K lines` is
  true and `Line.sequence` numbers what it says it numbers.
- **One ceiling of 16 KiB over the whole rendered block**, not per section — that is what ADR-0030's
  clause 4 asserts, and per-section caps sum to a total bound only if every section has one. 16 KiB
  sits above the largest carry ever measured, so no recorded Attempt would have been trimmed, and
  well under the ~36 KB that produced the measured failure.
- **The tried list is the elastic section.** The four others compose first; the remainder is filled
  with the newest tried entries. Newest-first, because the oldest commands are the ones a later
  Attempt is least likely to repeat.
- **The two sections that grow across Attempts are bounded too.** Checkpoint `moved` and `replay`
  get the `trimmed()` treatment already applied to labels and last-commands; the Attempt-lines
  section keeps the last N. Both are cheap now and stop being cheap once Checkpoints fire more often.

## What v2's spec must commission

These are requirements, not observations — a gate clause nothing measures is not a gate.

1. **A measurement for gate clause 3.** `grep -rn approach scripts/` returns zero hits today, and
   `scripts/stream.py`'s `Attempt` type surfaces `cause` and `flag` and nothing else. The clause
   cannot be scored on gate night without new code.
2. **A compile-time or test-time assertion for gate clause 4.** A promoted stream carries no bodies
   (`scripts/promote_run.py`), so the ceiling can never be checked post-hoc from `runs/`. The bound
   is asserted where the block is composed, or it is not asserted.
3. **The turn count reaches the record.** `_Held.turns` is incremented at `solver/run.py:434` and
   read by nothing. With a per-turn declaration and one surviving per Attempt, a Run that cannot say
   how many turns an Attempt held cannot be re-baselined.
4. **The two tests that pin the deleted weight are rewritten, not deleted** —
   `tests/test_order_and_budget.py::test_a_challenge_the_model_called_impossible_is_penalised_and_never_excluded`
   and `::test_the_penalty_for_calling_something_impossible_does_not_decay`. Removing
   `w_impossible` fails exactly those two, and the second half of the first — *never excluded* —
   still passes and is the property this decision keeps.

## The clauses this amends

- **ADR-0005**, the carry carve-out: the label names *what is about to be tried*, not *what was
  tried*. Everything else in that record — the stall call outside the solving model, Observations
  cross and conclusions do not, nothing asks the model to estimate its own budget — is untouched.
- **ADR-0009**, twice: the same tense correction to its restatement of the carve-out, and the field
  rename its own stability rule requires.
- **ADR-0015**, *Corrections to ADR-0009* item 1, which reads *"It becomes the `w_impossible`
  penalty above"*. There is still no queue; a cut Challenge is still not placed anywhere. What
  replaces the penalty is `spend_norm` and the demotion of §4, with the saturation bound recorded
  above.
- **ADR-0030**, v2 gate clause 3. Its *"5 of 40"* baseline counts **turns**, while the clause is
  worded per Attempt. Per Attempt the baseline is **3 of 25** cut Attempts. The ≥80% threshold is
  unchanged, and the field it is measured over is now the declaration.

## What this does not decide

The Checkpoint rule itself. It fires in 2 of 27 Attempts, which zeroes `w_progress` and holds every
Tier at its Triage prior for practically a whole Run — but that is a question about the stall
counters rather than about the boundary, and it is its own ticket under map #153. This decision
assumes Checkpoints stay rare and bounds the carry without depending on them.
