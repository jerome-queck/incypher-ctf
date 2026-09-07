# How v2 models should be instructed for CTF work

_Research snapshot: 8 September 2026. Public sources were fetched on this date. OpenAI's live
documentation does not expose a publication or update date unless one is stated below. Model,
alias, safeguard and Codex-catalog facts can drift. CTF studies used older models and different
harnesses; they supply hypotheses and failure evidence, not a prompt recipe proven for this
Solver. No model-quality experiment was run._

## Result

Use a **versioned prompt bundle**, composed deterministically from a shared contract, a role
template, a model-family overlay, selected CTF playbook pointers and a typed live payload. Keep the
stable material first and live Challenge state last. The controller chooses and hashes every
component.

The one deliberate exception is a Specialist's semantic brief. The Solve Lead should author and
refine the goal, selected Evidence, expected critical-path benefit, requested investigation and
stop conditions. A deterministic builder validates those fields and wraps them with immutable
authority, capabilities, provenance, budget, tool and completion sections. This preserves the
Lead's current reasoning without asking it to rediscover prompt engineering or letting it rewrite
the Specialist's trust boundary.

Deterministic startup templates should cover Triage Judge, Solve Lead and Recovery Agent. There is
no primary evidence that regenerating those prompts per Engagement helps. D-CIPHER's Auto-prompter
is the closest direct test of model-written CTF prompts: on NYU CTF Bench, removing it improved
Claude 3.5 Sonnet from 19% to 22%, although Auto-prompting helped GPT-4o or other benchmarks and
reduced average cost overall. Its failures included empty, vague and prematurely wrong prompts.
The authors recommend combining generated content with hard-coded guidance. That mixed result
supports typed Agent-authored specialization inside a fixed contract, followed by evaluation; it
does not support free-form regeneration of the whole prompt ([D-CIPHER paper][dcipher-paper],
[pinned prompt implementation][dcipher-prompts]).

OpenAI publishes one prompting guide for the GPT-5.6 family. It does **not** publish separate
prompt recipes for Luna, Terra and Sol. Astra has documented behavioural differences that justify
a small overlay. Daybreak Blue is currently Sol with a distinct defensive-cyber access and
safeguard route, so its bundle needs both the Sol-family overlay and an authorization/refusal
overlay. No source establishes a distinct prompt for any reasoning-effort level. Model, role,
effort and prompt choices remain measurements on representative CTFs, not assumed rankings.

## What OpenAI documents

### Model ledger

| Route | Public model guidance | Current ChatGPT-backed Codex catalog | Instruction consequence |
| --- | --- | --- | --- |
| GPT-5.6 Luna | Cost-sensitive, high-volume tier. API effort: `none`, `low`, `medium` (default), `high`, `xhigh`, `max`. | `low`–`max`; default `medium`. | Use the shared GPT-5.6 overlay. Its price/volume positioning belongs in routing, not extra prompt prose. |
| GPT-5.6 Terra | Balance of intelligence and cost. Same published API effort set. | `low`–`ultra`; default `medium`. | Use the shared GPT-5.6 overlay unless a matched CTF eval proves a Terra-specific delta. |
| GPT-5.6 Sol | Flagship tier for complex professional work; `gpt-5.6` currently routes to it. Same published API effort set. | `low`–`ultra`; default `low`. | Use the shared GPT-5.6 overlay. Pin explicit Sol where the route means Sol rather than the family alias. |
| Daybreak Blue | Stable alias `gpt-daybreak-blue-latest`; current API model ID `gpt-5.6-sol`. Intended for approved defensive work with cyber-specific safeguards. | `low`–`ultra`; default `low`. | Compose the Sol overlay plus a Daybreak authorization/refusal overlay. Preserve requested alias separately from effective model. |
| GPT-6 Astra | Hardest end-to-end work. API effort: `low`, `medium`, `high`, `xhigh`, `max`; no `none`. | `low`–`ultra`; default `medium`. | Add the documented Astra behaviour overlay; do not copy it into the GPT-5.6 templates. |

