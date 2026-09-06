# Native Codex capabilities for the complete pre-final Solver

_Research snapshot: 7 September 2026. Current public documentation was fetched on this date. The
source audit is pinned to `openai/codex` tag `rust-v0.147.0`, commit
`be6e8eac029b183056b7e4402879f15d2c85f61b`, because that is the exact Codex release in the Solver
image. No reset was consumed, no account limit was forced, and no API key was used._

This note is evidence and a recommendation for #218; it does not itself adopt architecture. The
wayfinding map owns that decision.

## Findings and recommended classification

Evidence supports keeping **pinned native `codex exec --json` as the primary Inference Harness**.
The recommendation for #218 is a narrow, Supervisor-owned **Codex Control** client to the same
pinned binary's stdio App Server only after a Gate proves four controls: account/auth health, live
model catalog, ChatGPT rate-limit snapshots, and earned-reset redemption. Evidence does not support
replacing the mature native loop with a new Responses loop on the primary route, or adding the
Codex SDK, MCP servers, implicit skills or nested subagents to the competition image.

The App Server is valuable because it exposes typed state that `exec --json` does not: auth mode,
model/effort catalog, sparse rate-limit updates, reset-credit inventory and idempotent reset
outcomes. It is not yet the primary Harness because adopting its complete bidirectional protocol
would also move process ownership, approvals, thread persistence, interruption and event parsing at
once. OpenAI describes App Server as the interface for rich clients and makes its schema explicitly
version-specific; the pinned binary can generate the exact TypeScript or JSON Schema it implements
([current App Server documentation][app-server]; [pinned App Server protocol][pinned-app-server]).

If adopted, this yields one ChatGPT-backed native route and the already-decided CPA recovery route.
The App Server and either Codex SDK are control surfaces over Codex, not a third source of
Inference.

## Product and credential boundary

Two accounting products must never be collapsed:

- **ChatGPT-managed Codex** uses the Owner's ChatGPT OAuth login, persists and refreshes access
  tokens in `CODEX_HOME`, consumes ChatGPT plan limits, and alone exposes the ChatGPT
  `account/rateLimits/*` and earned-reset controls. The official auth guide says `auth.json` is
  password-equivalent and may be plaintext; the pinned reset processor rejects non-ChatGPT auth
  ([authentication][auth]; [reset processor][pinned-reset-processor]).
- **Metered OpenAI API** uses an API key and standard API billing. Public Responses and Agents SDK
  capabilities describe this product, not the Owner's subscription limits or earned resets. The
  competition plan has no API key or credits, so it is not a fallback. Official documentation also
  says API-key auth can lack ChatGPT workspace features and uses standard API pricing
  ([authentication][auth]).
- **Codex access tokens** are for permitted Enterprise automation. They are not the Pro-account
  login path. The scored Run retains device-code login and a persistent, owner-only `auth.json`
  ([authentication][auth]).

The evidence supports this boundary for the map's decision: Supervisor/Codex Control owns
`CODEX_HOME`, `auth.json`, the App Server stdio pipe, reset authority and the native Codex process.
Attempt code receives neither the file nor the pipe. `codex exec` already launches its separate
`codex-code-mode-host` over inherited stdio;
the pinned source places that host in its own process group. A privilege-dropping host launcher is
therefore the promising seam for running Challenge commands as the Worker uid while the parent
retains the credential, but this is **prototype**, not a proven security boundary
([host spawn][pinned-host-spawn]). The alternative websocket host is not suitable as the boundary:
its listener has no client authentication beyond rejecting requests with an `Origin` header, so
loopback reachability is not authority separation ([host transport][pinned-host-transport]).

## Capability ledger

“Stable” below means supported in the exact pinned release, not an assurance about later releases.
Every App Server consumer must generate and pin the 0.147.0 schema; current online docs are discovery
material, never the wire contract for the image.

