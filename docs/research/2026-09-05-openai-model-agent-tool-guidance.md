# OpenAI model, agent and tool guidance for the Solver

_Research snapshot: 5 September 2026. Public OpenAI documentation was fetched through OpenAI's
developer-documentation service on this date. Repository evidence is pinned to the named commit;
local Codex evidence records the exact catalog fetch and completed-turn timestamps. “Documented”
below means a first-party source states or implements it. “Solver implication” is an inference for
this repository. No API-key request, Codex quota experiment, or model-quality benchmark was run._

## Result

The supported foundation is the Responses protocol with a small, stable, strict tool surface;
complete output-item and `call_id` preservation; one deliberate conversation-state strategy;
explicit limits and terminal-state handling; durable Solver-owned observations; and evals over
both outcomes and traces. OpenAI's guidance supports changing model and reasoning settings only by
representative measurement, not by assuming that the largest model or highest effort wins.

The four requested Codex choices are not interchangeable identifiers:

- GPT-6 Astra, GPT-5.6 Sol and GPT-5.6 Luna are public API model IDs and also appear in the current
  ChatGPT-backed Codex catalog.
- `gpt-daybreak-blue-latest` is a moving API alias, currently targeting `gpt-5.6-sol`, with a
  defensive-cyber safeguard/access policy. It is also a distinct Codex catalog choice.
- Public API capabilities do not describe the actual Codex-subscription harness. On this machine,
  the Codex catalog advertises a 272,000-token context for all four, different effort choices and
  defaults from the API, and completed local turns for all four. The API model pages advertise a
  1,050,000-token context. These are separate products and accounting paths.
- The public sources establish no Solver-specific quality or throughput ordering. Luna-max cheap
  throughput, Sol-medium quality, Astra uplift, and Daybreak routing therefore remain hypotheses
  to test by Flags, wall time, tokens, cached tokens and Codex subscription usage.

## Model and access ledger

| Requested choice | Public API identity at snapshot | Public API facts | Observed ChatGPT-backed Codex facts | Solver implication |
| --- | --- | --- | --- | --- |
| GPT-6 Astra | `gpt-6-astra`; the model page lists only that current snapshot | Released 3 Sep 2026. Responses, Chat Completions and Batch; tool calling requires Responses. 1,050,000 context, 128,000 max output. API effort `low` through `max`; no `none`. | Catalog fetched 2026-09-05 07:12:47Z lists it with 272,000 context, default `medium`, and `low`, `medium`, `high`, `xhigh`, `max`, `ultra`. A local completed assistant turn exists at 2026-09-05 03:45:17Z. | Treat Astra as a separate Codex route. Do not configure it from the API limit/default table. Measure uplift and usage. |
| GPT-5.6 Sol | `gpt-5.6-sol`; `gpt-5.6` routes to it | Released 9 Jul 2026. Responses, Chat Completions and Batch. 1,050,000 context, 128,000 max output. API effort `none` through `max`, documented default `medium`. | Same catalog lists 272,000 context, default `low`, and `low` through `ultra`. A completed assistant turn exists at 2026-09-05 07:19:55Z. | Pin the explicit Sol name rather than the `gpt-5.6` family alias. Treat Sol-medium as an experiment, because Codex currently defaults to low. |
| GPT-5.6 Luna | `gpt-5.6-luna`; model page lists only that current snapshot | Released 9 Jul 2026 for efficient, high-volume work. Responses, Chat Completions and Batch. 1,050,000 context, 128,000 max output. API effort `none` through `max`, documented default `medium`. | Same catalog calls it a fast, affordable agentic coding model; 272,000 context; default `medium`; `low` through `max`, with no `ultra`. A completed assistant turn exists at 2026-09-05 07:11:40Z. | “Efficient/high-volume” is positioning, not evidence that Luna-max yields more Flags or lower subscription usage. Benchmark it. |
| Daybreak Blue | `gpt-daybreak-blue-latest` -> **currently `gpt-5.6-sol`** | Released 7 Aug 2026. Responses only; separate Daybreak approval/provisioning for API use. Same current snapshot dimensions/features as Sol, with defensive-cyber safeguards. Pricing follows whichever model the moving alias targets. | Same catalog lists the Daybreak alias directly; 272,000 context; default `low`; `low` through `ultra`. A completed assistant turn exists at 2026-09-04 16:21:22Z. | The user's Codex subscription can currently run the catalog choice even though public API use has a separate provisioning gate. Record both requested alias and returned/effective model when available. Revalidate the alias before every event/gate. |

