# Codex Pro capacity and API-price proxies — 7 September 2026

## Question and answer

What can the Solver use to plan model and reasoning allocation when a ChatGPT Pro subscription
does not expose a known number of tokens?

**Use OpenAI's model-specific five-hour message ranges as the capacity envelope, then use observed
per-Attempt usage and outcomes to calibrate it.** OpenAI now publishes estimates for local Codex
messages on both Pro tiers. They are not fixed message limits: model, context, reasoning, tools,
retrieval, caching, task size, and local-versus-cloud execution all change the amount charged to
the shared allowance. For the Solver, the supported machine-readable view is the pinned Codex App
Server's `account/rateLimits/read`, which reports `usedPercent`, window duration and `resetsAt` by
returned `limitId`; the dashboard or `/status` exposes the human view. Missing fields remain
unknown. [Codex pricing][codex-pricing] [App Server][app-server]

API token prices are useful as a **directional relative-burn proxy**, especially because the
current model price ratios match OpenAI's token-based ChatGPT Work/Codex credit ratios. They are not
a conversion from a $200 Pro subscription into API dollars or a promise that a given Codex turn
will consume a fixed number of messages. ChatGPT and the API have separate billing systems, and API
usage is billed separately. [ChatGPT/API billing][billing] [Codex pricing][codex-pricing]

This is a live first-party OpenAI documentation snapshot taken on **7 September 2026**. Prices,
aliases, model access, promotions, and usage estimates can change.

## What “Pro 20x” means

OpenAI offers two Pro tiers with the same core capabilities:

- **Pro $100:** 5x the Plus usage allowance.
- **Pro $200:** 20x the Plus usage allowance and the highest Pro usage tier.

The multiplier is an allowance multiplier relative to Plus, not “20x messages regardless of
model,” not 20x API credit, and not unlimited Codex. [Pro tiers][pro-tiers]

OpenAI's current estimates for **local messages per rolling five-hour period** are:

| Model | Plus | Pro 5x ($100) | Pro 20x ($200) |
| --- | ---: | ---: | ---: |
| GPT-6 Astra | 5–45 | 25–225 | **100–900** |
| GPT-5.6 Sol / Daybreak Blue family | 10–100 | 50–500 | **200–2,000** |
| GPT-5.6 Terra | 25–200 | 125–1,000 | **500–4,000** |
| GPT-5.6 Luna | 250–2,000 | 1,250–10,000 | **5,000–40,000** |

These ranges are planning bands, not entitlements to their upper bound. OpenAI states that:

- cloud chats use GPT-5.6 Sol and may consume more allowance than local messages;
- local messages and cloud chats share the plan allowance, and weekly limits may also apply;
- Codex and ChatGPT Work share usage; other agentic features can share the same allowance;
- a small routine request may consume a fraction of the allowance represented by one estimate,
  while a long-running, context-heavy task can consume substantially more;
- prompt length alone is insufficient because model choice, context, reasoning, tools, retrieval,
  and caching all affect usage; and
- an active turn may finish after the limit is reached, subject to fair-use limits, but new turns
  then wait, use credits, or use another available option. [Codex pricing][codex-pricing]
  [Codex plan help][codex-plan]

The lower endpoint is one conservative scenario that trials should test, not an accepted numeric
dial. For a 5.5-hour Run, the five-hour window also does not imply a clean mid-Run refill: the
account's actual `resetsAt` matters.

## Current token rates and relative burn

### Standard API rates for ordinary-context cache reads

Prices are USD per 1 million tokens. Reasoning tokens are internal output tokens and are billed at
the output-token rate. [Token accounting][tokens]

This table is deliberately bounded to Standard processing, ordinary-context input and cache reads.
It does not model GPT-5.6's higher rate above 272K input tokens, the 1.25x cache-write rate, or
Priority processing. A trial must retain uncached input, cached input, cache writes, output and
reasoning separately rather than forcing every token into these three proxy columns.
[Sol model][sol]

| Requested route | Current underlying model | Uncached input | Cached input | Output, including reasoning |
| --- | --- | ---: | ---: | ---: |
| `gpt-daybreak-blue-latest` | `gpt-5.6-sol` | $4.00 | $0.40 | $20.00 |
| `gpt-5.6-sol` | GPT-5.6 Sol | $4.00 | $0.40 | $20.00 |
| `gpt-5.6-terra` | GPT-5.6 Terra | $2.00 | $0.20 | $12.00 |
| `gpt-5.6-luna` | GPT-5.6 Luna | $0.20 | $0.02 | $1.20 |
| `gpt-6-astra` | GPT-6 Astra | $10.00 | $1.00 | $50.00 |

