# One owner, one subscription, and one observed limit state

> **The native-first route selection is superseded by
> [ADR-0051](0051-v2-has-a-finite-core-and-proof-earned-capability-packs.md).** Release-candidate
> qualification may select native or CPA as primary. One owner, separate credentials and Harnesses,
> shared quota, classified route-local switching and Quota wait stand.

[The OpenAI-only inference and credential
posture](https://github.com/jerome-queck/incypher-ctf/issues/159) closes the Solver's inference set
to two routes over Jerome's one personal ChatGPT subscription: pinned native `codex exec --json`
first, and the separately-harnessed private CPA route only after its rehearsal proves the same
Attempt, tool, deadline, record and safety contract. Claude, local models, metered OpenAI API
inference, account sharing, pools, relays and quota rotation leave the runtime and setup surface.
Contributor-only `CLAUDE.md`, `.claude/skills/` and attribution remain development compatibility,
not a third Inference route.

The decision has three joined parts. **The Supervisor owns every inference and Board credential;
route changes answer only a positively classified route-local failure; and native Codex Control
observes the account's actual limit state and safely spends only resets authorised before the
Run.** This forward-amends ADR-0010's provider chain, fixed-window assumptions and board-overlay
environment, ADR-0011's absolute proxy ban, ADR-0014's provider branches, ADR-0032's abstract
wait/reset clause and ADR-0033's Observer scope. ADR-0039's native-primary and independent-CPA-
Harness choice stands.

## One persistent environment, injected once

The checkout has one persistent operator file: `.env`, dedicated to the official IN-CYPHER Run.
It holds the Board URL and access token, Team key, Run identity and non-secret Run choices such as
model and shortened duration. Docker reads it through `--env-file`; the file is never copied into
an image, mounted into an Attempt or searched for from inside the container. The Supervisor reads
the resulting process environment once at Boot, validates the whole snapshot before `run-open`,
and hands capabilities rather than values to its children.

The local Gate generates a short-lived env file with the same variable names and disposable local
values, starts the Solver through it and removes it after Docker has accepted the environment. It
never copies or reuses the official Team key or CTFd token. Repository-specific consumers stop
looking for `.env.incypher`, `.env.<event>` or arbitrary `.env.*` overlays; `.gitignore` may retain
the broad pattern as defence against accidental secret files. The cutover is the first post-map
implementation ticket, before CPA, local-Gate or Worker-boundary delivery, and atomically verifies
the merged official file before removing the old overlay.

A Boot or in-container process restart inherits the Supervisor's immutable snapshot. A Docker
container restart inherits the environment in that container's configuration. Recreating the
container requires `--env-file .env` again and is setup, not Recovery. No stuck Worker or Recovery
Agent searches the filesystem for secrets.

## Credentials are capabilities, not troubleshooting material

Native Codex authentication and CPA authentication are separate despite using the same Owner and
entitlement. Native Codex Control owns the native `CODEX_HOME`, App Server stdio pipe and reset
authority. CPA runs as a pinned, separately supervised sidecar with a CPA-only persistent OAuth
volume, private endpoint and local client key; its access token, refresh token, account identity,
logs and management surface never enter shared `/state` or an Attempt.

The Worker receives neither Board nor inference credentials. Recovery receives typed health and
bounded privileged operations—refresh, probe, restart, route change, reset and wait—not raw values,
auth files, endpoints or filesystem-search authority. Missing, revoked or unreadable credentials
fail closed. The Worker/process boundary remains a Gate obligation: an environment allowlist alone
does not stop a same-container root process from reading another process or mounted auth file.

Native remains selected through quota pressure. The route changes to CPA only for a positively
classified native-local failure: native auth storage or refresh, CLI startup, code host, tool loop,
sandbox, JSONL schema or parser. CPA OAuth, daemon, configuration, local transport and protocol
translation are CPA-local. Account exhaustion or restriction and the Codex backend are shared.
An unclassified failure invokes Recovery and does not change route. CPA is neither additional
capacity nor an answer to a shared outage.

## Codex Control observes limits without becoming the Harness

The pinned Codex binary's stdio App Server is prototyped as a narrow, Supervisor-owned **Codex
Control** using only generated, version-matched account/model methods. Native `codex exec --json`
remains the primary Harness. No SDK, API key or second Codex binary enters the image to obtain these
controls.

When available, `account/rateLimits/read` is authoritative for each returned `limitId`:
`usedPercent`, window duration, `resetsAt`, reached-limit classification and earned-reset inventory.
Sparse update notifications are hints merged into full snapshots, never replacement truth.
Missing or null fields mean unknown, not zero; remaining percentage is the clamped difference from
100, never a token estimate. `account/usage/read` may enrich a postmortem but cannot schedule,
reset, route or terminate work. The Observer may show the same normalized limit/reset state from
canonical Supervisor records without receiving control authority.

At preflight the Owner authorises a fixed Run reset budget no greater than the observed available
count. For every logical redemption, Codex Control durably journals the logical attempt, UUID
idempotency key and the selected credit identifier when the snapshot supplied one before sending
one request. When only the authoritative count is available, the identifier is omitted and the
backend chooses. `reset` and `alreadyRedeemed` are one idempotent success, charge the Run budget
exactly once and require a full limit refetch. `nothingToReset` is a conclusive non-success: the
backend found no currently eligible limit window and spent no credit. `noCredit` is a conclusive
absence. Timeout, EOF, process death or an undecodable response is uncertain; every retry reuses
the same optional credit and idempotency key until reconciled. A new key is forbidden while an
earlier attempt is uncertain, because a blind new request could spend a second credit.

Two and one percent remaining are early attempt thresholds, not eligibility claims. They apply to
the general `codex` bucket and to a model-specific bucket only after the live catalog/control
surface positively associates it with the Run's frozen model; an unassociated bucket is observed
but cannot spend a reset. A conclusive `nothingToReset` at two percent leaves Inference running and
permits a new logical attempt only at the later one-percent trigger or changed full snapshot. At
hard exhaustion of any applicable bucket only new Inference pauses: the Supervisor, Run, Intake,
deterministic preparation and Recovery stay alive. Control polls every full bucket once per minute
to detect automatic rollover or an Owner-manual reset; after a conclusive non-success it retries
redemption only on changed authoritative state or a bounded five-minute schedule. After any reset,
Inference resumes only when the refetched applicable buckets show capacity. The Run never
busy-retries model calls, spends a second reset on an uncertain first request, or closes merely
because quota is exhausted.

No quota-driven downgrade is introduced. Model, effort and Agent-role routing remain evidence
decisions, and two banked resets do not prove that the strongest model is available or sustainable
for the whole Run.

## Delivery and proof

Production work follows the cleared map through `/to-spec` and `/to-tickets`; this decision does
not migrate the files in place. The handoff removes obsolete Claude/API/metered variables and
`CODEX_HOME_METERED`; changes boot, adapter, setup, reporting, redaction and promotion from a
credential chain to the two-route capability model; and updates current instructions and tests.
Historical ADR bodies remain historical, with forward pointers rather than silent rewrites.

The Gate uses a fake account backend for every limit/reset outcome, sparse and out-of-order state,
timeout before and after backend commit, duplicate delivery, Control death, journal crash points,
manual reset and rollover. No live reset is consumed before competition. The App Server schema is
generated from the exact pinned Codex binary and re-audited on every binary upgrade. Read-only live
preflight may prove account, model, limit and reset inventory; it cannot prove the unpublished reset
eligibility threshold or that two credits cover five hours.
