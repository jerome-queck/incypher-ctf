# ChatGPT web Pro models have no supported machine inference route — 5 September 2026

## Question and standard

Can the unattended, single-owner Solver invoke the highest-compute models sold in ChatGPT Chat—
currently **GPT-5.6 Sol Pro** and **GPT-6 Pro**—through a supported, allowed and reproducible
machine interface?

This is a 5 September 2026 snapshot. It uses only first-party OpenAI product documentation, Help
Center material and terms retrieved on that date. A route qualifies only if OpenAI both permits the
automation and documents an interface that accepts work and returns the model's result. Mere
technical reachability, a browser login, a model-picker label, or access to a related base model is
not enough. No private endpoint, browser session, credential, quota or live account was probed.

## Result

**Unsupported / rejected. Do not prototype a web-Pro harness.** OpenAI offers supported machine
interfaces for the base `gpt-5.6-sol` and `gpt-6-astra` models, but none for **GPT-5.6 Sol Pro** or
**GPT-6 Pro**. The consumer terms expressly prohibit automatic/programmatic extraction of ChatGPT
output, reverse engineering and circumvention. Browser automation, session-cookie replay, private
ChatGPT endpoints and model-selector impersonation therefore fail both the permission and interface
tests.

The distinction is explicit in OpenAI's current catalogs: Chat exposes a **Pro** model option,
whereas Work, Codex and the API expose **Sol** and **Astra**. Codex `max` and `ultra` are reasoning
or orchestration settings for those base models, not aliases for Chat's Pro models. The strongest
supported subscription route remains Codex with an explicitly named base model. A metered
Responses route can also use the base models, but it is not a way to spend a ChatGPT entitlement or
obtain the Pro variants.

## Exact product and model ledger

| Surface | Exact current name or identifier | Availability on 5 September | What it proves |
| --- | --- | --- | --- |
| Chat model picker | **GPT-5.6 Sol Pro**; shown as the GPT-5.6 family's **Pro** option | Pro $100, Pro $200, Business and Enterprise; not Plus, Free or Go | A Chat entitlement and UI choice, not an API model ID. [Chat model/limit article][chat-models] |
| Chat model picker | **GPT-6 Pro**, powered by **GPT-6 Astra** | Rolling out to Pro $100, Pro $200, Business and Enterprise; not Plus in Chat | The current Help Center selector name is GPT-6 Pro. OpenAI's 3 September launch article also calls the entitlement “GPT-6 Astra Pro”; neither source publishes a machine identifier for it. [Chat model/limit article][chat-models] [Astra launch][astra-launch] |
| Codex / ChatGPT Work | **GPT-5.6 Sol**, `gpt-5.6-sol` (`gpt-5.6` routes to Sol) | Plus, Pro, Business and Enterprise, subject to rollout/client/workspace | Official CLI-selectable base model. Codex offers Low through Max and, where eligible, Ultra; its catalog never names Sol Pro. [Codex models][codex-models] [Sol API model][sol-api] |
| Codex / ChatGPT Work | **GPT-6 Astra**, `gpt-6-astra` | Rolling out separately across products; Codex requires 0.153.0 or newer | Official CLI-selectable base model, not GPT-6 Pro. [Chat model/limit article][chat-models] [Codex models][codex-models] |
| OpenAI Platform API | `gpt-5.6-sol`, `gpt-6-astra` | Metered API access, subject to organization/project access and tier limits | The API catalog lists only the base identifiers. It publishes no `gpt-5.6-sol-pro`, `gpt-6-pro`, or `gpt-6-astra-pro` model. [Sol API model][sol-api] [Astra API model][astra-api] |

The ChatGPT plan and the model are separate names. OpenAI currently sells **Pro $100** (5x Plus
usage) and **Pro $200** (20x Plus usage); both include the same core features, including Pro models,
Codex and file uploads. A plan called “Pro 20x” therefore does not imply a model called Pro is
available in Codex. [Pro tiers][pro-tiers]

## Candidate-route evidence matrix

