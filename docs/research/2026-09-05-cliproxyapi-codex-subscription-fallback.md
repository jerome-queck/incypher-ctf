# CLIProxyAPI as a Codex-subscription fallback

_Research snapshot: 5 September 2026. This is a technical and source-provenance finding, not legal
advice. Upstream code was inspected at CLIProxyAPI `v7.2.149`, commit
`2a6b87aca083a5bf498ac1f68a1b636c500d7aaa`._

## Finding

Reopen the absolute rejection in [ADR-0011](../adr/0011-the-sanctioned-path-is-the-only-path.md), but
do **not** replace it with “CPA is officially approved.” The primary evidence supports this narrower
statement:

> OpenAI's Codex lead publicly treated CLIProxyAPI as acceptable for a person using their own
> ChatGPT/Codex entitlement through a local client, and later distinguished personal OSS-client use
> from subscription traffic re-served or shared across users.

That is enough to justify an isolated, single-account CPA experiment. It is not an OpenAI terms
amendment, a support commitment, a security review, a guarantee against automated fraud controls,
or permission for account pooling, resale, quota evasion, or team access. The description
“completely supported and allowed” therefore overstates what the evidence proves.

CPA also is not automatically an independent fallback for this Solver. Put behind the same
`codex exec --json`, it replaces the authentication/transport path but retains the Codex harness and
JSONL seam. Called directly, it removes that dependency but supplies model-protocol events rather
than the Solver's command-execution Steps, so it requires a second agent harness and adapter.
The primary CLI already spends the personal Codex subscription: CPA is alternate plumbing to that
same entitlement, not a fallback “to the subscription.”

## What the OpenAI-side evidence proves

