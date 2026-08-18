# Dependency updates: what an agent does with a Dependabot pull request

This repository takes Dependabot's `github-actions` bumps (`.github/dependabot.yml`) and lands
them **by hand, one at a time, on a green check**. It auto-merges nothing — the reason is a plan
limit, recorded in [ADR-0003](../adr/0003-protection-and-automerge-are-convention-not-mechanism.md):
pull-request auto-merge is a paid feature on a private repository, so there is no queue to hand a
bump to. Every open bump is somebody's to review and merge.

## Surface them, twice

Any session that will touch a pull request lists the open Dependabot pull requests at its
**start** and again at its **end**:

```sh
gh pr list --state open --author app/dependabot \
  --json number,title,mergeStateStatus,statusCheckRollup
```

Say nothing when the list is empty — an empty report every session is noise, and noise is what
stops being read.

**Start** is not a formality: GitHub will not merge a pull request that has fallen behind `main`,
so every merge to `main` stales every open bump. Landing a bump before the session's own work
costs one rebase; landing it afterwards costs two. **End** catches what the session itself
created.

## Merge it, or hand it over

An agent may merge a Dependabot pull request when all four hold:

- the ecosystem is `github-actions`;
- the bump is **patch or minor**;
- the check is **green** — and green means it, because this repository's CI genuinely fails when
  an action bump misbehaves;
- the diff touches **nothing but the pin and its version comment**.

Everything else goes to a human with a one-line reason: every major, anything red, and anything
editing more than the pin — including a bump that also rewrites a workflow's inputs.

There is no auto-merge workflow to enforce this in parallel (ADR-0003), so the four conditions are
a checklist a person actually runs, not a second encoding of a machine rule. That is the whole
difference from the organisation this repository came from, where a workflow queued the same set
unattended.

## Landing one

1. **Rebase rather than merge `main` in** — `gh pr comment <n> --body '@dependabot rebase'`. The
   branch stays a single Dependabot-signed commit, so the green check describes the tree that
   lands rather than a merge of it. Dependabot also does this unasked when `main` moves.
2. **One at a time.** Two bumps editing the same file mean the second must be rebased again after
   the first lands.
3. **Verify the SHA against the upstream ref**, not against the pull request body:
   `gh api repos/<owner>/<action>/commits/<tag> --jq .sha`. Resolve the tag to a *commit* —
   `git/ref/tags/<tag>` returns the tag object's own SHA for an annotated tag, which will not
   match the pin.
4. **Keep the pin a commit SHA with a version comment.** A bump that leaves a bare tag behind is
   not done.
5. **For a major, read what it actually changed** before merging — the release notes, not the
   check.

## The pins Dependabot does not see

The conformance rules in `conformance/` are vendored shell scripts, not a pinned `uses:` reference
([ADR-0002](../adr/0002-org-machinery-is-vendored-to-stand-alone.md)). Dependabot bumps `uses:`
pins in the workflows and nothing else, so it will never open a pull request to update the
conformance scripts. Updating them is a deliberate re-vendor from the upstream template, not an
automated bump — worth remembering when a rule there is known to have improved upstream.
