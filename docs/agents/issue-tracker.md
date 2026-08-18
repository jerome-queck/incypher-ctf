# Issue tracker: GitHub

Issues and PRDs for this repo live as GitHub issues. Use the `gh` CLI for all operations.

## Conventions

Every issue carries exactly one **state** label and exactly one **category** label — the two axes
in [`triage-labels.md`](triage-labels.md). Both slots are filled at creation. An issue form's
`labels:` key does that on the web path, but `--template` supplies body text only, so on this
path the command is what carries them.

- **Create an issue**: `gh issue create --title "..." --body "..." --label needs-triage --label task`.
  Use a heredoc for multi-line bodies. Substitute the category the issue actually is — `task`,
  `decision` or `bug` — and a state other than `needs-triage` when you already know it.
- **Read an issue**: `gh issue view <number> --comments`, filtering comments by `jq` and also fetching labels.
- **List issues**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'` with appropriate `--label` and `--state` filters.
- **Comment on an issue**: `gh issue comment <number> --body "..."`
- **Apply / remove labels**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`
- **Close**: `gh issue close <number> --comment "..."`

Infer the repo from `git remote -v` — `gh` does this automatically when run inside a clone.

## Pull requests as a triage surface

**PRs as a request surface: no.** _(Set to `yes` if this repo treats external PRs as feature requests; `/triage` reads this flag.)_

When set to `yes`, PRs run through the same labels and states as issues, using the `gh pr` equivalents:

- **Read a PR**: `gh pr view <number> --comments` and `gh pr diff <number>` for the diff.
- **List external PRs for triage**: `gh pr list --state open --json number,title,body,labels,author,authorAssociation,comments` then keep only `authorAssociation` of `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, or `NONE` (drop `OWNER`/`MEMBER`/`COLLABORATOR`).
- **Comment / label / close**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub shares one number space across issues and PRs, so a bare `#42` may be either — resolve with `gh pr view 42` and fall back to `gh issue view 42`.

## When a skill says "publish to the issue tracker"

Create a GitHub issue.

## When a skill says "fetch the relevant ticket"

Run `gh issue view <number> --comments`.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a single issue with **child** issues as tickets.

A `wayfinder:<type>` **is** the ticket's category, so the two-axis rule applies here unchanged: a
wayfinding issue carries its `wayfinder:*` label and a state, and nothing else.

- **Map**: a single issue labelled `wayfinder:map`, holding the Notes / Decisions-so-far / Fog body. `gh issue create --label needs-triage --label wayfinder:map`.
- **The map's Notes block**: the house default, on top of whatever this effort needs — *"Every session reads `CONTEXT.md` for the glossary and `docs/agents/workflow.md` for the route through the skills, and runs `/grilling` with `/domain-modeling` on a grilling ticket."* Standing preferences for the effort go after it; a Notes block that says only the default still says something worth saying.
- **Child ticket**: `gh issue create --parent <map> --label needs-triage --label wayfinder:<type>`, where the type is one of `research`/`prototype`/`grilling`/`task`. `--parent` takes the map's plain issue number. Once claimed, the ticket is assigned to the driving dev.
- **Blocking**: GitHub's **native issue dependencies** — the canonical, UI-visible representation. `--blocked-by <n>` added to the create above, or `gh issue edit <n> --add-blocked-by <n>` / `--add-blocking <n>` afterwards; all of them take plain issue numbers. Read the edges back with `--json blockedBy,blocking`, whose nodes carry each edge's `state`. A ticket is unblocked when every blocker is closed.
- **Frontier query**: the map's open children, minus any with an open blocker or an assignee.

  ```sh
  gh issue list --state open --limit 200 --json number,title,parent,blockedBy,assignees \
    --jq '[.[] | select(.parent.number == <map>)
                | select([.blockedBy.nodes[] | select(.state == "OPEN")] | length == 0)
                | select(.assignees | length == 0)
                | {number, title}]'
  ```

  It filters the repository's open issues down to the map's children, so the `--limit` is over the tracker rather than over the map: the default of 30 would silently drop candidates on a busy repository, and a truncated frontier looks like a finished one. That yields the takeable set; first in **map order** wins, which is the map's own sub-issue order — `gh issue view <map> --json subIssues --jq '.subIssues.nodes[].number'`.
- **Claim**: `gh issue edit <n> --add-assignee @me` — the session's first write.
- **Resolve**: `gh issue comment <n> --body "<answer>"`, then `gh issue close <n>`, then append a context pointer (gist + link) to the map's Decisions-so-far.
