# An unmeasured turn is marked, and never guessed

**A Step whose tokens nobody counted says so on its own line — `usage_known: false` — and the count
is never recovered, inferred or estimated. The vendor states a turn's usage on `turn.completed` and
on no event before it, so a turn the deadline killed reports nothing at all, and what the record
writes down is the absence rather than a spend of zero.**

It extends [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md) and corrects nothing
in it. `usage_known` joins the `step-end` field list in that record's *The stream*, by its own rule:
*store the fact, derive the judgement.*

## What the stream said before

[#104](https://github.com/jerome-queck/incypher-ctf/issues/104). Across the four promoted gate Runs,
**22 of 27 Attempts carry no tokens at all** and **25 turns were killed before the vendor reported
one**. The Attempts that report nothing are the ones that ran to the deadline — the expensive ones —
so the loss was not random: the record was blindest about the Attempts that cost the most.

Two of ADR-0009's seven questions could not answer over that. Question 3, *where did the budget go?*,
showed Web, Pwn and Forensics spending **zero tokens** over 9 Attempts and 226 model Steps, which is
not what happened. Question 1, *Checkpoints per thousand tokens*, had no denominator.

## What `codex exec --json` actually emits

Read out of the 0.147.0 binary the `Dockerfile` pins, the `exec` schema names seven events —
`thread.started`, `turn.started`, `turn.completed`, `turn.failed`, `item.started`, `item.updated`,
`item.completed` — beside one `usage` group, `input_tokens` / `cached_input_tokens` /
`cache_write_input_tokens` / `output_tokens` / `reasoning_output_tokens`, and that group rides
`turn.completed` alone. The eighth thing the CLI emits is its own `error`, which carries a message
and no counts — *"Model metadata for `gpt-5` not found"* is one this repository has met. The captured
stream `tests/test_codex_adapter.py` runs every parser test over is a real turn and agrees.

The vendor does count sooner than that internally — `codex-core` has a `token_count` event, and the
rollout below is where it surfaces — but the `exec` translation this Solver reads does not carry it.

The Runs agree too, from the other side. An event kind `solver/codex.py` does not classify is written
to `claims/`, whole — so a usage-bearing event we did not know about would be sitting there. Over the
four gate Runs, **227 Claim bodies and not one carries a token or usage field**.

Both counts were taken against `/state` on the machine that ran the gates, which is where the bodies
and the rollouts live and where `runs/` deliberately does not reach (ADR-0009) — so they hold only
while that directory survives, and what to re-run matters more than the numbers. The Claim bodies are
`state/runs/<run>/claims/`, 227 files over the four gate Runs, and the question asked of each is
whether it parses as JSON carrying a key containing `token` or `usage`; none does. One body says the
word in prose, which is a model talking and is exactly the channel a Claim is for.

So on the stream the count is not recoverable. It is only ever markable as absent, which is what this
record does.

## The count survives elsewhere, and we still do not take it

`$CODEX_HOME/sessions/rollout-*.jsonl` — the vendor's own session log, which for the Solver lands in
`/state/codex` — carries a `token_count` record after each model response, holding
`total_token_usage` and `last_token_usage`. It is appended as the turn proceeds, so a killed turn's
last count is on disk: over the **63 rollouts** under `state/codex/sessions/`, **60 carry a
`token_count` payload** and **25 never reach a `task_complete`** — the same count of turns the four
streams mark unmeasured.

Recovering from it is the obvious fix and it is rejected, for three reasons that compound:

- **It is not promoted.** `runs/<run_id>.jsonl` is the committed evidence and the rollouts stay in
  `/state`, which ADR-0008 already made deletable mid-run. A number lifted out of a file nobody
  keeps is a number no later reader can check — precisely what ADR-0009 means by *the stream is
  committed, not a projection*.
- **Neither field is "what this turn cost" without a rule of our own.** `total_token_usage` is
  cumulative over the thread and `last_token_usage` is the most recent model response; turning
  either into a turn's spend takes an accounting judgement, and a judgement computed once and
  written down is the thing ADR-0009 exists to keep out.
- **It is the vendor's internal file, not their interface.** The pin moves — `CODEX_VERSION` is an
  `ARG` — and a reader wired to an undocumented shape breaks quietly at the version bump, which is
  the one failure mode a record about missing evidence must not have.

**Mark, do not guess.** If the killed turns' cost is ever wanted badly enough, it is a query over
`/state` beside the stream, written then and named as an estimate — never a field.

## Why a field, when the fact was already derivable

#104 noted the record was not ambiguous in principle: a killed turn's spawn Step still names its
`model` while a Step that invoked no model carries an empty one, so `model != "" and tokens_in == 0`
finds the loss. It also finds **568 other Steps** across the same four Runs — every command inside
every turn, which carries the model's name and no tokens by design, because the vendor meters a turn
and its tokens ride the invocation's Step.

So the derivation only works for a reader who already knows that an invocation is the one Step a
turn's usage ever lands on. That is a rule living in one module's docstring and in no query. The
field says it outright, and *which* Steps are unmeasured stays derived — `Step.unmeasured` in
`scripts/stream.py`, never a column in the stream.

## What the field is, and what it is not

`usage_known` is a fact about what the vendor said, at the grain the vendor says it. It defaults
**true**, because every Step but one knows what it spent: a Step that ran no model spent nothing, and
a command inside a turn spends nothing of its own. It is false only where a turn ran and the vendor
never reported it — a deadline kill, a failed turn, a CLI that died mid-stream. A CLI that never
started is `true`: nothing ran, so nothing is missing.

ADR-0009 holds unchanged around it. Still tokens and never cost — no price reaches the record. Still
no judgement frozen in — nothing here is a rule calibration will move, and the reader derives
"unmeasured" rather than reading it.

## Consequences

- **The four promoted gate Runs never gain the field.** They are committed as they were written, so
  `scripts/stream.py` keeps a second read for a stream that predates it: an invocation is the only
  Step a turn's tokens land on, so one carrying none was killed before any were reported. Both
  routes reach the same 25 turns.
- **The eval tables now carry blanks and floors.** A bare number is a total, `1234+` is a floor, a
  blank is a group where nothing was measured, and `≤0.006` is a rate taken over a floor — which is
  a ceiling, since tokens nobody counted can only push a rate down. `eval_budget.py` adds an
  `unmeasured` column beside them. A reader has to learn three marks; `scripts/stream.py` renders
  every one of them, so no two queries can spell them differently.
- **Question 3 still cannot say what the killed turns cost**, and `L*` — the per-Attempt cap chosen
  off it — is still being chosen against a floor. What changes is that the floor announces itself
  rather than passing as a total.
- **Every `step-end` line grows by one field.** Twenty-one bytes against a stream measured in
  hundreds of kilobytes, and `tests/test_observation_bodies.py`'s fixed-size bound moved with it.

## Revisit when

- **The vendor emits usage on an earlier event.** Then the count is on the stream, and *mark it*
  becomes *take it*. The moment to look is the `CODEX_VERSION` bump, where the event vocabulary is
  already being re-read.
- **The stall call starts cutting before the deadline does.** The `unmeasured` column is the measure
  of that: turns that end themselves are metered, so the column trending to zero is the behaviour
  fixing the evidence.
- **Somebody needs the killed turns' cost badly enough to write the rollout query.** The numbers are
  in `/state` for the gate Runs — until that directory is cleared, which nothing promises.
