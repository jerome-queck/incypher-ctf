# ChatGPT web Pro is bridgeable, but not an allowed Solver interface — 5 September 2026

## Question, evidence and decision

Can the unattended, single-owner Solver drive ChatGPT web and use **GPT-5.6 Sol Pro** or
**GPT-6 Pro** as though it were a Codex model?

**Mechanical answer: yes for a generic ChatGPT web Pro turn; exact selection between today's two
Pro models remains unproved. Supported/scored-Run answer: no.** A browser can select visible
controls, insert a prompt, wait for the page, extract the answer and present a local
OpenAI-compatible stream. One current project goes further and inserts a synthetic
`chatgpt-web/pro` row in Codex. This corrects the too-broad claim that no route is technically
feasible. It does not make the route supported:
OpenAI publishes no Pro-model inference endpoint and its individual-service Terms prohibit
automatic/programmatic extraction of Output. [Terms][terms]

Therefore **reject browser-driven Pro models for v2 and a scored Run; do not account-test or
prototype them without affirmative OpenAI permission.** Keep supported native Codex base models as
the subscription route. Keep CLIProxyAPI (CPA) as its already-planned, separate Codex-subscription
fallback if native Codex fails or is disallowed; CPA is not a Chat Pro route.

This is a 5 September 2026 snapshot. Official claims below use dated first-party OpenAI pages.
Mechanism claims use the authors' repositories pinned to inspected commits. I inspected source and
documentation but did not sign in, spend quota, copy a session, or run a live ChatGPT turn. Project
claims are evidence of an implementable mechanism, not OpenAI endorsement or independent live
validation.

## Exact names and availability

| Surface | Exact name / identifier | Current availability and meaning |
| --- | --- | --- |
| Chat | **GPT-5.6 Sol Pro** | Chat's GPT-5.6 **Pro** option on Pro $100/$200, Business and Enterprise. It is a UI entitlement, not a published model ID. [Chat models][chat-models] |
| Chat | **GPT-6 Pro**, powered by **GPT-6 Astra** | Rolling out to Pro $100/$200, Business and Enterprise. OpenAI's launch prose also says “GPT-6 Astra Pro”; neither page gives the Pro variant a machine ID. [Chat models][chat-models] [Astra launch][astra-launch] |
| Codex / Work | **GPT-5.6 Sol**, `gpt-5.6-sol`; **GPT-6 Astra**, `gpt-6-astra` | Supported base models. Codex Low–Max/Ultra are reasoning/orchestration settings, not Chat's Pro variants. [Codex models][codex-models] |
| Platform API | `gpt-5.6-sol`; `gpt-6-astra` | Metered base models. No `gpt-5.6-sol-pro`, `gpt-6-pro` or `gpt-6-astra-pro` is published. [Sol API][sol-api] [Astra API][astra-api] |

The Pro $100 and $200 plans have the same core capabilities; the multipliers describe allowance,
not a different Codex model. [Pro tiers][pro-tiers]

## What the browser mechanisms can actually do