| Candidate | Support and permission | Authentication / unattended viability | Tools, files, limits, recovery and observability | Pro-model identity and decision |
| --- | --- | --- | --- | --- |
| **ChatGPT web/desktop UI through Playwright, CUA, RPA or accessibility automation** | **Rejected.** The personal-service terms prohibit automatically/programmatically extracting Output, reverse engineering, and bypassing rate limits or protections. OpenAI's Pro-plan help repeats that programmatic extraction is abusive usage. Single ownership does not create an exception. [Terms][terms] [Pro tiers][pro-tiers] | Requires a password-equivalent personal browser session/cookies and an interactive model-picker action. Reusing them in the container has no documented scope, rotation or machine-auth contract. UI changes, reauthentication, confirmations and entitlement changes can stop it. | The Pro plan includes files and advanced tools, but OpenAI publishes no per-Pro-model context/output or file matrix, concurrency/rate contract, latency/SLA, event stream, extraction guarantee, idempotency or restart protocol. It consumes Chat's Pro allowance, has no machine quota/preflight interface, and may fall back at exhaustion. Consumer service is expressly not warranted uninterrupted or error-free. [Chat model/limit article][chat-models] [Pro tiers][pro-tiers] [Terms][terms] | The UI label can be selected, but no supported machine receipt confirms the effective model. On Pro $200, GPT-6 Pro automatically falls back to GPT-5.6 Thinking Medium at its weekly limit. **No prototype.** [Chat model/limit article][chat-models] |
| **Private ChatGPT endpoints, captured requests, session-cookie/token replay, or selector impersonation** | **Rejected.** These are undocumented and depend on extracting output and/or discovering underlying service components; rate-limit evasion is independently forbidden. [Terms][terms] | A copied browser credential is password-equivalent account access, has no documented scope/rotation contract for this use, and may fail when the session, UI or backend changes. | No public schema, stability, retry, terminal-state, file/tool, context/output, concurrency/rate, latency, extraction, restart, quota or telemetry contract. Any experiment would test the live account against an explicitly excluded route. | A requested label or hidden request field is not proof of the serving model. **Do not probe.** |
| **CLIProxyAPI (CPA)** | **Not an official Pro route.** CPA is a third-party relay around supported Codex authentication, not an OpenAI-published interface or permission grant. Prior research found a candidate base-Codex recovery path; nothing in OpenAI's catalogs assigns it a Pro identifier. [CPA research][cpa-research] [Codex models][codex-models] | It would reuse password-equivalent Codex credentials inside one owner's trusted container. That may be technically rehearsed only for the already-approved base-model recovery question, not to probe Chat UI or private endpoints. | Its context/output, tools, concurrency, rate handling, observability, latency, extraction and restart properties are implementation claims to test in that separate rehearsal. It has no independent allowance: loss of the underlying Codex entitlement/model availability stops it too. It cannot manufacture a Chat Pro quota, SLA or effective-model receipt. | No `gpt-5.6-sol-pro` or `gpt-6-pro` exists in the official Codex catalog. **Exclude Pro variants from CPA trials; keep only base-model CPA work in scope elsewhere.** |
| **Codex CLI `exec`, SDK or app server with ChatGPT sign-in** | **Supported machine interface**, including scripts/CI and programmatic local agents. This resolves the general automation-permission question for Codex, not for Chat UI. [Non-interactive Codex][codex-exec] [Codex SDK][codex-sdk] | ChatGPT login is supported; headless device-code login and a securely persisted `auth.json` are documented. The file contains access tokens, must be treated as a password and may be refreshed in place. A trusted private container is viable after pre-login. [Codex auth][codex-auth] | Local files and configured tools are available subject to sandbox/approval policy. `codex exec --json` provides a supported extraction stream of thread, turn, item, failure and usage events; sessions can resume by ID, but no exactly-once delivery guarantee is published. Codex/Work consume their own shared allowance, separate from Chat's Pro-model allowance; consumption varies with model, task size, context and execution location. The public pages give no numeric context/output, concurrency/rate or latency guarantee. Rollout, client, sign-in, entitlement or model-availability changes can remove the choice; without a stable failure contract, the Solver must revalidate and fail closed. [Codex models][codex-models] [Non-interactive Codex][codex-exec] [Codex usage][codex-usage] | Selectable IDs are `gpt-5.6-sol` and `gpt-6-astra`; the Codex catalog does not expose either Pro variant. Max means more single-model reasoning; Ultra adds subagents. The selected ID is observable, but no dated serving-snapshot receipt is promised. **Keep as base-model candidate only.** [Codex models][codex-models] |
| **OpenAI Responses / Chat Completions / Batch APIs** | **Supported machine interface**, but metered Platform access is distinct from ChatGPT subscription access. [Codex auth][codex-auth] [Sol API model][sol-api] [Astra API model][astra-api] | API key or supported workload identity; suitable for unattended services. Keys belong in a secrets manager. | Both base models publish 1,050,000-token context and 128,000 maximum output, streaming, function calling and structured outputs. Responses supports web/file search, code interpreter, hosted shell, apply patch, skills, computer use, MCP and tool search. Current Tier 1 starts at 500 RPM/500,000 TPM; higher tiers differ. API state, usage and errors are machine-visible; background responses can be polled/resumed, but the application owns tool-side effects and crash recovery. The model pages promise no latency or availability SLA. [Sol API model][sol-api] [Astra API model][astra-api] [Background mode][background] | Only `gpt-5.6-sol` and `gpt-6-astra` are listed. Each currently has only an unversioned alias in its snapshot section, so even the base model cannot be pinned to a dated snapshot from the published catalog. **Not a Pro route; metered and outside this map's inference policy.** |
| **Workspace Agents API** | A genuine official API for triggering a published workspace agent, but not a result-bearing inference API; policy risk is low only as documented. [Workspace Agents][workspace-agents] | Requires an admin-enabled, scoped access token stored as a secret. The cited page documents provisioning, scope and storage, but no rotation/revocation lifecycle or entitlement-change behavior. It can enqueue unattended work, but is not a personal Pro credential. [Workspace Agent auth][workspace-agent-auth] | It durably queues text input, accepts an idempotency key and exposes status across caller restarts. It has no file, tool or model-selection field; no retrievable output/extraction path; and no published context/output, concurrency/rate or latency limit. Quota coupling is undocumented. [Workspace Agents][workspace-agents] | Cannot request or verify a Pro model and cannot return the answer to the Solver. **Rejected.** |
| **ChatGPT Work / cloud browser background task** | Supported in-product automation, not an interface exposed to the Solver. A user starts the task in ChatGPT; it may keep running after they leave. [Cloud browser][cloud-browser] | Uses ChatGPT's isolated remote-browser session, not a reusable container credential. It pauses for input, sign-in or confirmation and may require takeover; plan, rollout, workspace permission or entitlement changes can remove it. | Websites may block it; consequential actions require confirmation. Its tools/files stay inside Work. There is no external result/extraction or observation stream, and no published external context/output, concurrency/rate, latency, restart or quota-coupling contract. [Cloud browser][cloud-browser] | No external model selector or effective-model receipt exists. **Rejected for an unattended container and no-human-intervention Run.** |
| **GPTs, ChatGPT apps/plugins/actions or MCP connected to ChatGPT** | Supported extension points in the opposite direction: they let ChatGPT call developer tools/data. GPTs are for use inside ChatGPT, not embedding; OpenAI directs external products to the API. Policy risk is low only within those published contracts. [GPTs][gpts] | App/action OAuth secrets authenticate ChatGPT to external data; they are not model-entitlement credentials and provide no unattended Solver login. Revocation or workspace/app disablement removes access. | Tool and file inputs/results remain inside ChatGPT. Custom actions are unavailable in Pro mode, so no callback can bridge a Pro turn. There is no external result/observation or extraction API and no published external context/output, concurrency/rate, latency, retry/restart or quota-coupling contract. [GPT actions][gpt-actions] | No machine model identifier or effective-model receipt exists. **Not a machine inference route.** The Workspace Agents API above is the only documented ChatGPT-side trigger found, and it withholds output. |

