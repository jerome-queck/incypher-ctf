# Confirmed progress buys a bounded stall epoch

> **The Flags-first objective is replaced by
> [ADR-0055](0055-one-score-basis-qualifies-one-release-candidate-profile.md).** Final official Board
> score leads inside non-negotiable legality, autonomy, authority and safety constraints; score
> availability never controls Run liveness. The stall guards, Checkpoint proof and carry lifecycle
> below stand.

The first promoted replay is good enough to expose broken composition, but not good enough to tune:
27 Attempts and 539 model Steps produced three Checkpoints and one Flag, all before the tool-image
repairs, while 22 Attempts lost their token measurement. The live `1 / 5 / 25` repetition, novelty
and step thresholds therefore remain provisional. This decision repairs what each signal means and
how the signals compose; fresh Runs, not this sparse replay, decide whether their numbers move.

Decided on [What ends an Attempt, earns more time, and clears its
carry](https://github.com/jerome-queck/incypher-ctf/issues/168), under wayfinder map
[#153](https://github.com/jerome-queck/incypher-ctf/issues/153). This is a planning decision. Its
production changes and proofs belong to the map's `/to-spec` -> `/to-tickets` handoff.

## The objective is lexicographic

The Solver first maximises accepted Flags within the fixed Run, then prefers those Flags earlier,
then minimises subscription tokens, Turns and wasted Steps. No resource saving earns a lost likely
Flag. An accuracy mechanism still has to prove that it improves Flag yield or time-to-Flag; merely
keeping an Attempt alive longer is not success.

## The three stall guards stay, across Turn boundaries

A Turn is a vendor metering boundary, not a progress boundary. When a Turn ends early with budget
left, the orchestrator re-invokes within the same Attempt and explicitly asks the model to continue
until a Flag or Cut. Repetition history, seen Observation digests, the novelty count and the step
epoch all survive that re-invocation. There is no separate Turn cap.

The provisional guards are:

- **Repetition `1`**: cut after the same normalised command produces the same exit status and
  Observation digest on one consecutive repeat. A different command or a novel Observation breaks
  the streak. Re-running an old orientation command after other work is therefore information, not
  a Cut.
- **Novelty `5`**: cut after five consecutive model Steps whose Observation digests have already
  appeared in the Attempt. A new digest resets only this weak counter. Novel output buys no time,
  Tier, step reset or carry clearing.
- **Step cliff `25`**: cut after 25 model Steps since the Attempt opened or since its last confirmed
  Checkpoint. It is a bounded stall epoch, not an absolute lifetime counter. A Checkpoint observed
  on the twenty-fifth Step wins before the cliff is evaluated and opens a fresh epoch.

The Attempt's wall-clock budget, Instance expiry and Run expiry remain hard bounds around every
epoch. A stream of changing noise can defeat novelty, but never the step cliff; a stream of genuine
progress can reset the cliff, but never escape the clocks.

This disposes of all three residual questions superseded from
[#105](https://github.com/jerome-queck/incypher-ctf/issues/105): repetition stays but becomes a
consecutive cross-Turn loop guard; the cliff stays at 25 rather than moving to the Flag-losing 15;
and early Turn completion gets both a continuation instruction and cross-Turn stall state rather
than another Attempt or a Turn cap.

## A Checkpoint is proved, not narrated

A Checkpoint is a replay-confirmed environment transition. The model may nominate a verification
command, but the nomination is a Claim and earns nothing. Credit exists only after the existing
bounded Attempt tool surface produces stable replay evidence or a verified before/after state.

The verifier gains no new execution authority. A risky or non-idempotent command cannot be run
again merely because model prose called it verification; the model must reach a safe postcondition
probe through the same tool surface it already has. The current changed-answer heuristic may still
nominate a candidate, but nondeterministic output receives no credit without confirmation. This
broadens detection to first proofs such as an extracted artefact or reachable shell while preserving
the Claim/Observation boundary.

One confirmed Checkpoint:

- resets repetition, novelty and the 25-Step epoch;
- banks 120 seconds, consumed only when the base deadline expires, at most three times per Attempt
  and never beyond Instance or Run expiry;
- raises the Challenge one Tier rung after the Attempt, capped at absolute Tier 4; and
- clears pre-transition repetition/novelty state and tried-command carry, since the environment in
  which those commands ran no longer exists.

The Working directory, confirmed-Checkpoint ledger and Attempt summaries survive. ADR-0031's 16 KiB
whole-carry ceiling and its per-Attempt summary rule stand. Further Checkpoints at Tier 4 can still
reset the current Attempt and bank its bounded time; they cannot create the accidental Tier 5-7
budgets the current arithmetic permits.

## Numbers move only on qualifying evidence

No change to `1 / 5 / 25`, 120 seconds or three grants is commissioned until two post-tool-fix,
Gate-qualified practice Runs have each produced at least one accepted Flag. Their promoted evidence
must expose, per Attempt and Turn:

- known or explicitly unknown token usage and Turn counts;
- Checkpoint candidates, confirmations, rejections and model-Step positions;
- extensions banked and consumed;
- old-versus-proposed shadow Cuts, including every accepted Flag a setting would have cut away; and
- time-to-Flag, wasted model Steps and tokens per accepted Flag.

A number moves only when accepted-Flag yield improves, or when yield is equal and time-to-Flag or
resource use improves. The old Brunner replay remains useful incident evidence and a shadow baseline;
it is not mixed with the qualifying sample or promoted into a rate it cannot support.

## What remains unchanged

A Flag ends the Attempt. Budget, Instance expiry, crash and the retained
`cut:self-reported-impossible` alarm remain distinct endings. Nothing here makes a Cut terminal for
the Challenge, lowers a Tier, reopens ADR-0031's carry decision, or implements production v2 inside
the wayfinder map.
