# Productive and token-efficient agent loops

_Research snapshot: 5 September 2026. This answers the decision ticket with constraints and
falsifiable hypotheses. It does not select an architecture. “Productive” means verified progress
toward an accepted Flag per wall-clock minute and per separately reported token class—not activity,
claims, cache hits, or transcript length._

## Answer

The productive loop keeps the model's repeated region small, stable and judgement-heavy. Durable
facts, deterministic extraction, bulk filtering and long compute live outside model context; each
model turn receives bounded observations and leaves a machine-readable progress boundary. Retry
spend is earned by new evidence or a genuinely different lane. Delegation is worthwhile only when
work is independent enough to repay its coordination and verification overhead. Compaction is a
milestone transition whose retained facts are tested, not an invisible licence to forget.

The evidence does **not** support “more agents”, “more context”, “more effort” or “more tokens” as a
general answer. It supports measuring each as an intervention against accepted Flags and durable
progress. OpenAI's current harness guidance makes repeated overhead multiplicative, caps ordinary
tool output at 10,000 tokens, and preserves cacheable prefixes with append-only history and
deterministically ordered tools ([OpenAI harness engineering][openai-harness]). In an OpenAI
ARC-AGI-3 experiment, retained reasoning plus compaction moved one score from 13.3% to 38.3% while
using about six times fewer output tokens; that is strong evidence that continuity can beat
reconstruction, but it is one non-CTF harness experiment, not an expected Solver gain
([GPT-5.6 builder guide][openai-builder]).

## Evidence boundary

The old-workspace audit is pinned to `jerome-queck/ctf-workspace` commit
[`4631053`][old-head], the final pre-teardown snapshot. Its current Desktop checkout has extensive
uncommitted TFC setup changes, so none of those changes is treated as historical evidence. The
commit retains only 13 attempt `findings.md` files, all from the Athena backsolve. It therefore
shows mechanisms and recorded incidents, not a representative solve-rate or token benchmark.

The current evidence is the four checked-in Run streams:
[`brunner-gate-1`][gate-1], [`brunner-gate-2`][gate-2],
[`brunner-gate-3`][gate-3], and [`v1-gate`][v1-gate]. Counts below were recomputed directly from
their JSONL. `brunner-gate-1` is partial: a second Attempt is open, one `step-begin` lacks a matching
`step-end`, and the Run has no close. The other three have balanced Step boundaries and
`write_failures: 0`. Token fields also changed during the gate sequence: zero means unmeasured for
several invocations, not free. In current code, `tokens_in` is uncached input and
`tokens_in + cache_read` is total input context ([Codex usage mapping][usage-mapping]).

| Run | Closed Attempts | Step ends | Flags | Close causes | Measured uncached input / cache read / output | Run wall |
|---|---:|---:|---:|---|---:|---:|
| `brunner-gate-1` (partial) | 1 | 39 | 0 | 1 crashed | 97,551 / 353,305 / 4,677 | unclosed |
| `brunner-gate-2` | 5 | 136 | 0 | 3 step-cliff, 1 novelty, 1 budget | 0 / 0 / 0 (unmeasured) | 799.4 s |
| `brunner-gate-3` | 9 | 376 | 0 | 9 step-cliff | 118,581 / 1,381,829 / 9,986 | 3,116.5 s |
| `v1-gate` | 11 | 452 | 1 | 8 step-cliff, 2 novelty, 1 Flag | 266,041 / 1,677,224 / 13,802 | 3,297.2 s |

Across all four streams, 20 of 26 closed Attempts ended at the step cliff, only 4 had a non-empty
`approach_label`, and none of 1,003 closed Steps carried a checkpoint. The stream did preserve 227
content-addressed Claims, and the three complete Runs closed without writer failures: provenance
and mechanical replay are strengths, while semantic progress/retry state is too sparse to judge
which spend advanced the solve.

The final gate makes the distinction concrete:

- The Flag Attempt took 91.9 seconds and 32 Steps. Its one completed Codex invocation reported
  53,803 uncached input, 595,899 cache-read input and 2,720 output tokens.
- The second Blackboard Attempt took 342.5 seconds and 51 Steps. Seven of eight Codex invocations
  completed and reported 212,238 uncached input, 1,081,325 cache-read input and 11,082 output
  tokens, but no Flag.
- Ten of 18 `v1-gate` Codex wrapper invocations were killed with exit `-9`; their inclusive
  durations totalled 1,871.8 seconds, 56.8% of Run wall time. They may have emitted useful Claims
  before termination, so this is **unsettled work with missing final usage**, not proof of waste.
- Deterministic recon/submission tools accounted for 194 of 452 Step ends. The two North Star
  Attempts shared 13 of their 21 combined unique normalized commands (Jaccard 0.619), including the
  same deploy and recon cascade. Retry replay is therefore directly visible even though its token
  cost is not fully visible.

