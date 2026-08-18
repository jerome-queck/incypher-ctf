# Coding standards

This file is what `/code-review`'s **Standards** axis reads. It is layered:

- **The core** (§1–§5) — shared by every repository, seeded from the template. Treat it as
  fixed; it changes only by an org-level decision (see §7).
- **Repo-specific standards** (§6) — each repository fills these in and evolves them freely.

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
  workflow that is not a check — an automation that acts on a merge — is its own file. So is
  `conformance.yml`, and for a reason the rule was never about: that file is not this repository's
  to own. It arrives written, its pin is moved by Dependabot, and folding it into `ci.yml` would
  make every rule the Organisation agrees an edit to a file this repository owns.
- **No path filters.** A filtered workflow never reports on a pull request it does not match, so
  a required check sits pending forever and the merge blocks on a report that will never arrive.

## 6. Repo-specific standards

*(Each repository fills this in and owns it.)* Language and framework conventions, the seams
where tests are written, naming or layout rules particular to this codebase, and anything the
core leaves open. Add them here; they evolve through this repository's normal pull-request flow.

This section is empty because this repository is newly generated. Fill it in with the first
change that has an opinion worth holding the next one to — the formatter and linter that run in
CI, where the tests live, and the one or two layout rules a newcomer would otherwise guess
wrong.

## 7. Evolution — what is rigid, what moves

- **The core (§1–§5) is rigid.** It is identical in every repository and changes only by an
  org-level decision recorded as an ADR in the management hub, then rolled out through the
  template (and to existing repositories as wanted). Do not quietly edit the core in one repo.
- **§6 moves freely** per repository, through that repository's own pull requests.
- **`MAP.md` is required everywhere, but its contents are repo-specific** and are updated
  continuously alongside the code they describe.
