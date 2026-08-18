# AGENTS.md — <repository>

> Canonical instruction file for AI agents (Claude Code and others) working in this repo.
> `CLAUDE.md` is a symlink to this file, so the two can never drift.

## What this repo is

*(One paragraph: what this repository is for, and what it is not for. Replace this and the
heading above before the first pull request.)*

- **Visibility:** *(private | public)*
- **Organisation:** [Jerome-Group](https://github.com/Jerome-Group)

## Getting it running

*(The commands an agent could not have guessed — install, run, test, lint — and any constraint
on where they may be run. Fill this in with the first real code; until then it is honestly
empty.)*

## Conventions

- Default branch: `main`.
- Domain glossary lives in `CONTEXT.md`; decisions are recorded as ADRs in `docs/adr/`.
- Keep secrets out of the repo. **Never commit a token.** The conformance check scans every pull
  request for one, and it fires after the push — so a caught credential is burned: rotate it
  first, then clean up. The full response is in `CONTRIBUTING.md`.

## Code standards

`CODING_STANDARDS.md` is the full version: the burden is on the code, not on docs — names,
placement and small cohesive units carry the *what*, and docs carry only the *why*. `MAP.md` is
required at the root and updated in the same pull request as any top-level change.

## How work flows

`CONTRIBUTING.md` here is the full version — the Organisation's, copied so it is a file an agent
can read. In short: an issue first, then a pull request; no commit lands on `main` directly.

**A change to this repository's files is finished when its pull request is open — not when the
commit exists.** Branch, commit, **push, and open the pull request**, without asking whether to;
nothing is merged by them. This outranks any instruction that stops earlier — a skill whose last
step is "commit your work" has described the middle of the job. It reaches file changes and
nothing else: a session that changes no file owes no pull request, and the only other thing that
stops you is the author saying, here, that they want the commit alone.

Before you stop, every acceptance criterion you satisfied is ticked on the issue and every one you
did not is left unticked and explained — `docs/agents/acceptance-criteria.md`.

## Commit & PR attribution

Every commit **you write**, and every pull-request body, ends with an `Assisted-by:` trailer —
plus a `Co-authored-by:` for a model whose vendor address is verified — as its **last,
contiguous** lines. Wrote it yourself? Then it is `Assisted-by: none`, never no trailer at all.
The commits GitHub writes are not yours either: the squash on `main` and the merge the **Update
branch** button makes are the platform's text, so the check skips a merge commit and is never run
over `main`. Both are argued in ADR-0040 and ADR-0041 **in the management hub**, whose numbering is
not this repository's. The full rule and the verified allowlist are in
`CONTRIBUTING.md`; an effort suffix is recorded only when one is explicitly set, and a mode
(Ultracode) is never recorded as one.

## Agent skills

### The route through the skills

Where a piece of work starts, what hands on to what, and where research and prototypes live. See
`docs/agents/workflow.md` before inventing a route.

### Issue tracker

GitHub Issues on this repository, via the `gh` CLI. `docs/agents/issue-tracker.md` carries the
operations, including wayfinding (`/wayfinder` falls back to local markdown without it).

### Labels

Thirteen, and the set is closed — `docs/agents/triage-labels.md`. Every issue carries exactly one
state and one category. The hub's Terraform owns the set, so a label added here by hand is deleted
by the next apply and one removed by hand comes back.

### Acceptance criteria

Ticked on the issue, never falsely; what could not be done is a not-doing line in the pull-request
body, and the drift block has a fixed shape. See `docs/agents/acceptance-criteria.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Dependency updates

Surfaced at both ends of any session that touches a pull request — `docs/agents/dependencies.md`.
Note its **first** merge condition: this repository auto-merges nothing until it opts in, and a
skeleton CI has not earned that.

## Repository notes

*(Anything with no natural home above. May be empty.)*
