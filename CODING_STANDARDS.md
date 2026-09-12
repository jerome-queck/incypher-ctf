# Coding standards

This file is what `/code-review`'s **Standards** axis reads. It is layered:

- **The general standards** (§1–§5) — seeded from the template this repository was born from, and
  broadly true of any codebase. Change them rarely and deliberately (see §7).
- **Repo-specific standards** (§6) — filled in and evolved as this codebase grows.

Both layers are this repository's own now — the standards were seeded from an organisation this
repository has since left (ADR-0002), so all of it evolves through this repository's own
pull-request flow.

## 1. The principle: the code explains itself

Every unit is written to be understood from the code alone by the next reader — increasingly
an LLM agent — so that reader can take exactly what it needs without a human in the loop. Prose
is a poor substitute for legible code: if a piece of code needs a paragraph to be understood,
the code is wrong, not under-documented. **The burden is on the code, not on the docs.**

## 2. What the code must do

These are checkable; `/code-review` holds a change against them.

- **Self-explanatory.** Names state what a thing is and does; control flow reads plainly. No
  cleverness that needs a comment to decode. A comment earns its place only for a genuine *why*
  the code cannot carry (a workaround, a non-obvious constraint) — never to restate *what*.
- **Placed predictably.** Files live where their purpose says they belong — the file system is
  itself a map. A reader guesses where something lives from its role and is right.
- **Small, cohesive units.** One concern per file and per function, sized so an agent can load
  it and reason about it without dragging in the whole repository.
- **Interface separated from implementation.** The public surface — types, signatures, the
  contract — is separable from how it is carried out, so a reader takes just the interface it
  needs and ignores the rest.
- **Deep, not shallow.** A unit's public surface is small relative to what it does, and it hides
  its internals. Prefer a few powerful, well-named entry points over many thin pass-throughs.
- **Few, obvious dependencies.** What a unit needs is explicit at its edge, not reached for
  through globals or hidden state. Minimise what a reader must hold in their head at once.
- **Formatted by tooling, not by hand.** Formatting and lint are automated so they are never a
  review topic; review is about design, not whitespace.
- **No dead weight.** No unused code, commented-out blocks, or speculative generality. If it
  isn't used now, it isn't here.

## 3. Documentation boundary

Docs carry only what the code cannot say — the *why*, the decisions, the domain language, the
constraints outside the code. That layer already exists and is required:

- **`docs/adr/`** — the decisions and their rationale.
- **`CONTEXT.md`** — the ubiquitous language (the glossary).

Do not narrate the code in prose. If you are writing documentation that explains *what the code
does*, fix the code until it says so itself.

## 4. `MAP.md` is required

Every repository carries a **`MAP.md`** at its root — a one-screen orientation map so an agent
finds its way fast. This is navigation, not explanation: it points at where things are; it never
restates what the code already says. Keep it light so it stays true:

- One line: what this repository is.
- The top-level areas **only** — each a single line: *what lives there* and its *entry point*.
  Do not mirror the directory tree; list the handful of places that matter.
- A "start here" pointer for a newcomer.

`MAP.md` is part of the definition of done: a change that adds, moves, or removes a top-level
area updates `MAP.md` in the **same** pull request. A stale map is worse than none, so
`/code-review` treats a drifted `MAP.md` as a Standards finding.

## 5. What CI must prove

The core makes one claim about a repository's own checks; everything else about them is §6's
business. Two obligations:

- **CI proves this repository's own artefact.** From a clean checkout, with no manual step, the
  run builds what the repository produces and runs one formatter and one linter in check mode,
  plus the tests. Where the artefact is not code — Terraform, a set of documents, a template
  tree — the obligation is unchanged and only the commands differ: run whatever would catch that
  artefact being wrong.
- **The whole run finishes inside ten minutes.** Past that, developers route around it and a
  failure stops naming one change. A suite outgrowing the bound is a signal to split the check,
  not to raise the bound.

How the checks grow forks on **cost**, which is countable, rather than on importance, which is
not:

- **Structural checks are added on sight** — deterministic, sub-second, needing no judgement.
- **A behavioural check waits until the mistake has happened three times.** Anything cheaper to
  write than to be wrong about is already covered by the line above; the rest is a guess until
  the failure has a history.