Public API model dimensions come from the [Astra][astra-model], [Sol][sol-model],
[Luna][luna-model] and [Daybreak Blue][daybreak-model] model pages. Release dates and family
features come from the [API changelog][api-changelog]. The Daybreak pricing page expressly says
the Blue alias currently points to Sol and will change as new Daybreak models arrive
([pricing, cyber models][pricing]).

### Local Codex evidence and its limit

The local source is `/Users/jeromequeck/.codex/models_cache.json`, SHA-256
`e09c62d6844e76e9d07cabb4d68a8ecc3dd586511984cb8b24aebcbb3849706c`, catalog client version
`0.153.0`, ETag `W/"8098bcd2b409935d0e08f2dd7d737657"`, fetched
`2026-09-05T07:12:47.024314Z`. Installed `codex-cli` was `0.153.2`. Recent local rollout files
contain a `turn_context` selecting each model and a later completed assistant message at the
timestamps in the table. This proves actual ChatGPT-backed Codex use on this account, not API-key
entitlement, future availability, comparative quality, or quota cost.

The catalog descriptions and effort controls are backend/product evidence, not a stable public
contract. In particular, Codex `ultra` is a product orchestration setting: the current Codex
app-server documentation says Ultra enables proactive multi-agent behaviour. It is not the API's
`reasoning.effort=max` and should not be collapsed into it ([Codex app-server at
`ddf04ad`][codex-app-server]).

The completion claims are traceable to local rollout metadata without relying on message content:

| Model | Local rollout basename | Session ID | Selected at (UTC) | Final answer at (UTC) |
| --- | --- | --- | --- | --- |
| Astra | `rollout-2026-09-05T11-45-03-01a06d35-afd8-75f3-a53b-c52bbf2324d4_01a06fab-76ed-7b00-be7c-edcc96ddd937.jsonl` | `01a06d35-afd8-75f3-a53b-c52bbf2324d4` | 2026-09-05 03:45:05.874 | 2026-09-05 03:45:17.836 |
| Sol | `rollout-2026-09-05T15-12-25-01a07069-5090-77c2-9103-59f26e6ddff9.jsonl` | `01a07069-5090-77c2-9103-59f26e6ddff9` | 2026-09-05 07:12:25.259 | 2026-09-05 07:19:55.390 |
| Luna | `rollout-2026-09-05T12-05-22-01a06fbe-1392-7170-98a4-7865fafac711.jsonl` | `01a06fbe-1392-7170-98a4-7865fafac711` | 2026-09-05 04:05:25.549 | 2026-09-05 07:11:40.776 |
| Daybreak | `rollout-2026-09-05T00-21-17-01a06d39-74ef-7483-9d17-60f6f3516a5a.jsonl` | `01a06d39-74ef-7483-9d17-60f6f3516a5a` | 2026-09-04 16:21:20.425 | 2026-09-04 16:21:22.086 |

Each row was checked from `session_meta.payload.id`, `turn_context` timestamp/model, and an
assistant item whose phase is `final_answer`; content fields were neither read nor used. These
rollouts are local evidence retained outside the repository, so the table makes the observation
auditable on this machine but does not turn it into a public product guarantee.

## Prompting and reasoning effort

### Documented

