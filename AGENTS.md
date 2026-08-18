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
- **Owner:** [jerome-queck](https://github.com/jerome-queck), sole admin; teammates have write access
- **Origin:** generated from the Jerome-Group private template, then adapted to stand alone
  ([ADR-0002](docs/adr/0002-org-machinery-is-vendored-to-stand-alone.md))

## Getting it running

*(The commands an agent could not have guessed — install, run, test, lint — and any constraint
on where they may be run. Empty until the first Solver code, which is built against the
competition's Agent Development Kit released 14 September 2026.)*

Copy `.env.example` to `.env` and fill in the team key and LLM provider keys. **Never commit the
real values** — see Conventions.

## Conventions

- Default branch: `main`.
- Domain glossary lives in `CONTEXT.md`; decisions are recorded as ADRs in `docs/adr/`.
- Keep secrets out of the repo. **Never commit a token.** The conformance check scans every pull
  request for one, and it fires *after* the push — so a caught credential is burned: rotate it
  first, then clean up. The full response is in `CONTRIBUTING.md`. The two secrets this repository
  most handles are the **team key** and the **LLM API keys**; both belong in `.env`, never a
  commit.

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

### The route through the skills

Where a piece of work starts, what hands on to what, and where research and prototypes live. See
`docs/agents/workflow.md` before inventing a route.

### Issue tracker

GitHub Issues on this repository, via the `gh` CLI. `docs/agents/issue-tracker.md` carries the
operations, including wayfinding (`/wayfinder` falls back to local markdown without it).

### Labels

Thirteen, and the set is closed — `docs/agents/triage-labels.md`. Every issue carries exactly one
state and one category. There is no Terraform behind them here: the set was created by hand at
repository setup, so a label added by hand *stays* until a human removes it — the discipline is
yours to keep, not an apply's to enforce.

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