- **A bug fix always carries the check that would have caught it**, whichever kind it is. The
  mistake has happened, so there is nothing left to estimate.

Two shapes are settled, so no repository re-argues them:

- **One workflow file for the checks, many jobs.** Jobs already give the parallelism and the
  separate check contexts, so a second file buys neither and splits the place a reader looks. A
  workflow that is not a check — an automation that acts on an event — is its own file. So is
  `conformance.yml`, and for a reason of its own: `ci.yml` proves this repository's own artefact
  and grows with the code, while `conformance.yml` asks whether the repository still keeps its
  conventions. The two go red for different reasons and are read in different moods, so they stay
  apart (ADR-0002).
- **No path filters.** A filtered workflow never reports on a pull request it does not match, so
  a required check sits pending forever and the merge blocks on a report that will never arrive.

## 6. Repo-specific standards

The rules §1–§5 leave open, as this repository keeps them. Most are here because the tree already
follows them. Where one is a commitment the tree has not met yet, it says so and names the issue
that will enforce it — a rule with neither a practice nor an issue behind it belongs in neither
place.

### The languages, and what checks them

Two languages so far, one tool each, plus the test runner:

- **Python** — `ruff`, as both: `ruff format --check` and `ruff check`.
- **Shell** — `shellcheck`.
- **Tests** — `pytest`, in **`tests/` at the repository root**. One place rather than beside the
  code, so a reader looking for the seams looks once.