Daybreak Blue is not a separate capability tier with a separately published base price. OpenAI's
current API help maps the stable alias `gpt-daybreak-blue-latest` to model ID `gpt-5.6-sol`, and
the OpenAI Daybreak overview describes Blue as GPT-5.6 Sol with more precise safeguards for
approved defensive work. It requires Trusted Access for Cyber approval. [Daybreak identifiers]
[Daybreak overview]

The live API model pages publish these effort settings:

- GPT-5.6 Sol, Terra, and Luna: `none`, `low`, `medium` (default), `high`, `xhigh`, `max`.
- GPT-6 Astra: `low`, `medium`, `high`, `xhigh`, `max`.

The API does not publish a separate per-token price for each effort. Higher effort can still cost
more in total because it generates more reasoning tokens, and those reasoning tokens are billed as
output. [Sol model][sol] [Terra model][terra] [Luna model][luna] [Astra model][astra]
[Token accounting][tokens]

These are API capabilities, not the competition Gate menu. Local Codex availability and supported
efforts are account/backend state. The pinned App Server's `model/list` is the preflight source for
the actual menu, which may include local-only choices such as Ultra and need not include every API
choice such as `none`. [App Server][app-server]

At identical token counts and Standard processing:

| Comparison | Input/cached ratio | Output/reasoning ratio |
| --- | ---: | ---: |
| Sol or Daybreak Blue vs Terra | **2x** | **1.67x** |
| Terra vs Luna | **10x** | **10x** |
| Sol or Daybreak Blue vs Luna | **20x** | **16.67x** |
| Astra vs Sol or Daybreak Blue | **2.5x** | **2.5x** |
| Astra vs Luna | **50x** | **41.67x** |

The price ratio is not the task-cost ratio. A stronger model may solve with fewer Turns or tokens;
a smaller model may fail or loop. OpenAI explicitly advises comparing the total tokens and cost
needed to complete representative tasks rather than price per million tokens or visible response
length alone. [Token accounting][tokens]

### Why the API ratio is a reasonable but bounded proxy

OpenAI's current token-based ChatGPT Work/Codex rate card uses the same relative rates:

| Model | Input credits/M | Cached-input credits/M | Output credits/M |
| --- | ---: | ---: | ---: |
| GPT-6 Astra | 250 | 25 | 1,250 |
| GPT-5.6 Sol | 100 | 10 | 500 |
| Daybreak Blue | 100 | 10 | 500 |
| GPT-5.6 Terra | 50 | 5 | 300 |
| GPT-5.6 Luna | 5 | 0.5 | 30 |

OpenAI says GPT-5.6 usage averages **5–30 credits per message**, but that average spans the family
and is not a per-model, per-effort promise. [Codex pricing][codex-pricing]

Therefore the API rates can rank likely relative burn—Luna cheapest, Sol/Blue next, Astra
highest—but cannot predict the exact subscription-percentage movement of an Attempt. The Solver
should learn empirical conversion bands from matched local trials:

`model + effort + Agent role + Challenge Category + stated difficulty + Tier + input/cached/output/reasoning tokens -> observed 5h and weekly usage movement`

Do not invent a stable “messages remaining” counter from API token cost. Use the live usage
snapshot for capacity state and the rate ratios only for planning.

## Effort and unpublished multipliers

OpenAI's Codex guidance says to start at default effort and raise it when deeper planning or
analysis is needed. Higher effort takes longer and uses more tokens. It characterizes low as fast
and light, medium as balanced, high/xhigh as deeper reasoning, and max as the hardest single-model
work. Ultra is different: it uses subagents and therefore goes beyond a single-agent run; subagent
work consumes more tokens than a comparable single-agent run. [Codex models][codex-models]
[Subagents][subagents]

No official source inspected publishes a fixed credit multiplier for `low` versus `medium`,
`high`, `xhigh`, or `max`. The token rate is unchanged; the realized reasoning/output tokens and
wall time vary. Ultra's additional-agent count and resulting multiplier are also not fixed. Speed
configuration is outside ticket 224's model/effort decision.

## Candidate implications for ticket 224

These are experiment-design implications, not accepted Solver policy. Ticket 224 and its HITL
prototype must decide whether to promote them; any accepted architectural decision belongs in an
ADR.