| Surface | Recommended classification | Integration seam and authority boundary | Failure domain and stability | Evidence gap and required Gate proof |
| --- | --- | --- | --- | --- |
| `codex exec --json` native loop | **Adopt** | Keep `solver/codex.py` as the Adapter; Supervisor launches one process group per turn, prompt on stdin, JSONL on stdout. Add explicit `--ignore-user-config`, `--ignore-rules` and `--ask-for-approval never` after compatibility tests; auth still comes from Supervisor-owned `CODEX_HOME`. | CLI/process/tool-loop failures are native-local; account/backend/quota failures are shared with CPA. JSONL is supported, but its enum can grow between releases ([non-interactive mode][noninteractive]; [pinned events][pinned-events]). | Replay split/truncated/malformed/unknown events; success, typed failure, signal kill and descendant cleanup; one live shell solve with exact 0.147.0 and code-mode host. Unknown events must be retained, never treated as success. |
| App Server as primary Attempt Harness | **Prototype** | Possible future replacement behind the existing Adapter: one stdio JSON-RPC process, `thread/start`, `turn/start`, streamed item/turn events, `turn/interrupt`. Never expose its pipe to a Worker. | Rich, bidirectional, stateful protocol adds overload/backpressure, client-request and approval deadlocks, thread lifecycle and version skew. stdio is supported; websocket is explicitly experimental/unsupported ([pinned App Server protocol][pinned-app-server]). | Parity Gate against `exec`: shell, image, search, deadline interruption, crash/restart, malformed frames, approvals, token usage, no orphan descendants and no lost/duplicated Steps. Do not promote merely for richer events. |
| App Server as narrow Codex Control | **Prototype, then adopt** | Separate Supervisor-owned stdio child using only `initialize`, `account/read`, `account/rateLimits/read`, `account/rateLimitResetCredit/consume` and `model/list`. Generate its schema from the pinned binary. Graceful stop closes stdin and waits; deadline expiry sends SIGTERM to the owned process group, then SIGKILL. There is no `shutdown` RPC. | A Control crash must not kill the Run or mutate an in-flight Attempt. Auth/backend/quota responses are shared failure state; JSON-RPC/process/schema errors are native-control-local. | Fake-server protocol tests plus a live read-only preflight; EOF, SIGTERM and forced kill with descendant reap; kill/restart Control during an Attempt; prove no credential or pipe is reachable by Worker; prove a stale/sparse limit update cannot erase known fields. |
| App Server Unix-socket control transport | **Reject for v2** | Do not expose a persistent socket; one Supervisor owns one stdio child. The supported `--listen unix://` transport uses websocket framing, a mode-0600 socket and `app-server proxy`, but exists for multi-client local control planes ([pinned App Server protocol][pinned-app-server]). | Adds socket lifecycle, reconnect, multi-client ordering and another credential-reachable endpoint. The transport is supported in 0.147.0, unlike experimental TCP websocket, but is unnecessary for one owner. | Reconsider only if two independent Supervisor clients are required; then Gate socket mode/ownership, stale-socket recovery, simultaneous clients, reconnect ordering and Worker denial. |
| App Server direct shell/process/filesystem RPCs | **Reject and contain** | Codex Control's client method allowlist excludes `thread/shellCommand`, `command/exec*`, experimental `process/*` and all `fs/*`. The pipe remains Supervisor-only. `thread/shellCommand` and `process/spawn` are explicitly unsandboxed; filesystem RPCs accept absolute host paths ([pinned shell RPC][pinned-shell-rpc]; [pinned process RPC][pinned-process-rpc]; [pinned filesystem RPC][pinned-fs-rpc]). | A client bug or pipe compromise can otherwise execute with the App Server uid, bypass the code-mode-host uid split, or read/write/remove auth and Run state. These methods are present in the pinned schema; `process/*` alone is experimental. | Fake server must reject unallowlisted outbound methods. Gate a hostile Worker and compromised Attempt against pipe/fd/procfs access; call every direct surface in a sacrificial fixture and prove it is unreachable through production Control. Reject the whole Control seam if RPC-level allowlisting is not enforceable. |
| TypeScript Codex SDK | **Reject** | None in the image. It starts/continues/resumes local Codex threads but requires Node 18; the image deliberately carries no Node. | Adds a runtime and wrapper while retaining the same Codex failure domain. SDK version can drift from the pinned binary ([Codex SDK][sdk]). | Reconsider only if it exposes a supported control absent from App Server and beats direct JSON-RPC in a version-skew Gate. |
| Python Codex SDK | **Reject** | None in the image. Current releases wrap local App Server and ship their own pinned CLI runtime. | Installing it risks two Codex pins and an opaque runtime selection; it removes no failure domain ([Codex SDK][sdk]). | Reconsider only if an SDK version can be byte-pinned to 0.147.0 and materially reduces protocol risk without adding a second binary. |
| ChatGPT managed login and refresh | **Adopt** | Human device-code login before the Run; root-owned `/state/codex/auth.json`; Supervisor/Codex owns refresh. Recovery receives typed health only. | Expiry/refresh/backend failures are auth failures, not solve failures. File is password-equivalent and must survive container recreation ([authentication][auth]). | Preflight `account/read` and a harmless live turn; restart container and refresh; Worker must fail to read/copy the file or obtain tokens through environment/procfs. |
| API key / metered Responses / Agents SDK | **Reject for scored Run** | No `OPENAI_API_KEY`, `CODEX_API_KEY` or metered `CODEX_HOME` in the competition environment. | Separate billing/access path; no available key or credits. It would introduce a credential and does not inherit earned resets. | Revisit only by a new explicit Owner funding decision and separate spend/secret Gate. |
| Enterprise personal/access token | **Reject** | No `CODEX_ACCESS_TOKEN`; do not confuse it with Pro OAuth. | Account entitlement mismatch; static automation token has a different lifecycle. | Revisit only if the workspace actually grants Enterprise automation access. |
| Live model catalog and effort selection | **Adopt through Control** | At preflight call `model/list`; validate configured model and effort against the returned ordered capabilities, then freeze both in the Run record. Continue passing model and effort explicitly per invocation. | Catalog and aliases are account/backend state and may drift without an image change. Unsupported model is setup failure, not an Attempt failure. | Fixture missing/hidden/changed model; live read using scored auth; one harmless turn proving the frozen model/effort; record requested and returned/effective identifiers where available. |
| Strongest-model routing | **Prototype** | Solver policy chooses model/effort; Codex only executes it. No quota-driven downgrade. | Larger model/effort can cost more wall and usage; public API dimensions do not define ChatGPT-backed Codex behavior. | Paired Gate over representative Challenges: accepted Flags, wall time, cached/uncached/output/reasoning tokens, usage-window movement and failures. |
| `account/usage/read` activity summary | **Prototype for diagnostics; reject for control** | Codex Control may snapshot token-activity totals/daily buckets into the Run report. It must not schedule, reset or terminate from this coarse account history. | ChatGPT-only account aggregation can lag and has a different grain from per-turn JSONL and quota windows ([App Server account controls][app-server]). | Fake missing/lagged buckets and one live read; reconcile without forcing equality to Solver Steps. Promote only as postmortem context, never authoritative spend. |
| `account/rateLimits/read` and updates | **Prototype, then adopt** | Codex Control polls the full snapshot; notifications are hints merged by `limitId`, never the sole truth. Store `usedPercent`, duration, `resetsAt`, reached type and reset-credit inventory in Supervisor state. | Only ChatGPT auth supports it. Updates are sparse; absence/null can mean unavailable, not zero. Backend/product schema can drift despite a pinned client ([App Server account controls][app-server]; [pinned reset protocol][pinned-reset-protocol]). | Fake multi-bucket/sparse/out-of-order updates; live read without consumption; restart from persisted snapshot; remaining percent is clamped `100-usedPercent`, never inferred from tokens. |
| Earned reset consumption | **Prototype, then adopt** | Only Codex Control can redeem; one pre-authorised Run budget from the preflight inventory. Journal credit ID, logical attempt ID, UUID idempotency key and state **before** the RPC. | Reset eligibility is backend state. RPC timeout/EOF is uncertain; `nothingToReset` and `noCredit` are conclusive non-successes; `reset` and `alreadyRedeemed` are successes. The pinned client has a 10-second request timeout ([reset processor][pinned-reset-processor]). | Deterministic fake backend for every outcome, timeout before/after commit, duplicate delivery, crash between journal/RPC/result/refetch, expiring/redeeming credits, two concurrent callers. No live credit consumption in Gate. |
| Quota waiting and manual reset detection | **Adopt after reset prototype** | At <=2% remaining, Control may make one logical reset attempt. A conclusive `nothingToReset` leaves inference running. At hard exhaustion, pause only new Inference; Supervisor, Intake, deterministic work and Recovery live. Poll full limits on a bounded cadence and resume when capacity returns or a manual reset appears. | Backend outage, account exhaustion and native parser failure must remain distinct. A reset threshold is not documented and 2% may be ineligible. | Simulated 2%, 1%, 0%, rollover, manual reset and backend outage; Run stays alive; no busy request/inference loop; reserved tail is preserved. |
| Reset retry safety | **Adopt as an invariant** | Reuse the **same** persisted idempotency key after any transport timeout, EOF or unknown response. Never create a new key while that logical attempt is uncertain. Create a new key only after a conclusive non-success and a later policy trigger. Prefer an explicit available `creditId`; refetch after every success/uncertain result. | A blind new-key retry could consume a second credit. `alreadyRedeemed` means the first request succeeded; `nothingToReset` means no current window is eligible; `noCredit` means none is available ([pinned reset protocol][pinned-reset-protocol]; [reset backend request][pinned-reset-backend]). | Crash/replay model must show at-most-one credit spent per logical attempt and immediate recovery from `alreadyRedeemed`; a `redeeming` credit blocks any new-key attempt until reconciled. |
| Structured final output | **Prototype selectively** | `--output-schema` can constrain a final answer, but tool/item events remain the operational record. Use only for narrow Triage/Recovery decisions, never as proof of a Flag. | Schema conformance does not make claims observations; unsupported/model failure can suppress a final item. | Real schema success/refusal/truncation and deadline kill; independent verification still gates every state transition. |
| Resume and persisted threads | **Prototype within one Attempt only** | If promoted, resume only the same Attempt/thread from a durable ID after reconciling completed side effects. Never carry a thread across Attempt reset or into another Challenge. | Native context can replay stale Claims or duplicate side effects; thread files are writable state. `exec resume` is supported, and App Server exposes read/resume/fork ([non-interactive mode][noninteractive]; [pinned App Server protocol][pinned-app-server]). | Kill before request, during model stream, during command and after command/before journal; resume must never duplicate a submission or command and must retain the authority/budget boundary. |
| Native compaction | **Prototype** | Trigger only at a Solver milestone and retain authoritative state outside model context. App Server exposes `thread/compact/start` and a typed compaction item; `exec` JSONL does not expose the full App Server item set. | Compaction content is model/backend behavior and may omit facts; a killed compact can leave uncertain context. | Force compaction with planted objective, budget, pending side effect, confirmed facts, dead ends and malicious Claim; require 100% invariant retention and lower uncached input before promotion. |
| Image input | **Adopt** | Keep bounded staged `-i` files from the Challenge workdir. It adds no filesystem reach beyond staged Artefacts. | Model/catalog modality can change; malformed/oversized images affect context or turn success. CLI supports image attachments on new and resumed turns ([non-interactive mode][noninteractive]). | Existing malformed/large/missing/multiple-image cases plus live OCR/visual Challenge; model catalog says image input is supported. |
| Hosted web search | **Adopt conditionally** | Keep explicit on/off from the Board profile and record every `web_search` item. Treat results as untrusted Claims. | Hosted search is outside local command sandbox/network rules and may be disabled by workspace policy; live/indexed behavior can drift ([web search][web-search]). | Board-profile off proves zero search items; on proves one cited result; prompt-injection fixture cannot cause authority expansion or unverified Flag admission. |
| Command network and sandbox | **Adopt outer isolation; prototype uid split** | Continue full access only inside the dedicated container because nested `bwrap` is not viable there. Add Worker uid separation through the code-mode-host launcher; give Challenge commands only workdir/Target access, never `/state` or auth. Approval policy must be noninteractive/fail-closed. | `danger-full-access` makes the container the boundary; official guidance recommends it in a container only when the container is the intended isolation boundary ([approvals and security][security]). | Worker cannot read/write Run ledger, env, auth or Supervisor IPC; can reach allowed Target; process escape/reboot/recovery tests; no prompt can open an approval wait. |
| Headless approval controls | **Adopt `never`; reject auto/bypass** | Pin `--ask-for-approval never`; exec mode already defaults to it and rejects approval client requests. Reject `--approve-for-me` because it installs an automatic reviewer. Reject `--dangerously-bypass-approvals-and-sandbox`; explicit `--sandbox danger-full-access` already records the container-boundary choice without also bypassing approval handling ([pinned approval behavior][pinned-approvals]). | `never` makes a command needing elevation fail instead of waiting for absent input. Auto-review expands authority; the bypass flag collapses two independent controls. CLI/config semantics can change with the pinned binary. | Spawn fixtures that request command, patch and extra-permission approvals: each must refuse promptly with a typed failure and no side effect. Prove no client request can deadlock the headless process; argv must contain neither auto-review nor bypass. |
| Code-mode-host process split | **Prototype** | Replace the installed host path with a tiny root-owned launcher that drops to Worker uid before execing the byte-pinned real host; keep inherited stdio, not websocket. | The pinned host is an implementation seam, not documented as a privilege boundary. Host death must fail closed; inherited environment and filesystem descriptors require audit. | Prove actual uid, minimal env/fds, auth/ledger denial, output fidelity, limits, cancellation and whole-tree reap. Reject if any direct tool bypasses the host. |
| MCP servers/apps/connectors | **Reject by default** | No MCP config or OAuth in the scored image. A future single tool must sit behind the same Adapter/credential boundary and be explicitly `required` only if absence should refuse boot. | Extra startup, network, OAuth, prompt-injection, side-effect and schema failure domains. App Server exposes typed startup/auth status, but that does not make the tools necessary ([MCP][mcp]). | New decision ticket and representative Gate per tool; fail startup, revoked OAuth, malicious result, duplicate side effect and unavailable server. |
| Repo/user/system skills | **Reject for Attempts** | Attempt workdirs must not discover contributor skills; prompt policy remains one Solver-owned, versioned input. Do not mount user skill roots into the Worker. | Implicit selection is model behavior; skill inventories consume context and can change outside the image. Skills use progressive disclosure and several discovery scopes ([skills][skills]). | Reconsider only if one CTF skill beats the same prompt/tool baseline without context pollution or new authority. |
| Native nested subagents / Ultra proactive delegation | **Reject for v2** | Solver Lanes own concurrency across Challenges. One Attempt has one accountable native Harness; no nested agent may launch independent side effects. | Subagents multiply tokens, share context/authority, complicate deadlines and obscure Step ownership. Official docs state they consume more tokens; proactive delegation is product-level behavior ([subagents][subagents]). | A later prototype must prove separable read-only work, bounded child count, joined usage, deadline propagation, no duplicate commands/submissions and higher accepted Flags per Run. |
| `exec --json` telemetry | **Adopt** | Continue translating lifecycle/item events to durable Solver Steps and preserve unknown raw events. Usage is final-turn telemetry, not a complete count for killed turns. | Simplified JSONL omits some richer App Server items and a killed turn may lack final usage. Pinned event types include command, file, MCP, collab, web search, messages, reasoning and final usage ([pinned events][pinned-events]). | Contract fixtures generated from exact binary; forced kill marks usage unknown; parser mutation tests; compare emitted tool items against filesystem/process observations. |
| OpenTelemetry export | **Reject as Run truth** | Keep Solver JSONL as canonical. Do not export prompts/tool results or depend on remote collector availability during competition. | OTel is optional, batched and flushed on shutdown; it duplicates sensitive data and can lose the tail on crash ([advanced configuration][advanced-config]). | May be reconsidered only as local diagnostics with redaction and a crash-loss measurement; never gate Recovery on it. |
| Recovery hooks and status | **Adopt typed events, reject advisory hooks** | Recovery consumes Supervisor-normalized auth, quota, Harness, code-host and process states. App Server thread/item/turn/account notifications are evidence; Session hooks never own recovery. | Notifications can be sparse or lost with the process; hooks are advisory. Poll/reconcile authoritative state after restart. | Drop/reorder/duplicate notifications, kill Control and Broker, corrupt one rollout, lose code host, backend down; each must produce one typed state and bounded action. |