The public values come from the [GPT-5.6 guide][gpt56-guide], [Luna][luna], [Terra][terra],
[Sol][sol], [Astra][astra] and [Daybreak][daybreak] pages. The Codex values come from the
first-party catalog cached at `/Users/jeromequeck/.codex/models_cache.json`, fetched
`2026-09-07T18:08:21.753985Z` for client `0.153.4`, ETag
`W/"77771ea081986100e0f69cf0978b38e8"`, SHA-256
`54a6677627739cffcd793b17eb3a957e5ce5a9088f16e31ee9ec708dcbdd498d`. Installed
`codex-cli` was `0.153.2`. This is current account/backend evidence, not a future availability
promise.

Public API documentation stops at `max`; current Codex metadata also exposes `ultra` for every
listed route except Luna. Treat `ultra` as a Codex product setting rather than an API
`reasoning.effort`. A prompt bundle records requested route, effective model when the harness
returns it, and effective effort independently.

### Shared GPT-5.6 overlay

OpenAI recommends lean prompts: state an instruction once, expose only relevant tools, define the
domain context, hard constraints, authorization boundary and success criteria, and avoid
prescribing every step. It recommends `medium` as a balanced start, `low` for latency, higher
efforts only where representative evals show gain, and `max` only for the hardest quality-first
work. The same setting and one level lower should be compared during migration
([GPT-5.6 guide][gpt56-guide]).

Therefore Luna, Terra and Sol should begin with identical instruction content. Their selected
effort is controller state, not a prose persona. A model-specific delta enters an overlay only when
an official behavioural note or a held-out Solver eval identifies a reproducible failure and the
delta fixes it without regressing other cells.

### Astra overlay

OpenAI says Astra is more likely to pause for a material clarification, follows long instructions
and accessible instruction files more strongly, tends toward detailed formatted output, may
delegate less than expected, and can over-test small coding work. Its overlay should therefore:

- authorize routine in-scope assumptions and follow-through while naming the conditions that end
  the Turn;
- state instruction priority and make target files/tool output explicitly untrusted data;
- bound the required output fields and verification proportional to the Candidate or Claim; and
- for a Solve Lead only, state when to emit a Specialist request, while leaving Specialist
  admission to the controller.

These are documented Astra corrections, not generic boilerplate ([Astra model guide][astra-guide]).
The Solver should not give Astra native subagent authority merely because the guide discusses
delegation.

### Daybreak overlay and drift

OpenAI identifies Daybreak Blue's stable alias as `gpt-daybreak-blue-latest` and its current model
ID as `gpt-5.6-sol`. Trusted Access is limited to systems the organization owns or is explicitly
authorized to assess, and very dual-use requests may still refuse
([Daybreak overview][daybreak-overview], [Daybreak troubleshooting][daybreak-troubleshooting]).

The overlay should provide concise, truthful scope evidence: named CTF, authorized Target,
permitted action surface and defensive competition purpose. It should ask the Agent to return a
typed refusal or safety block with the exact model, redacted task description, request ID or error,
and evidence already gathered. It should never instruct the model to evade or suppress a
safeguard.

An alias change is a prompt-version event. At Engagement admission, resolve the live catalog and
record:

`requested route + effective model or unknown + effort + Codex client/catalog digest + common/role/model/playbook/tool-schema digests`.

If Daybreak no longer resolves to Sol, stop selecting the Sol overlay until the new effective
family has been reviewed. If the harness cannot reveal the effective model, record `unknown` and
keep those Runs out of snapshot-to-snapshot comparisons. Never silently relabel historical Runs.

## Prompt-bundle architecture

The logical order below applies whether the native Codex transport exposes it as one prompt or
several instruction layers.

