# Public repository rights reservation — 14 September 2026

## Question and result

Which licensing posture best fits publishing the Solver for GitHub Actions capacity while
withholding permission to reuse it?

**Recommendation: an explicit proprietary, all-rights-reserved notice for rights the project
controls, with prior-license, third-party, GitHub Terms and applicable-law exceptions.** This
states the intended boundary clearly; it cannot make publicly readable code impossible to copy.
Without a license, copyright already reserves rights by default, but a prominent notice removes
ambiguity about whether a license was accidentally omitted. [GitHub licensing guidance][licensing]
[Choose a License: no permission][no-permission]

Public visibility, rather than an open-source license, is the documented condition for free
standard GitHub-hosted Actions runners. Larger runners remain chargeable. Self-hosted runners are
also free of Actions usage charges, so publication is a cost/workflow choice, not a GitHub
requirement. [Actions billing][billing]

## Limits that the notice must preserve

- **GitHub rights:** public repositories grant users viewing and forking rights, including use,
  display, performance and reproduction through GitHub functionality. GitHub and affiliates also
  receive hosting/copying and AI-training rights under the current Terms. A repository notice
  cannot withdraw those grants. [GitHub Terms D.3–D.5 and J.3][terms]
- **Earlier MIT versions:** the inspected tree at `510f995eddfa80743781bf589273e7d11e9c4dfe`
  has an MIT `LICENSE`, naming Jerome Queck; it has remained MIT since the initial August commits.
  MIT grants broad reuse rights subject to preserving its notices. Replacing today's license
  does not erase the license attached to older copies. **Publishing this Git history also
  exposes MIT-licensed revisions of the Solver.** That practical limit remains even if the new
  default-branch notice reserves all rights. [Existing license][old-license] [MIT terms][mit]
- **Other people's material:** ownership is not established by repository admin access. Existing
  permissively licensed contributions can remain included under their original notice
  obligations; their authors' rights cannot simply be claimed as exclusively Jerome's. Future
  contributions to an all-rights-reserved project need explicit permission sufficient for team
  development and distribution. [GitHub's legal guide][legal]
- **Lawful exceptions and retained copies:** copyright exceptions can authorize some uses;
  public forks and local copies do not disappear if visibility later becomes private.
  [Choose a License][no-permission] [GitHub licensing guidance][licensing]

The local author log records Jerome under two email addresses, plus model attribution trailers.
This is evidence of commit authorship, not a complete ownership audit. `skills-lock.json` also
identifies vendored `mattpocock/skills`; other vendored material and bundled dependencies retain
their own terms. GitHub's contribution default licenses additions under the repository's existing
license unless a separate agreement applies. [GitHub Terms D.6][terms]

## Why the common alternatives miss the requirement

| Option | Consequence |
| --- | --- |
| MIT, Apache, GPL or AGPL | Open-source licenses permit redistribution and derived works; copyleft cannot prohibit copying. [OSI definition][osi] |
| Business Source License 1.1 | Expressly permits copying, modification, redistribution and non-production use, with a later change to an open-source license. [Publisher's text][bsl] |
| Elastic License 2.0 | Permits use, copying, distribution and derivatives, subject chiefly to service, license-key and notice restrictions. [Publisher's text][elastic] |
| No license / explicit rights reservation | Gives no additional general reuse permission, subject to the exceptions above. The explicit notice communicates that choice most clearly. [Choose a License][no-permission] |

These source-available alternatives restrict some uses; none supplies the requested blanket
copying ban. The recommendation is a rights-reservation notice, not an OSI-approved license.

## Competition boundary

The [tracked official rules](../competitions/incypher-2026-hackathon.rules.txt) require a Docker
container and prohibit sharing flags or solutions during the event. The inspected span specifies
no public-source or open-source-license requirement. A license change must therefore leave
organizer submission permission explicit and must not be treated as permission to publish
challenge solutions. The official event page returned HTTP 403 to this research fetch; this
competition observation is limited to the repository's captured first-party rules, not a fresh
full-rule verification. [Official event page][event]

## Implementation boundary

Keep prior MIT and upstream notices reachable; scope the new reservation to rights actually
controlled; document team/contributor and organizer permissions; update public/private claims
only to match observed repository visibility. Changing the notice alone cannot deliver
exclusive control over the old MIT code. History rewriting, deleting releases, changing
visibility, and contacting contributors were outside this research task and were not performed.

[licensing]: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/licensing-a-repository
[no-permission]: https://choosealicense.com/no-permission/
[billing]: https://docs.github.com/en/billing/concepts/product-billing/github-actions
[terms]: https://docs.github.com/en/site-policy/github-terms/github-terms-of-service
[old-license]: https://github.com/jerome-queck/incypher-ctf/blob/510f995eddfa80743781bf589273e7d11e9c4dfe/LICENSE
[mit]: https://choosealicense.com/licenses/mit/
[legal]: https://opensource.guide/legal/#what-if-i-want-to-change-the-license-of-my-project
[osi]: https://opensource.org/osd
[bsl]: https://mariadb.com/bsl11/
[elastic]: https://raw.githubusercontent.com/elastic/elasticsearch/main/licenses/ELASTIC-LICENSE-2.0.txt
[event]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/
