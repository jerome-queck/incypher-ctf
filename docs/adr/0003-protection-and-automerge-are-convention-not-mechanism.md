# Branch protection and auto-merge are convention here, not mechanism

> **Historical private-repository decision.** ADR-0060 supersedes its protection and secret-
> scanning consequences now that the repository is public. Manual squash/attribution conventions
> remain; public rulesets now enforce the minimum merge gate.

This repository is **private** on the GitHub **Free** plan, with four collaborators. Two platform
features the organisation's workflow leaned on are unavailable in that combination, confirmed
against GitHub's own documentation at setup:

- **Branch protection and rulesets are not enforced on a Free private repository** — they are a
  paid feature there. So "a pull request is required, nobody pushes to `main`, a red check blocks
  the merge" cannot be *enforced*; GitHub will let a collaborator push to `main` and merge past a
  red check.
- **Pull-request auto-merge is not available on a Free private repository** — it needs Pro or
  above. So the `allow_auto_merge` setting is inert, and `gh pr merge --auto` cannot queue a bump
  behind a check.

The decision: **keep the conventions, and enforce them socially rather than mechanically.**

- The pull-request-first, squash-only, red-is-a-stop rules stay, written down in `CONTRIBUTING.md`
  and `AGENTS.md` as rules the four contributors keep — not walls the platform builds. With a
  small, trusted team on a short-lived-then-ongoing repository, that trade is acceptable; the cost
  of the alternatives (paying for Pro, or a fork-only flow with no write access) is not worth it
  for the protection bought.
- **`auto-merge-conformance-pin.yml` is deleted, not adapted.** It is doubly dead: auto-merge does
  not exist here, and once conformance is vendored (ADR-0002) there is no pin left to bump.
- The conformance **gating** rule is dropped for the same root cause — there are no required checks
  to gate against (ADR-0002).
- What *is* replicable is applied by hand via the API: squash-only merges, `COMMIT_MESSAGES` squash
  format (which is what makes the attribution-trailer rule matter on `main`), delete-branch-on-merge,
  and allow-update-branch. These are plain repository settings with no plan gate.

## Consequences

- **`main` is technically pushable and a red check is technically mergeable.** The check outputs
  are advisory. This is the standing risk the team accepts; a contributor who ignores a red check
  is the failure mode, and there is no backstop but review.
- Secret scanning is *not* one of the losses: GitHub's native scanning is also paid on private
  repositories, and the vendored gitleaks rule in `conformance/` replaces it — which is why that
  rule is more load-bearing here than it was in the organisation.
- A single gitleaks scanner, not two: the vendored conformance rule already scans, so no separate
  CI scanning job was added.

## Revisit when

- The repository goes public (rulesets and auto-merge become free) or the plan is upgraded to Pro
  or above (auto-merge and private rulesets become available). At that point the Baseline's
  branch-protection and required-check rules can be imported via `gh api /repos/.../rulesets`, and
  the gating rule restored (ADR-0002).