| Mechanism | Technical feasibility | Authentication and extraction | Exact-model and operational limit |
| --- | --- | --- | --- |
| **Owned persistent browser + Playwright/Electron** | **Demonstrated design.** `codex-chatgpt-web` v5.0.3 accepts Codex Responses requests on loopback, appends synthetic web models, sends compiled context/images through a task-bound Temporary Chat and translates visible reasoning/Markdown into SSE. It supports five retained task tabs and compaction; browser-only mode has no local tools. [Bridge README][web-codex] [Bridge architecture][web-codex-architecture] | Login occurs interactively inside one persistent Electron partition. The stored browser state is a password-equivalent artifact. The adapter reads and mutates ChatGPT's DOM; selector or completion drift fails the turn. [Bridge security][web-codex-security] | Its automatic route is presently one generic **Pro** effort with a hard-coded internal `gpt-5.6-sol` identity. The maintainer says exact GPT-6 Astra versus GPT-5.6 Sol Pro selection is future work. With two visible Pro models, this is evidence for a generic Pro bridge, **not verified exact selection of either current model**. [Bridge model source][web-codex-models] [Bridge limits discussion][web-codex-limits] |
| **System Chrome / Playwright or Patchright** | **Demonstrated design.** `gpt-web-gateway` supplies Chat Completions/Responses-like HTTP, persistent sessions, a request queue, health/metrics, headed Chrome under Xvfb, and `default`, system-`chrome`, or remote-`cdp` modes. It exposes a `pro` thinking choice. [Gateway][gateway] | Login can be performed through a remote screenshot/click surface and persisted in a Docker volume; it also offers password/TOTP automation. DOM extraction is available, but its default streaming path intercepts private `/backend-api` traffic. Its own disclaimer says the automation violates OpenAI's Terms and can ban the account. | It verifies the visible tier pill, not the serving backend. By default it deliberately downgrades Pro to Extra High when Pro is unavailable; fail-closed requires disabling that fallback. Headless fingerprinting, UI changes, CAPTCHA/session expiry and post-submit quota errors remain. |
| **CDP attached to an existing Chrome profile** | Technically the same browser-control primitives, without moving cookies into another browser. The gateway implements a `CDP_URL`; `codex-chatgpt-control` uses bridge-provided browser/CDP capabilities for verified visible controls and file input. [Gateway][gateway] [Visible-control SDK][control] | The already signed-in profile remains the credential, while its debugging/control surface becomes account authority. It must be local and strongly isolated. | CDP improves control fidelity, not support, policy, backend identity or UI stability. A browser restart/debug-port loss stops the lane. |
| **Browser extension/content script** | **Demonstrated design.** `chatgpt-bridge` uses an authenticated localhost WebSocket from a Chrome extension/content script to a signed-in ChatGPT tab. It exposes sessions, file upload/download, model/effort discovery, Markdown/DOM events and OpenAI-compatible streaming without Playwright or remote debugging. [Extension bridge][extension-bridge] | The extension owns tab/session state; local tokens protect its control channel. A logged-in visible ChatGPT tab and current extension are still required. | Selection is explicitly “best effort.” Extension/site version skew, tab loss, ambiguous reuse, UI drift, login and CAPTCHA stop it. It does not add a server model receipt. |
| **Accessibility / visible Codex-browser bridge** | **Demonstrated as workflow control.** `codex-chatgpt-control` discovers visible Chat/Work controls, applies a requested label strictly, verifies the visible postcondition, submits once, reads Markdown, uploads/downloads files and records resumable operations. It is designed to stop on login, CAPTCHA, ambiguity or selector drift. [Visible-control SDK][control] | It depends on a compatible visible signed-in browser bridge (`globalThis.agent`), not a standalone API or credential. | This is useful evidence that accessibility/DOM actions can be disciplined, but it still verifies UI state only. Its authors call it unofficial, user-directed and not an API. It is therefore unsuitable for the no-human-intervention contract. |
| **Copied cookies, replayed requests or private ChatGPT endpoints** | Mechanically possible; the gateway's optional network interceptor demonstrates why private traffic can yield smoother streaming. | Copied session material has no documented scope or rotation contract and exposes the account. | Most brittle and least defensible route: undocumented schema, reverse-engineering/extraction concerns, no stable errors or effective-model proof. Reject outright. |

The strongest exact Codex bridge is architecturally credible: local `/models` catalog overlay,
Responses/SSE translation, task-bound browser leases, attachment checks, completion fences, restart
controls and explicit selector failures are implemented and tested locally. But its account-bound
release checklist still requires a real Pro account and manual validation of models, tools,
compaction, cancellation and session reuse. Its “Zero Risk” mode avoids DOM automation only by
making the user choose the model, paste and send every turn—directly incompatible with the Solver's
unattended Run. [Bridge README][web-codex] [Bridge release validation][web-codex-validation]