## What the old workspace got right—and what failed

The useful ideas are contracts, not its topology:

- **Durable, scoped memory.** Board state, challenge metadata, one Attempt record, scripts and dead
  ends lived on disk; the resume ritual read only the board and active challenge rather than the
  entire history ([context management][old-context]). All 13 retained Attempt records have an
  `approach` field, stronger than the current streams' 4/26 non-empty close labels.
- **Compute/model separation.** Long compute was detached and polled so waiting consumed wall time
  and machine resources, not an open model turn ([context management][old-context]).
- **Verification over narration.** A fresh nonce had to round-trip with the candidate after the
  tool channel had twice rendered fabricated success; service-gated results required a fresh
  instance ([operating discipline][old-discipline]). These are valuable evidence boundaries.
- **Cost-aware delegation.** The workspace recorded roughly 1–2 minutes per subagent round trip and
  reserved fan-out for high-tier work; it also admitted that only one or two deep fan-outs were
  meaningfully monitorable ([operating discipline][old-discipline]).

The same audit exposes failure modes:

- The generated anti-grind block is 6,651 bytes for a one-Attempt challenge. Its changing Attempt
  list comes **before** the large repeated `LOOP_CONTRACT`, although prefix caching only reuses an
  exact prefix. A retry therefore changes bytes before the static policy it most wants cached
  ([prompt composer][old-prompt]; [OpenAI prompt caching][openai-cache]).
- The contract says there is always another angle and forbids stopping before the wall, then later
  permits `no-flag` if every avenue is exhausted. This semantic tension encourages activity until
  kill instead of an evidence-based stop. Its own history records a 29-minute runaway and leaked
  containers ([prompt composer][old-prompt]; [operating discipline][old-discipline]).
- Retrospective prose carries useful incidents, but there is no joined event series for token
  spend, wall time, tactic identity, verification and outcome. The current Run stream repairs much
  of the provenance gap, yet the missing usage on killed invocations and empty progress fields
  still prevent productivity attribution.
- Memory was bounded by counts (14 approaches, 16 dead ends), not information value. It could
  replay unsafe or irrelevant wording verbatim into every future prompt; the workspace itself
  records that this amplified policy-wall vocabulary ([prompt composer][old-prompt];
  [operating discipline][old-discipline]).

Independent primary evidence points in the same direction. SWE-agent found that an agent-specific
interface mattered materially: a 100-line viewer, terse search results, syntax checks at edit time,
and explicit success text for empty output; its paper reported 12.5% SWE-bench and 87.7%
HumanEvalFix pass@1 in that historical model/eval setting ([SWE-agent ACI][swe-agent-aci];
[SWE-agent paper][swe-agent-paper]). Anthropic's production research system reported 90.2% better
performance than its single-agent baseline and up to 90% lower research time from parallelism, but
also about 15 times chat token use and poor fit where work is highly interdependent. Its early
failures included 50-agent over-dispatch, duplicated work and vague task boundaries
([Anthropic multi-agent system][anthropic-multi]). Those numbers justify a delegation experiment,
not importing a research topology into CTF solving.

## Design constraints

1. **Optimize a vector, not a token scalar.** Primary outcome is accepted Flags. Report verified
   intermediate milestones, wall time, uncached input, cache read/write, output, model calls, tool
   calls and unmeasured intervals separately; never convert missing usage to zero.
2. **Make progress durable at every action boundary.** A boundary names the current hypothesis,
   action, new observation, evidence reference, phase, dead-end reason and next discriminating test.
   Free-form Claims remain evidence, not scheduler state.
3. **Keep stable bytes first.** Version and hash the fixed objective, authority, tool schema and
   verification contract. Append Attempt-specific facts and new observations after them. Keep tool
   definitions and order deterministic.
4. **Bound observations without destroying provenance.** Return a small typed summary plus byte
   count, digest and artifact pointer. Preserve head/tail when truncating. Let the model request a
   targeted slice rather than ingest an entire log. OpenAI's shell design similarly caps output and
   marks omissions ([OpenAI computer environment][openai-environment]).
5. **Move deterministic recurrence out of inference.** Intake, file typing, extraction, filtering,
   aggregation and duplicate detection are code. Content-address their results so a retry reuses
   unchanged facts rather than redeploying and rerunning the same cascade.
6. **Retry on a falsifiable delta.** A successor must identify what changed: new evidence, tactic,
   tool, model/effort, environment, or repaired failure. Identical work is rejected or charged to a
   repeat metric. Policy/config/quota/transport walls are classified separately from solve failure.
7. **Budget hierarchically and abort observably.** Run, Challenge, Attempt, model turn, tool and
   detached job each have explicit remaining wall/resource budgets. A kill closes the boundary and
   preserves partial usage, Claims, child processes and a restart-safe reason.