**None of these three runs yet.** `ci.yml` proves only that `CLAUDE.md` is still a symlink and
that `MAP.md` exists, and `tests/` arrives with the first test — wiring all of it into CI is
[issue #22](https://github.com/jerome-queck/incypher-ctf/issues/22). Until that lands they bind
the author rather than the build, which is the whole reason to name them before the tooling
exists: §6 is the contract and CI is the enforcement, so the next pull request cannot reach for a
different formatter while #22 is still open.

They reach the files this repository writes, and stop there. `conformance/*.sh` are the hub's
checker scripts held byte-for-byte (ADR-0002 — the `manifest` beside them is this repository's
own and is edited freely), and `.claude/skills/` and `.agents/skills/` are vendored skill copies
pinned by `skills-lock.json`. Reformatting any of them would break the re-vendor that keeps them
current, so nothing in this section reaches them.

### Scripts are run through the interpreter, never `./`

`sh conformance/check-conformance.sh .`, `python3 scripts/ctfd_probe.py` — the interpreter is
named at the call site, so the file itself needs neither a shebang nor an exec bit. Every
`conformance/*.sh` and `scripts/check-rules-drift.sh` is mode `100644` and opens with a comment
rather than `#!`, and `.github/workflows/conformance.yml` invokes each checker through `sh`.

What a newcomer gets wrong, in both directions:

- **Do not add a shebang or `chmod +x` to make a script "runnable".** An exec bit is a second
  claim about how a file is invoked, made silently and in the one place a diff review does not
  read.
- **Declare the dialect instead.** A shebang-less `sh` script tells `shellcheck` nothing about
  which shell it is, so its first line is `# shellcheck shell=sh`, as `check-rules-drift.sh`'s
  is. Without it the file is unlintable rather than clean.

**The exception is a file a generator owns.** `scripts/setup-board.sh` comes from the `/wizard`
template with its library byte-for-byte untouched, and the template supplies the shebang and asks
for the exec bit; the file's own header says not to hand-edit it. Reshaping a generated file to
fit this rule trades away regenerating it, and buys a tidier `ls`.

### Standard library only, inside the Solver image

Anything that has to run in the Solver container imports from the standard library except the exact
Candidate vault. ADR-0045 requires that vault to be encrypted; this repository implements that
requirement with `cryptography`'s reviewed AES-GCM rather than a locally designed cipher. The
dependency is an immutable `Dockerfile` input with a behavioral build probe. The competition run is
unattended, so an undeclared or runtime-installed dependency is not a thing anyone is there to fix.
`solver/__init__.py` states the constraint for the package, and `scripts/ctfd_probe.py` states the
stricter constraint for its own seam.

### Where a new file goes

- **`solver/` — the Solver's own code, a plain package at the repository root** and not
  `src/solver/`, for the reason
  [ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md) gives. A
  seam lives here the moment two callers would otherwise each keep their own copy of a rule:
  `Board` is here because the pre-flight check and the competition run have to be the same code,
  not two things that agree today.
- **`Dockerfile` — at the repository root, producing one image** and no `image/` directory, for
  the reason [ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)
  gives. The established v1 package lists remain inline. v2 Tool additions instead live in one
  independently owned lock fragment under `tool-supply/`; its deterministic assembler validates
  and orders exact package and file inputs before the root Dockerfile consumes them. This is
  [ADR-0047](docs/adr/0047-one-immutable-image-exposes-only-proved-free-tool-components.md)'s one
  immutable image and deduplicated profile contract, not a second image or a run-time installer.
  A tool the Solver needs at run time is a locked build input and never a runtime install — the
  venue network at 14:00 is not a dependency we get to have. **That rule is about what a solve
  depends on, and `pip` does not change it**: the image ships `pip` and the PEP 668 marker is removed
  ([ADR-0024](docs/adr/0024-the-image-carries-what-a-run-reached-for-and-a-picture-is-attached.md)),
  so the model can reach PyPI for its own long tail mid-Attempt — but anything a Run is *entitled*
  to find is still a line in the list, because a run-time install that the venue network refuses is
  a Challenge lost with nobody there to notice. Where two packages ship the same tool, the `Dockerfile` installs one
  and says which, so nothing depends on whichever binary resolves first.
- **`docs/competitions/` — one reading, one Board profile, and a snapshot per mutable source.** Our
  `<event>.md` reading sits beside generated `<event>.<source>.txt` snapshots, so a rules or
  operational-source change is a diff rather than something nobody noticed — and an
  `<event>.board.json`, the tracked half of a Board profile, holds the URL and the prohibitions
  that exist in that Board's prose and nowhere in its API
  ([ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)). Each
  snapshot is generated and says so; the reading and `.board.json` are hand-written, and the
  `.board.json` is what a human edits when `scripts/check-rules-drift.sh` shows a prohibition has
  moved.
- **`scripts/` — the checks and setup run by hand against a live board.** They want a human, a
  credential, or a network the runner does not have, which is what keeps them out of `ci.yml`.
  They consume `solver/` and never re-implement it; a rule that exists in both places is a rule
  that will disagree. The eval queries live here too, `eval_*.py` — one per
  question for the seven ADR-0009 names
  ([ADR-0009](docs/adr/0009-store-what-was-observed-derive-every-judgement.md) — *eval is seven
  questions, not a harness*), plus `eval_refusals.py`, which answers none of the seven and carries
  the refusal and premature-quit derivation
  [ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md) asks
  for instead of two record fields. They consume `solver/` like every other script here: a replay
  against a re-written counter measures a rule that never ran.
- **`runs/` — the Step stream of every Run that reached an Attempt**, one file per Run, written
  only by `scripts/promote_run.py` and never by the Solver, which has no git binary
  ([ADR-0009](docs/adr/0009-store-what-was-observed-derive-every-judgement.md)). It is **data, not
  code**: excluded from lint in `pyproject.toml` and held to none of the conventions above. It
  needs no conformance exclusion, because `conformance/check-conformance.sh` reads top-level names
  and the manifest and never looks inside a directory — so `runs/` owes it a `MAP.md` row like
  every other area and nothing more.

  **It is deliberately not excluded from the secret scan** — but know what that buys. Nearly every
  gitleaks rule is anchored on a quote character, and JSON escapes every quote, so a credential the
  scanner flags instantly in a plain file is invisible inside a JSONL string value. The real
  control is therefore `scripts/promote_run.py`, which scans a **decoded** copy of the stream
  before anything is written, and refuses on a hit — because nothing human stands between that file
  and the push.

## 7. Evolution — what is rigid, what moves

- **The general standards (§1–§5) move rarely.** They are broadly true of any codebase, so a
  change to them is a change to how everything here is reviewed — worth an issue and, if it
  reverses a stated principle, an ADR. Do not edit them casually, but they are this repository's
  to change: there is no hub holding a canonical copy (ADR-0002).
- **§6 moves freely**, through this repository's own pull requests.
- **`MAP.md` is required, and its contents are repo-specific** — updated continuously alongside
  the code they describe.
