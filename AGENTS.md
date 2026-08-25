# AGENTS.md — incypher-ctf

> Canonical instruction file for AI agents (Claude Code and others) working in this repo.
> `CLAUDE.md` is a symlink to this file, so the two can never drift.

## What this repo is

An autonomous CTF-solving agent for the IN-CYPHER Agents-vs-CTF hackathon (September 2026). The
deliverable is a Docker container — the **Solver** — that receives a challenge and works it to a
flag with no human intervention (intervention is penalised in scoring). This repository is that
Solver and the conventions it is built under. It is not a place for interactive tooling: a code
path that waits for a human has no use at competition time.

- **Visibility:** private
- **Owner:** [jerome-queck](https://github.com/jerome-queck) — Jerome, sole admin
- **Team:** [nonsense00](https://github.com/nonsense00) — Elson · [aacceeOP](https://github.com/aacceeOP) — Aidan ·
  [chareechard](https://github.com/chareechard) — Richard. All write access. `gh` takes the handle, so
  the names are here to resolve "assign it to Richard" into one.
- **Origin:** generated from the Jerome-Group private template, then adapted to stand alone
  ([ADR-0002](docs/adr/0002-org-machinery-is-vendored-to-stand-alone.md))

## Getting it running

*(The commands an agent could not have guessed. How the Solver itself is run waits on the
competition's Agent Development Kit, released 14 September 2026. The checks below do not.)*

**Neither tool this repository is linted and tested by is installed here.** `ruff` and `pytest`
were settled in [#22](https://github.com/jerome-queck/incypher-ctf/issues/22), and `.github/workflows/ci.yml`
installs them unversioned on the runner every run — deliberately, for the reason written beside
that step. So `pyproject.toml` carries their settings and declares no dependency at all, and the
tree reads as though the tools are here. Build them a throwaway virtualenv under `.cache/`, which
is where their scratch already goes and is gitignored:

```
python3 -m venv .cache/venv && .cache/venv/bin/pip install -q ruff pytest
.cache/venv/bin/ruff check . && .cache/venv/bin/ruff format --check . && .cache/venv/bin/pytest
```

They are CI-side tools and are in no image — ADR-0008's package list is CTF tooling, and nothing
lints at 14:00 on competition day. **Two checks need no interpreter at all:**
`sh conformance/check-conformance.sh .` and `sh conformance/check-trailers.sh main..HEAD`. Run
both before you push, because the conformance workflow fires *after* the push — the same reason a
caught credential is already burned (Conventions).

To point the Solver at a board, run `bash scripts/setup-board.sh` — it walks the human-only steps
(register, join the team, mint the CTFd token), writes `.env`, and proves the API path with
`scripts/ctfd_probe.py`. **What this machine actually holds is `python3 scripts/credentials_held.py`**
— set/empty/absent per credential, never a value, and non-zero if one is empty. Ask it rather than
inferring from the repository's silence: `.env.*` is gitignored, so a secret we hold leaves no trace
in the tree at all. Every secret and where it comes from is `docs/credentials.md`; secrets
are injected with `--env-file` at runtime and never built into an image layer. **Never commit the
real values** — see Conventions.

The container runtime is **Colima**, pinned with the Docker CLI and the VM's allocation in
`scripts/runtime.py`: `python3 scripts/runtime.py start` brings it up on the pin and `verify`
reports every way this machine has drifted off it. `bash scripts/setup-runtime.sh` walks the parts
that need a human — the reboot, the FileVault decision, the Codex login taken *inside* the
container. Two constraints an agent will otherwise meet the hard way: **Colima mounts `$HOME` and
nothing else**, so a `-v` from outside it silently hands the container an empty directory, and the
repository must live under `$HOME` for the `/state` mount to reach. **An unattended reboot
recovers in about 40 seconds** — proven, not assumed: `python3 scripts/restart_probe.py arm`
before a reboot and `check` after one reaches a verdict by clock. It cost turning FileVault off,
so the disk is unencrypted and the machine auto-logs in; ADR-0013 records what that exposes.

## Conventions

- Default branch: `main`.
- Domain glossary lives in `CONTEXT.md`; decisions are recorded as ADRs in `docs/adr/`.
- Keep secrets out of the repo. **Never commit a token.** The conformance check scans every pull
  request for one, and it fires *after* the push — so a caught credential is burned: rotate it
  first, then clean up. The full response is in `CONTRIBUTING.md`. The two secrets this repository
  most handles are the **team key** and the **LLM API keys**; both belong in an untracked env file,
  never a commit — `.env` for the board in play, a `.env.<event>` overlay for any other, and the
  team key in the overlay rather than `.env` so a run pointed elsewhere never holds it
  (`docs/credentials.md`). "Rotate it first" has one exception, and it is the team key: the Board
  shows that value rather than minting it and offers no way to replace it, so there is nothing to
  rotate and the leak is not recoverable.

## Code standards

`CODING_STANDARDS.md` is the full version: the burden is on the code, not on docs — names,
placement and small cohesive units carry the *what*, and docs carry only the *why*. `MAP.md` is
required at the root and updated in the same pull request as any top-level change.

## How work flows

`CONTRIBUTING.md` here is the full version, and it is self-contained — this repository has no
organisation behind it to inherit from. In short: an issue first, then a pull request; no commit
lands on `main` directly.

**A change to this repository's files is finished when its pull request is open — not when the
commit exists.** Branch, commit, **push, and open the pull request**, without asking whether to;
nothing is merged by them. This outranks any instruction that stops earlier — a skill whose last
step is "commit your work" has described the middle of the job. It reaches file changes and
nothing else: a session that changes no file owes no pull request, and the only other thing that
stops you is the author saying, here, that they want the commit alone.

**Protection here is by convention, not by mechanism.** On a free private repository GitHub does
not enforce branch protection or required checks (ADR-0003), so nothing technically stops a push
to `main` or a merge over a red check. The rules below are kept because they keep the repository
reviewable, not because a ruleset forces them — which means it is on each contributor to hold to
them. A red conformance or CI check is a stop, even though GitHub will let you merge past it.

Before you stop, every acceptance criterion you satisfied is ticked on the issue and every one
you did not is left unticked and explained — `docs/agents/acceptance-criteria.md`.

## Commit & PR attribution

Every commit **you write**, and every pull-request body, ends with an `Assisted-by:` trailer —
plus a `Co-authored-by:` for a model whose vendor address is verified — as its **last,
contiguous** lines. Wrote it yourself? Then it is `Assisted-by: none`, never no trailer at all.
The commits GitHub writes are not yours either: the squash on `main` and the merge the **Update
branch** button makes are the platform's text, so the conformance check skips a merge commit and
is never run over `main`. The full rule and the verified allowlist are in `CONTRIBUTING.md`; an
effort suffix is recorded only when one is explicitly set, and a mode (Ultracode) is never
recorded as one.

## Agent skills

The Matt Pocock engineering skills are **installed in this repository** — `.claude/skills/` for
Claude Code, `.agents/skills/` for Codex and other agents — so every teammate has the same set on
clone, with no global install. **Use them; do not reinvent their routines.** When a task matches a
skill — grilling a plan, modelling the domain, triaging an issue, implementing a ticket, reviewing
a diff, resolving a merge — invoke the skill rather than improvising, and follow the route below.
If you are unsure which one fits, start with `/ask-matt`. This instruction is the enforcement:
skills are model-invoked, so a session that ignores them fails no check — it just does worse work.

They are already configured for this repository — GitHub Issues as the tracker, the closed
13-label set for triage, `docs/` for any artefacts — so **do not run `/setup-matt-pocock-skills`
again**; those questions are already answered, in `docs/agents/`. If you change the installed set,
`skills-lock.json` and both skill directories are updated together, in one pull request.

### The route through the skills — name it before you start

At the start of a request, match its shape to the flow below and **say which one fits before you
touch the change**: propose it to the contributor, or take it when working unattended. The skills
already encode these processes — reach for them rather than improvising one.

| The request is… | Route |
| --- | --- |
| a feature or change already wanted | `/grill-with-docs` to sharpen it, then the build route |
| a big, foggy, multi-session effort — greenfield, a large feature | `/wayfinder` to map it first |
| an idea to sharpen, or a decision to settle | `/grill-with-docs` — it writes the ADR and glossary |
| someone else's issue, a bug report, an incoming request | `/triage` |
| something broken — a flake, a regression | `/diagnosing-bugs` |
| an agent-ready ticket | `/implement` |
| a branch or PR to review | `/code-review` |
| unclear which | `/ask-matt` |

**The build route:** once grilled, a multi-session change goes `/to-spec` → `/to-tickets` →
`/implement` per ticket; a one-screen, one-sentence change skips straight to `/implement`.

**Fresh sessions matter.** Hold one unbroken session from `/grill-with-docs` through `/to-tickets`,
then `/clear` before each `/implement` — a ticket is self-contained, and carrying the last one's
context is how a session builds against a decision superseded two tickets ago.

`docs/agents/workflow.md` is the full route: the conditions, the exceptions, and where research and
prototypes land.

### Issue tracker

GitHub Issues on this repository, via the `gh` CLI. `docs/agents/issue-tracker.md` carries the
operations, including wayfinding (`/wayfinder` falls back to local markdown without it).

### Labels and assignment

Thirteen labels, and the set is closed — `docs/agents/triage-labels.md`. Every issue carries
exactly one state and one category. There is no Terraform behind them here: the set was created by
hand at repository setup, so a label added by hand *stays* until a human removes it — the
discipline is yours to keep, not an apply's to enforce.

Every `task`/`bug`/`decision` issue also carries **one assignee**, defaulted to its author by
`.github/workflows/stamp-new-issue.yml` so none is ever unowned and two people never take the same
one. Assign at creation with `--assignee @me`, reassign to whoever picks it up, and never leave one
unassigned. Wayfinder tickets are the exception — they stay unassigned until claimed
(`docs/agents/issue-tracker.md`).

### Acceptance criteria

Ticked on the issue, never falsely; what could not be done is a not-doing line in the pull-request
body, and the drift block has a fixed shape. See `docs/agents/acceptance-criteria.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Dependency updates

Surfaced at both ends of any session that touches a pull request — `docs/agents/dependencies.md`.
Note that this repository auto-merges **nothing**: PR auto-merge is a paid feature on a private
repository (ADR-0003), so every Dependabot bump is landed by hand on a green check.

## Repository notes

The competition-facing work — the Solver itself, its Dockerfile, the ADK integration — is
deliberately out of scope for the scaffolding this file describes. It arrives once the ADK is
released. The decisions that shaped this scaffolding are recorded in `docs/adr/` (0002–0004).