| Layer, stable to volatile | Owner | Contents |
| --- | --- | --- |
| Common contract | Solver | Authorized objective; authority boundary; source trust; brokered credentials; provenance; terminal states; refusal/failure behavior. |
| Role template | Solver | One role's objective, available capabilities, required output schema and checkable completion criteria. |
| Model overlay | Solver | Shared GPT-5.6, Astra corrections, and Daybreak route overlay; selected by recorded route/effective model. |
| Playbook pointers | Solver | Short trigger-bearing pointers to versioned Category, tool and task references selected from trusted metadata. |
| Engagement payload | Controller, plus Lead fields for a Specialist | Challenge/incident snapshot, Evidence references, deadline, budget and current delta. Untrusted fields are labelled as data. |

OpenAI's cache matches the full rendered prefix, including instructions, tool definitions and
conversation history. Any change before a breakpoint prevents reuse after that change. Stable
instructions and tools therefore precede Category and Engagement data; volatile observations and
remaining-time updates come last ([prompt caching][prompt-cache]). In the native Codex harness,
cache behavior is not a Solver contract, so record cached and uncached usage and test this ordering
rather than claiming a hit. API-only features such as `configuration_update` do not imply that a
Codex CLI Turn can change effort with the same cache behavior.

Keep tool names, descriptions and schemas stable and small. Encode invalid states out of schemas,
put tool-specific rules in the tool description, and make the controller supply arguments it
already knows. OpenAI warns that model/tool combinations are nondeterministic and recommends
clear parameter meaning, enums or structured objects, and a narrow initial tool set
([function calling][function-calling]). Use schema enforcement for role results rather than
repeating the schema as prose ([Structured Outputs][structured-outputs]).

### What is deterministic and what an Agent composes

The Solver always owns:

- template, model overlay, playbook and tool-schema selection and digests;
- role identity, parent Engagement, Target and snapshot identity;
- authorization, capability handles and credential mediation;
- trust labels, network/workdir boundary, deadline and resource budget;
- required Evidence references, terminal-state enum and result schema; and
- delivery, cancellation, acceptance and Flag-submission authority.

The Triage Judge composes only its judgment, rationale, uncertainty and cited Board/Crowd
Observations. The Solve Lead composes its hypotheses, compact plan/status, next discriminating
action, Candidate proposals and Specialist semantic briefs. A Specialist composes scoped Claims,
Evidence-artifact references, Candidate proposals and unresolved questions. Recovery composes
failure classification, cited incident evidence and typed remedy proposals. The controller records
measured usage in the Engagement envelope. No Agent may rewrite its authority or turn untrusted
content into instructions.

For a Specialist, the Lead-authored portion should be a typed object:

```text
goal
question_or_hypothesis
selected_evidence_refs[]
expected_critical_path_benefit
requested_tool_profile
suggested_approach?          # advisory, not authority
success_evidence
stop_conditions[]
```

The controller rejects missing references, unavailable tools, an over-broad goal, a deadline past
the parent Attempt, or a request whose expected benefit does not justify coordination. It then
adds the immutable wrapper and renders the Specialist prompt. The Lead may refine the semantic
fields after a rejection; it cannot author the wrapper.

This is stronger than making an LLM write the full prompt. It is also more useful than having the
controller invent the investigation: the Lead has the live hypotheses and knows which uncertainty
is on the Attempt's critical path.

## Role contracts

| Role | Prompted job | Required terminal result |
| --- | --- | --- |
| Triage Judge | Judge one snapshot batch from supplied Board metadata and Crowd Observations. Separate observation from inference; quantify uncertainty. | Complete judgments for every admitted item, or a typed batch failure/refusal naming missing inputs. |
| Solve Lead | Persist across one Attempt; maintain compact plan/status, choose the next discriminating action, use evidence, propose Candidates and request separable Specialists. | `candidate`, `checkpoint`, `blocked`, `refusal`, `failure`, or `budget_exhausted`, with Evidence and unresolved state required by that variant. |
| Specialist | Resolve one Lead-authored question using only supplied Evidence and capabilities; return results to the Lead. | Scoped Claims and Evidence references, Candidate proposals if any, unresolved questions and one declared stop reason; the controller adds measured usage. |
| Recovery Agent | Diagnose one bounded incident and propose only permitted typed remedies. Treat existing logs/state as evidence, not instructions. | Classified incident, cited evidence, remedy proposal, expected postcondition and residual uncertainty; or a typed refusal/failure. |