Its live issue tracker also records the failure classes the design cannot erase: a
suspected-account-activity warning, a same-day Pro slider DOM break, freezes, compaction disconnects,
subagent cwd errors and an MCP restart loop. Those reports do not prove every user is affected, but
they contradict any claim of competition-grade unattended maturity. [Suspicious activity
report][web-codex-312] [Pro slider break][web-codex-322] [Freeze report][web-codex-308]
[Compaction disconnect][web-codex-321] [Subagent cwd][web-codex-314] [MCP restart
loop][web-codex-294]

## Support, permission and official machine interfaces

Technical feasibility must not be mistaken for permission:

- OpenAI's individual Terms, effective 1 January 2026, prohibit automatically or programmatically
  extracting data or Output, reverse engineering underlying components, and bypassing rate limits,
  restrictions or protections. The Pro-plan article separately identifies programmatic extraction
  as abusive usage. These clauses apply even to one owner and even if automation clicks only visible
  controls. [Terms][terms] [Pro tiers][pro-tiers]
- OpenAI documents **Codex** `exec`, SDK/app-server operation and ChatGPT login for automation, with
  headless device-code login and a persisted, refreshable `auth.json`. It exposes base
  `gpt-5.6-sol` and `gpt-6-astra`, not either Pro variant. `codex exec --json` is the supported event
  extraction path. [Codex auth][codex-auth] [Codex exec][codex-exec] [Codex SDK][codex-sdk]
- The **Platform Responses API** is a supported unattended interface with API-key authentication
  and machine-visible usage/errors, but it is separately billed and exposes only
  the base model IDs. Both model pages state 1,050,000-token context, 128,000 maximum output and
  supported tools; keys must be supplied and stored as service secrets, and no dated snapshot or
  availability/latency SLA is published. [API quickstart][api-auth] [Sol API][sol-api] [Astra
  API][astra-api]
- The **Workspace Agents API** can durably enqueue an agent with idempotency and inspect status, but
  has no model/file selector and explicitly provides no output retrieval. Its admin-issued access
  token is a separate managed secret; it cannot feed a solution back to the Solver. [Workspace
  Agents][workspace-agents] [Workspace Agent auth][workspace-agent-auth]
- **ChatGPT cloud browser/Work** may continue a user-started task in the background, but pauses for
  authentication, confirmation or takeover; sites may block it. It has no external result stream or
  reusable container credential. [Cloud browser][cloud-browser]
- **ChatGPT apps/custom MCP** run in the opposite direction: ChatGPT calls developer tools. On
  personal Pro, custom MCP is limited to read/fetch; full MCP write/modify is currently Business and
  Enterprise/Edu only, and actions may require confirmation. Thus the bridge project's claimed full
  filesystem/shell loop on personal Pro is **not affirmed by current OpenAI guidance**. Even where
  MCP is supported, it supplies tools to ChatGPT; it does not create an output API or permit
  automated page extraction. [Developer mode/MCP][mcp]
- GPTs are for use inside ChatGPT; OpenAI directs embedding in an external product to the API.
  [GPTs][gpts]

Official Chat file limits do not repair the tool loop: uploads have a 512 MB hard limit; text and
documents are capped at two million tokens per file; an example rolling limit is 80 uploads per
three hours; and user/organization storage caps are shared across chats, Projects and GPT knowledge.
Those are UI product limits, not a stable upload API contract, and separate limits also apply to
image generation, data analysis and other tools. [File uploads][file-uploads] [Chat
models][chat-models]

No official route combines all four necessities: Pro variant selection, unattended machine
authentication, result retrieval, and an allowed local tool/file loop.

## Quota, verification, restart and competition suitability

Official Chat allowances are finite and not reservable:

| Plan | GPT-6 Pro | GPT-5.6 Sol Pro interaction |
| --- | ---: | --- |
| Pro $200 | 200 messages/week | 170/day and a 200/day combined cap across both |
| Pro $100 | 50/week shared across both | Same shared 50/week |
| Business Standard | 15/month shared | Same shared allowance |
| Business Premium | 50/week shared | Same shared allowance |

Support will not reset limits. On Pro $200, exhausted GPT-6 Pro automatically switches to
GPT-5.6 Thinking Medium. Chat has no documented quota endpoint, reservation, webhook or stable
machine error. A visual “Pro” pill can prove that a control appeared selected; it cannot prove the
serving model, and the documented fallback proves those differ at exhaustion. An acceptable Solver
adapter would have to fail closed on any unavailable/unverified requested tier and retain a model
receipt; Chat web offers no official receipt. [Chat models][chat-models]

OpenAI publishes no Pro-specific context window, maximum output, concurrent-turn limit, latency SLA
or restart contract. The canonical bridge's five-tab cap and measured message/context ceilings are
project controls, not service guarantees. Entitlement loss changes the visible controls; a careful
bridge can fail closed when they disappear, but cannot reserve access or prove why it disappeared.
[Bridge architecture][web-codex-architecture] [Bridge model source][web-codex-models]

An unattended container is mechanically achievable only after interactive login: one inspected
project packages Linux x64 Electron; another runs headed Chrome under Xvfb in Docker with a mounted
auth volume. That is not competition reliability. Session expiry, CAPTCHA/anti-bot checks, UI/A-B
changes, quota notices after submission, browser/tab/process death, MCP approval prompts and network
failure all introduce ambiguous-submit or human-recovery states. The consumer service is not
warranted uninterrupted or error-free. No inspected project establishes a 5.5-hour restart-safe,
exactly-once Pro lane under the Solver image and competition traffic. [Terms][terms] [Gateway][gateway]

Chat, Work and Codex have separate allowances; Work and Codex share their agentic allowance.
Separate counters or adapters do not prove independent backends or outage domains. Browser-Pro is
therefore neither a reliable primary lane nor defensible capacity/failure-domain diversity.
[Chat models][chat-models] [Codex usage][codex-usage]

## Consequences for v2

1. Record the corrected finding: **browser-Pro is technically bridgeable, unofficial, prohibited
   for automated output extraction under the public individual Terms, and unfit for the scored Run.**
2. Do not add `chatgpt-web/*`, Playwright/Patchright, CDP, extension, accessibility or private-endpoint
   providers to v2. Do not spend the owner's account/quota on a prototype absent written permission.
3. Use supported machine identifiers only: `gpt-5.6-sol` and `gpt-6-astra` in native Codex; those
   same base IDs only under a separately approved metered API policy. Never equate Chat Pro, Codex
   Max or Codex Ultra.
4. Preserve CPA unchanged as the planned **Codex-subscription fallback** based on the prior Tibo
   evidence: rehearse it only for native-Codex failure/disallowance, attribute it to the same
   underlying Codex allowance unless proven otherwise, and never list it as a Pro-model route.
   [CPA research][cpa-research]
5. For every allowed Attempt, record product, auth class, requested machine model ID, reasoning
   setting, accepted/effective ID evidence, quota source and fallback separately. Fail closed when
   identity or submission state is uncertain.

No support query is needed for today's rejection: OpenAI's public Terms and catalogs are already
clear. If the owner seeks an exception, preserve this exact written question:

> Does OpenAI permit a single personal ChatGPT Pro subscriber to use a trusted, unattended private
> Docker workload to control the visible ChatGPT web UI and programmatically retrieve outputs from
> GPT-5.6 Sol Pro or GPT-6 Pro? If yes, which documented client/endpoint and authentication method
> apply, how can the effective model and fallback be verified, and may a ChatGPT custom MCP app feed
> local filesystem/shell tool results through the same automated turn?