1. A candidate governor would treat capacity as **observable but not predictable**: read remaining
   five-hour and weekly percentages plus reset times without claiming a known future number of
   Turns.
2. Trials should test role-to-effort selection conditioned on urgency, Challenge uncertainty,
   stated difficulty, derived Tier, prior failure evidence, and remaining observed capacity rather than
   assuming a static cell.
3. Luna is worth testing for bounded classification/routing only where the success criterion is
   explicit. Its 20x/16.67x token-price advantage disappears if it causes repeated failed Turns.
4. Difficult-solving trials should include non-trivial Challenges, verified stall/progress evidence,
   and multi-Turn completion; easy single-Turn tasks cannot test escalation value.
5. Telemetry experiments should group Daybreak Blue with Sol for price/capability comparison while
   retaining the different safeguard/access route as a separate observed field.

## Facts OpenAI does not publish

The inspected first-party sources do **not** provide:

- the account's exact future token or message capacity;
- the numeric size of the five-hour or weekly allowance in credits for an included $200 Pro plan;
- a deterministic mapping from one local message or task to usage percentage;
- a per-effort credit multiplier for low through max;
- a fixed Ultra/subagent multiplier;
- a concurrency guarantee or a reserved-capacity/latency SLA for Pro;
- a promise that the low or high end of a message range applies to a given workload;
- a stable weekly-limit quantity for each model; or
- a guarantee that current prices, promotional Sol pricing, aliases, or availability will persist
  through competition day.

The pricing page says GPT-5.6 Sol's current $4/$0.40/$20 promotional price is available at least
through **21 November 2026**; this date is after the September competition, but the live page
should still be captured in the Run preflight. [Sol model][sol]

## Sources

- [Codex pricing and usage limits][codex-pricing] — model-specific five-hour estimates, task-cost
  factors, credit rates, shared allowance, and current plan tiers; fetched 7 September 2026.
- [About ChatGPT Pro tiers][pro-tiers] — $100 5x versus $200 20x meaning; updated 25 August 2026
  according to the fetched page, fetched 7 September 2026.
- [Using Codex with your ChatGPT plan][codex-plan] — shared allowance and active-turn behavior;
  updated 30 August 2026 according to the fetched page, fetched 7 September 2026.
- [Managing billing for ChatGPT and the API platform][billing] — separate billing systems and API
  billing; updated 1 September 2026 according to the fetched page, fetched 7 September 2026.
- [GPT-5.6 Sol][sol], [GPT-5.6 Terra][terra], [GPT-5.6 Luna][luna], and
  [GPT-6 Astra][astra] API model pages — current token rates, context thresholds, effort
  availability, and processing multipliers; fetched 7 September 2026.
- [OpenAI Daybreak identifiers][daybreak-identifiers] and [Daybreak overview][daybreak-overview] —
  current alias-to-model mapping and access meaning; fetched 7 September 2026.
- [Understanding and counting tokens][tokens] — reasoning-token billing and model-comparison
  caveat; updated 29 August 2026 according to the fetched page, fetched 7 September 2026.
- [Codex models][codex-models], [App Server][app-server], and [Subagents][subagents] — effort
  semantics, live model/limit discovery, and the cost consequence of subagents; fetched
  7 September 2026.

[app-server]: https://learn.chatgpt.com/docs/app-server
[astra]: https://developers.openai.com/api/docs/models/gpt-6-astra
[billing]: https://help.openai.com/en/articles/9039756-managing-your-work-in-the-api-platform-with-projects
[codex-models]: https://learn.chatgpt.com/docs/models
[codex-plan]: https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan
[codex-pricing]: https://developers.openai.com/codex/pricing
[daybreak-identifiers]: https://help.openai.com/en/articles/20001259-openai-daybreak-common-issues-and-troubleshooting
[daybreak-overview]: https://help.openai.com/en/articles/20001258-openai-daybreak-trusted-access-for-cyber-overview
[luna]: https://developers.openai.com/api/docs/models/gpt-5.6-luna
[pro-tiers]: https://help.openai.com/en/articles/9793128-about-chatgpt-pro-plans
[sol]: https://developers.openai.com/api/docs/models/gpt-5.6-sol
[subagents]: https://learn.chatgpt.com/docs/agent-configuration/subagents
[terra]: https://developers.openai.com/api/docs/models/gpt-5.6-terra
[tokens]: https://help.openai.com/en/articles/4936856-what-are-tokens-and-how-to-count
