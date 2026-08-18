# Dependency updates: what an agent does with a Dependabot pull request

The policy is
[ADR-0010](https://github.com/Jerome-Group/org/blob/main/docs/adr/0010-dependency-update-policy.md),
which says why. This is the part of it an agent carries out.

This file is seeded verbatim into every repository the Organisation generates, so its one link
to the policy is absolute: a relative path would resolve to a `docs/adr/` that has no ADR-0010
in it. The policy is Organisation-wide and lives in the management hub, which is where the link
points.

## Surface them, twice

Any session that will touch a pull request lists the open Dependabot pull requests at its
**start** and again at its **end**:

```sh
gh pr list --state open --author app/dependabot \
  --json number,title,mergeStateStatus,statusCheckRollup
```

Say nothing when the list is empty — an empty report every session is noise, and noise is what
stops being read.

**Start** is not a formality. `strict_required_status_checks_policy` (ADR-0004) means a pull
request that is merely behind the default branch cannot merge, so every merge to `main` stales
every open bump. Landing a bump before the session's own work costs one rebase; landing it
afterwards costs two.

**End** catches what the session itself created, and hands over what the agent may not decide.

## Merge it, or hand it over

An agent may merge a Dependabot pull request **only** when all five hold:

- **this repository has opted in** — it carries `dependabot-auto-merge.yml`, the workflow that
  queues a Dependabot bump on this repository's *own* green tick. A repository that has not opted
  in auto-merges nothing, and neither does an agent standing in for it. The repository *setting*
  `allow_auto_merge` is not the opt-in and never was a reliable reading of one: every repository
  in the Organisation now has it on, because it is the queue a different workflow needs
  (ADR-0045). What a repository will merge unattended is decided by which workflows it carries;
- the ecosystem is `github-actions`;
- the bump is **patch or minor**;
- the required check is **green**;
- the diff touches **nothing but the pin and its version comment**.

Everything else goes to the Owner with a one-line reason. That is every `terraform` bump whatever
the semver, every major, anything red, and anything editing more than the pin — including a bump
that also rewrites a workflow's inputs.

In an opted-in repository the last four conditions are also enforced by
`.github/workflows/dependabot-auto-merge.yml`, which queues the same set without waiting for a
session. They are one rule with two encodings: change one and change the other.

The opt-in is the condition that does not travel. This file is seeded into every repository, and
the reason `github-actions` is safe to merge on a green tick is that *this* repository's check
genuinely fails when an action bump misbehaves. A template-born repository whose CI is still a
skeleton has a green tick that means nothing, and must not inherit "green ⇒ merge" for free —
ADR-0010 makes that the whole basis of the split.

## Landing one

Whoever merges, the shape is the same:

1. **Rebase rather than merge `main` in** — `gh pr comment <n> --body '@dependabot rebase'`. The
   branch stays a single Dependabot-signed commit, so the green tick describes the tree that
   lands rather than a merge of it. Dependabot also does this unasked when `main` moves, which is
   the only reason a queued auto-merge survives a moved `main`: GitHub itself will not update a
   pull request that has fallen behind, auto-merge or not (ADR-0010).
2. **One at a time.** Two bumps editing the same file mean the second must be rebased again after
   the first lands.
3. **Verify the SHA against the upstream ref**, not against the pull request body:
   `gh api repos/<owner>/<action>/commits/<tag> --jq .sha`. Resolve the tag to a *commit* —
   `git/ref/tags/<tag>` returns the tag object's own SHA for an annotated tag, which will not
   match the pin.
4. **Keep the pin a commit SHA with a version comment.** A bump that leaves a bare tag behind is
   not done.
5. **For a major, read what it actually changed** before merging — the release notes, not the
   tick.
