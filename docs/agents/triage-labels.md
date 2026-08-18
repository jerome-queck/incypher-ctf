# Triage Labels

The skills speak in terms of canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

**Every issue carries exactly one state and exactly one category.** There is no third axis. A
wayfinding ticket's `wayfinder:<type>` counts as its category, so the rule has no exemption
clause. When you cannot choose a state, the state is `needs-triage` — not an omitted label.

## State — where the issue is in its life

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

## Category — what kind of thing it is

| Label in mattpocock/skills | Label in our tracker | Meaning                                        |
| -------------------------- | -------------------- | ---------------------------------------------- |
| `enhancement`              | `task`               | A change someone already wants made            |
| —                          | `decision`           | A question to be settled and recorded as an ADR |
| `bug`                      | `bug`                | Something is behaving wrongly                  |

`decision` is a category the skills do not have. `enhancement` is not part of this vocabulary and
does not exist here — a skill that names it means `task`.

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from these tables.

## The set is closed, and how it stays closed

These thirteen — the two tables above plus the five `wayfinder:*` types the wayfinding operations
in `issue-tracker.md` name — are the whole of what this repository carries.

They were created by hand when the repository was set up ([ADR-0002](../adr/0002-org-machinery-is-vendored-to-stand-alone.md)),
from the same set the Jerome-Group template used. **There is no automation keeping the set closed
here.** In the organisation this repository was seeded from, Terraform reapplied the set and
deleted anything outside it; standalone, that backstop is gone. So closing the set is a discipline,
not a mechanism:

- GitHub creates seven default labels on a new repository (`documentation`, `duplicate`,
  `enhancement`, `good first issue`, `help wanted`, `invalid`, `question`). They were deleted at
  setup. Don't recreate them.
- A label added by hand **stays** until a human removes it — nothing reaps it. So don't add one
  casually: a fourteenth word is a change to how every issue is classified, worth an issue and an
  ADR, not a one-off `gh label create`.
- Renaming a label is a delete and a create, so the old label's assignments do not follow. Rename
  deliberately.

`bin/audit` — the organisation's cross-repository label audit — does not exist here. The only
backstop that a mis-stamped issue gets noticed is a person reading the tracker, and the
conformance check, which fails a pull request whose linked issue is not on exactly one label per
axis.

An issue form's `labels:` key is applied by the web interface and never fires on the command-line
path — `gh issue create --template` supplies starting body text and nothing else — so a form
cannot label an agent's issue. On that path it is the documented command that carries the labels;
see `issue-tracker.md`.

## An empty slot is filled for you, and that is not the same as filling it

`.github/workflows/stamp-new-issue.yml` runs on every issue as it is opened — from a form, from
`gh issue create`, from the API, from a skill — and adds `needs-triage` if the issue has no state
and `task` if it has no category. It only ever fills an **empty** slot: an issue that arrives
carrying an axis keeps exactly what it arrived with, and nothing is replaced or removed. The same
workflow assigns the issue's author when it opens unassigned — that convention, and its one
exception, is in [`issue-tracker.md`](issue-tracker.md).

So the two defaults are the answer for an issue nobody classified. They are not an excuse to stop
classifying: an issue you know is a `bug` and label `task` by omission reads as a task until
somebody notices — and here, "somebody notices" is the whole backstop, so classify at creation.
