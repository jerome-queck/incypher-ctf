# Secondary harnesses for a Codex tool loop through CPA

_Research snapshot: 5 September 2026. This is a shortlist for a later prototype and architecture
decision, not the selection. “Documented” below means an upstream primary source says or implements
it; “inference” means the consequence for this Solver. No candidate was exercised against CPA in
this ticket._

## Question and fixed boundary

The fallback has to use one owner's Codex/ChatGPT entitlement through a private
[CLIProxyAPI (CPA)](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.2.151) sidecar, not
Claude, a local model, a metered OpenAI API key, account pooling or a hosted relay. CPA `v7.2.151`
was the latest release at this snapshot; the earlier sanction/containment research inspected the
same project at `v7.2.149`. CPA supplies a Responses-compatible model protocol and tool calls, not
the Solver's command loop or `Taken`/`Step` records. A secondary harness must own that missing loop.

For this comparison, a credible path must:

- stream every completed command and its real output soon enough for the external stall call;
- let the Solver kill the whole process group at its deadline;
- preserve the Claim/Observation split and map usage into `tokens_in`, `tokens_out`, `cache_read`
  and `cache_write` without inventing zeros;
- run unattended with a deliberately small, closed tool surface and no Board credential;
- fail closed on a malformed stream, unknown event/tool, pending approval or ambiguous retry; and
- be pin-able and restartable without confusing a Boot, Attempt, model turn and tool call.

Those are stricter requirements than “can talk to an OpenAI-compatible endpoint.”

## Upstream identities at the snapshot

