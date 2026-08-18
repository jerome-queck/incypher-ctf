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
no longer exists here — GitHub creates it on every new repository and the closed set deletes it. A
skill that names it means `task`.

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from these tables.

## The set is closed, and it is not editable here

These thirteen — the two tables above plus the five `wayfinder:*` types the wayfinding operations
in `issue-tracker.md` name — are the whole of what this repository carries. They are created by
the hub's Terraform (`Jerome-Group/org`, `modules/repository`), and the set is authoritative: a
label added here by hand is **deleted** by the next apply, and one edited or removed by hand is
put back as the hub wrote it.

That includes `bug` and `wontfix`, which GitHub creates by default and the hub then takes over.
Renaming a label means renaming it in the hub, which renames it everywhere — and a rename is a
delete and a create, so the old label's assignments do not follow.

A repository that genuinely needs a fourteenth word has to argue for it in the hub, where every
repository would get it.

An issue form's `labels:` key is applied by the web interface and never fires on the command-line
path — `gh issue create --template` supplies starting body text and nothing else — so a form
cannot label an agent's issue. On that path it is the documented command that carries the labels;
see `issue-tracker.md`.

## An empty slot is filled for you, and that is not the same as filling it

`.github/workflows/stamp-issue-labels.yml` runs on every issue as it is opened — from a form, from
`gh issue create`, from the API, from a skill — and adds `needs-triage` if the issue has no state
and `task` if it has no category. It only ever fills an **empty** slot: an issue that arrives
carrying an axis keeps exactly what it arrived with, and nothing is replaced or removed.

So the two defaults are the answer for an issue nobody classified. They are not an excuse to stop
classifying: an issue you know is a `bug` and label `task` by omission reads as a task until
somebody notices. `bin/audit` in `Jerome-Group/org` reports every open issue that ends up on the
wrong number of labels on either axis, which is the backstop rather than the mechanism.