## Recommended reset state machine

If adopted, the reset interface should prevent the exact dangerous retry: “the click/RPC looked
like it failed, so try again with a fresh request.” The request body sends the caller's idempotency
key as `redeem_request_id`; the backend can return four closed outcomes
([backend request][pinned-reset-backend]; [protocol enum][pinned-reset-protocol]).

1. Preflight reads the full snapshot and durably records the authorised available count and detail
   rows. A detail count can be capped, so `availableCount`, not array length, is the inventory.
2. At a policy trigger, choose an `available` credit when a detail row exists. Before sending,
   append `prepared(logical_attempt, credit_id, idempotency_key)` to the Supervisor journal and
   fsync it.
3. Send once. On `reset`, record success; on `alreadyRedeemed`, record idempotent success. In both
   cases refetch limits before resuming or spending another credit.
4. On timeout, EOF, process death or undecodable response, mark the attempt `uncertain`. Retry only
   the same `creditId` and same idempotency key, or first refetch and reconcile a `redeeming` or
   `redeemed` row. Never mint another key for an uncertain attempt.
5. `nothingToReset` is not a transport error and not “the click failed.” It means the backend found
   no current eligible quota window. It does **not** authorise another immediate request. Record the
   conclusive non-success, keep Inference running if capacity remains, and wait for the next policy
   trigger or changed full snapshot.
