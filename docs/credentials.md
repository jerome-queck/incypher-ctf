# Credentials

Every secret this repository touches, where it comes from, and how it reaches the Solver. The
template is [`.env.example`](../.env.example); the filled copy is `.env`, which is gitignored and
never leaves your machine.

## Setting up

```bash
bash scripts/setup-board.sh
```

It opens each page, tells you exactly what to click, takes the values with hidden input, stages the
complete result, atomically replaces `.env`, and finishes by running the probe against the board.
Re-running it is safe — pressing Enter at any prompt keeps the value already saved. A legacy
`.env.<event>` file makes setup refuse before the first prompt: merge the intended values into
`.env`, remove every legacy overlay, then rerun setup.

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

**The order above is a config value, not code.** Codex leads because it is the credential we are
certain to hold, which is a claim about procurement rather than about capability — so a practice run
can lead with Claude without a rebuild, and if Claude 20× ever becomes certain for a scored run the
change is one field ([ADR-0014](adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)).
The chain switches on **exhaustion only**: v1 runs one brain per Run and never picks a model by what
the Challenge looks like.

**The quota is the cap, and it hard-blocks inside our own run.** Codex meters on a **5-hour rolling
window** *and* a separate **weekly cap**, by tokens rather than messages, across one pool shared by
the CLI, the IDE extension and cloud tasks. The scored run is **5.5 hours**. Two things follow that
are easy to get wrong:

- **Subscription usage is what spends the allowance.** There is no separate pot that only metered
  calls touch — practice draws from the budget the scored run needs.
- **You share the pool with the Solver.** Anything you run personally on Codex on 21–22 September
  comes out of the run's headroom, and the day-1 rehearsal spends from the run's allowance rather
  than from nothing. Keep the primary account quiet before the run.

**The metered key lives only in the scored board's active `.env`.** The chain is automatic, so a
practice run left going overnight would switch to paid the moment the subscription capped and
spend real money unwatched. A practice `.env` omits the key entirely, so that Run structurally
cannot spend it. Switching boards means one atomic `.env` replacement, never composing files.

That is also why the scored key carries a **budget alert and not an enforcing spend limit**: a
console limit that blocks requests is the same unwatched hard stop we are designing around. Practice
keys are the opposite — cap those hard, because a stop there costs a rerun and nothing else.

**A lent credential is rotated by its owner.** A teammate's subscription token is their personal
credential: it rides in the active `.env` like everything else, and if a run misbehaves the rotation
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

**Three things around that command fail silently, and all three were found by running it.** Each
one produces a container that looks fine and loses the credential or the login:

- **The mount has to be under your home directory.** Colima mounts `$HOME` and nothing else, and a
  `-v` from outside it does not error — the container gets an empty directory. The login is taken,
  written nowhere that survives, and gone at the next `docker rm`.
- **`CODEX_HOME` must already exist.** Writable is not enough: against a missing path the CLI
  refuses to load configuration and never reaches the login prompt.
- **The image must ship `ca-certificates`.** The Codex CLI is a native binary that reads the
  *system* trust store, which `node:*-slim` does not have. Node carries its own roots, so
  `npm install` succeeds and hides the gap, and the login then dies on `error sending request for
  url` with egress working perfectly.
- **The image should ship `bubblewrap`.** Without it `codex exec` warns and falls back to a bundled
  copy. It runs either way — but the sandbox around challenge-supplied code is not the thing to
  leave to a vendored fallback.

`bash scripts/setup-runtime.sh` walks this login and checks all three first.

Nothing is copied from your machine, and the `Dockerfile` must not bake `auth.json` into a layer any
more than it may bake a key. **Prerequisite:** device-code login has to be enabled in the ChatGPT
account's security settings — it is enabled on ours, and without it `--device-auth` fails with no
browser to fall back to.

**A credential on disk is not covered by the environment allowlist.** The orchestrator's allowlist
spawn (below) stops a challenge reading a secret out of its own environment; it does nothing about a
file. Everything in this container runs as root, so `$CODEX_HOME/auth.json` is readable by it.
Accepted for v1 and recorded in ADR-0011.

