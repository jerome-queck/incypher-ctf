# AGENTS.md — incypher-ctf

> Canonical instruction file for AI agents (Claude Code and others) working in this repo.
> `CLAUDE.md` is a symlink to this file, so the two can never drift.

## What this repo is

An autonomous CTF-solving agent for the IN-CYPHER Agents-vs-CTF hackathon (September 2026). The
deliverable is a Docker container — the **Solver** — pointed at a whole **Board** and left for the
window: it syncs every Challenge (Intake), judges what each is worth (Triage), ranks what the
remaining clock is committed to (Order), and works that working set to flags with no human
intervention, which scoring penalises — [spec #63](https://github.com/jerome-queck/incypher-ctf/issues/63)
is the shape and `CONTEXT.md` is the vocabulary. This repository is that Solver — whose own code
paths wait for nobody — and the conventions it is built under.

- **Owner:** [jerome-queck](https://github.com/jerome-queck) — Jerome, sole admin; repo is private
- **Team:** [nonsense00](https://github.com/nonsense00) — Elson · [aacceeOP](https://github.com/aacceeOP) — Aidan ·
  [chareechard](https://github.com/chareechard) — Richard. All write access; `gh` takes the handle.

## Getting it running

*(The commands an agent could not have guessed. To run the Solver: `docker build -t solver .`
then `docker run --rm --env-file .env -v "$PWD/state:/state" solver` — it refuses loudly.)*

**`ruff` and `pytest` are not installed here.** `pyproject.toml` carries their settings and
declares no dependency at all, so the tree reads as though they are. Build them a throwaway
virtualenv under `.cache/`, which is gitignored:

```
python3 -m venv .cache/venv && .cache/venv/bin/pip install -q ruff pytest
.cache/venv/bin/ruff check . && .cache/venv/bin/ruff format --check . && .cache/venv/bin/pytest
```

They are CI-side tools and are in no image — the image's two package lists live in the `Dockerfile`
itself, one package per line with the reason beside it (ADR-0024 amends them), and nothing lints at
14:00 on competition day. **Three checks run before you push, because the workflows fire *after*
it:** `sh conformance/check-conformance.sh .` and `sh conformance/check-trailers.sh main..HEAD`,
which need no interpreter, and `docker build .`, which CI has run since #81 and which needs Colima.

To point the Solver at a board, run `bash scripts/setup-board.sh`. **What this machine actually
holds is `python3 scripts/credentials_held.py`** — ask it rather than inferring from the
repository's silence: `.env.*` is gitignored, so a secret we hold leaves no trace in the tree at
all. Every secret, where it comes from, and how it reaches the container is `docs/credentials.md`.

The container runtime is **Colima**, pinned with the Docker CLI and the VM's allocation in
`scripts/runtime.py`: `python3 scripts/runtime.py start` brings it up on the pin and `verify`
reports every way this machine has drifted off it — and nothing builds an image while it is down.
`bash scripts/setup-runtime.sh` walks the parts that need a human — the reboot, the FileVault
decision, the Codex login taken *inside* the container. Two constraints an agent will otherwise
meet the hard way: **Colima mounts `$HOME` and nothing else**, so a `-v` from outside it silently
hands the container an empty directory, and the repository must live under `$HOME` for the
`/state` mount to reach. **An unattended reboot recovers in about 40 seconds** — proven, not
assumed: `python3 scripts/restart_probe.py arm` before a reboot and `check` after one reaches a
verdict by clock. It cost turning FileVault off; ADR-0013 records what that exposes.

## Conventions

- Default branch: `main`.
- Domain glossary is `CONTEXT.md`; decisions are ADRs in `docs/adr/` (`docs/agents/domain.md`).
- Keep secrets out of the repo. **Never commit a token.** The conformance check scans every pull
  request for one and fires *after* the push, so a caught credential is burned: rotate it first,
  then clean up. The **team key** and the **LLM API keys** belong in an untracked env file, `.env`
  for the board in play and a `.env.<event>` overlay for any other. The full response, and the one
  secret that **cannot be rotated** (the team key: the Board displays that value rather than
  minting it), are in `CONTRIBUTING.md` and `docs/credentials.md`.

## Code standards

`CODING_STANDARDS.md` is the full version: the burden is on the code, not on docs — names,
placement and small cohesive units carry the *what*, and docs carry only the *why*. `MAP.md` is
required at the root and updated in the same pull request as any top-level change.

**`README.md` moves on a different clock, and it is the one document no rule named.** It is the
front door, so its **Status** is updated when a version lands or a gate moves — not per change,
because a rule firing on every pull request is one that gets ignored, and most changes have nothing
to say to a newcomer. It went 48 commits claiming there was no Solver code
([#130](https://github.com/jerome-queck/incypher-ctf/issues/130)); the conformance check asserts it
exists and keeps its headings, never that it is true.

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

**Protection here is by convention, not by mechanism.** GitHub enforces neither branch protection
nor required checks on a free private repository (ADR-0003), so nothing stops a push to `main` or
a merge over red. A red conformance or CI check is a stop anyway.

Before you stop, every acceptance criterion you satisfied is ticked on the issue and every one
you did not is left unticked and explained — `docs/agents/acceptance-criteria.md`.

## Commit & PR attribution

Every commit **you write**, and every pull-request body, ends with an `Assisted-by:` trailer —
plus a `Co-authored-by:` for a model whose vendor address is verified — as its **last,
contiguous** lines. Wrote it yourself? Then it is `Assisted-by: none`, never no trailer at all.
Do not copy `main`'s own commits: the squash and the **Update branch** merge are the platform's
text, and the check skips them. The full rule, the verified allowlist and the effort suffix are in
`CONTRIBUTING.md`; a mode (Ultracode) is never recorded as one.

## Agent skills

The Matt Pocock engineering skills are **installed in this repository** — `.claude/skills/` for
Claude Code, `.agents/skills/` for Codex and other agents — so every teammate has the same set on
clone, with no global install. **Use them; do not reinvent their routines.** If you are unsure
which one fits, start with `/ask-matt`. This instruction is the enforcement: skills are
model-invoked, so a session that ignores them fails no check — it just does worse work.

They are already configured for this repository, and the answers are in `docs/agents/` — so **do
not run `/setup-matt-pocock-skills` again**. If you change the installed set, `skills-lock.json`
and both skill directories are updated together, in one pull request.

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

`/clear` before each `/implement`: a ticket is self-contained, and carrying the last one's context
is how a session builds against a decision superseded two tickets ago. `docs/agents/workflow.md` is
the full route — the build chain (`/to-spec` → `/to-tickets` → `/implement`), when to skip it, and
where research and prototypes land.

### Issue tracker

GitHub Issues on this repository, via the `gh` CLI. `docs/agents/issue-tracker.md` carries the
operations, including wayfinding (`/wayfinder` falls back to local markdown without it).

### Labels and assignment

Thirteen labels, and the set is closed — `docs/agents/triage-labels.md`; no automation keeps it
closed here, so a label added by hand *stays* until a human removes it. Every issue carries exactly
one state, one category and one assignee (`docs/agents/issue-tracker.md`, which has the wayfinder
exception).

### Dependency updates

Surfaced at both ends of any session that touches a pull request — `docs/agents/dependencies.md`.
This repository auto-merges **nothing** (ADR-0003): every bump is landed by hand on a green check.

## Repository notes

v1 has landed against [spec #63](https://github.com/jerome-queck/incypher-ctf/issues/63) — tagged
`v1`, with four gate Runs promoted under `runs/` and the layout in `MAP.md`. The **ADK
integration** is the one part still gated on an outside release — ADR-0008 expects it behind
`Target` rather than `Board`, and names the kit's arrival as the date that expectation gets tested.

**The roadmap is ADR-0030, and ADR-0006's table is historical.** Three versions remain — v2 (the
failsafes and the solve rate), v3 (the ADK against IN-CYPHER from 14 Sep), v4 (freeze and go/no-go,
on-site 21–22 Sep) — and a version number written anywhere before 31 August 2026 resolves through
ADR-0030's mapping table, because **the older ADRs were deliberately not rewritten**: a `v3` inside
ADR-0005 or ADR-0015 records what was decided then. Two standing consequences for a session working
here. **The practice board is one we build**, not an event — the live calendar was measured and only
about 13% of it runs CTFd, so no version gates on somebody else's competition and an external board
can never fail a gate. And **a status code is not a platform fingerprint**: nine hosts answered
HTTP 200 on `/api/v1/challenges` *and* on the chall-manager `mana` endpoint with a single-page-app
catch-all, so content-type and the body compared against the landing page are the test.