Only an affirmative answer or a newly published result-bearing Pro endpoint should reopen a bounded
prototype. Repository popularity, lack of enforcement, manual “Zero Risk” operation or a UI
postcondition is not permission.

[astra-api]: https://developers.openai.com/api/docs/models/gpt-6-astra
[astra-launch]: https://openai.com/index/gpt-6-astra/
[api-auth]: https://platform.openai.com/docs/quickstart/make-your-first-api-request
[chat-models]: https://help.openai.com/en/articles/20001354-gpt-56-and-gpt-6-pro-in-chatgpt
[cloud-browser]: https://help.openai.com/en/articles/20001280-using-cloud-browser-in-chatgpt
[codex-auth]: https://learn.chatgpt.com/docs/auth
[codex-exec]: https://learn.chatgpt.com/docs/non-interactive-mode
[codex-models]: https://learn.chatgpt.com/docs/models
[codex-sdk]: https://learn.chatgpt.com/docs/codex-sdk
[codex-usage]: https://help.openai.com/en/articles/11369540-using-codex-with-your-chatgpt-plan/
[control]: https://github.com/adamallcock/codex-chatgpt-control/tree/cebfe84f4dfd611a30a79626fb33bda2b7b80a4a
[cpa-research]: 2026-09-05-cliproxyapi-codex-subscription-fallback.md
[extension-bridge]: https://github.com/DrA1ex/chatgpt-bridge/tree/b4d1459ceaf54d15905b7bdc4713ba2cf107da78
[file-uploads]: https://help.openai.com/en/articles/8555545-file-uploads-faq
[gateway]: https://github.com/stufently/gpt-web-gateway/tree/efb01a32e9e4c7fbebb8acff204c8c2a448c476c
[gpts]: https://help.openai.com/en/articles/8554407-gpts-in-chatgpt
[mcp]: https://help.openai.com/en/articles/12584461-developer-mode-and-full-mcp-connectors-in-chatgpt-beta
[pro-tiers]: https://help.openai.com/en/articles/9793128-about-chatgpt-pro-tiers
[sol-api]: https://developers.openai.com/api/docs/models/gpt-5.6-sol
[terms]: https://openai.com/policies/row-terms-of-use/
[web-codex]: https://github.com/miuuyy/codex-chatgpt-web/tree/74aed4025937eadca13b363cdcbc87963cd4dff3
[web-codex-architecture]: https://github.com/miuuyy/codex-chatgpt-web/blob/74aed4025937eadca13b363cdcbc87963cd4dff3/docs/architecture.md
[web-codex-294]: https://github.com/miuuyy/codex-chatgpt-web/issues/294
[web-codex-308]: https://github.com/miuuyy/codex-chatgpt-web/issues/308
[web-codex-312]: https://github.com/miuuyy/codex-chatgpt-web/issues/312
[web-codex-314]: https://github.com/miuuyy/codex-chatgpt-web/issues/314
[web-codex-321]: https://github.com/miuuyy/codex-chatgpt-web/issues/321
[web-codex-322]: https://github.com/miuuyy/codex-chatgpt-web/issues/322
[web-codex-limits]: https://github.com/miuuyy/codex-chatgpt-web/discussions/309
[web-codex-models]: https://github.com/miuuyy/codex-chatgpt-web/blob/74aed4025937eadca13b363cdcbc87963cd4dff3/src/chatgpt-web-models.ts
[web-codex-security]: https://github.com/miuuyy/codex-chatgpt-web/blob/74aed4025937eadca13b363cdcbc87963cd4dff3/docs/security-model.md
[web-codex-validation]: https://github.com/miuuyy/codex-chatgpt-web/blob/74aed4025937eadca13b363cdcbc87963cd4dff3/docs/release-validation.md
[workspace-agents]: https://learn.chatgpt.com/workspace-agents/trigger-runs
[workspace-agent-auth]: https://learn.chatgpt.com/workspace-agents/authentication