8. **Delegate only separable work.** Each child gets one non-overlapping objective, evidence packet,
   output schema, budget and stop condition. Dispatch requires predicted critical-path saving above
   coordination plus verification cost; never fan out merely because capacity exists.
9. **Compact at tested milestones.** Keep authoritative state outside the summary. After compaction,
   assert retention of objective, authority, pending side effects, hypotheses, dead ends, artifact
   pointers and budgets. Compact after a phase, not every turn; pass native compaction items through
   opaquely as OpenAI specifies ([OpenAI compaction][openai-compaction]).
10. **Verification is an independent observation.** A candidate, completion claim or cached result
    cannot become productive work until an external verdict, deterministic checker, or fresh
    reproduction binds it to the exact artifact and run.

## Measurable hypotheses for the next prototype/eval

Use a paired, stratified Challenge corpus and replayable environment. Hold model snapshot, effort,
tool surface, prompt content, files and time budget constant; vary one constraint at a time. Run
enough repeats to report distributions, not a best run. A Flag remains the primary outcome;
pre-registered challenge-specific checkpoints are secondary.

| Hypothesis | Intervention | Pass measure |
|---|---|---|
| Stable-prefix ordering pays | Fixed policy/tools first; volatile Attempt state last | Higher `cache_read / (tokens_in + cache_read)` and lower first-action latency, with no loss in accepted Flags |
| Durable delta briefs prevent grind | Inject only typed prior hypotheses, evidence and dead ends | At least 50% fewer repeated normalized commands on retry and more distinct falsifying tests per 10k uncached tokens |
| Content-addressed recon buys time | Reuse unchanged recon outputs across Attempts | At least 50% fewer deterministic recon Steps and lower time-to-first-new-experiment, with identical recon digests |
| Semantic checkpoints beat activity cliffs | Close/update progress after each discriminating experiment | Lower step-cliff share and higher checkpoint-to-Flag conversion; no increase in false progress on planted dead ends |
| Abort-aware accounting closes the blind spot | Persist usage and Claims incrementally through forced kill | Under 5% of model-active wall time has unknown usage after SIGKILL; no duplicated side effect after resume |
| Compaction preserves useful continuity | Compact only at phase thresholds; validate retained invariants | Lower uncached input and reconstruction Steps with 100% retention of the pre-registered state fields |
| Delegation has a separability threshold | Gate fan-out by independent lanes and predicted critical path | Better median wall time only on separable cases, and better or equal Flags per total token; reject if coordination dominates |
| Verification spend prevents expensive false success | Fresh nonce/checker/instance reproduction before admission | Zero admitted planted fake Flags and zero success from missing/unrendered output; report verification wall/token overhead |
| Effort escalation must be evidence-earned | Raise effort only after a named capability gap | Better Flags per usage-window movement than automatic retry escalation; reject if output grows without checkpoint gain |

Do not collapse cached and uncached input into a synthetic “token cost” until the actual Codex
subscription/usage weighting is observable. Track both, plus usage-window movement, and let the
later model-access decision define the cost function.

[openai-harness]: https://openai.com/index/gpt-5-6-frontier-intelligence-efficiency/
[openai-builder]: https://openai.com/index/builders-guide-to-gpt-5-6/
[openai-environment]: https://openai.com/index/equip-responses-api-computer-environment/
[openai-compaction]: https://developers.openai.com/api/reference/java/resources/responses/methods/compact
[openai-cache]: https://openai.com/index/api-prompt-caching/
[anthropic-multi]: https://www.anthropic.com/engineering/multi-agent-research-system
[swe-agent-aci]: https://github.com/SWE-agent/SWE-agent/blob/main/docs/background/aci.md
[swe-agent-paper]: https://arxiv.org/abs/2405.15793
[old-head]: https://github.com/jerome-queck/ctf-workspace/tree/463105313f82817d74cc0df67a648b3e698d3b10
[old-context]: https://github.com/jerome-queck/ctf-workspace/blob/463105313f82817d74cc0df67a648b3e698d3b10/docs/CONTEXT-MANAGEMENT.md
[old-discipline]: https://github.com/jerome-queck/ctf-workspace/blob/463105313f82817d74cc0df67a648b3e698d3b10/docs/AGENTS-TEAM.md#operating-discipline-shakedown-lessons--binding-for-claude-and-codex
[old-prompt]: https://github.com/jerome-queck/ctf-workspace/blob/463105313f82817d74cc0df67a648b3e698d3b10/toolkit/compose-prompt
[gate-1]: ../../runs/brunner-gate-1.jsonl
[gate-2]: ../../runs/brunner-gate-2.jsonl
[gate-3]: ../../runs/brunner-gate-3.jsonl
[v1-gate]: ../../runs/v1-gate.jsonl
[usage-mapping]: ../../solver/codex.py#L686-L705