6. `noCredit` is also conclusive: no earned reset is available. Do not retry until a later snapshot
   shows one.
7. At hard exhaustion, Inference pauses but the Run does not end. Poll `account/rateLimits/read` on
   a bounded schedule, observing both automated and Owner-manual resets. Backend failure backs off;
   it never becomes a tight inference retry loop.

The public and pinned documentation define eligibility only as a backend decision; neither says a
reset becomes eligible at 2%, 1% or exactly 0%. Therefore <=2% is an early **attempt threshold**, not
a claim that redemption will work. The fake Gate must accept `nothingToReset` at both 2% and 1%,
then `reset` at exhaustion, while the Run and deterministic work stay alive.

## Suggested downstream sequencing if adopted

If the map adopts these recommendations:

1. First implement the already-decided one-file `.env` cutover. It is independent production work,
   not part of this research ticket.
2. Build the uid/process boundary and make the native `exec` baseline pass through the
   privilege-dropped code-mode host.
3. Add Codex Control with generated 0.147.0 schema, read-only account/model preflight and fake
   limit/reset server.
4. Add the journalled reset/wait state machine. Run every destructive-path test against the fake
   backend; do not consume a live credit.
5. Only then decide whether App Server inference/compaction/resume earns a separate prototype. None
   is required to ship the safe account controls.