| Evidence | Authority | What it supports | What it does not support |
| --- | --- | --- | --- |
| [Tibo Sottiaux's 12 July post](https://x.com/thsottiaux/status/2076119366647894371) explicitly gives CLIProxyAPI setup steps for pointing another coding client at GPT-5.6 Sol and says, informally, “If this gets blocked, I owe you a reset.” | Personal public statement by an OpenAI employee. [OpenAI identifies Thibault Sottiaux as its Codex Lead](https://openai.com/index/openai-to-acquire-astral/). | CPA itself, not merely an unnamed OSS client, was knowingly recommended for personal client use. | Formal product support, a contractual safe harbour, every CPA release/configuration, or this unattended CTF workload. |
| [Sottiaux's 21 August post](https://x.com/thsottiaux/status/2090675027670978569) says multi-user subscription-to-API re-serving such as `sub2api` is unsupported and fraud-flagged, while a user signing in with their own ChatGPT account through official or OSS clients is “completely fine.” | Personal public operational guidance from the Codex lead; still not a policy page. | The operative boundary is personal client use versus re-serving/sharing, not simply “official binary versus proxy.” | CPA by name in that second statement; pooling accounts; sharing one account with teammates; evading limits; a legal opinion. |
| [OpenAI's individual Terms of Use](https://openai.com/policies/row-terms-of-use/) (effective 1 January 2026) prohibit sharing account access, automated extraction, and bypassing restrictions or rate limits. | Formal contract for an individual account outside the EEA/UK/Switzerland. | One owner only; no credential sharing; no behaviour intended to defeat quotas or safeguards. | The page does not name CPA or create a CPA-specific exception. |
| [OpenAI's Codex-plan help](https://help.openai.com/en/articles/11369540-codex-and-chatgpt-plan-usage-limits) describes included Codex usage and says limits may require waiting, credits, a reset, or an upgrade. | Official product documentation. | A proxy does not add entitlement; account limits remain the limits. | It documents official Codex surfaces, not CPA support. |

The two X posts were retrieved from X's public oEmbed representation on 5 September. Their status
IDs, authorship and text were observable; this search found no reply providing a more specific
blessing for this repository. The combined inference is strong for **one owner using one account
through a private client** and weak beyond it.

“Single user” must consequently mean all of the following here:

- Jerome's account and entitlement only; no teammate, service customer, public client, or shared
  proxy credential.
- One locally controlled Solver deployment. Multiple autonomous workers remain the same owner's
  use, but the posts do not expressly bless high-concurrency, unattended workers; parallelism needs
  a bounded concurrency policy and should be confirmed with OpenAI Support if account-action risk
  is unacceptable.
- No CPA account pool or round-robin, despite upstream advertising those features.
- A `429`, quota notice, fraud response, or account restriction is a shared-capacity condition—not
  a signal to route around a restriction or, by itself, to end the Run.

## Upstream facts: capability is real; sanction is not inherited

The inspected upstream is [router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI).
Its [README at the pinned commit](https://github.com/router-for-me/CLIProxyAPI/blob/2a6b87aca083a5bf498ac1f68a1b636c500d7aaa/README.md#L107-L135)
claims Codex OAuth, OpenAI-compatible Responses endpoints, streaming and function calling. The
[signed `v7.2.149` release](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v7.2.149) has
Linux artifacts and checksums. Its [MIT license](https://github.com/router-for-me/CLIProxyAPI/blob/2a6b87aca083a5bf498ac1f68a1b636c500d7aaa/LICENSE)
permits using/modifying the software; it grants no rights to an upstream model service.

The implementation substantiates the basic mechanism:

- The [Codex OAuth implementation](https://github.com/router-for-me/CLIProxyAPI/blob/2a6b87aca083a5bf498ac1f68a1b636c500d7aaa/internal/auth/codex/openai_auth.go#L23-L86)
  uses OpenAI authorization/token endpoints and PKCE, then refreshes the resulting token.
- The [Codex executor](https://github.com/router-for-me/CLIProxyAPI/blob/2a6b87aca083a5bf498ac1f68a1b636c500d7aaa/internal/runtime/executor/codex_executor_execute.go#L22-L76)
  forwards to `chatgpt.com/backend-api/codex/responses`. It therefore consumes the same ChatGPT
  account, backend and allowance as the direct subscription route.
- [Token serialization](https://github.com/router-for-me/CLIProxyAPI/blob/2a6b87aca083a5bf498ac1f68a1b636c500d7aaa/internal/auth/codex/token.go#L16-L83)
  includes plaintext access and refresh tokens, account ID and email. A private directory helps at
  rest, but placing it in the Solver container or shared `/state` recreates ADR-0011's credential
  exposure.
- The [example configuration](https://github.com/router-for-me/CLIProxyAPI/blob/2a6b87aca083a5bf498ac1f68a1b636c500d7aaa/config.example.yaml#L1-L42)
  defaults an empty host, documented as all IPv4 and IPv6 addresses; TLS is off. Remote management
  is off by default, but the control panel is not disabled by default. These defaults are unsuitable
  for a credential broker without hardening.
- Disabling ordinary request logging is not sufficient evidence of no prompt retention. The
  [middleware captures request/response material](https://github.com/router-for-me/CLIProxyAPI/blob/2a6b87aca083a5bf498ac1f68a1b636c500d7aaa/internal/api/middleware/request_logging.go#L27-L60),
  and [actionable errors can force a log](https://github.com/router-for-me/CLIProxyAPI/blob/2a6b87aca083a5bf498ac1f68a1b636c500d7aaa/internal/api/middleware/response_writer.go#L289-L297)
  even when normal request logging is disabled. The selected deployment must prove where error
  bodies go and make that storage sidecar-private and ephemeral.
- GitHub reports [no upstream `SECURITY.md`](https://github.com/router-for-me/CLIProxyAPI/security/policy).
  This is an absence of a documented disclosure policy, not proof of a vulnerability or of safety.

The README's multi-account balancing, relay sponsors, dashboards and related-project claims are the
project's statements. None is OpenAI authorization. The Solver should consume only pinned upstream
source/release artifacts and must not inherit a hosted relay, GUI, dashboard, account pool, sponsor,
or auto-updater.

## Failure independence

The current seam is not just HTTP. [`solver/codex.py`](../../solver/codex.py) launches
`codex exec --json`, lets that official harness execute tools, and translates its JSONL stream into
the deterministic `Taken`/`Step` vocabulary required by
[ADR-0014](../adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md).
[Official Codex configuration](https://developers.openai.com/codex/config-advanced#custom-model-providers)
does support a custom Responses provider, so pointing that same CLI at a local CPA endpoint is
technically plausible. The [official CLI has its own OAuth refresh implementation](https://github.com/openai/codex/blob/a07158c7846e5cb6684216d779598e540998f295/codex-rs/login/src/auth/manager.rs#L188-L200),
separate from CPA's. The resulting independence is limited:

| Failure | Direct `codex exec --json` | Same `codex exec --json` via CPA | Direct CPA plus a new harness |
| --- | --- | --- | --- |
| Official CLI's `auth.json` missing, unreadable, or refresh path broken | Fails | Potentially survives: CPA owns a separate OAuth store | Potentially survives |
| Codex CLI binary, startup, tool loop, sandbox, or JSONL format/parser broken | Fails | **Also fails**: the same CLI/harness/JSONL seam remains | Can survive if the second harness is genuinely separate |
| CPA daemon/config/local key/translation broken | Not affected | Fails | Fails |
| Account allowance exhausted, `429`, fraud flag, suspension | Fails | **Also fails**; same account and entitlement | **Also fails**; same account and entitlement |
| `chatgpt.com` Codex backend or path unavailable | Fails | **Also fails** | **Also fails** |
| Solver cannot execute/model-map tool calls into `Taken` Steps | Existing adapter owns it | Existing adapter still owns it | New harness and adapter must own it before this is a usable fallback |

Two different CPA topologies therefore answer different problems:

1. **CPA as a custom provider behind Codex CLI** is the small, testable option. It is auth-path
   diversity and a credential-containment opportunity. It does not answer “the Codex JSON stream
   died.”
2. **CPA called without Codex CLI** is harness diversity. CPA emits an OpenAI/Responses-compatible
   model protocol and tool calls; it does not itself run the Solver's shell loop or emit the current
   JSONL Steps. A separate client/harness must execute commands, preserve deadlines and process-group
   killing, withhold the Board credential, classify Claim versus Observation, and map every event to
   `Taken`. That is a second adapter/product path, not graceful configuration fallback.

Using CPA with OpenCode or another Codex-capable harness could supply that second loop. It also
imports that client's binary, event schema, tool policy and provider terms. The July post
demonstrates technical precedent with another client; it is not a review of that client's terms or
fitness for this Solver. Claude Code is outside the closed inference set regardless of protocol
compatibility.

## Minimum safe shape for an experiment

Run CPA as a separately supervised sidecar, not inside the process that executes challenge code:

- Pin a reviewed release/commit and artifact SHA-256; disable auto-update and dynamic plugins.
- Exactly one Codex account. Disable/remove account-pool and round-robin paths.
- Put OAuth files on a CPA-only persistent volume. Never mount that volume or raw tokens into the
  Solver or shared `/state`.
- Expose no host/public port. Use loopback in a shared network namespace, or a private container
  network with only the Solver admitted. Disable the management API and control panel. Use a random
  local client key.
- Treat that local key and endpoint as **quota-spend authority**. A sidecar keeps raw OAuth out of
  challenge code, but any agent process able to call the proxy can still burn or misuse the account;
  network/broker controls and concurrency/rate caps must bound that authority.
- Keep request/error logs on a sidecar-private ephemeral filesystem; verify by fault injection that
  prompts, flags and tokens are not retained. Do not infer this from `logging-to-file: false` alone.
- Complete and persist OAuth during human setup; preflight refresh immediately before the Run. No
  competition-time login flow.
- Supervise liveness and readiness separately. A live PID is not readiness: probe authenticated
  model discovery/a minimal request, classify token expiry/revocation versus transient backend
  failure, cap restarts, and never fail over on generic errors or shared-quota responses.
- Revoke the CPA OAuth grant after the competition or any suspected exposure.

The sidecar boundary is materially better than the current raw `auth.json` on `/state`, but it is
not complete isolation while Solver-controlled code can reach the proxy. That residual authority
must be explicit rather than described as “credentials are not exposed.”

## Wayfinder consequences

CPA should enter the map as a conditional v2 workstream, split at the architecture decision:

1. Record the evidence boundary: personal, private, one-account use is supported by the employee
   statements; multi-user re-serving, pooling and limit circumvention remain prohibited/out.
2. Decide which failure is being insured. Choose **auth-only diversity behind Codex CLI** or fund a
   **second harness/adapter** for JSONL independence. Do not call the former a fallback for the latter.
3. Prototype the chosen route at the pinned CPA version using a disposable OAuth grant, then test
   login/refresh, restart recovery, timeout/kill, streaming interruption, quota exhaustion, corrupt
   token state, dead sidecar, stale-but-live sidecar and upstream outage.
4. Prove the containment and logging claims from the running image: filesystem reachability from
   challenge commands, network reachability, port binding, management endpoints, log contents and
   post-restart secrets.
5. Gate activation on classified independent failures. A generic “Codex failed, try CPA” switch can
   duplicate spend, amplify an outage, or look like restriction evasion.
6. Before enabling future parallel workers, set one account-wide concurrency budget and ownership
   lease. The public statements do not settle whether arbitrary unattended parallelism is considered
   ordinary single-user client use.

**Decision recommendation:** accept CPA for a time-boxed, isolated prototype and possibly an
auth-path fallback. Do not yet put it in the scored critical path, describe it as formally sanctioned,
or count it as capacity/JSONL independence. Promotion requires the topology decision, containment
proof, failure-classification tests, and—if high parallelism is intended—written OpenAI Support
confirmation of that specific workload.