OpenAI's GPT-5.6 guide recommends a lean prompt and relevant tool set: state instructions once,
expose only relevant tools, keep descriptions precise, and remove one instruction/tool group at a
time while rerunning the same evals. It says to supply domain context, hard constraints, approval
boundaries and success criteria; set effort intentionally; start at `medium` for balance or `low`
for latency; and reserve `high`/`xhigh`/`max` for measured quality gains. It explicitly recommends
comparing the same setting and one level lower during migration ([GPT-5.6 guide][gpt56-guide]).

General reasoning guidance says prompts should be simple and direct, should specify the end goal,
and should not request chain-of-thought or “think step by step.” For tool-heavy reasoning loops,
Responses preserves reasoning items; Chat Completions does not, which can degrade complex
multi-call performance and spend more reasoning tokens ([reasoning best practices][reasoning]).

Astra-specific guidance adds four operational facts: it follows longer instructions more strongly,
is more sensitive to instructions in skills and `AGENTS.md`, may ask clarifying questions rather
than persist, and may delegate less than a harness expects. OpenAI recommends auditing accessible
instruction files and directly specifying autonomy, style, delegation, and proportional testing.
When migrating, preserve the current effective effort unless it was `none`/`minimal`, in which case
start at `low` ([Astra guide][astra-guide]).

### Solver implication

Build one compact, versioned Attempt prompt shared across candidates. It should state the objective
(submit valid Flags), fixed authority boundary, available time/budget, observable success and stop
conditions, required evidence, and exact tool contract. Avoid duplicating repository policy in
several layers. Hash the rendered prompt and tool definitions into each Attempt record.

Run model/effort cells against the same Challenge corpus and environment. Compare at least Flags,
valid/invalid submissions, wall time, input/output/reasoning/cached tokens, tool calls, stalls,
recoveries and actual Codex usage-window movement. A larger model or effort is promoted only when
the outcome gain justifies its elapsed time and subscription consumption.

### Absent guidance

No first-party source found gives Daybreak-Blue-specific prompting or reasoning-effort advice,
Sol-versus-Luna CTF routing, Codex-subscription token weights, expected Flags, or a recommended
effort for autonomous CTF solving. Daybreak's current underlying Sol snapshot makes the GPT-5.6
family advice relevant, but its alias-specific safeguards and access path remain distinct.

## Tool definitions, calls and results

### Documented

OpenAI recommends `strict: true`. Every object schema must set `additionalProperties: false`, and
every property must be required; nullable unions express optional values. Responses may otherwise
normalize a schema or fall back to best-effort non-strict calling, so the returned effective
`strict` value matters ([function calling, strict mode][strict]).

A response can contain zero, one or several `function_call` items. The application executes each,
then returns a `function_call_output` carrying the original `call_id`; output is normally a string
whose JSON/plain-text/error convention belongs to the application. OpenAI tells clients to assume
several calls rather than only one ([handling function calls][function-results]).

`parallel_tool_calls: false` constrains a model turn to zero or one function call. Model-side
parallel emission is distinct from executor concurrency. The current Agents SDK separately exposes
`tool_execution.max_function_tool_concurrency`, while its default unknown-tool behaviour raises a
model-behaviour error ([parallel calling][parallel]; [Agents runner][agents-running]).

Astra alone among the four supports async application-run tools in the cited guide. The application
marks a function/custom tool `async`, owns the pending work, and later returns the result under its
original `call_id`. Hosted built-ins are excluded; multi-agent mode must not combine async tools
with parallel tool calls ([async tools][async-tools]).

### Solver implication

Keep the competition surface small and strict: one narrow command/action interface plus explicit
Board operations only where the model truly needs them. Validate arguments again in the Solver;
reject unknown tools/fields; return structured success, failure, timeout and truncation facts; and
journal call ID, arguments, start, result, exit status and output digest before advancing state.