**Two things that page and this one both got wrong, and a live Run found them
([#144](https://github.com/jerome-queck/incypher-ctf/issues/144)).**

*The actor is not only challenge code.* Both records described this as a challenge binary reading a
file. On `compfest-2026-seg1` the read was made by **the model itself** — it ran `rg` over
`/state/runs/*/stream.jsonl` hunting for session material, and found the Run's own record. Any fix
scoped to "code a Challenge supplied" would not have covered the one read that happened.

*And uid separation does not close it on `/state`.* Both records name that as the boundary. Measured
in this image, on this mount:

| path | `chmod 700`, root-owned, read as another uid |
|---|---|
| container filesystem (`/var/lib/...`) | **blocked** — modes are enforced |
| the `/state` bind mount | **read succeeds**, while `stat` reports `mode=700 uid=0` |

Colima's mount does not enforce modes, so a non-root uid buys nothing for anything under `/state` —
which is where the record, the working directory and `auth.json` all live. Uid separation remains
right for the container filesystem and is **not** on its own the fix here; what would close it is
keeping the live record off the shared mount, or running the agent somewhere it cannot reach.
Neither is a v1 change, and ADR-0027's crowding rule is what currently stops the consequence.

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
[`competitions/brunnerctf-2026-global.md`](competitions/brunnerctf-2026-global.md). It is also
what selects the Board profile: every event's `docs/competitions/<event>.board.json` is baked into
the image, and the Solver refuses to start where none of them claims this URL — so an image
pointed at the Danish board plays nothing rather than playing it under Global's rules.

Three more non-secrets share the file, and one of them is load-bearing. **`RUN_ID` names the Run
and the Solver refuses to mint it**: the Run's absolute deadline is stamped under
`/state/runs/<RUN_ID>` and is consulted only when there is no window there already, so an id
generated at startup would hand every restart a fresh window with nobody there to notice. Reuse it
to rejoin a Run and change it to start a new one. `RUN_SECONDS` shortens the window and can never
lengthen one, which is what makes a half-hour practice Run possible without any variable being able
to buy a Run past the event it is playing. `CODEX_MODEL` is the model every rung of the chain runs
at, config rather than a constant because which model a subscription serves is account state.

## One board, one environment authority

`.env` holds exactly one board and every credential sanctioned for that board. There is no merge
order and no overlay: `.env.incypher`, `.env.<event>`, and any other `.env.*` file except the
tracked `.env.example` are legacy ambiguous authority. Setup refuses while one exists.

To switch boards, run `bash scripts/setup-board.sh`. It edits a private same-directory staging file,
flushes it, and replaces `.env` atomically only after the wizard succeeds. A failed or interrupted
setup leaves the previous `.env` authoritative. To perform the one-time migration from an overlay,
manually combine the intended board values into `.env`, delete the legacy file, and rerun setup.

The values are state — gitignored and necessarily absent from the repository. Ask the machine what
the active `.env` holds instead:

```bash
python3 scripts/credentials_held.py
```

It prints **set / empty / absent** for every declared name and **never prints a value**. Absent is
ordinary for a credential the active board does not require. **Empty is a defect and exits
non-zero** — the variable occupies its slot and authenticates with nothing. During migration it
also exposes a legacy filename as configuration to remove, never as a second authority to compose.

This exists because its absence cost a wrong answer. Resolving
[#59](https://github.com/jerome-queck/incypher-ctf/issues/59), a session read this repository, found
nothing stating that an account for that board existed, recorded that we held none, and left an
acceptance criterion unticked on it — when running the command above would have answered the
question in one line. The repository was not wrong to be silent; it had no way to be asked.
**Silence about a secret is not evidence there is no secret**, and one command is cheaper than
remembering that.

**`TEAM_KEY` belongs only in an IN-CYPHER `.env`.** It is specific to the IN-CYPHER platform and a
practice board has no equivalent, so its *absence* from that board's active `.env` is the point: a
container working a Brunner challenge never holds an IN-CYPHER credential it has no use for, and
cannot leak one if a challenge gets code execution inside it. That is the same rule
[`ctfd_probe.py`](../scripts/ctfd_probe.py) already applies one level down, withholding the CTFd
token when a challenge file redirects to object storage that never asked for it. It matters more
here than there, because the team key is the one secret in the table that cannot be rotated.

**The metered key belongs there for a different reason, and it is worth keeping the two apart.** The
team key is in the scored `.env` because a Brunner run has no *use* for it. The metered key is there
because a Brunner run would *use* it — the chain is automatic, so an overnight practice run would
switch to paid the moment the subscription capped. Absence is the control in both cases; what it is
protecting against is a leak in one and a bill in the other.

## Scanner vault

Evidence publication reads retained secret history from a distinct generic-password item in the
macOS login Keychain. `.env` is only the live Board authority: it may confirm that each active value
is in the vault's `current` set, but it is never a history source. Any legacy `.env.*` overlay makes
publication refuse.

The Keychain item has service `incypher-ctf.evidence-scanner-vault` and account `solver`. Its
password is one JSON object:

```json
{
  "schema_version": 1,
  "kind": "evidence-scanner-vault",
  "version": 1,
  "completeness_through": {"run_id": "RUN_ID", "chain_head": "64-lowercase-hex-digest"},
  "values": {
    "ANTHROPIC_API_KEY": {"current": [], "historical": []},
    "ANTHROPIC_AUTH_TOKEN": {"current": [], "historical": []},
    "CLAUDE_CODE_OAUTH_TOKEN": {"current": [], "historical": []},
    "CTFD_API_TOKEN": {"current": ["replace-with-exact-live-value"], "historical": []},
    "OPENAI_API_KEY": {"current": [], "historical": []},
    "TEAM_KEY": {"current": [], "historical": []}
  }
}
```

Every declared name must be present. Lists contain exact values, not hashes: move a rotated or
deleted value from `current` to `historical`. Multiple simultaneously valid Board tokens may remain
in `current`. Increment `version` on every vault edit, and bind
`completeness_through` to the closed Run being promoted. Its chain head is the final
`event_digest` in `state/runs/<RUN_ID>/canonical/events.jsonl`.

Retire a historical value only after auditing `state/runs/`: every remaining Run that could predate
the rotation must have a verified capsule whose `content_basis.source.run_id` names that Run, or the
Run must already have completed the storage-governor retirement transaction. If any unpromoted Run
remains, the value remains. This is ADR-0045's reachability boundary; it avoids both premature
deletion and indefinite retention after no source can contain the value.

Prepare the completed object in a password manager's secure editor and compact it to one line
there. Never save a plaintext scratch file, and never paste the displayed multi-line example at a
shell prompt. Create or replace the item without putting its password in shell history:

```bash
security add-generic-password -U -s incypher-ctf.evidence-scanner-vault -a solver -w
```

Keep `-w` last; `security` prompts for the password. Paste the completed JSON minified to one line
at that prompt. Do not add `-A`, and do not pass the JSON as a command argument. The Solver reads
only the password body with `security find-generic-password ... -w`; a missing item, inaccessible
`security`, invalid schema, incomplete name set, empty vault, mismatched active value, or stale Run
attestation refuses publication without logging Keychain output.

Verify only the non-secret metadata; the password body travels through the pipe and is not printed:

```bash
security find-generic-password -s incypher-ctf.evidence-scanner-vault -a solver -w \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["kind"],d["version"],d["completeness_through"])'
```

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
which is why the image stays *handover-shaped* anyway. If we are wrong on the day, the ask is
**three** things and not one — `-e` or `--env-file`, a **writable mount** for `CODEX_HOME`, and
**one interactive login in the container** before the run — because since
[ADR-0011](adr/0011-the-sanctioned-path-is-the-only-path.md) Codex authenticates by a file rather
than by the environment. Getting only the first leaves no subscription login, and the run spends
5.5 hours on the metered key. If injection itself is refused, **we do not compete rather than bake
a key into a layer** (ADR-0010).

**Bootstrap custody stops at owner-specific brokers.** The v2 seam transfers Board, Codex and CPA
material once into separate broker processes, clears its mutable source buffers and temporary
files, and gives hostile execution only scoped opaque handles. Every handle request re-derives the
kernel peer identity and checks the canonical Work generation. A Step still receives only the
explicit `PATH`, `HOME`, `TERM`, and `LANG` allowlist. The seam exists beside v1 calls; each domain
ticket migrates its own operation semantics rather than teaching the custody layer those semantics.

**Two more places the values exist, and this is now measured rather than expected.**
`docker run --env-file` resolves the file at `run` and keeps the values in the container's config.
That is what lets an unattended `docker start` re-authenticate with no file present — verified on
the build machine by deleting the file between `stop` and `start` and reading the variable back out
of the container ([ADR-0012](adr/0012-the-runtime-is-colima-and-filevault-is-the-wall.md)). The
same fact wears a second face: **`docker inspect` prints every one of them**, so an inspect output
pasted into an issue is a leak. And the shell you type in is a third: never `echo` a real token, as
above.

## If one is committed

The conformance check scans every pull request, and it runs **after** the push — so a hit means
the credential has already reached GitHub's servers, the Actions log, and any clone taken since.

1. **Rotate or revoke it**, using the table above. This is the fix.
2. Take the value out of the code.
3. Rewrite the branch.

Steps 2 and 3 are tidying up; a branch rewritten without step 1 leaves a live credential in
somebody else's hands. If the flagged value is not a credential — a fixture, a documented example
— mark the line `# gitleaks:allow`, which asserts that the value opens nothing.
