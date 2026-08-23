# Credentials

Every secret this repository touches, where it comes from, and how it reaches the Solver. The
template is [`.env.example`](../.env.example); the filled copy is `.env`, which is gitignored and
never leaves your machine.

## Setting up

```bash
bash scripts/setup-board.sh
```

It opens each page, tells you exactly what to click, takes the values with hidden input, writes
`.env`, and finishes by running the probe against the board. Re-running it is safe — pressing
Enter at any prompt keeps the value already saved.

By hand instead: `cp .env.example .env`, then fill it in with an editor. Never `echo` a real
token into a shell — it lands in your history.

## What each secret is

| Variable | Opens | Where you get it | If it leaks |
|---|---|---|---|
| `CTFD_API_TOKEN` | Full control of our account on one CTFd board — reading challenges, submitting flags, and anything else we could do in the browser | On the board: **Settings → Access Tokens → Generate**. Shown once; copy it then. | Delete it on that same page and generate another. Scoped to one board, so nothing else is affected. |
| `TEAM_KEY` | Our identity on the IN-CYPHER platform, including the proof-of-work gate on raw-TCP challenges | On the IN-CYPHER board: **Settings → Access Tokens**, where it is *displayed*. Read it; there is nothing to generate. Not needed for practice boards. | **Nothing we can do, and nobody we can currently tell.** The page offers no rotate control and no regenerate — the value is shown, not minted — so unlike a CTFd token this one cannot be replaced from our side, and the organisers publish no working channel to ask ([#36](https://github.com/jerome-queck/incypher-ctf/issues/36)). That is what makes it the most valuable secret here: it is the only one whose leak we could not undo. |
| `CLAUDE_CODE_OAUTH_TOKEN` | Inference against our Claude **subscription** — no per-token cost, a quota that hard-stops until it resets | Run `claude setup-token` and complete the browser login; it prints the token | Run `claude setup-token` again to mint a replacement, and sign out of the leaked session. |
| `ANTHROPIC_API_KEY` | **Metered** inference billed per token, with no quota cliff | `console.anthropic.com` → **API keys** → *Create key* | Revoke it in the console. Assume the spend between leak and revocation is ours. |

## Which inference credential, and when

**We compete on a subscription. Metered credit is break-glass, and it breaks its own glass.** An
earlier revision of this page said "practice on a subscription; compete on metered billing" — that
described credit we do not hold, and [ADR-0010](adr/0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)
replaces it. The scored run goes on a **Codex Pro 20×** subscription. A metered key exists, and the
aim is that it never fires.

The aim is not a plan, though, because **a fallback that waits for a human is human intervention**
and that is the penalised act. So the credentials form a **chain**, pre-armed and switched
automatically by the Solver: subscription first, metered last, nobody present. "Never used" is then
something we check *after* a run rather than something we hope for during one.

| Order | Provider | Credential | Present when |
|---|---|---|---|
| 1 | OpenAI / Codex — subscription | `codex login --device-auth`, **run inside the container**; see the caveat below | always |
| 2 | *any further subscription* | e.g. Anthropic's `CLAUDE_CODE_OAUTH_TOKEN`, minted by `claude setup-token` | if it is in the environment at boot |
| 3 | metered — break-glass | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | **scored board only** — see below |

Slot 2 is a socket rather than a promise. We hold Claude 20× today but do not assume it on 22
September; a teammate arriving with one drops straight into the chain, and its absence is not a
hole. The Claude path is built either way, because training runs use it.

**The quota is the cap, and it hard-blocks inside our own run.** Codex meters on a **5-hour rolling
window** *and* a separate **weekly cap**, by tokens rather than messages, across one pool shared by
the CLI, the IDE extension and cloud tasks. The scored run is **5.5 hours**. Two things follow that
are easy to get wrong:

- **Subscription usage is what spends the allowance.** There is no separate pot that only metered
  calls touch — practice draws from the budget the scored run needs.
- **You share the pool with the Solver.** Anything you run personally on Codex on 21–22 September
  comes out of the run's headroom, and the day-1 rehearsal spends from the run's allowance rather
  than from nothing. Keep the primary account quiet before the run.

**The metered key lives in the scored board's overlay, never in `.env`.** The chain is automatic, so
a practice run left going overnight would switch to paid the moment the subscription capped and
spend real money unwatched. Putting the key in the overlay means a Brunner run *structurally cannot*
— the credential is not in its environment at all. Same mechanism as `TEAM_KEY`, same reason.

That is also why the scored key carries a **budget alert and not an enforcing spend limit**: a
console limit that blocks requests is the same unwatched hard stop we are designing around. Practice
keys are the opposite — cap those hard, because a stop there costs a rerun and nothing else.

**A lent credential is rotated by its owner.** A teammate's subscription token is their personal
credential: it rides in the event overlay like everything else, and if a run misbehaves the rotation
is their call, on request, not ours to do for them.

**Codex authenticates by file, not by environment — so it is the one credential that does not
arrive by `--env-file`.** An `OPENAI_API_KEY` in `.env` is enough for the OpenAI SDK, but the Codex
CLI keeps its subscription credentials in `auth.json` under `CODEX_HOME` (default `~/.codex`).
Exporting a variable does not log it in. `codex login --with-access-token` is **Enterprise-only**
and not available on our Pro plan, so there is no `CODEX_ACCESS_TOKEN` to keep; an earlier revision
of this page said otherwise, and [ADR-0011](adr/0011-the-sanctioned-path-is-the-only-path.md)
replaces it.

**The container logs itself in, before the run starts.** The Codex CLI is baked into the image and
`CODEX_HOME` points at `/state/codex`, inside the host mount:

```bash
docker run --env-file .env -v "$PWD/state:/state" -e CODEX_HOME=/state/codex solver:latest
# then, once, in the container, before the scored window opens:
codex login --device-auth
```

It prints a code you approve on your phone — no browser in the container, no localhost callback.
Doing this **before** the Run is setup, not **Intervention** (`CONTEXT.md`); doing it mid-Run would
be the penalised act, which is precisely why the file lives on the mount: a restarted container
inherits the login instead of asking for one at 13:00.

Two things follow. Codex refreshes tokens itself during use and a session goes stale only after
about **eight days**, so a login taken minutes before a 5.5-hour run cannot expire inside it. And
`CODEX_HOME` must be **writable**, because the refreshed token is written back there.

Nothing is copied from your machine, and the `Dockerfile` must not bake `auth.json` into a layer any
more than it may bake a key. **Prerequisite:** device-code login has to be enabled in the ChatGPT
account's security settings — it is enabled on ours, and without it `--device-auth` fails with no
browser to fall back to.

**A credential on disk is not covered by the environment allowlist.** The orchestrator's allowlist
spawn (below) stops a challenge reading a secret out of its own environment; it does nothing about a
file. Challenge code runs as root in this container, so `$CODEX_HOME/auth.json` is readable by it.
Accepted for v1 and recorded in ADR-0011 — the boundary that actually fixes it is v2's uid
separation.

**One credential per provider.** Within a provider these are not a fallback chain: the client
takes the first credential it finds, and given both an API key and a token the Anthropic SDK sends
two auth headers, which the API rejects outright. Two things that are easy to get wrong:

- **An empty value is not an unset one.** `ANTHROPIC_API_KEY=` still occupies its slot in the
  search order and authenticates with an empty key rather than falling through. `docker run
  --env-file` exports empty values too, so a blank line in `.env` breaks auth inside the container
  while the file still looks right. Delete the line; don't blank it.
- **A token is not an API key.** `ANTHROPIC_API_KEY` is sent as `x-api-key`; an OAuth token
  (`sk-ant-oat01-…`) must ride `Authorization: Bearer`. Put a token in the key's variable and
  every request fails auth. On raw HTTP a Bearer token also needs the header
  `anthropic-beta: oauth-2025-04-20`.

`bash scripts/setup-board.sh` asks which mode you want and removes the other variable for you.

`CTFD_URL` is not a secret, but it lives beside them because it is the value that decides which
competition the Solver enters. BrunnerCTF runs a Danish platform under a strict no-AI policy
alongside the Global one that permits AI; pointing the Solver at the wrong host is a
disqualification, not a misconfiguration. See
[`competitions/brunnerctf-2026-global.md`](competitions/brunnerctf-2026-global.md).

## One board at a time, and the overlay for the others

`.env` holds exactly one board, because `CTFD_URL` is the guard described above and a file that
held two would need something else to choose between them. A second board gets an **overlay file**
named for it — `.env.incypher`, `.env.<event>` — carrying only that board's values:

```
.env             CTFD_URL, CTFD_API_TOKEN  → the default board   + the subscription credential
.env.incypher    CTFD_URL, CTFD_API_TOKEN, TEAM_KEY, <metered key> → IN-CYPHER
```

Source it in a subshell, so the default board is never silently switched:

```bash
( set -a; . ./.env.incypher; set +a; python3 scripts/ctfd_probe.py --no-attempt )
```

This works because the loader is `os.environ.setdefault` — **the environment wins and `.env` only
fills the gaps** — so the overlay's two values shadow `.env`'s while everything it does not mention
still comes from `.env`. `.gitignore` already covers the pattern: `.env.*` is ignored, with
`.env.example` the single exception.

**`TEAM_KEY` belongs in the overlay, not in `.env`.** It is specific to the IN-CYPHER platform and
a practice board has no equivalent, so its *absence* on a run pointed elsewhere is the point: a
container working a Brunner challenge never holds an IN-CYPHER credential it has no use for, and
cannot leak one if a challenge gets code execution inside it. That is the same rule
[`ctfd_probe.py`](../scripts/ctfd_probe.py) already applies one level down, withholding the CTFd
token when a challenge file redirects to object storage that never asked for it. It matters more
here than there, because the team key is the one secret in the table that cannot be rotated.

**The metered key belongs there for a different reason, and it is worth keeping the two apart.** The
team key is in the overlay because a Brunner run has no *use* for it. The metered key is there
because a Brunner run would *use* it — the chain is automatic, so an overnight practice run would
switch to paid the moment the subscription capped. Absence is the control in both cases; what it is
protecting against is a leak in one and a bill in the other.

## How they reach the container

**Injected at runtime, never built into the image.**

```bash
docker run --env-file .env solver:latest
```

A secret placed in a `Dockerfile` — `ENV`, `ARG`, or a `COPY` of `.env` — becomes a layer. Layers
are readable by anyone holding the image, survive a later `RUN rm`, and travel with every push and
every `docker save`. There is no way to take it back out short of rebuilding from before the layer
that added it, so the rule is absolute rather than a preference: nothing in the image, everything
on the command line.

The competition deliverable is an image the organisers may run themselves, which makes this sharper
than good practice. [Issue #13](https://github.com/jerome-queck/incypher-ctf/issues/13) settled that
**we** almost certainly run it, so `--env-file` survives — but only as a high-confidence inference,
which is why the image stays *handover-shaped* anyway. If we are wrong on the day we ask for `-e`,
and if that is refused **we do not compete rather than bake a key into a layer** (ADR-0010).

**The environment stops at the orchestrator.** The Solver runs challenge-supplied code — archives,
binaries, whatever a pwn challenge hands it — as root, in this same container. So the orchestrator
reads every credential once at boot and spawns each Step with an explicit **allowlist** environment
(`PATH`, `HOME`, `TERM`, `LANG`), never an inherited one. Nothing that executes a challenge's code
can read a secret out of its own environment, and the team key in particular is passed as an
argument inside the `Target` seam rather than exported at all — it is the one value here we could
never replace.

**Two more places the values exist.** `docker run --env-file` is expected to keep the *resolved*
values in the container's config, which is what lets an unattended `docker start` re-authenticate
with no file present — and also means `docker inspect` will print them. Unverified so far: there is
no container runtime on the build machine yet. And the shell you type in is a third: never `echo` a
real token, as above.

## If one is committed

The conformance check scans every pull request, and it runs **after** the push — so a hit means
the credential has already reached GitHub's servers, the Actions log, and any clone taken since.

1. **Rotate or revoke it**, using the table above. This is the fix.
2. Take the value out of the code.
3. Rewrite the branch.

Steps 2 and 3 are tidying up; a branch rewritten without step 1 leaves a live credential in
somebody else's hands. If the flagged value is not a credential — a fixture, a documented example
— mark the line `# gitleaks:allow`, which asserts that the value opens nothing.