## Limitations

- Primary sources do not publish the server's eligibility threshold for an earned reset, the exact
  subscription weight of models/efforts/tools, or a guarantee that two banked resets cover a
  five-hour Run.
- Source proves idempotency is represented and `alreadyRedeemed` exists; only a fake backend can be
  exercised safely before competition. The live end-to-end consume path remains deliberately
  untested.
- The code-mode-host launcher is a plausible uid seam, not yet a proven security boundary. The Gate
  must enumerate every direct execution path and reject the design if any bypasses the dropped uid.
- Current online App Server docs already contain surfaces beyond the pinned release. All production
  parsing must be generated/tested against 0.147.0 and re-audited with any Codex upgrade.

[app-server]: https://learn.chatgpt.com/docs/app-server
[noninteractive]: https://learn.chatgpt.com/docs/noninteractive
[sdk]: https://learn.chatgpt.com/docs/codex-sdk
[auth]: https://learn.chatgpt.com/docs/auth
[web-search]: https://learn.chatgpt.com/docs/web-search
[security]: https://learn.chatgpt.com/docs/agent-approvals-security
[advanced-config]: https://learn.chatgpt.com/docs/config-file/config-advanced
[mcp]: https://learn.chatgpt.com/docs/mcp
[skills]: https://learn.chatgpt.com/docs/skills
[subagents]: https://learn.chatgpt.com/docs/subagents
[pinned-app-server]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server/README.md
[pinned-events]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/exec/src/exec_events.rs
[pinned-reset-protocol]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server-protocol/src/protocol/v2/account.rs#L289-L387
[pinned-reset-processor]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server/src/request_processors/account_processor/rate_limit_resets.rs#L1-L119
[pinned-reset-backend]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/backend-client/src/client/rate_limit_resets.rs#L1-L112
[pinned-host-spawn]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/code-mode/src/remote_session/connection.rs#L213-L264
[pinned-host-transport]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/code-mode-host/src/transport.rs#L199-L326
[pinned-shell-rpc]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server-protocol/src/protocol/v2/thread.rs#L1003-L1018
[pinned-process-rpc]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server-protocol/src/protocol/v2/process.rs#L19-L65
[pinned-fs-rpc]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/app-server-protocol/src/protocol/v2/fs.rs#L1-L138
[pinned-approvals]: https://github.com/openai/codex/blob/be6e8eac029b183056b7e4402879f15d2c85f61b/codex-rs/exec/src/lib.rs#L388-L405