Start with `parallel_tool_calls: false` for side-effecting Challenge commands and submissions. If
parallelism is enabled later, admit only independence-proven read operations and cap local executor
concurrency separately. Never infer completed side effects from a duplicated/replayed model call;
reconcile by the Solver's durable idempotency key and observed external state.

Do not design the common Sol/Luna/Daybreak loop around Astra-only async calls. Treat Astra async
calling as a later optimization with explicit pending-call persistence and crash tests.

## Context, reasoning continuity, compaction and caching

### Documented

OpenAI lists four state strategies: replay the result history, use an SDK session, use a server-side
Conversation, or chain `previous_response_id`. It recommends choosing one per conversation because
mixing local replay with server state can duplicate context. Sessions are the SDK default when the
application needs durable, resumable state it controls ([Agents conversation strategy][state]).

For GPT-5.6, `reasoning.context=all_turns` is the API default. It has an effect only when earlier
output items remain accessible through a response ID, Conversation, or complete local replay.
Stateless/ZDR callers must preserve every output item, including encrypted reasoning. Persisted
reasoning crosses Sol/Terra/Luna within the GPT-5.6 family, but not across model families
([persisted reasoning][persisted-reasoning]).

Responses compaction returns an opaque encrypted item that carries needed prior state. Server-side
compaction can trigger at a threshold inside `/responses`; the standalone `/responses/compact`
returns a canonical next context that must be passed as-is. With stateless input-array chaining,
items before the latest in-stream compaction item may be dropped only as the guide specifies;
`previous_response_id` chains must not be manually pruned ([compaction][compaction]).

Prompt-cache reuse depends on an exact stable prefix. For GPT-5.6 and later, the minimum visible
cacheable prefix is 1,024 tokens. OpenAI recommends stable instructions/reference material first,
append-only history, stable tool definitions/order/schema, `tool_choice: none` or `allowed_tools`
instead of removing definitions, and explicit breakpoints when useful. Summarisation, compaction or
truncation can reset prefix reuse. Cache keys influence routing but do not guarantee hits. The API
reports cached tokens and cache-write tokens; GPT-5.6+ cache writes and reads have different token
rates ([prompt caching][cache]).

### Solver implication

The authoritative Run/Boot/Attempt journal remains Solver-owned. Within one model Attempt, choose
one protocol continuation strategy and record it. Preserve the complete ordered output-item stream
and effective model/family so recovery can resume or fail closed; never reconstruct reasoning or
tool completion from prose.

Compact only at a closed tool boundary. Persist the canonical compacted window/item and its source
response before switching. A cross-family failover starts from a deliberately rebuilt visible
state because prior opaque reasoning is incompatible. Keep the stable policy, task contract and
tool schemas before dynamic Challenge state, and measure `cached_tokens`/`cache_write_tokens`
rather than assuming a cache hit.

Native Codex already persists Thread/Turn/Item history, can resume a thread, streams item lifecycle
and final usage, and exposes `thread/compact/start`. A custom Responses path must implement the
same provenance explicitly; a Codex thread ID is not interchangeable with an API response ID
([Codex app-server][codex-app-server]).

### Absent guidance

OpenAI does not define a crash-consistent application journal, exactly-once tool effects, duplicate
submission reconciliation, Boot/Attempt semantics, or a safe cross-harness transcript format.
Compaction preserves model context, not the Solver's evidence ledger. No first-party source promises
cache hits, retention through alias drift, or reasoning compatibility between Astra and GPT-5.6.

## Long-running work, handoffs and recovery

### Documented

Responses background mode makes a long inference pollable, cancellable and resumably streamable by
response ID and event `sequence_number`; clients poll through `queued`/`in_progress` to a terminal
state. It protects against connection loss, but it does not execute application tools or provide a
durable side-effect transaction ([background mode][background]).