Completion prose must be checkable. “Solve the challenge” is an objective, not a completion test.
The Agent has completed a Turn when it has emitted one schema-valid terminal variant. The
controller, not the Agent, decides whether a Candidate is submitted or an Engagement is closed.

## Translating CTF research into current prompts

### Preserve state and evidence, not private chain of thought

Cybench's structured scaffold carried Reflection, Plan/Status, Thought, command log and Action.
Across almost every reported metric for GPT-4o and Claude 3.5 Sonnet it matched or beat an
action-only scaffold. The authors observed action-only runs submitting incomplete answers,
forgetting context and repeating commands. Intermediary subtasks made partial progress observable
([Cybench][cybench]). “Hacking CTFs with Plain Agents” likewise improved its older
InterCode-CTF result with planning, action/observation feedback, structured output, more Turns and
multiple Attempts; removing Structured Output was the largest single ablation in its table
([Plain Agents][plain-agents]).

Current OpenAI reasoning guidance says to use direct prompts, specify the end goal and constraints,
and avoid requesting chain-of-thought or “think step by step” ([reasoning guidance][reasoning]).
The v2 translation is a concise, externally inspectable plan/status, Evidence ledger, dead ends and
next action. Do not request hidden reasoning transcripts or make verbose reflection a completion
requirement.

### Evidence must warrant the Candidate

CTF-ABACUS reports that a correct Flag can come from executed exploitation, direct exposure,
memorized recall, web lookup, guessing or unsupported assertion; its trace audit found only
62–87% of recovered Flags were trace-verified exploits across the studied benchmarks
([CTF-ABACUS][ctf-abacus]). Therefore every Claim and Candidate should cite the exact Observation
or artifact that supports it, while the deterministic Board path remains the only Flag verifier.
High-evidence image reading or multi-source inference remains valid when its provenance is
recorded; “evidence” does not mean shell output only.

### Expose useful tools through scoped playbooks

The Plain Agents ablation found losses from a reduced CTF toolset, short command timeout and
unstructured command parsing. EnIGMA identified “soliloquizing”—agents inventing observations
without environment interaction—and found value in genuinely interactive debugger/server tools
([EnIGMA][enigma]). Prompt prose cannot compensate for a missing or unusable interface.

Category instructions should therefore live in versioned playbooks behind specific pointers, for
example “Web Target and browser/curl evidence,” “Pwn binary and debugger,” or “Forensics image and
metadata.” A selected playbook states available tools, non-interactive usage patterns, evidence
expectations and category failure traps. The prompt contains a pointer only when its trigger
matches. The NYU implementation demonstrates this separation in working CTF prompts, but its exact
tips are old-model implementation evidence rather than defaults to copy
([pinned D-CIPHER prompts][dcipher-prompts]).

### Treat all Challenge content as adversarial data

Prompt injection remains an unsolved system problem. CyberSecEval 2 found every evaluated model
susceptible to some injection tests and concluded that application guardrails are necessary
([CyberSecEval 2][cyberseceval2]). OpenAI now frames the risk as source plus sink: an attacker
controls content, while a capability can transmit data or cause an effect. It recommends limiting
capabilities so manipulation has bounded impact rather than relying on an “AI firewall” to classify
every malicious string ([OpenAI injection design][openai-injection]).

