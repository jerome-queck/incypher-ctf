# The route work takes through the skills

Which skill starts a piece of work, what it hands on to, and where the artefacts land. Each skill
documents itself; this file documents the **route between them**, so a session does not have to
invent one. It records the flow as the skills' author wrote it — `/ask-matt` is the router it
comes from — and overrides none of them.

## Where you come in

| You have | Start at |
| -------- | -------- |
| A foggy effort too big for one session — a greenfield build, a large feature | `/wayfinder` |
| A wayfinder map whose fog has cleared | `/to-spec` — **mandatory**, see below |
| An idea that needs sharpening | `/grill-with-docs` |
| A ticket someone already wrote | `/implement` |
| Someone else's issue — a bug report, an incoming request | `/triage` |
| Something broken — a flake, a regression, a bug that resists a first glance | `/diagnosing-bugs` |

`/grill-with-docs` rather than `/grill-me`: work here always has a working directory under it, and
it is the one that leaves its paper trail in `CONTEXT.md` and `docs/adr/`.

`/triage` is for issues you did **not** create. Tickets `/to-tickets` produced are agent-ready by
construction and are not re-triaged. What triage produces is an agent-ready issue, which joins the
route at `/implement`.

## The ordinary route

`/grill-with-docs` → (`/to-spec` → `/to-tickets`) → `/implement`.

`/to-spec` here is **conditional**: run it when the work is a **multi-session build**, more than
one context window's worth of implementation. When it is not, go from the grilling straight to
`/implement` in the same window — a spec for work that fits in one session is ceremony that buys
nothing.

`/implement` drives `/tdd` at the agreed seams and closes by running `/code-review` before
anything is committed, so the review the Owner reads is a conclusion rather than a promise. The
pull request that follows is not optional: **How work flows** in `AGENTS.md` is the rule.

## From a cleared map, `/to-spec` is mandatory

A wayfinder map is an **index**. It gists each decision in one line and points at the ticket
holding the detail, and `/to-spec` is what collapses those linked decisions into a single
buildable plan. Going from a cleared map straight to `/to-tickets` or `/implement` reads the index
and discards everything the tickets hold — which is most of what the map cost to produce.

The one exception is the effort that turned out small: if the fog cleared and what remains is a
single session's work, implement it and close the map.

## When to skip the ceremony altogether

A change **describable in one sentence and reviewable in one screen** goes straight to
`/implement`. Fixing a typo, correcting a link, adding the missing row to a table — grilling these
costs more than making them.

The test is both halves at once. A one-sentence change that rewrites two hundred lines is not one
of these, and neither is a one-screen diff whose point nobody can state.

## Context hygiene

Two rules, and they pull in opposite directions on purpose.

- **One unbroken window from the grilling through `/to-tickets`.** Do not `/clear` or `/compact`
  between them. The spec is built on the grilling and the tickets on the spec, and each
  compaction flattens the reasoning the next step wants verbatim.
- **`/clear` between each `/implement`.** Every ticket is self-contained by construction, so the
  last one's context is disposable — and carrying it forward is how a session ends up confidently
  building against a decision that was superseded two tickets ago.

Everywhere else, the choice at a phase boundary — continue, `/clear`, `/handoff`, subagent,
`/compact` — is the ordered tree in `PHASE-BOUNDARIES.md`. It ships with the skills rather than
with any repository, so reach it through `/ask-matt`, which links it. It is deliberately not
restated here: a restatement is the copy that goes stale while the original moves.

## Where the artefacts land

**Research findings: `docs/research/`, on the default branch, through an ordinary pull request.**
One file per question, cited, named for the question it answers. `/research` saves its findings
wherever the repository already keeps such notes — this is that place, declared so that every
session picks the same one. A finding left on a branch is a finding the next session does not
find.

**Prototypes: a `prototype/<name>` branch off the default branch, linked from the implementation
issue.** A prototype is deliberately throwaway code that answered one design question, and both
obvious fates for it are wrong: merged to the default branch it becomes something an agent finds
and copies, and deleted it takes the primary source of the decision with it. A branch keeps it
readable without putting it where an agent searching the tree will trip over it. What lands on
the default branch is the validated decision, never the prototype.
