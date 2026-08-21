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
| `TEAM_KEY` | Our identity on the IN-CYPHER platform, including the proof-of-work gate on raw-TCP challenges | Issued by the IN-CYPHER organisers. Not needed for practice boards. | Tell the organisers — we cannot rotate this one ourselves, which is what makes it the most valuable secret here. |
| `CLAUDE_CODE_OAUTH_TOKEN` | Inference against our Claude **subscription** — no per-token cost, a quota that hard-stops until it resets | Run `claude setup-token` and complete the browser login; it prints the token | Run `claude setup-token` again to mint a replacement, and sign out of the leaked session. |
| `ANTHROPIC_API_KEY` | **Metered** inference billed per token, with no quota cliff | `console.anthropic.com` → **API keys** → *Create key* | Revoke it in the console. Assume the spend between leak and revocation is ours. |

## Which inference credential, and when

**Practice on the subscription; compete on the metered key.** Training runs are frequent and
throwaway, so paying per token for them is money spent proving something we already believe. The
5.5-hour scored run is the opposite: a quota that hard-stops mid-run cannot be topped up, and the
Solver has no human to notice. That split is what [issue #20](https://github.com/jerome-queck/incypher-ctf/issues/20)
means by a metered key held as the *armed* fallback rather than the daily driver — the danger it
names is a subscription being the **sole** credential path, not a subscription being used.

**Exactly one may be set.** These are not a fallback chain: the SDK picks the first credential it
finds and, given both an API key and a token, sends two auth headers, which the API rejects. Two
consequences that are easy to get wrong:

- **An empty value is not an unset one.** `ANTHROPIC_API_KEY=` still occupies its slot in the
  search order and authenticates with an empty key rather than falling through. `docker run
  --env-file` exports empty values too, so a blank line in `.env` breaks auth inside the container
  while the file still looks right. Delete the line; don't blank it.
- **The token is not an API key.** `ANTHROPIC_API_KEY` is sent as `x-api-key`; an OAuth token
  (`sk-ant-oat01-…`) must go on `Authorization: Bearer`. Put a token in the key's variable and
  every request fails auth. For a Solver calling the Messages API directly the variable is
  `ANTHROPIC_AUTH_TOKEN`; on raw HTTP that also needs `anthropic-beta: oauth-2025-04-20`.

`bash scripts/setup-board.sh` asks which mode you want and removes the other variable for you.
Which of the two the Solver actually reads is still open —
[issue #17](https://github.com/jerome-queck/incypher-ctf/issues/17) picks the brain and the
provider seam.

`CTFD_URL` is not a secret, but it lives beside them because it is the value that decides which
competition the Solver enters. BrunnerCTF runs a Danish platform under a strict no-AI policy
alongside the Global one that permits AI; pointing the Solver at the wrong host is a
disqualification, not a misconfiguration. See
[`competitions/brunnerctf-2026-global.md`](competitions/brunnerctf-2026-global.md).

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
than good practice. Where that image runs and whether we get to pass `--env-file` at all is still
open — [issue #13](https://github.com/jerome-queck/incypher-ctf/issues/13).

## If one is committed

The conformance check scans every pull request, and it runs **after** the push — so a hit means
the credential has already reached GitHub's servers, the Actions log, and any clone taken since.

1. **Rotate or revoke it**, using the table above. This is the fix.
2. Take the value out of the code.
3. Rewrite the branch.

Steps 2 and 3 are tidying up; a branch rewritten without step 1 leaves a live credential in
somebody else's hands. If the flagged value is not a credential — a fixture, a documented example
— mark the line `# gitleaks:allow`, which asserts that the value opens nothing.