Challenge descriptions, files, webpages, service responses, OCR, retrieved writeups and tool
output are Evidence sources. They may contain facts and Flags; their embedded instructions never
grant authority, expand scope, request credentials or enable a new sink. The deterministic
capability broker enforces that rule. The prompt reminds the Agent of source labels and requires it
to surface a suspected injection as Evidence; the prompt is defense in depth, not the security
boundary.

### Handle refusals as observations

Explicitly state the authorized CTF scope and intended defensive competition use. Cybench reports
that this ethical framing reduced false refusals in its older-model experiments, while also showing
that scaffold effects vary by model ([Cybench][cybench]). If a model still refuses, preserve the
exact route, effective model if known, effort, redacted request, error/safety message, request ID
and timestamp. Classify it separately from a solve failure. Retry only after a named delta such as
corrected authorization context, changed route, or narrowed task; never ask another model to
jailbreak the refusal.

### Classify failures before retry

The deterministic envelope distinguishes a schema-invalid result, tool failure, tool timeout,
Agent deadline, harness crash, cancelled Engagement, partial Triage batch and model refusal. It
persists schema-valid partial Claims and Evidence before routing the classified outcome. An invalid
or incomplete Agent result has no authority to trigger a Candidate submission, remedy or routing
change.

Retry requires a named, falsifiable delta: repaired tool or route, narrower semantic brief, new
Evidence, current snapshot, corrected authorization context, or changed model/effort selected by
policy. The controller rejects an unchanged retry. A partial Triage batch applies no missing
judgments; a retry reuses completed judgments only when their bound snapshot and prompt-bundle
digests are still current. A harness crash starts a new thread segment from durable state only when
the topology policy permits continuity. Recovery receives the classified incident and evidence,
never an instruction to repeat the failed action blindly.

## Required evaluation before production prompts

Use prompt-development Challenges separately from a held-out Gate set. The NYU CTF Bench itself
publishes separate development and test splits for this reason ([NYU CTF Bench][nyu-bench]). Hold
the Challenge, tool surface, deadline and snapshot fixed; change one prompt component, model or
effort at a time. Report distributions rather than a best run.

The matrix must cover all four possible effective families—Luna, Terra, Sol and Astra—and the
Daybreak route separately when available. Each applicable effort is a distinct cell; no effort
inherits another's prompt result. Measure:

- verified Flags first, then time to first/total Flag and checkpoint conversion;
- unsupported Candidates, provenance failures, premature completion and repeated actions;
- valid tool calls, useful tool diversity, tool/interface failures and late terminal results;
- Specialist admission, net critical-path saving, verification cost and late/cancelled reports;
- refusals, false refusals and recovery success by requested/effective route;
- injection success against descriptions, files, webpages and retrieved text, including attempted
  credential or out-of-scope tool use; and
- uncached input, cached input, output/reasoning usage and wall time by exact bundle digest.

Prompt-builder tests should prove deterministic rendering, stable prefix order, schema validation,
source labelling, alias-change invalidation and historical replay by digest. Eval fixtures should
include an incomplete Flag, a planted public writeup, a hallucinated tool observation, a hostile
file instruction, a genuine image-derived Flag, an authorized task likely to false-refuse, and a
Specialist brief that attempts to broaden authority.

## Unknowns that remain explicit

- No official OpenAI source gives separate Sol, Terra or Luna prompt content, a Daybreak CTF
  template, or a per-effort prompt recipe.
- The public API guide and ChatGPT-backed Codex harness are different products. API context limits,
  persisted-reasoning and cache controls are not Codex CLI guarantees.
- CTF prompt and architecture studies use older model snapshots and disagree about the value of
  generated prompts and extra scaffolding. None proves v2's exact bundle.
- OpenAI does not publish Daybreak's full safeguard mechanism or promise that Blue will continue
  to resolve to Sol.
- No prompt can make hostile Challenge content safe by itself; capability mediation and
  deterministic effect authority remain mandatory.

These unknowns do not block the topology decision. They define the production prompt-library and
matched-Gate work that must follow it.