## Quota, coupling and competition reliability

Chat's Pro-model quotas are concrete but unsuitable for an unattended 5.5-hour Solver even before
the policy rejection:

| Plan | GPT-6 Pro in Chat | GPT-5.6 Sol Pro interaction |
| --- | ---: | --- |
| Pro $200 | 200 messages/week | 170 messages/day, plus a 200-message/day combined cap across both |
| Pro $100 | 50 messages/week | Same shared 50-message/week allowance |
| Business Standard | 15 messages/month | Same shared included allowance |
| Business Premium | 50 messages/week | Same shared included allowance |

At exhaustion, availability disappears until reset; Support will not reset limits. GPT-6 Pro on
Pro $200 changes capability class by falling back to GPT-5.6 Thinking Medium. The UI may
show a reset time, but no official machine quota endpoint, preflight reservation, webhook or stable
error contract is documented for these Chat allowances. [Chat model/limit article][chat-models]

OpenAI explicitly says Chat, Work and Codex have separate allowances. Work and Codex themselves
share agentic usage, and their consumption depends on the model, task complexity, context and
execution location. Separate counters do **not** establish different model backends, failure
domains or outage independence. Likewise, changing from native Codex to another Codex-subscription
harness cannot be counted as capacity diversity without evidence of an independent allowance and
backend. A metered API key has separate billing and API rate limits, but remains another OpenAI
service with no consumer-level continuity guarantee. [Chat model/limit article][chat-models]
[Codex usage][codex-usage] [Terms][terms]

