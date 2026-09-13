# Contributing

This is a small, private, four-person hackathon repository. If you have not worked this way
before, this page is the whole of it — a two-minute read, and nothing here assumes you have seen
it anywhere else. The short version: **open an issue, send one pull request per change, squash to
merge, and say who (or what AI) helped.**

## What lives outside this page

How work flows is all here. Three things sit outside it, and each is the kind of thing you find
out too late:

- **[`docs/credentials.md`](docs/credentials.md) — read it before you point anything at a board.**
  The **team key** cannot be rotated, while the CTFd token can. Inference uses broker-private native
  Codex and CPA stores, never an API key in `.env`.
- **The [Matt Pocock skills](.claude/skills/) are installed on clone** — `/triage`, `/implement`,
  `/code-review`, `/grill-with-docs` and the rest, for Claude Code and for Codex. They are how work
  is done here rather than an optional extra: start with `/ask-matt` if you are unsure which fits.
  [`AGENTS.md`](AGENTS.md) has the route through them.
- **The plan is [issue #11](https://github.com/jerome-queck/incypher-ctf/issues/11)**, a wayfinder
  map — not a document in `docs/`. It carries what is decided, what is next, and what is still fog.
  Its closed tickets link the ADRs, which is the fastest way into why the repository looks the way
  it does.

## Before you write code

**Open an issue first.** Not as a formality — as the cheaper half of the work. An issue is where
the shape of a change gets argued, and a pull request that arrives without one fuses the proposal
and the implementation together, so disagreeing with the proposal means throwing away the code.

Pick the form that fits: a **Task** proposes a change you already want; a **Bug** reports
something behaving wrongly. There is a maintainer-only blank issue for anything that fits neither.

Every issue carries one **state** label and one **category** label. It lands on `needs-triage`
and is then moved to exactly one state:

| Label | What it means |
|-------|---------------|
| `needs-triage` | Waiting to be evaluated. Every issue starts here. |
| `needs-info` | Answered questions will move it forward; parked until you reply. |
| `ready-for-agent` | Fully specified. An AI agent may pick this up and finish it unattended. |
| `ready-for-human` | Specified, but needs a person — credentials, judgement, or a manual check. |
| `wontfix` | Deliberately not doing this. The comment says why. |

There is no `in-progress` label — whether something is being worked on is a question for the
issue thread. The full label set and how it is used is `docs/agents/triage-labels.md`.

## Sending a change

You have push access, so you branch here directly — no fork needed. Branch from `main`; there is
no naming convention to get wrong.

**A change is finished when its pull request is open, not when the commit exists.** That is as
true of a one-line fix as of a feature. Push the branch and open the pull request; nothing is
merged by doing so, and an open pull request is the only form in which work here can be reviewed.

How `main` is treated — and one honest caveat:

- **A pull request is expected. Nobody pushes to `main` directly.** On a free private repository
  GitHub cannot *enforce* that (branch protection is a paid feature there), so this is a rule the
  four of us keep, not a wall the platform builds. Keeping it is what keeps the repository
  reviewable.
- **No approvals are required to merge** — with four teammates and no reviewer rota, the pull
  request itself is the review surface. Ping someone if a change deserves a second pair of eyes.
- **A red check is a stop.** CI and the conformance check run on every pull request. GitHub will
  *let* you merge past a red one here (no required-check enforcement on this plan) — don't. Red
  means the change is not finished.
- **Merges are squashes, and history stays linear.** Your branch does not need to be tidy; it
  needs to be one coherent change. If it is two changes, send two pull requests. The repository is
  set to squash-only, so the button does the right thing.
- **Resolve every review comment before merging**, including your own.

So: keep it small, describe *why* in the body — the diff already says what — and link the issue
with `Closes #123`. The pull-request template lays out the rest.

### Pull-request lifecycle cleanup

Checkout cleanup is the last step of every session that touches a pull request. The **primary
worktree** is the first path printed by `git worktree list --porcelain`; it is the checkout that
rests on `main`, even when the change was built in another worktree.

After the pull request opens, fetch `origin`, fast-forward the primary worktree's `main`, and leave
that worktree clean on `main`. Retain the local topic branch while the pull request is open.

Before removing a worktree or branch, inspect every worktree for that topic branch. Retain and
report any dirty worktree, including its path and status, until its changes are deliberately
discarded or preserved on a named ref; never discard or move changes merely to make cleanup pass.
Before deleting the local branch, resolve the pull request's recorded head OID and prove
`git rev-list <local-branch> --not <pr-head-oid>` is empty. Otherwise retain and report the branch
until every additional commit is deliberately discarded or copied to a named retained ref.

After the pull request merges, verify GitHub reports it merged; fetch `origin` with pruning;
fast-forward local `main`; then remove its clean extra worktree and delete its local topic branch. A
closed, unmerged pull request has the same cleanup only after its unique work was deliberately
discarded or copied to a named retained ref; otherwise retain and report that branch.

Cleanup is complete only when the primary worktree is clean on updated `main`. The local branch
inventory contains only `main`, branches for open pull requests, intentional `prototype/*` evidence
branches, and reported branches whose unique commits prevented safe deletion. The worktree
inventory contains the primary worktree plus worktrees for those retained branches; every dirty
worktree is reported.

## Disclosing AI assistance

We build with AI agents, and every commit says so. Every commit **you write**, and your
pull-request body, ends with attribution trailers as its **last, contiguous** lines:

```
Assisted-by: <the exact model that helped>
Co-authored-by: <bare name> <verified address>
```

`Assisted-by:` names the model — `Claude Opus 4.8`, `GPT-5-Codex` — with an effort suffix only
when one was explicitly set. `Co-authored-by:` is added **only** for a model whose vendor address
is known to be real: Claude (`noreply@anthropic.com`), Codex (`noreply@openai.com`), Copilot
(`198982749+Copilot@users.noreply.github.com`). Anything else gets `Assisted-by:` alone, because
a guessed address credits a stranger.

Wrote it yourself? Then it is `Assisted-by: none`, and no second trailer. **Every** commit says
who helped, including the ones where the answer is nobody — because a rule where silence sometimes
means "a human wrote this" and sometimes means "somebody forgot" discloses nothing either time.
It costs a word, and it is the only word this rule has ever asked you to invent.

The conformance check reads this on every pull request and goes red on a commit that is missing
its trailer or carries an unrecognised `Co-authored-by:` address. **The commits GitHub writes are
not yours, and the rule does not reach them** — the squash commit that lands on `main` and the
`Merge branch 'main' into …` commit the **Update branch** button writes are the platform's text,
written after every check has passed and editable by nobody. The check skips a merge commit and
is never run over `main`.

## What gets a change rejected

Not much, and none of it is about style — formatting and lint are automated so they are never a
review topic. The recurring three are: it does something the issue did not ask for; it explains
in a comment what the code should have said in a name; or it leaves the repository's `MAP.md`
describing a layout that no longer exists.

## Conduct and security

Behaviour is governed by our [Code of Conduct](CODE_OF_CONDUCT.md) — conduct@jeromegroup.org.
Vulnerabilities go to security@jeromegroup.org and never into a public issue; the full policy is
[SECURITY.md](SECURITY.md).

Never commit a credential. The conformance check scans every pull request for one and fails on a
hit — but it runs **after** the push, which is the whole thing to understand about it: by the time
it goes red, the credential has reached GitHub's servers, the Actions log and any clone taken
since. A force-push takes it off the branch and out of nothing else.

So the response to that check failing is, in order: **rotate or revoke the credential**, then take
it out of the code, then rewrite the branch. The first step is the fix and the other two are
tidying up. If the value is not a credential at all — a fixture, a documented example — say so on
the line itself with a `# gitleaks:allow` comment, which is an assertion that the value opens
nothing, so make sure it does.

**One credential has no first step, and it is the one that matters most.** The **team key** is
*displayed* by the IN-CYPHER board rather than minted by it — no generate control, no rotate
control — and the organisers publish no working channel to ask
([#36](https://github.com/jerome-queck/incypher-ctf/issues/36)). There is nothing to revoke, so a
leak is simply not recoverable, which makes it the one secret here to handle as if the scan did
not exist. It lives only in the active IN-CYPHER `.env`; a practice board's `.env` omits it, so
that Run never holds it.
