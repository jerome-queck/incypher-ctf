# The sanctioned path is the only path, and Codex logs in inside the container

[#37](https://github.com/jerome-queck/incypher-ctf/issues/37) asked whether v1's inference should
reach a **subscription** through a local OpenAI-compatible shim — the [sub2api][sub2api] pattern
reduced to a single user — and, if so, by which mechanism per provider.

The answer is **no shim, and no proxy of any kind**. The credential chain stays exactly as
[ADR-0010](0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md) set it:
Codex subscription → any further subscription → metered. What replaces the shim is smaller and
better than what the shim was for: **the Codex CLI ships inside the image and logs in there**.

This record **supersedes ADR-0010 in part** — three of its statements about the Codex credential
were wrong on the evidence available when it was written, and the correction is what made this
decision easy.

## Both of the shim's justifications were false

#37 was chartered with two reasons to want a shim. Neither survived contact.

**It never added quota, and quota was never the objection it could answer.** `CONTEXT.md` already
says it — *a quota burned through a different door is burned the same*. The 5-hour window meters
tokens against the account, whatever protocol addresses it. This was the ticket's own suspicion and
it is now settled rather than restated: subscription-as-API is **plumbing, never strategy**.

**Auth durability — the job ADR-0010 handed it — was a worry about a mechanism we cannot use,
guarding a failure that does not occur.** ADR-0010's consequence line asks *"whether a piped
`CODEX_ACCESS_TOKEN` survives 5.5 hours"*. Two corrections:

- **`codex login --with-access-token` is Enterprise-only.** It is not available on the Pro plan the
  scored run goes on, so `CODEX_ACCESS_TOKEN` was never a path for us. `docs/credentials.md` named
  it too, and is rewritten by this record.
- **A Codex session does not expire inside a run.** The CLI refreshes tokens proactively during use
  and reactively on a 401; a session goes stale only after roughly **eight days** without a refresh.
  The 5.5-hour question has no failure in it to answer.

So the shim had nothing left to buy. What remained was the reason to refuse it.

## The ToS posture, recorded as a decision

The posture is **vendor-sanctioned programmatic paths only**. Both vendors ship a non-interactive
mode built for this, and OpenAI documents running Codex under a personal account in automation. A
session-wrapping reverse proxy is out — not because we would be selling access, which was never the
operative test, but because of an asymmetry ADR-0010 already named and this record acts on:

**an account action mid-run is a harder stop than quota.** It hits the whole team's credential, it
has no appeal on a Saturday afternoon, and it arrives unattended. The circularity is the point: the
contingency for *"our account was blocked for automated use"* would be a mechanism whose principal
risk is being blocked for automated use.

Nothing we operate goes in the critical path either. ADR-0008 makes the Solver PID 1 with no
supervisor until v2, so a proxy is a second long-lived process that can die at 13:00 with PID 1
unaware. Everything the credential path needs is **in-process, or a short-lived subprocess the
orchestrator spawns and reaps** — a failure then surfaces as a failed Step the existing machinery
already handles, rather than as a silently dead daemon.

## The container logs itself in, and a human before the run is not intervention

This is the mechanism that made the shim unnecessary, and it turns on a distinction the repository
had been using without ever making: **a human at 10:15 is setup; a human at 13:00 is intervention.**
ADR-0010 conflates them — *"`--device-auth` needs a human, which the Solver will not have"* is true
of a mid-run restart and false of the initial boot, where one is standing right there. `CONTEXT.md`
now carries **Intervention** so this is not re-argued.

The mechanism:

- The **Codex CLI is baked into the image**. No credential is, and none ever will be.
- **`CODEX_HOME` points at `/state/codex`** — inside the host mount ADR-0008 already establishes.
- Before the run starts, a human runs **`codex login --device-auth`** in the container: the CLI
  prints a code, it is approved on a phone, and no browser or localhost callback is needed inside
  the container.
- Nothing is copied from the operator's machine. The credential is minted in the container that
  spends it.

Three properties follow, and together they are strictly better than the `auth.json`-copy pattern
OpenAI documents for CI:

- **A restart re-authenticates with no human.** This is ADR-0010's actual requirement, which that
  record tried to get from `docker run --env-file` keeping resolved values in the container config
  and marked **unverified**. Here it comes from a file on a mount we already have — and it is
  verifiable now rather than on the day.
- **Staleness cannot bite.** The eight-day clock starts at zero when the login happens, minutes
  before a 5.5-hour run. Refreshes land on the host mount and persist across restarts.
- **No credential travels.** Nothing leaves the operator's machine, so there is no host file to
  forget to clean up.

**One prerequisite gates all of it**: device-code login must be **enabled in the ChatGPT account's
security settings**. If it is not, `codex login --device-auth` fails and there is no browser in the
container to fall back to. It is enabled on our account as of 23 August 2026, and it is a pre-flight
check rather than a competition-day discovery.

The rejected alternative is the one OpenAI's own CI/CD guidance describes — `codex login` on a
trusted machine, `auth.json` placed in the container, the refreshed file kept for next time. It
works, and it is worse here for one reason: it puts a live personal credential on the operator's
disk and then moves it, to buy a property (surviving restart) that the host mount gives us for free.

## The seam is one build, and one adapter per credential

#37 blocks [#17](https://github.com/jerome-queck/incypher-ctf/issues/17), which owns the
provider-abstraction seam, and the question underneath was whether the subscription route and the
metered route are different **builds**. They are not. There is **one image, one ReAct loop, one
internal interface** — roughly `complete(prompt) → text` — and a small concrete adapter behind it
per credential.

The fear was that a CLI-shaped credential would drag its own agent loop into ours, colliding with
[ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md), which puts the loop, the Steps
and the stall counters in **our** orchestrator. It does not: `codex exec` takes
`--output-last-message <file>`, `--output-schema <file>` and `-s read-only`, which reduce it to
prompt in, structured text out. That **is** the interface. The loop stays ours.

What this hands #17 is the asymmetry rather than a problem: **Codex authenticates by file and by
login; Claude authenticates by environment variable** (`CLAUDE_CODE_OAUTH_TOKEN`, which rides
`--env-file` like everything else). Each lives inside its adapter and neither reaches the seam. No
abstract base — the same rule
[ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md) applies to `Board` and
`Target`.

## Consequences

- **`auth.json` is a live credential on disk that ADR-0010's control does not cover, and this is
  accepted.** That record's invariant is *no secret in the **environment** of a process that runs
  challenge-supplied code*, enforced by an allowlist `env=` on every Step spawn. A file at a fixed
  path defeats it: challenge code runs as **root** in the same container (ADR-0008), so reading
  `$CODEX_HOME/auth.json` walks straight past the allowlist. It is named here because ADR-0010
  otherwise reads as though the allowlist closed this class of problem. `CODEX_HOME` under a
  non-root user is not a fix — a control root walks through is a comment, not a control. The real
  boundary is v2's uid separation, alongside the supervisor, exactly where ADR-0008 put it.
- **`docs/credentials.md` loses `CODEX_ACCESS_TOKEN`.** The variable named a flag we cannot use.
  Codex is now the one credential that does **not** arrive by `--env-file`, and the page says so.
- **The container needs a writable `CODEX_HOME`**, because Codex writes refreshed tokens back. That
  is the mount, not a layer.
- **Verification belongs to [#49](https://github.com/jerome-queck/incypher-ctf/issues/49).** That
  in-container device-auth works, that it survives a restart through `/state`, and that
  `codex exec --output-last-message` returns usable text are mechanical proofs, all blocked on the
  same missing thing #49 already owns — there is no container runtime on the build machine. Splitting
  them would make two tickets waiting on one prerequisite.
- **ADR-0010 stands as written.** Its reasoning was sound on the evidence it had; three of its facts
  about the Codex credential were not, and are corrected above rather than edited away, so the record
  shows the decision moved.

[sub2api]: https://github.com/Wei-Shaw/sub2api