The Agents runner loops model -> tool/handoff -> model, raises at its turn limit, and distinguishes
runtime/validation failure from an expected approval pause. OpenAI says to resume an approval from
the same state rather than model it as a fresh turn. Sessions or serialized `RunState` provide
application-controlled resumability; HTTP/SSE is recommended when reliability matters more than
WebSocket latency. A lost stateless WebSocket chain cannot recover an uncached response ID and must
rebuild from local state ([Agents runner][agents-running]).

Agents SDK handoffs are model-visible tools. By default the receiving agent sees the prior history;
filters can shape it. Handoffs stay within one run. Input guardrails apply only to the first agent
and output guardrails only to the final agent, so per-tool guardrails are needed inside the chain
([Agents handoffs at `3e0e893`][agents-handoffs]).

Native `codex exec --json` emits JSONL lifecycle events including thread/turn start and completion,
failure/error, tool items and final usage; it supports session resume. The richer app-server has a
version-matched generated schema, `thread/start`/`resume`, `turn/start`/`interrupt`, item events and
final usage. Codex recommends full access only inside a controlled runner/container and warns that
ChatGPT-managed auth files are password-equivalent ([Codex non-interactive][codex-exec];
[Codex app-server][codex-app-server]).

### Solver implication

The Supervisor owns deadline, cancellation, retries and recovery across every harness. Persist a
closed Step before any next model call; on restart, replay only closed facts or fail closed on the
ambiguous call. Distinguish a pending response, pending tool, approval pause, terminal failure and
completed turn. Bound model turns, commands, output, retries and wall time explicitly.

Use handoffs only for a clear ownership transition with a typed payload, bounded specialist tool
surface and durable parent/child identity. A handoff is not recovery and does not itself isolate
tools or make history durable. Avoid human-approval pauses in the scored autonomous path; treat any
unexpected approval request as a classified blocked failure.

Keep the Board credential outside Challenge command environments. Pin the Codex binary and generate
the app-server schema from that exact version if it becomes the adapter. Flush/journal usage and
terminal events before the process exits.

### Absent guidance

OpenAI does not promise that background Responses, Agents sessions or Codex resume deliver exactly
once tool execution after process/container death. It gives no CTF-specific stall detector,
automatic model failover policy, retry budget, recovery-agent authority, or guarantee that a
Codex-subscription thread can be resumed across alias/backend changes. Those are Solver contracts
and require injected crash/replay tests.

## Evals and promotion evidence

### Documented

OpenAI calls evals essential when upgrading or trying models. The basic loop is task definition,
representative test inputs, explicit graders, analysis and prompt iteration. Its older Evals
platform becomes read-only on 31 Oct 2026 and is scheduled to close 30 Nov 2026; new work should
prefer Datasets/current evaluation surfaces ([evals guide][evals]).

Trace grading scores the end-to-end decisions, tool calls and reasoning steps; trace evals compare
many examples to locate orchestration failures and regressions rather than judging only final text
([trace grading][trace-grading]). The Agents SDK traces model generations, tools, handoffs and
guardrails by default and supports custom processors; payload capture can contain sensitive data
and tracing is unavailable under ZDR ([Agents tracing at `3e0e893`][agents-tracing]).

### Solver implication

Use the promoted practice Runs as the model-routing dataset. Grade the outcome with deterministic
Board facts first: valid Flags, score, time-to-first-Flag, challenge coverage, invalid submissions,
deadline compliance and recovery correctness. Grade the trace for tool selection, redundant calls,
evidence before submission, output loss, ambiguous replay and policy violations. Track tokens,
cache reads/writes and actual subscription-window movement as costs, never as substitutes for Flags.

Run paired cells with the same Solver image, prompt/tool hash, board snapshot, Challenge assignment,
deadline and concurrency. Report uncertainty and repeated trials. Promote a routing rule only on a
measured Pareto improvement or an explicit quality/latency tradeoff; do not convert API dollar prices
into Codex subscription weights.

### Absent guidance

