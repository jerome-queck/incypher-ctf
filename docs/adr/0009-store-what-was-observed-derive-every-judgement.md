# Store what was observed, derive every judgement

> **Amended by
> [ADR-0025](0025-the-event-namespaces-the-working-directory-and-it-is-run-input.md).** *Two homes*
> below rests on ADR-0008's *"one-way pipe for output"*, and there is one exception to it that was
> written down nowhere: **a Challenge's working directory under `/state/work` is read back as Run
> input.** It is the memory that crosses an Attempt boundary, so a later Attempt is handed the
> half-unpacked archives, notes and scripts an earlier one left there — by design, and load-bearing
> since ADR-0023 made every turn a fresh spawn. Nothing in this record moves: the read-back is the
> **model's**, of the model's own prior output, and deleting `/state` mid-Run still costs the record
> and not the ability. ADR-0025 also puts the event in the path —
> `/state/work/<event>/<challenge_id>/` — because a `challenge_id` is a per-installation integer and
> a second Board mints the same ones.

> **One enforcement in *Two homes* is moved by
> [ADR-0024](0024-the-image-carries-what-a-run-reached-for-and-a-picture-is-attached.md).** The rule
> below is unchanged — the Solver never commits mid-Run — but "the Solver has no git binary" is no
> longer how it is kept: `git` is in the image for the *model*, which reached for it more than any
> other binary it could not find, and the guard that binds our own code is the AST test over every
> `solver/` module (`tests/test_two_homes.py`).

> **Corrected in three places by
> [ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md), and one of those
> narrowed again by [ADR-0016](0016-an-empty-list-is-not-an-empty-board.md).** The principle and
> the stream stand entirely; what moves is three details, and ADR-0015's *Corrections to ADR-0009*
> holds them in full. In short: **"a cut Challenge goes to the back of the queue" is wrong** —
> there is no queue, and that cause is a penalty inside a function recomputed at every Attempt
> boundary. **Attempt-open also carries Order's rank for every unsolved Challenge**, without which
> a Run that passed over sixty Challenges and one that only had fourteen read identically and eval
> question 5 cannot be asked. And **each Intake records the scoreboard top-N and every Challenge's
> `(solves, value)` pair** — which is where ADR-0016 lands: an empty collection is not evidence of
> an empty Board, so a Board failing its control contributes *no* scoreboard rather than zero rows.
> This record's *"a crowd-poller"* reasoning is untouched; the additions ride Intake's existing
> cycle rather than adding a second poller.
>
> **Extended — not corrected — by
> [ADR-0019](0019-the-boards-statement-of-a-challenge-is-not-evidence.md).** The Claim/Observation
> split below is a pair, and a Challenge's description is neither half of it: the model did not
> write it and nothing ran to produce it. A `source` on every Step says which, and what authorises a
> Flag submission reads it. Nothing here changes — it is this record's own rule, *store the fact and
> derive the judgement*, applied to a case it did not name. **`source` joins the `step-end` field
> list in *The stream* below**, which is written as it was decided and does not name it.
>
> **Extended — not corrected — by
> [ADR-0022](0022-an-unmeasured-turn-is-marked-and-never-guessed.md), for the same reason.** The
> per-Step token fields below cannot tell *nothing was spent* from *nobody counted what was spent*,
> and the vendor reports a turn's usage on one event that a killed turn never reaches — so 22 of the
> 27 Attempts in the four gate Runs recorded a spend nobody measured, and questions 1 and 3 could not
> answer over them. **`usage_known` joins the `step-end` field list in *The stream* below**; which
> Steps are unmeasured stays derived, and nothing else here moves.

v1 emits telemetry, and two earlier records already rest their weight on it.
[ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md) leaves its three stall
thresholds uncalibrated on purpose and names this record as the thing that will calibrate them.
[ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md) makes every version gate a verdict
read off a run. So the schema is not bookkeeping; it is the evidence base for every decision from
here to v5, and changing it mid-effort destroys the cross-run comparability that is the entire
point of measuring.