| Candidate | Pinned upstream identity | Maintenance and licence |
| --- | --- | --- |
| Native Codex CLI | [`openai/codex@ddf04ad`](https://github.com/openai/codex/tree/ddf04ad26789d040f9ef6a96736f76602e35a6cc), latest release [`rust-v0.153.4`](https://github.com/openai/codex/releases/tag/rust-v0.153.4) | OpenAI project; commit and release on 4–5 Sep 2026; [Apache-2.0](https://github.com/openai/codex/blob/ddf04ad26789d040f9ef6a96736f76602e35a6cc/LICENSE). |
| Hermes Agent | [`NousResearch/hermes-agent@b51c055`](https://github.com/NousResearch/hermes-agent/tree/b51c055a12220f8c7c18660e8599365012e19532), latest release [`v2026.8.31`](https://github.com/NousResearch/hermes-agent/releases/tag/v2026.8.31) | Nous Research project; head updated 5 Sep 2026; [MIT](https://github.com/NousResearch/hermes-agent/blob/b51c055a12220f8c7c18660e8599365012e19532/LICENSE). |
| Oh My Pi (OMP) | [`can1357/oh-my-pi@5964a0f`](https://github.com/can1357/oh-my-pi/tree/5964a0f7649275bcde818f20073193fd032451f2), latest release [`v18.1.10`](https://github.com/can1357/oh-my-pi/releases/tag/v18.1.10) | Community project and extensive fork of Pi; commit and release on 4 Sep 2026; [MIT](https://github.com/can1357/oh-my-pi/blob/5964a0f7649275bcde818f20073193fd032451f2/LICENSE). |
| OpenCode | [`anomalyco/opencode@e289456`](https://github.com/anomalyco/opencode/tree/e2894562f8ba943d72172d10b727c24d5f650c16) on its `dev` default branch, latest release [`v1.18.29`](https://github.com/anomalyco/opencode/releases/tag/v1.18.29) | Anomaly project; commit and release on 4–5 Sep 2026; [MIT](https://github.com/anomalyco/opencode/blob/e2894562f8ba943d72172d10b727c24d5f650c16/LICENSE). |
| Smallest credible custom loop | New Solver-owned Python adapter, using CPA's HTTP interface and the already-installed `python3-requests`; no independent upstream harness | Repository-owned maintenance and no new harness licence. Protocol behaviour still depends on pinned CPA and the Codex backend. |

Recent commits prove activity, not stability. OMP and OpenCode in particular have rapidly moving,
large surfaces; a trial must pin release bytes, not install a floating package.

## Comparison

### Native Codex CLI: reference implementation, not harness independence

**Documented.** Codex has the best protocol fidelity for Codex models. User-defined providers accept
a `base_url`, bearer/API-key sources and HTTP headers, and the only supported wire protocol is now
Responses ([provider implementation](https://github.com/openai/codex/blob/ddf04ad26789d040f9ef6a96736f76602e35a6cc/codex-rs/model-provider-info/src/lib.rs#L58-L145)).
`codex exec --json` emits JSONL, which the existing adapter already maps. The richer
[`codex app-server`](https://github.com/openai/codex/blob/ddf04ad26789d040f9ef6a96736f76602e35a6cc/codex-rs/app-server/README.md#L82-L92)
offers typed JSON-RPC `thread/start`/`thread/resume`, streamed item lifecycle and tool progress,
interrupt, and final token usage. Codex also owns its sandbox/approval implementation and native
context compaction, exposed explicitly as `thread/compact/start`
([app-server compaction](https://github.com/openai/codex/blob/ddf04ad26789d040f9ef6a96736f76602e35a6cc/codex-rs/app-server/README.md#L223-L234)).

**Solver consequence.** Pointing Codex at CPA is the cheapest compatibility experiment and retains
the current Step adapter, tool semantics, timeout/process-group handling and token accounting.
Stable prompts/tools also give the backend its best chance of prefix-cache reuse. It is not a
secondary harness: a broken Codex binary, code-mode host, tool loop, sandbox or JSONL/app-server
schema breaks both direct and CPA-backed invocations. Keep it as the primary/reference and as an
auth-path experiment only, not as the answer to this ticket.

### Oh My Pi: strongest full secondary-harness prototype

**Documented.** OMP has native `openai-responses` and `openai-codex-responses` transports, plus
custom provider `baseUrl`, API selection and compatibility controls
([models](https://github.com/can1357/oh-my-pi/blob/5964a0f7649275bcde818f20073193fd032451f2/docs/models.md#L37-L110)).
Its provider layer normalizes text, reasoning, tool calls, tool results and usage into one streamed
event contract across Responses implementations
([streaming internals](https://github.com/can1357/oh-my-pi/blob/5964a0f7649275bcde818f20073193fd032451f2/docs/provider-streaming-internals.md#L3-L14)).
It exposes `json`, stdio `rpc`, `acp` and SDK modes, plus a wall-time flag
([CLI](https://github.com/can1357/oh-my-pi/blob/5964a0f7649275bcde818f20073193fd032451f2/docs/cli-reference.md#L117-L201)).
Sessions are durable JSONL and record provider/cache usage
([session format](https://github.com/can1357/oh-my-pi/blob/5964a0f7649275bcde818f20073193fd032451f2/docs/session.md#L37-L154));
local and provider-native compaction are configurable
([compaction](https://github.com/can1357/oh-my-pi/blob/5964a0f7649275bcde818f20073193fd032451f2/docs/compaction.md)).
Tool approvals are granular and default unknown custom tools to the `exec` tier, but OMP's
extensions run in-process and are explicitly not sandboxed
([approval](https://github.com/can1357/oh-my-pi/blob/5964a0f7649275bcde818f20073193fd032451f2/docs/approval-mode.md#L3-L16),
[extension boundary](https://github.com/can1357/oh-my-pi/blob/5964a0f7649275bcde818f20073193fd032451f2/docs/extension-loading.md#L245-L254)).

**Solver consequence (inference).** OMP is the best first full-harness trial because RPC/JSON gives
the adapter a live, typed seam instead of requiring database tailing, and the Responses transport
is closer to CPA than a Chat-Completions shim. It has enough session, usage, abort and compaction
machinery to survive a Boot if its persisted state is mounted deliberately. Its broad feature set
is also the principal risk: default prompts/tools, automatic background helpers, subagents,
provider fallbacks, memory, MCPs, plugins and auto-update paths can add schema tokens, hidden model
turns, concurrency and unclassified side effects. The prototype must construct an allowlist profile
with one shell-shaped tool, no auxiliary inference, no delegation, no fallback provider, no remote
sharing/telemetry, no dynamic plugin and no auto-update. OMP itself is not the security boundary;
the Solver container/uid/egress design remains so.

**Unknown until trial.** CPA compatibility of OMP's exact Responses request headers and strict tool
schema; whether its JSON/RPC stream exposes command start, completion, exit status, untruncated raw
bytes and per-turn usage in a lossless order; behaviour after a half-written stream and process
restart; baseline and per-Step input/cache tokens under the minimal profile.

### OpenCode: credible alternate, larger integration boundary

**Documented.** OpenCode configures arbitrary OpenAI-compatible providers and explicitly selects
`@ai-sdk/openai` for `/v1/responses`
([custom providers](https://github.com/anomalyco/opencode/blob/e2894562f8ba943d72172d10b727c24d5f650c16/packages/web/src/content/docs/providers.mdx#L2543-L2620)).
`opencode run --format json` emits raw events and can continue a named session or attach to a
long-lived server
([CLI](https://github.com/anomalyco/opencode/blob/e2894562f8ba943d72172d10b727c24d5f650c16/packages/web/src/content/docs/cli.mdx#L339-L381)).
The headless server publishes OpenAPI, a global SSE event stream, asynchronous prompts, abort,
session status/messages and experimental model-specific tool schemas
([server](https://github.com/anomalyco/opencode/blob/e2894562f8ba943d72172d10b727c24d5f650c16/packages/web/src/content/docs/server.mdx#L8-L42),
[session API](https://github.com/anomalyco/opencode/blob/e2894562f8ba943d72172d10b727c24d5f650c16/packages/web/src/content/docs/server.mdx#L91-L143)).
It has a generated TypeScript SDK. Automatic compaction is on by default; optional pruning removes
old tool output
([config](https://github.com/anomalyco/opencode/blob/e2894562f8ba943d72172d10b727c24d5f650c16/packages/web/src/content/docs/config.mdx#L747-L762)).
Permissions can allow/ask/deny tools, but most operations default to allow and the code's own prompt
states that the operating environment is not sandboxed
([permissions](https://github.com/anomalyco/opencode/blob/e2894562f8ba943d72172d10b727c24d5f650c16/packages/web/src/content/docs/permissions.mdx#L6-L24),
[no sandbox](https://github.com/anomalyco/opencode/blob/e2894562f8ba943d72172d10b727c24d5f650c16/packages/opencode/src/session/prompt/kimi.txt#L62)).

**Solver consequence (inference).** OpenCode is a credible second full-harness trial. `run --format
json` is simplest, while a supervised loopback-only server plus SSE/abort is more restart- and
concurrency-friendly but imports an HTTP daemon, Bun/TypeScript runtime, durable application state
and another authentication boundary into the image. Disable pruning for evidence fidelity, disable
all agents/tools except the chosen tool surface, bind only loopback, set server authentication even
inside the container, and let the outer Supervisor—not OpenCode—own retries and concurrency.

**Unknown until trial.** The raw event stream's exact stability and whether tool output/exit status
is complete enough for `Taken`; CPA Responses compatibility through the Vercel AI SDK (including
reasoning items and cached-token usage); persistence atomicity across a killed turn; comparative
prompt/tool-schema overhead. OpenCode's default branch is `dev`, increasing the importance of a
release+digest pin.

### Hermes Agent: capable agent, not shortlisted for this adapter

**Documented.** Hermes supports its own Codex OAuth and custom OpenAI-compatible endpoints, durable
sessions/resume, context compression, prompt-cache accounting, subagents and container execution
([providers](https://github.com/NousResearch/hermes-agent/blob/b51c055a12220f8c7c18660e8599365012e19532/website/docs/integrations/providers.md#L92-L99),
[custom endpoint](https://github.com/NousResearch/hermes-agent/blob/b51c055a12220f8c7c18660e8599365012e19532/website/docs/integrations/providers.md#L657-L711),
[compression/caching](https://github.com/NousResearch/hermes-agent/blob/b51c055a12220f8c7c18660e8599365012e19532/website/docs/developer-guide/context-compression-and-caching.md)).
It has an unusually broad registry—about 86 tools in current documentation
([tools](https://github.com/NousResearch/hermes-agent/blob/b51c055a12220f8c7c18660e8599365012e19532/website/docs/reference/tools-reference.md#L7-L16))—and
offers Docker/other backends. Its own security guide correctly says file deny rules are not a
sandbox because terminal commands run as the same OS user
([security](https://github.com/NousResearch/hermes-agent/blob/b51c055a12220f8c7c18660e8599365012e19532/website/docs/user-guide/security.md#L283-L330)).

The blocking integration fact is its documented programmatic one-shot seam: `hermes -z` emits only
the final response as plain text; `hermes chat --oneshot` adds a human transcript, not a documented
live machine event protocol
([CLI](https://github.com/NousResearch/hermes-agent/blob/b51c055a12220f8c7c18660e8599365012e19532/website/docs/reference/cli-commands.md#L104-L175)).
Hermes can export JSONL after the fact, and its Python `AIAgent` can be imported, but neither is a
documented subprocess stream equivalent to Codex JSONL, OMP RPC or OpenCode SSE.

**Solver consequence (inference).** Do not shortlist Hermes for the first prototype. Recovering
live Steps would require embedding private Python internals, adding hooks, or tailing evolving
session storage; all couple the Solver more deeply than the other full harnesses while its default
tool/auxiliary surface threatens token and concurrency control. Reconsider only if upstream exposes
a supported live JSON event/RPC interface or a very small adapter prototype proves one already
exists. Its direct Codex OAuth path is also outside this ticket's CPA topology.

### Smallest credible custom loop: lowest fixed overhead, highest burden of proof

The small loop is not “send prompt, run whatever string comes back.” It is one Responses client and
one narrow tool dispatcher:

1. send the fixed Attempt prompt, a single strict tool schema and `stream: true` to CPA;
2. assemble tool arguments from ordered streaming deltas, validate them, append `step-begin`, run
   the command in a new process group under the remaining deadline, append `step-end`, and return a
   `function_call_output` with the same `call_id`;
3. preserve every model output item needed for the next turn, including encrypted reasoning when
   operating statelessly, and repeat until the external stall/deadline call ends the Attempt; and
4. append the model protocol and adapter state to an fsync-capable private journal so a later Boot
   can either replay a fully completed boundary or fail closed.

This shape follows the official Responses protocol: function calls are correlated by `call_id`,
stream events carry sequence numbers and argument deltas, and stateless reasoning continuation
requires returned reasoning items / `reasoning.encrypted_content`
([create response](https://developers.openai.com/api/reference/cli/resources/responses/methods/create),
[stream events](https://platform.openai.com/docs/api-reference/responses-streaming/response/refusal?lang=python)).
OpenAI also documents Responses compaction, but CPA support for `/responses/compact` is not
established; do not assume it
([compaction guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.2)).

**Solver consequence (inference).** This is the clean control candidate: smallest static prompt and
tool schema, no hidden helper calls, native `Taken` production, outer-owned deadlines/concurrency,
and no new language runtime (`requests` is already in the image). It is also the only candidate
whose behaviour can be made exactly the Solver's domain contract. But it must implement protocol
reassembly, reasoning continuation, cancellation, retries, output truncation/storage, cache/usage
accounting, compaction, transcript repair and version drift itself. Until adversarial contract tests
prove those, it is less credible operationally than OMP—not more credible merely because it is
shorter.

## Token, cache and operational comparison

No upstream publishes a comparable “tokens per identical CPA Attempt” benchmark, and different
default prompts/tool sets make marketing or community comparisons unusable. The only defensible
ordering before a controlled trial is architectural:

| Path | Token/cache mechanism | Concurrency, restart, telemetry, sandbox |
| --- | --- | --- |
| Native Codex | Native compaction and backend-aware Responses behaviour; current adapter records turn usage. | Mature interrupt/sandbox/events; existing process-per-invocation restart shape. Same-harness failure domain. |
| OMP | Local/provider compaction; explicit cache read/write usage and prompt-cache identity; overhead depends heavily on enabled features. | RPC/JSON, durable JSONL, subagents and telemetry surfaces. No inherent OS isolation for tools/extensions. |
| OpenCode | Automatic summary compaction; optional tool-output pruning must be off; AI SDK mediates cache/usage fields. | Long-lived server, SSE, async prompt/abort and durable sessions support multiple clients. Permissions are not OS isolation. |
| Hermes | Compression and cache-aware provider switching, but broad default tool/auxiliary surface can add calls and invalidate prefixes. | Resume, subagents, logs and optional containers are strong; no supported live machine Step stream found. |
| Custom | Minimal stable prefix/schema and no hidden calls; no compaction until deliberately added. Must faithfully persist cached-token usage from CPA. | Exactly one loop per Lane and native journal are feasible. No sandbox beyond the Solver boundary unless explicitly built. |

Measure all shortlisted paths with the same CPA release/account/model, Attempt prompt, tool schema,
working tree and scripted fake tool workload. Record first-turn input, subsequent input/cache-read,
output, number of model calls, first-token/tool-call latency, complete Step bytes, compaction calls,
recovery after SIGKILL, and behaviour on malformed/truncated SSE, dead CPA and shared quota errors.

## Hard-disqualifier screen

| Candidate/path | Hard disqualifier now? | Reason |
| --- | --- | --- |
| Native Codex through CPA as the “secondary harness” | **Yes, for harness independence** | Retains the same binary, code-mode/tool loop and event schema; useful only for auth/transport diversity. |
| Hermes | **Yes, for the first shortlist** | No documented live structured subprocess/RPC stream from which the external stall call can safely derive Steps. |
| OMP | No, conditional | Disqualify if a minimal profile cannot eliminate auxiliary models/delegation/fallbacks, or RPC loses raw command output/exit/usage ordering. |
| OpenCode | No, conditional | Disqualify if CPA Responses fails through `@ai-sdk/openai`, raw events omit Step facts, or durable recovery requires an unauditable server database state. |
| Custom loop | No, conditional | Disqualify if CPA cannot carry stateless reasoning/tool state faithfully, or the prototype cannot fail closed on partial streams/restart without duplicate commands. |
| Any path | **Yes** | Requires Claude/local/metered inference; pools or shares accounts; routes around quota; needs runtime approval/login; exposes CPA/OAuth/Board secrets to challenge commands; silently retries an unchanged tool; cannot be pinned; or cannot emit the Solver's closed Step/usage vocabulary. |

## Shortlist and next evidence

Shortlist, without selecting:

1. **Oh My Pi RPC/JSON over CPA Responses** — first full secondary-harness prototype.
2. **OpenCode `run --format json` (then server/SSE only if needed) over CPA Responses** — alternate
   full harness.
3. **Solver-owned minimal Responses loop** — control implementation and possible eventual choice if
   its smaller surface outweighs the protocol burden.

Retain **native Codex through CPA** as the compatibility baseline and auth-path experiment, not a
secondary harness. Park **Hermes** unless a supported live event interface is found.

The next ticket should prototype the three shortlisted paths against a fake deterministic CPA-like
Responses server before spending a real entitlement, then run the survivors against pinned CPA.
This research did not verify exact CPA request compatibility, token efficiency, restart safety,
sandbox containment or quota semantics for any secondary harness; those are measured promotion
gates, not claims to inherit from upstream documentation.