There is no official CTF benchmark for these aliases, no official conversion from API price to
Codex quota, no published Daybreak-vs-Sol Flags comparison, and no first-party threshold for routing
a Challenge to Astra, Sol, Luna or Daybreak. OpenAI's product claims and generic evals are inputs to
the experiment design, not acceptance evidence.

## Architecture decision inputs

1. **Protocol baseline:** Responses semantics, strict schemas, complete output-item preservation,
   original `call_id`, and explicit terminal states.
2. **Native Codex baseline:** pinned `codex exec --json` or version-matched app-server, with
   ChatGPT-managed auth isolated from Challenge commands and actual subscription usage observed.
3. **State owner:** Solver journal is authoritative; provider/Codex continuation is an optimization
   referenced from it.
4. **Concurrency:** separate model call emission, local tool execution and Lane concurrency. Begin
   with one side-effecting tool per turn.
5. **Compaction/recovery:** compact only on closed boundaries; preserve opaque items; rebuild across
   families; fail closed on ambiguous effects.
6. **Routing:** explicit model slug plus requested effort, catalog/version and effective model where
   observable. Revalidate moving aliases.
7. **Promotion:** Flags, wall time, token/cache fields, failures, recovery and subscription usage on
   identical practice workloads. The four requested performance ideas remain hypotheses.

## Source record

Public documentation was retrieved 5 September 2026. The OpenAI repositories are frozen at commits
dated 5 September 2026: `openai/codex@ddf04ad26789d040f9ef6a96736f76602e35a6cc` and
`openai/openai-agents-python@3e0e89374f629c974929054e56e823a43c91a013`.

[api-changelog]: https://developers.openai.com/api/docs/changelog
[astra-model]: https://developers.openai.com/api/docs/models/gpt-6-astra
[sol-model]: https://developers.openai.com/api/docs/models/gpt-5.6-sol
[luna-model]: https://developers.openai.com/api/docs/models/gpt-5.6-luna
[daybreak-model]: https://developers.openai.com/api/docs/models/gpt-daybreak-blue-latest
[pricing]: https://developers.openai.com/api/docs/pricing#cyber-models
[astra-guide]: https://developers.openai.com/api/docs/guides/latest-model?model=gpt-6-astra
[gpt56-guide]: https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6
[reasoning]: https://developers.openai.com/api/docs/guides/reasoning-best-practices#how-to-prompt-reasoning-models-effectively
[strict]: https://developers.openai.com/api/docs/guides/function-calling#strict-mode
[function-results]: https://developers.openai.com/api/docs/guides/function-calling#handling-function-calls
[parallel]: https://developers.openai.com/api/docs/guides/function-calling#parallel-function-calling
[async-tools]: https://developers.openai.com/api/docs/guides/async-tool-calling#compatibility
[state]: https://developers.openai.com/api/docs/guides/agents/running-agents#choose-one-conversation-strategy
[persisted-reasoning]: https://developers.openai.com/api/docs/guides/reasoning#preserve-reasoning-across-calls
[compaction]: https://developers.openai.com/api/docs/guides/compaction
[cache]: https://developers.openai.com/api/docs/guides/prompt-caching
[background]: https://developers.openai.com/api/docs/guides/background
[agents-running]: https://github.com/openai/openai-agents-python/blob/3e0e89374f629c974929054e56e823a43c91a013/docs/running_agents.md
[agents-handoffs]: https://github.com/openai/openai-agents-python/blob/3e0e89374f629c974929054e56e823a43c91a013/docs/handoffs.md
[agents-tracing]: https://github.com/openai/openai-agents-python/blob/3e0e89374f629c974929054e56e823a43c91a013/docs/tracing.md
[codex-exec]: https://developers.openai.com/codex/noninteractive
[codex-app-server]: https://github.com/openai/codex/blob/ddf04ad26789d040f9ef6a96736f76602e35a6cc/codex-rs/app-server/README.md
[evals]: https://developers.openai.com/api/docs/guides/evals
[trace-grading]: https://developers.openai.com/api/docs/guides/trace-grading