[#16](https://github.com/jerome-queck/incypher-ctf/issues/16) asked for a **per-Attempt record**.
That grain cannot do the job it was created for, and the reason generalises into the decision.

The decision: **store what was observed, derive every judgement.** Anything computed by a rule
that calibration will change — novelty, repetition, stall, "was that progress" — is recomputable
from the record and never frozen into it. Mechanically, that makes the record a **Step stream**,
and the per-Attempt record a *projection* over it rather than a row in it.

## Why the roll-up loses

ADR-0005's stall signal is three counters — a repeated normalised command, N Steps without a novel
Observation, a hard step cliff — and all three are functions over the *ordered sequence of Steps*.
Calibrating them means asking what a different threshold would have done to a run that already
happened.

A stored Attempt row saying `last_novel_step: 14` answers that question for exactly one threshold:
the one that was live when the row was written. Keep the per-Step sequence of normalised command
and Observation digest, and it answers for every threshold, including ones nobody has thought of.
The roll-up is not smaller in any way that matters — it is the same run with the answers already
chosen.

Two smaller cases fall out of the same principle and are worth naming because both look like
harmless conveniences:

- **No stored `novel` boolean.** Novelty is a rule over digests. Store the digest; derive novelty.
- **`command_raw` is kept beside `command_normalised`.** Normalisation *is* the repetition
  counter's rule, and calibration will change it. Keep only the normalised form and no future rule
  can be applied to a past run.

The same argument kills a shadow-mode record type. ADR-0005 asks for every stall trip to be logged
with its counterfactual; if the counters are pure functions of the stream, the counterfactual is
not something to record but something to compute. **Shadow mode is an offline replay**, which is
strictly better than logging trips — a logged trip reports what the current thresholds would have
done, a replay reports what every threshold would have done — and it needs no code in the Solver
at all.

## What the model may write

#16's headline field was "the turn index at which the agent last had a genuinely new idea",
proposed as the signal that judges the context system. It is a Claim, and ADR-0005 — written after
that ticket — removed exactly that judgement from the loop. Recorded, it would be a Claim with a
permanent address, which is the shape that record went to some length to prevent.

It is replaced by two indices the orchestrator computes anyway, because the stall counters already
need them: the last Step that produced a **novel Observation**, and the last Step that produced a
**Checkpoint**. The second is the stronger signal, since ADR-0005 already made a Checkpoint the
only currency that buys an Attempt more time.

The one model-authored field that survives is ADR-0005's carve-out: the short **approach label**
naming what an Attempt tried, at Attempt grain. That also answers the original question across
Attempts — a label that changes is a new approach; a label that repeats is not.

## What is deliberately not recorded

**A Challenge's point `value`, anywhere.** #16 asked for points kept fresh by a crowd-poller,
because CTFd's dynamic scoring moves `value` as solves come in. It moves further than that: the
value is recomputed retroactively for every solver, so "the points we banked at 14:00" is not a
quantity that exists. Only the final standings carry points, and those are read off the scoreboard
at the end of a run without any telemetry at all.

**A crowd-poller.** [#18](https://github.com/jerome-queck/incypher-ctf/issues/18) established that
`solves` ships in the LIST payload and that `value` is a strict function of it — one signal, not
two — and `CONTEXT.md` already defines Intake as a *sync on a fixed cycle*. The poller exists. A
second one would be a second thing hitting a rate-limited Board for data Intake already holds.
`solves` is snapshotted at Attempt-open, Attempt-close, and Flag-accepted.

## How an Attempt ends

#16 proposed `flag` / `no-flag` / `abandoned` / `crashed`. ADR-0005 removed giving up — an Attempt
is **cut and requeued**, and a Challenge is never terminal — and
[ADR-0007](0007-truth-about-an-instance-lives-on-the-board.md) added a cause that ticket could not
have known about. Calibration needs to know *which* counter fired, so the field records a cause,
not an outcome:

`flag`, `cut:repetition`, `cut:novelty`, `cut:step-cliff`, `cut:budget`, `cut:instance-expired`,
`cut:self-reported-impossible`, `crashed`.

`no-flag` disappears: it is the absence of a cause rather than one.

`cut:self-reported-impossible` is ADR-0005's single narrow exception — a volunteered "impossible"
may shorten a budget and never lengthen one — and it is in this list **because the aim is for it
never to fire.** A cut Challenge goes to the back of the queue instead. A cause that is not
recorded is a defect that cannot be watched trending to zero, so it stays in the vocabulary as an
alarm rather than as an ending.

## The stream

One append-only, sequence-numbered JSONL file per run, written under an atomic flock, in `/state`.
SQLite was the alternative and is also standard-library; it loses because a crash mid-write makes
a corrupt database where JSONL makes a truncated last line. v2's crash-restart is precisely when
that difference is collected, and losing one Step beats losing a run.

Steps are written as **`step-begin` / `step-end` pairs**, not one record at completion. A single
end-of-Step record makes a Step that never finished invisible — and at a crash the command that
was in flight is the prime suspect, while a hang produces no record at all. A begin with no end
names both. The doubled line count costs nothing at the sizes involved.

`step-end` carries `seq`, wall-clock `ts` and monotonic `mono`, the run and Attempt ids, the Step
index, `command_raw` and `command_normalised`, `exit_code`, `duration_ms`, `observation_digest`,
`observation_bytes`, `observation_ref`, the `checkpoint` command if one was produced, the tool
name, and per-Step `model` / `tokens_in` / `tokens_out` / `cache_read` / `cache_write`.

**Tokens, never cost.** Cost is tokens times a price table that lives outside the record and
changes underneath it; computing it at analysis time from a table in the repo keeps the record true
when prices move. #16's "context size per turn" needs no field of its own — it is
`tokens_in + cache_read`.

Attempt-open carries the Challenge's id, name, category and `type`, `solves_at_open`, its Tier, its
budget, **the Attempt's sequence number for that Challenge**, and an Instance's `until` if one was
deployed. Attempt-close carries the cause, the approach label, `solves_at_close`, and the number of
extensions granted. Run-open carries the whole **Board profile as discovered** — ADR-0008's named
failure is a profile that discovers the *wrong* thing, and a post-mortem cannot otherwise
distinguish "the Solver behaved wrongly" from "the Solver read the Board wrongly", which want
completely different fixes.

**Observation bodies stay whole**, in separate files under `observation_ref`, so the JSONL line
stays small and fixed-size and the stream stays cheap to parse after a run that produced gigabytes.
Bounding `/state` is v2's endurance work; a cap chosen now is a cap that cannot be un-chosen,
because the data would be gone.

## Redaction

Every Observation is redacted **before it is written**, not at the point it would be published.
`/state` lives on a laptop and gets copied, zipped and pasted into issues; treating it as clean
because it is untracked is how the leak happens anyway. The digest is taken over the redacted
bytes, so it never becomes an oracle for a secret.

Matching is on **exact known values only** — the Solver was handed every secret it holds — plus
each value's base64 and URL-encoded forms, which is what a verbose HTTP call actually emits. No
patterns and no heuristics: a regex for a key shape adds false positives that silently eat
Observations, and an eaten Observation is a worse failure than an unredacted one, because nothing
detects it. **Redaction never touches the Flag field**, so a Flag can never be silently destroyed.

A failed write is retried once, then counted, and the count appears in the run-close record — never
fatal, never silent. The Solver's job is Flags, and ADR-0008 already committed to `/state` being
deletable mid-run without costing the ability to solve; but a run whose evidence is holed has to
say so on its own face.

## Two homes, and the Solver never touches git

`/state` holds everything. **The Solver has no git binary, no credential, and writes to exactly one
path** — a commit mid-run costs resources during the scored 5.5 hours and writes outside the
one-way pipe ADR-0008 established.

Afterwards — and post-run is not intervention, since the container is dead — a human-run script
promotes the stream to `runs/<run_id>.jsonl` in this repository, Observation bodies stripped,
digests kept. **The stream is committed, not a projection**, for the same reason the grain is a
stream: a projection in git is a frozen judgement, and anyone in v4 must be able to re-run shadow
mode and re-derive novelty from what git holds alone. A 5.5-hour run is a few hundred kilobytes
once bodies are stripped, so the size argument for a roll-up does not exist.

Every run that reached Attempt-open is promoted; gate runs are mandatory. They land batched — one
pull request per gate or practice weekend, against the gate issue ADR-0006 already produces —
because a pull request per run is ceremony heavy enough to get skipped, and a convention people
skip is worse than one never written.

## Stability, and what "stable from v1" can mean

A literal freeze is impossible: v3 adds concurrency, model routing adds per-model detail. Stability
is therefore three enforceable rules and a `schema_version` integer on every record. **A field's
meaning never changes once written. A retired field stops being written and its name is never
reused. Readers access by name with a default**, so a v1 run stays readable by v5 analysis.

## Eval is seven questions, not a harness

The schema is only defensible if every field earns its place by answering a named question;
otherwise it is a field list nobody can ever prune. There is no harness — each of these is a query
over the JSONL, living in `scripts/`:

1. **Is the context system helping?** Steps-to-last-Checkpoint over Steps-spent, per Attempt; and
   Checkpoints per thousand tokens.
2. **Do the stall thresholds cut early or late?** Replay at N thresholds against known outcomes.
3. **Where did the budget go?** Tokens and wall-clock per Attempt, per Category, per cause.
4. **Is Tier predictive?** Tier against the cause an Attempt actually ended with.
5. **Is Order banking Flags early?** Cumulative Flags against elapsed run time.
6. **Is the alarm firing?** The count of `cut:self-reported-impossible`, target zero.
7. **Are Instances leaking?** Deploys against terminates per Attempt (ADR-0007's leak sweep).

A field that answers none of these does not go in.

## Markdown is for readers, not for the Solver

A standing constraint for this repository, recorded here because this is the first record whose
artefact could plausibly have been a markdown report: **markdown carries decisions that humans and
agents read — ADRs, `CONTEXT.md`. Anything the Solver reads or writes is structured data.**

The evidence is `ctf-workspace`, harvested in #18. Every scheduling decision there was gated behind
a hand-typed README toggle, and its ADRs describe a re-tiering loop that exists in zero source
files — `final_tier` never becomes anything but a copy of the static prior. Markdown that a machine
is supposed to act on is not maintained into agreement with the machine; it decays into a
description of a program nobody wrote.

## Consequences

- **The per-Attempt record #16 asked for still exists — as a projection.** That is the payoff
  rather than a compromise: a projection can be redefined in v4 against v1 runs, and a stored row
  cannot.
- **Observation bodies live only in `/state`.** Delete it and the digests in git can never be
  resolved back to content. Accepted, because what decisions rest on is the *judgements*, and those
  stay re-derivable from the committed stream.
- **Flags appear in the committed stream**, since redaction never touches that field. Fine in a
  private repository among the team who earned them, and one more reason the repository stays
  private.
- **Shadow mode is not a Solver feature.** Nothing in the hot path implements it, and it can be
  written after a run that has already happened.
- **`runs/` is a new top-level area** — data, so excluded from lint and from the conformance scan,
  but *not* from the secret scan, which is the one check that has to see it.
- **v1's "active time on the Challenge, not end-to-end" collapses to Attempt duration.** v1 is
  single-threaded, so active time is Attempt duration summed across a Challenge's Attempts. The
  distinction becomes real at v3's concurrency, and `mono` per Step already carries it either way.

## What this record does not settle

Where a cut Challenge lands in the queue, and what recomputes Order. "Back of the queue" is the
intent behind `cut:self-reported-impossible` above, but the general policy — whether every cause
requeues alike, and what Order reads — is a decision nothing on the map owns yet. Telemetry's only
obligation is to make it *evaluable*, which the Attempt-sequence-per-Challenge field does.

Bounding what accumulates in `/state` remains v2's, unchanged by this record except that Observation
bodies are now identified as the bulk of it.

## Revisit when

- **A practice run has produced a stream long enough to replay.** These field choices were argued
  from two other ADRs and no data of our own; the first real replay supersedes the argument.
- **v3's concurrency**, which is the first version where an Attempt is not the unit of elapsed time
  and where a per-model breakdown stops being a single row.
- **A field is wanted that answers none of the seven questions.** That is either an eighth question
  worth writing down, or a field that does not belong — and the two are worth telling apart
  deliberately rather than by adding the field.
