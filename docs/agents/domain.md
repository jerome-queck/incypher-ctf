# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, or
- **`CONTEXT-MAP.md`** at the repo root if it exists — it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in. In multi-context repos, also check `src/<context>/docs/adr/` for context-scoped decisions.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo (most repos):

```
/
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-event-sourced-orders.md
│   └── 0002-postgres-for-write-model.md
└── src/
```

Multi-context repo (presence of `CONTEXT-MAP.md` at the root):

```
/
├── CONTEXT-MAP.md
├── docs/adr/                          ← system-wide decisions
└── src/
    ├── ordering/
    │   ├── CONTEXT.md
    │   └── docs/adr/                  ← context-specific decisions
    └── billing/
        ├── CONTEXT.md
        └── docs/adr/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## House style for an ADR

Three rules about ADRs are **checked**, and CI names them when they fire: a filename is
`NNNN-hyphenated-title.md`, no two records share a number, and a record that supersedes another
requires the one superseded to point forward at it — a one-way link leaves the reader who arrives
at the older record acting on guidance that was overtaken. The reasoning is
[ADR-0033](https://github.com/Jerome-Group/org/blob/main/docs/adr/0033-decision-records-are-held-to-their-number-and-their-links.md);
the link is absolute because this file is seeded verbatim into every repository, whose own
`docs/adr/` has no such record.

Everything below is **guidance, enforced by nothing.** An ADR may be a single paragraph; the value
is in recording the decision, not in filling out sections. A required section produces records
whose section reads "None", which passes a check and teaches nobody. So write these because they
are worth writing:

- **Lead with the decision**, in the title and the first paragraph. A reader who stops after two
  sentences should still know what was decided.
- **Argue against the option you rejected**, especially when it was the obvious one. The record is
  read by someone about to suggest it again.
- **`## Consequences`** — what this costs, what it makes harder, what now has to be remembered.
  The honest ones are what make a record worth keeping.
- **`## Revisit when`** — the conditions under which this should be reopened. It is what turns a
  record from a headstone into something with a trigger.

Superseding a record is two edits: the new record says what it supersedes, and the superseded one
gains a line at the top pointing forward. Say what is superseded — a section, a paragraph, the
whole thing — and what still stands.

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders) — but worth reopening because…_