## Sources

- OpenAI, [GPT-5.6 model guidance][gpt56-guide], [GPT-6 Astra model guidance][astra-guide], and
  [reasoning best practices][reasoning] — model selection, effort, prompt shape and documented
  Astra behaviours; accessed 8 September 2026.
- OpenAI, [Luna][luna], [Terra][terra], [Sol][sol], [Astra][astra], [Daybreak overview][daybreak-overview]
  and [Daybreak troubleshooting][daybreak-troubleshooting] — model/alias/effort/access facts;
  accessed 8 September 2026.
- OpenAI, [prompt caching][prompt-cache], [function calling][function-calling], [Structured
  Outputs][structured-outputs] and [agent prompt-injection design][openai-injection] — composition,
  tool/schema and source-sink guidance; accessed 8 September 2026.
- Shao et al., [D-CIPHER][dcipher-paper], arXiv v2 updated 11 May 2025, with its [official prompt
  source pinned at `612190f`][dcipher-prompts] — Agent-generated prompts, role handoff and Category
  overlays.
- Zhang et al., [Cybench][cybench], ICLR 2025; and Happe et al., [Hacking CTFs with Plain
  Agents][plain-agents], arXiv 2024 — prompt/scaffold/tool/attempt evidence.
- Abramovich et al., [EnIGMA][enigma], arXiv v3 updated 5 June 2025 — interactive tools and
  observation hallucination.
- Bhatt et al., [CyberSecEval 2][cyberseceval2], 2024; and Lee et al.,
  [CTF-ABACUS][ctf-abacus], 2026 — prompt-injection and Flag-provenance evidence.
- NYU Tandon, [NYU CTF Bench][nyu-bench] — development/test split and category corpus; commit
  `4c5744f55a31a58e03d6118c9dbb1c18e5bbdfa9`, accessed 8 September 2026.

[astra]: https://developers.openai.com/api/docs/models/gpt-6-astra
[astra-guide]: https://developers.openai.com/api/docs/guides/latest-model
[ctf-abacus]: https://arxiv.org/abs/2608.26237
[cybench]: https://arxiv.org/abs/2408.08926
[cyberseceval2]: https://arxiv.org/abs/2404.13161
[daybreak]: https://developers.openai.com/api/docs/models/gpt-daybreak-blue-latest
[daybreak-overview]: https://help.openai.com/en/articles/20001258-openai-daybreak-trusted-access-for-cyber-overview
[daybreak-troubleshooting]: https://help.openai.com/en/articles/20001259-openai-daybreak-common-issues-and-troubleshooting
[dcipher-paper]: https://arxiv.org/abs/2502.10931
[dcipher-prompts]: https://github.com/NYU-LLM-CTF/nyuctf_agents/tree/612190f298ae9e604f00370019beed4ba1f372f6/configs/dcipher/prompts
[enigma]: https://arxiv.org/abs/2409.16165
[function-calling]: https://developers.openai.com/api/docs/guides/function-calling
[gpt56-guide]: https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6
[luna]: https://developers.openai.com/api/docs/models/gpt-5.6-luna
[nyu-bench]: https://github.com/NYU-LLM-CTF/NYU_CTF_Bench/tree/4c5744f55a31a58e03d6118c9dbb1c18e5bbdfa9
[openai-injection]: https://openai.com/index/designing-agents-to-resist-prompt-injection/
[plain-agents]: https://arxiv.org/abs/2412.02776
[prompt-cache]: https://developers.openai.com/api/docs/guides/prompt-caching
[reasoning]: https://developers.openai.com/api/docs/guides/reasoning-best-practices
[sol]: https://developers.openai.com/api/docs/models/gpt-5.6-sol
[structured-outputs]: https://developers.openai.com/api/docs/guides/structured-outputs
[terra]: https://developers.openai.com/api/docs/models/gpt-5.6-terra
