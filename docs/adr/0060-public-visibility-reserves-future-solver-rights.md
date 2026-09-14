# Public visibility reserves future Solver rights

On 14 September 2026 the repository became public to use GitHub's free standard hosted runners.
Visibility was already `PUBLIC` when this decision was implemented. The tree still carried MIT,
GitHub native secret scanning and push protection were disabled, no ruleset existed, and CI's one
serial Python job exceeded this repository's deliberate ten-minute bound.

The decision has four parts.

1. **Reserve controlled rights prospectively.** Replace top-level MIT with an explicit proprietary,
   all-rights-reserved notice. It grants no new use, copying, modification, distribution, model-
   training, benchmarking or competition permission. Named collaborators work under express Owner
   authorisation; organisers receive only the permission needed to execute and judge a submission.
2. **State the unavoidable exceptions.** GitHub's Terms still govern hosting, viewing and public
   forks. Separately licensed material keeps its terms. Lawful exceptions remain. Commit
   `510f995eddfa80743781bf589273e7d11e9c4dfe` and its ancestors were published under MIT; recipients
   keep those rights. The new notice protects new revisions rather than pretending to revoke the
   old grant.
3. **Turn on public controls.** Enable GitHub native secret scanning and push protection, retain
   the independent redacted PR-history scan, and require pull requests plus the exact CI and
   conformance contexts through a public-repository ruleset. Auto-merge stays off; squash and
   attribution remain deliberate acts.
4. **Keep the ten-minute CI contract.** Public Actions capacity solves billing, not the manual job
   timeout. #300 separates static checks and partitions every tracked Python test into four named,
   exhaustive shards. A coverage guard refuses an unassigned or duplicate future test; the bound
   stays visible in the workflow and `CODING_STANDARDS.md` rather than being raised.

## Publication boundary

Public source makes operational evidence a separate decision. Raw Run state, active-event flags,
credentials, private Board responses and solution material remain outside git. A public evidence
artifact must be sanitized and approved after the event's disclosure restriction ends. Existing
expired Brunner gate evidence remains; its flags were already published under the earlier MIT
history.

At transition, GitHub's public secret-scanning API reported no alerts. The pinned local scanner
examined all 176 commits and reported 62 `generic-api-key` matches, all introduced by the one
#299 Crypto-profile commit and confined to deterministic crypto fixtures, their generated
inventories/receipts and retained qualification evidence. No provider-specific credential or
other commit was reported. Native scanning and push protection were then enabled; the redacted
local history check remains defence in depth.

## Why not another source-available licence

Business Source License and Elastic License both permit copying and specified uses. Open-source
licences necessarily permit redistribution and derived works. No licence/default copyright would
reserve rights, but an explicit notice makes the intended boundary visible to readers and tools.
The supporting primary-source comparison is
`docs/research/2026-09-14-public-repository-rights-reservation.md`.

## Consequences

- This repository is public-source and proprietary, not open source.
- A GitHub fork is technically available but supplies no permission beyond GitHub operation.
- Use of a revision covered by the new notice in IN-CYPHER is unauthorized; this is a copyright
  reservation, not a click-through agreement.
- Older MIT revisions remain legally reusable. History rewriting, deleting a fork or later making
  the repository private cannot withdraw copies or rights already received.
- Third-party/vendored notices remain mandatory and the top-level notice claims no ownership over
  them.
- ADR-0003 remains history for the private period; this ADR supersedes its live protection and
  secret-scanning consequences. ADR-0009's flag-publication consequence is amended.

## Revisit when

- A lawyer supplies replacement language or ownership/contributor facts change.
- GitHub changes the public-repository grants or Actions billing condition.
- The repository returns private after competition; preserve the notice and old-MIT limitation.