## Consequences for v2

1. Keep **GPT-5.6 Sol Pro** and **GPT-6 Pro** out of the model policy, secondary-harness trials and
   v2 specification. Do not add browser/session/private-endpoint adapters.
2. Name actual supported candidates by machine identifier: `gpt-5.6-sol` and `gpt-6-astra` for
   Codex; the same IDs only under a separately metered API route. Never translate Chat “Pro,” Codex
   Max, or Codex Ultra into one another.
3. Require every Attempt to record requested product, authentication class, machine model ID and
   reasoning setting separately. Verify that the harness accepted the requested ID and retain any
   effective-model or usage receipt it exposes. An absent immutable serving-snapshot receipt is an
   explicit provenance limitation, not a reason to reject supported Codex; a UI label or private
   request field with no documented machine identifier still fails verification.
4. Treat Chat, Work, Codex and API quota independently in records. Do not infer backend or outage
   independence from a separate UI, harness or allowance.
5. Reopen only if OpenAI publishes a result-bearing interface with an explicit Pro-model identifier,
   safe non-browser authentication, documented quotas/limits and affirmative automation support.

No written support answer is required to make today's decision: public terms and product docs are
already sufficient to reject the route. The precise question to preserve for reconsideration is:

> Does OpenAI support a single personal ChatGPT Pro subscriber using a trusted, unattended private
> Docker workload to explicitly invoke and programmatically retrieve outputs from **GPT-5.6 Sol
> Pro** or **GPT-6 Pro**? If yes, please identify the documented endpoint/client, authentication
> method, exact model identifier, tool/file and context/output limits, quota endpoint and fallback
> behavior, and confirm that this documented route is permitted notwithstanding the consumer
> Terms' prohibition on automatic/programmatic extraction of Output.

An affirmative written answer would warrant fresh research and then a bounded prototype. Silence,
an undocumented workaround, or confirmation that only base Sol/Astra are programmatic leaves this
rejection unchanged.

[astra-api]: https://developers.openai.com/api/docs/models/gpt-6-astra
[astra-launch]: https://openai.com/index/gpt-6-astra/
[background]: https://developers.openai.com/api/docs/guides/background
[chat-models]: https://help.openai.com/en/articles/20001354-gpt-56-and-gpt-6-pro-in-chatgpt
[cloud-browser]: https://help.openai.com/en/articles/20001280-using-cloud-browser-in-chatgpt
[cpa-research]: 2026-09-05-cliproxyapi-codex-subscription-fallback.md
[codex-auth]: https://learn.chatgpt.com/docs/auth
[codex-exec]: https://learn.chatgpt.com/docs/non-interactive-mode
[codex-models]: https://learn.chatgpt.com/docs/models
[codex-sdk]: https://learn.chatgpt.com/docs/codex-sdk
[codex-usage]: https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan/
[gpt-actions]: https://help.openai.com/en/articles/9442513-configuring-actions-in-gpts
[gpts]: https://help.openai.com/en/articles/8554407-gpts-in-chatgpt
[pro-tiers]: https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers
[sol-api]: https://developers.openai.com/api/docs/models/gpt-5.6-sol
[terms]: https://openai.com/policies/row-terms-of-use/
[workspace-agent-auth]: https://learn.chatgpt.com/workspace-agents/authentication
[workspace-agents]: https://learn.chatgpt.com/workspace-agents/trigger-runs
