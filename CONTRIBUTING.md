# Contributing

This is the Jerome Group default. It applies to every repository in the organisation that has
not committed its own copy, so it describes the flow rather than any one project's build.

## Before you write code

**Open an issue first.** Not as a formality — as the cheaper half of the work. An issue is where
the shape of a change gets argued, and a pull request that arrives without one is a proposal and
an implementation fused together, so disagreeing with the proposal means throwing away the
implementation.

Pick the form that fits: a **Task** proposes a change you already know you want; a **Bug**
reports something behaving wrongly. A question about *whether* something should be done at all
belongs in the repository's own decision record, not here.

Every issue lands on `needs-triage` and is then moved to exactly one of:

| Label | What it means |
|-------|---------------|
| `needs-triage` | Waiting to be evaluated. Every issue starts here. |
| `needs-info` | Answered questions will move it forward; it is parked until you reply. |
| `ready-for-agent` | Fully specified. An AI agent may pick this up and finish it unattended. |
| `ready-for-human` | Specified, but needs a person — credentials, judgement, or a manual check. |
| `wontfix` | Deliberately not doing this. The comment says why. |

There is no `in-progress` label. Whether something is being worked on is a question for the
issue thread.

## Sending a change

You will be working from a fork unless you have been given push access. Branch from the default
branch; there is no naming convention to get wrong.

**A change is finished when its pull request is open, not when the commit exists.** That is true
of a one-line fix as much as of a feature, and it is the job of whoever wrote the change —
including an agent working unattended, whose own instructions may well stop at "commit". Push the
branch and open the pull request. Nothing is merged by doing so, and an open pull request is the
only form in which work here can be looked at at all.

The default branch of every repository here is protected the same way:

- **A pull request is required.** Nobody pushes to it directly.
- **Zero approvals are required, and that is not an oversight** — the organisation has one
  maintainer, so a required approval would be a second reviewer who does not exist. The pull
  request itself is the review surface.
- **A repository with CI requires its check to pass.** That is set per repository, once there is
  a check worth requiring, so whether it applies to yours is visible on the pull request rather
  than promised here.
- **Merges are squashes, and history is linear.** Your branch does not need to be tidy; it needs
  to be one coherent change. If it is two changes, send two pull requests.
- **Every review comment must be resolved before merge**, including your own.

So: keep it small, describe *why* in the body — the diff already says what — and link the issue
with `Closes #123`.

## Disclosing AI assistance

Every commit **you write**, and your pull-request body, ends with attribution trailers as its
**last, contiguous** lines:

```
Assisted-by: <the exact model that helped>
Co-authored-by: <bare name> <verified address>
```

`Assisted-by:` names the model — `Claude Opus 4.8`, `GPT-5-Codex` — with an effort suffix only
when one was explicitly set. `Co-authored-by:` is added **only** for a model whose vendor address
is known to be real: Claude (`noreply@anthropic.com`), Codex (`noreply@openai.com`), Copilot
(`198982749+Copilot@users.noreply.github.com`). Anything else gets `Assisted-by:` alone, because
a guessed address credits a stranger.

Wrote it yourself? Then it is `Assisted-by: none`, and no second trailer. **Every** commit says who
helped, including the ones where the answer is nobody — because a rule where silence sometimes
means "a human wrote this" and sometimes means "somebody forgot" discloses nothing either time
(ADR-0041). It costs a word, and it is the only word this rule has ever asked you to invent.

**The commits GitHub writes are not yours, and the rule does not reach them.** It writes two, both
after every check has passed and neither editable by anybody: the *squash* commit that lands on
`main`, where an aggregated `Co-authored-by:` block is appended after a `---------` separator and
your trailers end up mid-message; and the `Merge branch 'main' into …` commit that the **Update
branch** button writes when your branch has fallen behind. The check skips a merge commit and is
never run over `main`, so neither is yours to get right — and neither is a licence to leave a
trailer off a commit you did write.

## What gets a change rejected

Not much, and none of it is about style — formatting and lint are automated so they are never a
review topic. The recurring three are: it does something the issue did not ask for; it explains
in a comment what the code should have said in a name; or it leaves the repository's `MAP.md`
describing a layout that no longer exists.

## Conduct and security

Behaviour is governed by our Code of Conduct — conduct@jeromegroup.org. Vulnerabilities go to
security@jeromegroup.org and never into a public issue. GitHub publishes both documents on this
repository, whether it carries its own copies or inherits the Organisation's.

Never commit a credential. The conformance check scans every pull request for one and fails on a
hit — but it runs **after** the push, which is the whole thing to understand about it: by the time
it goes red, the credential has reached GitHub's servers, the Actions log and any clone taken
since. A force-push takes it off the branch and out of nothing else.

So the response to that check failing is, in order: **rotate or revoke the credential**, then take
it out of the code, then rewrite the branch. The first step is the fix and the other two are
tidying up. If the value is not a credential at all — a fixture, a documented example — say so on
the line itself with a `# gitleaks:allow` comment, which is an assertion that the value opens
nothing, so make sure it does.
