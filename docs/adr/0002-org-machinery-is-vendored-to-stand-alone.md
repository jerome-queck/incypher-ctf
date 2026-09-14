# The org's machinery is vendored so this repository stands alone

> **Revisited by ADR-0060.** The predicted public transition occurred. Vendoring remains, while a
> repository-local ruleset now restores required checks without restoring a hub dependency.

This repository was generated from the `Jerome-Group/private-template`, which is one repository in
an organisation held together by a management hub: a public `.github` repository serving a reusable
conformance workflow, a private Terraform hub owning a closed label set and a "Baseline" ruleset,
and community-health files inherited org-wide. None of that follows a repository out of the
organisation. This is a **personal** repository, on the **Free** plan, and it wanted to keep the
organisation's *conventions* without the organisation behind them.

The decision: **keep the conventions by vendoring their enforcement into this repository, and cut
every dependency on the hub.** Concretely —

- **Conformance is vendored, not called.** `conformance/` holds six of the hub's eight checker
  scripts verbatim and a manifest this repository owns. The scripts are unmodified; the manifest is
  edited to name this repository's own files. The two dropped scripts are the required-checks
  *gating* pair (`check-gating.sh` and `resolve-required-checks.sh`), because gating reads a
  ruleset a Free private repository cannot have (ADR-0003).
- **The label set is recreated by hand** and its two-axis discipline kept, but nothing reaps
  additions now — see `docs/agents/triage-labels.md`.
- **Community-health files are committed in-repo** (CODE_OF_CONDUCT, SECURITY, the issue forms and
  PR template) rather than inherited, and their `@jeromegroup.org` contacts kept — they still route
  to the maintainer.
- **The pieces that only served the organisation are dropped**: the hub-calling `conformance.yml`
  wrapper and `auto-merge-conformance-pin.yml` (ADR-0003 explains why the latter cannot work here).

It supersedes, in part, [ADR-0001](0001-decisions-are-recorded-as-adrs.md): that record's
consequence that org-wide decisions live in a hub and reach here through the Baseline is no longer
true — there is no hub, so this `docs/adr/` is the whole of what governs the repository.

## Why not keep calling the hub's public workflow

A reusable workflow in a public repository is callable cross-owner, so this was technically open to
us — and it was rejected. The hub's gating rule compares the repository's workflows against its
required status checks; on a Free private repository that list is always empty, so the rule fails
every pull request forever, and its waivers are keyed to `Jerome-Group/*` names that never match
this repository. The check would be permanently red with no edit available to fix it, because the
manifest that would waive it lives in the hub. Vendoring is what puts the contract where this
repository can hold itself to it.

## Consequences

- **Rule updates stop arriving.** In the organisation, an improved rule reached every repository as
  a pin bump. Here the scripts are a copy that drifts; updating them is a deliberate re-vendor, not
  a Dependabot pull request (`docs/agents/dependencies.md`).
- **This repository can now edit the contract it is held to** — the manifest is in the tree. For a
  hub-governed repository that was forbidden on purpose; for a solo private repository it is fine,
  and it is the price of standing alone.
- **Provenance is kept honest.** `CONTEXT.md` defines the **Seed** as a historical source, not a
  live authority, and the manifest's `originals` names it rather than a repository this tree could
  be mistaken for.

## Revisit when

- This repository goes public or the plan changes: rulesets and required checks become available,
  the gating rule can be restored from `Jerome-Group/.github`, and the conformance check can be a
  genuinely required context rather than an advisory one.
- The upstream conformance scripts gain a rule worth having: re-vendor from the template and record
  what changed.
