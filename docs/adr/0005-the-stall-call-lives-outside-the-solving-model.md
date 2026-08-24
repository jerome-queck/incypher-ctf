# The stall call lives outside the solving model

> **Extended by [ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md).**
> *Time is bought by state transitions* now reaches **across** Attempts as well as within one: a
> Challenge's Tier rises by the Checkpoints it has earned, capped at the same small K, so the
> evidence this record trusts to extend an Attempt is the same evidence that decides how long the
> next one gets. Nothing lowers a Tier — a barren Attempt costs a Challenge its place in Order and
> never its budget. Nothing here is contradicted; the asymmetry is the same one, applied one level
> up.

> **Amended in one bullet by [ADR-0014](0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md).**
> The decision above, and six of the seven Consequences, stand exactly as written — the title most of
> all: the stall call still lives outside the solving model. What moves is a mechanism this record
> assumed rather than argued, that our orchestrator issues each command. `codex exec` has no
> supported way to stop it acting, so the vendor's agent drives the loop and we read its event
> stream; the three counters still read only Observations, because that stream is where the
> Observations now come from. **Only *time is bought by state transitions* coarsens**: Codex takes
> its next turn without asking, so what a Checkpoint extends is the kill deadline rather than a
> grant of one more Step. Extensions are still capped at K, and a confident rabbit-hole still gets
> less time. One residual cost is named there and not here: Codex compacts its own context inside a
> long Attempt, which this record's Attempt-boundary rule does not reach.

The Solver runs 5.5 hours unattended against a batch of Challenges nobody on the team has seen, and
the failure that costs most is not a Challenge it cannot solve. It is a Challenge it *could* have
solved, abandoned early — or a dead approach it grinds until the clock runs out. Something has to
decide when an Attempt has stopped going anywhere, and the obvious candidate is the model doing the
solving, since it is the one thing that knows what it is trying.

The decision: **the model never ends its own Attempt.** The orchestrator computes the stall signal
from evidence the model cannot author, and cuts. The single exception runs in one direction only —
a model that volunteers "this is impossible" may *shorten* a budget, and nothing the model says may
ever *lengthen* one.

## Why not simply ask it

Because it has been asked, and measured, and it does not answer. EnIGMA gave a CTF agent an explicit
`exit_forfeit` action: it fired in **0.5%** of runs, while **63.1%** ended by exhausting the cost cap
instead (arXiv:2409.16165, Table 13). An agent handed a give-up button does not press it.

Worse than staying silent, its sense of progress is confidently wrong for most of a doomed run. On
failed trajectories BAGEN measures models reporting feasibility **above 70% after 60% of the budget
is spent**, the alarm firing only in the final 20% (arXiv:2606.00198, §5.3). A design that let
self-assessed progress buy more time would therefore buy it on very nearly every run that was going
to fail — the more confidently wrong the model, the more of the 5.5 hours it takes. Closing that
loop is the whole point of this record.

The asymmetry is why the exception above is narrow rather than absent. The same self-report that is
useless as a continue signal is a cheap stop signal: terminating on a volunteered "impossible" saved
**28–64% of tokens on failed trajectories for 1.6–4.2 points of overall success** (§6.1). We
take that direction and only that direction, and we expect it to fire rarely — MIRAGE-Bench finds
that in genuinely unachievable states agents fabricate an action **46–65%** of the time rather than
say so, the worst of the seven risk settings it tested (arXiv:2507.21017, Table 6).

## Consequences

- **A Challenge is never marked impossible.** What ends is an Attempt; the Challenge returns to the
  queue. There is no terminal verdict for the model to reach, which is the cheapest way to prevent it
  reaching one.
- **The stall signal is three orchestrator-computed counters**, tripping on whichever comes first:
  a repeated normalised command, N steps without a novel Observation, and a hard step cliff. All
  three read only Observations, never the model's prose. Their thresholds are parameters, because
  nobody has calibrated them for this Board and #16's telemetry is what will.
- **Time is bought by state transitions, never by interpretations.** An Attempt extends only on a
  Checkpoint — an environment change the orchestrator can re-verify by replaying a command. "This
  looks like a spectrogram" is a Claim and buys nothing, which is what makes a confident rabbit-hole
  get *less* time rather than more, without anyone having to detect that it is wrong. **Extensions
  are capped at a small K**, so an approach that keeps yielding cheap Checkpoints still ends — the
  Checkpoint rule decides *what* buys time, and the cap decides *how much there is to buy*.
- **The reset is silent.** Telling a model mid-Attempt that it appears stuck puts the judgement back
  inside the narrative this decision removes it from, and is close to an ideal prompt for inducing
  "impossible". Silence is *within* an Attempt: the next Attempt is separately told what became of
  the last one, which is a fact about a finished run rather than a verdict on a live one.
- **Observations and orchestrator-derived records cross an Attempt boundary; hypotheses, notes and
  conclusions do not.** There is exactly one carve-out and it is deliberate: the per-Attempt record
  carries a short model-authored label naming the approach that was tried, because a reset that
  carries only verified facts is a *deterministic* starting state and the next Attempt would have
  every reason to repeat the last. The model may name what it tried; it may not state what it
  concluded. Beyond that label, **v1 keeps no model-written summary at all** — a summary is a Claim
  with a permanent address, and that is exactly how a verdict survives a reset.
- **Nothing asks the model to estimate its own budget.** BAGEN's calibrated interval coverage caps
  at 47% even after training that targets it, with 17–36% cross-task transfer; on an unseen batch
  that number is noise.
- **The detector runs in shadow mode on practice Boards** — logging every trip while acting on
  none, each trip recorded with its counterfactual, so a practice run yields the whole
  distribution of would-have-cuts against known outcomes. That is the cheapest threshold
  calibration available and it needs no separate harness; #16 is where the record it depends on is
  specified.

## What this record does not settle

Whether the Solver should be *told* its remaining wall-clock is open, and deliberately left so. It
was not decided in [#15](https://github.com/jerome-queck/incypher-ctf/issues/15) and no measurement
exists — SWE-Marathon names it an open question in its own limitations (arXiv:2606.07682). Given
BAGEN's optimism result, feeding a model a clock it will reason optimistically about is a risk
rather than an obvious win, so v1 does not do it and v2 may test it.

## Revisit when

- A practice run has produced enough shadow-mode data to say whether the thresholds cut early, late,
  or about right. This decision was made from other people's benchmarks; ours supersede them.
- The v1 → v2 version gate, which reopens the prior version's decisions by design
  ([#18](https://github.com/jerome-queck/incypher-ctf/issues/18)) — the full anti-derailment
  subsystem is v2's, and it inherits these principles rather than being bound by them.
