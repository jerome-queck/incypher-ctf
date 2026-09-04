# IN-CYPHER live-surface recheck — 5 September 2026

## Question and method

Has the official event or platform exposed anything materially new since the standing facts on
[wayfinder map **Re-lock the roadmap to the scored run, and specify v2**](https://github.com/jerome-queck/incypher-ctf/issues/153),
measured on 29 August 2026?

The check ran from **2026-09-04T15:56:32Z to 2026-09-04T16:04:46Z**
(23:56:32 SGT on 4 September to 00:04:46 SGT on 5 September). Every platform operation was a
`GET`. Authenticated checks used the already-held team API token without printing it; no Flag was
submitted, no Instance was deployed, renewed, or terminated, and no personal response body was
retained.

## Result

**No new event information, scoring rule, Starter Pack, ADK, demo agent, practice Challenge, or
working hackathon-specific Discord/support channel is exposed yet.** The 29 August operational
picture still holds:

- The first batch still opens **14 September at 10:00 SGT**, the same batch is used on-site on
  21 September, and the remaining Challenges open on 22 September. The scored Run remains
  **22 September, 10:30–16:00 SGT**. The [official page](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/),
  [agenda](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/hackathon-agenda/),
  [platform landing page](https://hackathon.in-cypher.com/), and
  [How to play](https://hackathon.in-cypher.com/how-to-play) agree.
- The canonical rules span is unchanged from the committed 21 August capture, and the official
  page still says that full scoring details will be announced later. `scripts/check-rules-drift.sh`
  returned `rules unchanged` at **2026-09-04T16:00:53Z**.
- The agenda still promises the **Agent Development Kit and runnable demo agent via the hackathon
  website on 14 September**. At **2026-09-04T15:59:19Z**, none of the links on the official page,
  agenda, platform landing page, or How to play page pointed to a Starter Pack, ADK, demo-agent
  repository, or download. This establishes only that none is linked on the published surfaces;
  an unlinked artifact cannot be ruled out.
- The published Discord URL is still `https://discord.com/invite/MKUvNVE5`. The
  [Discord invite API](https://discord.com/api/v10/invites/MKUvNVE5) answered **404**, code
  **50270**, `Invite is expired.` at
  **2026-09-04T15:58:49Z**. No replacement link is published on the checked pages.

The one useful new fact is a **narrower CTFd fingerprint**, not evidence of a recent deployment:
the board's stock-core asset set matches CTFd **3.8.3 through 3.8.7**, rather than merely “3.8+”.
It still does not identify the exact server version.

## Official-page receipts

These hashes are receipts for the bytes observed, not durable version identifiers: the HTML
contains request-varying data such as a CSRF nonce.

| Source | Observed (UTC) | Result | Response receipt | Material content |
| --- | --- | --- | --- | --- |
| [Official hackathon page](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/) | 2026-09-04T15:56:55Z | 200, `text/html; charset=UTF-8` | 49,610 bytes; SHA-256 `dd15964c8a6ccf877d4d3aa99c24bfc5875ac09d0628c6adaa9c8d00efde7d5f` | Dates and rules unchanged; scoring still unannounced. |
| [Hackathon agenda](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/hackathon-agenda/) | 2026-09-04T15:56:56Z | 200, `text/html; charset=UTF-8` | 44,482 bytes; SHA-256 `d5919607bf01c4930a458a1125d8f35a8b7ebd81e076fc0eed1bd59bb9eb1ed9` | ADK/demo remains scheduled for 14 September; Run remains 10:30–16:00 SGT. |
| [How to play](https://hackathon.in-cypher.com/how-to-play) | 2026-09-04T15:56:56Z | 200, `text/html; charset=utf-8` | 46,095 bytes; SHA-256 `d6c8aae8060bda25daa5a41c8ea14d88aa68049993b74c8da53fdef1e416f6b4` | Two-batch schedule, per-player Isolated Instances, unspecified time limit, unique per-Instance Flag, `47.236.162.54`, team-key PoW, and `https://<token>.in-cypher.com/` remain stated. |
| [Platform landing page](https://hackathon.in-cypher.com/) | 2026-09-04T15:56:57Z | 200, `text/html; charset=utf-8` | 42,058 bytes; SHA-256 `b9455f81449eef9f06ae5e5a6ec13c6048f759f598f7fab2f80de221fb3f812f` | Still advertises first Challenges on 14 September at 10:00 SGT. |

The authenticated landing-page `window.init` still carries `start=1782835200` and
`end=1790006400`, i.e. **1 July 2026 00:00 SGT through 22 September 2026 00:00 SGT**. That remains
the practice window, not the separately configured scored Run.

## Board and API receipts

The board is still behind Cloudflare (`Server: cloudflare`, `CF-Cache-Status: DYNAMIC` on the
sampled HTML/API responses). The following are the exact safe summaries of current responses.

| Observed (UTC) | Auth | Source | HTTP / content type | Current content |
| --- | --- | --- | --- | --- |
| 2026-09-04T16:04:31Z | no | [`GET /api/v1/challenges`](https://hackathon.in-cypher.com/api/v1/challenges) | 200 / `application/json` | Exact 30-byte body `{"success": true, "data": []}`; SHA-256 `13885b4929bf8c98465c2eb6a99815901ba66ea6c50aa15fe5c338894ce6312b`. |
| 2026-09-04T16:04:31Z | yes | [`GET /api/v1/challenges`](https://hackathon.in-cypher.com/api/v1/challenges) | 200 / `application/json` | Byte-identical empty body. Authentication does not reveal a batch. |
| 2026-09-04T16:04:32Z | yes | `GET /api/v1/challenges?field=intake-is-not-a-field&q=a` | 200 / `application/json` | Byte-identical empty body. [Stock CTFd constrains `field` to a fixed enum](https://github.com/CTFd/CTFd/blob/3.8.7/CTFd/api/v1/challenges.py#L121-L145) and [returns 400 on validation failure](https://github.com/CTFd/CTFd/blob/3.8.7/CTFd/api/v1/helpers/request.py#L79-L94), so collection suppression/interposition still exists and `data: []` is **not evidence of an empty database**. |
| 2026-09-04T16:04:38Z | no | [`GET /api/v1/challenges/8/solves`](https://hackathon.in-cypher.com/api/v1/challenges/8/solves) | 200 / `application/json` | 5 rows, 636 bytes; newest remains `2026-08-21T04:19:24.482558Z`; SHA-256 `4ba0212e8299d7b07557967cf73020d9e27e3a0d2c9968395788176b12fc300e`. |
| 2026-09-04T16:04:38Z | no | [`GET /api/v1/challenges/15/solves`](https://hackathon.in-cypher.com/api/v1/challenges/15/solves) | 200 / `application/json` | 5 rows, 639 bytes; newest remains `2026-08-20T09:21:39.002082Z`; SHA-256 `861fdf90d826ee9cd619d0a344907272d4efaffea12887ac3ccc905b9bd689aa`. |
| 2026-09-04T16:04:38–39Z | yes | `GET /api/v1/scoreboard`, `/teams`, `/users` | each 200 / `application/json` | Each returns the same exact 30-byte empty body as `/challenges`; the synthetic collection behavior persists. |
| 2026-09-04T16:04:39Z | yes | [`GET /api/v1/users/me`](https://hackathon.in-cypher.com/api/v1/users/me) | 200 / `application/json` | A real identity object with a team link; token remains recognized. No identity fields are reproduced here. |
| 2026-09-04T16:04:39Z | yes | [`GET /api/v1/configs`](https://hackathon.in-cypher.com/api/v1/configs) | 403 / `application/json` | 138-byte message object; participant still cannot read scoring, submission-limit, or exact platform configuration. |
| 2026-09-04T16:04:40Z | yes | [`GET /api/v1/plugins/ctfd-chall-manager/mana`](https://hackathon.in-cypher.com/api/v1/plugins/ctfd-chall-manager/mana) | 200 / `application/json` | Exact values `used: 0`, `total: 0`; 51 bytes; SHA-256 `ab8674c7f58d7b0a5b3b785ee9293d1c37e073376958958d5531c6071d8584d1`. Plugin remains installed; [upstream defines total zero as mana disabled](https://github.com/ctfer-io/ctfd-chall-manager/blob/v0.10.1/api/mana.py#L35-L40), so mana remains disabled on this practice configuration. |
| 2026-09-04T16:04:46Z | yes | [`GET /plugins/ctfd-chall-manager/instances`](https://hackathon.in-cypher.com/plugins/ctfd-chall-manager/instances) | 200 / `text/html; charset=utf-8` | 37,931 bytes; authenticated non-zero viewer marker, genuine ledger table, **0 held rows**. SHA-256 `b6334ef289cd60200f4086a63c19575a83faae9b0de1a154862dbfeb9364fa49`; this page hash varies with request data. |

The two historical solve endpoints returning the same five rows while every collection returns a
canned empty response is the same contradiction documented before the recheck. There is no sign of
a new Challenge drop, but the collection endpoint alone must never be used to make that claim.

The ledger result is consistent with the correction recorded on
[**How the Solver learns which Instances it holds**](https://github.com/jerome-queck/incypher-ctf/issues/156):
the API token authenticates this page, its table currently reports no held Instance, and `/mana`
agrees. **That is not proof strong enough to declare an unexplained Instance orphaned.** The
[official plugin route catches a chall-manager failure and renders an authenticated 200 page with
an empty Instance list](https://github.com/ctfer-io/ctfd-chall-manager/blob/v0.10.1/__init__.py#L211-L244);
with mana disabled, its `unknown` marker is not rendered. A later supervisor therefore cannot infer
“nobody owns this” from an empty ledger or inactivity alone, especially once several solving agents
can hold or imminently use Leases. No state-changing lifecycle probe was attempted, so
deploy/renew/terminate and the real Instance TTL remain unverified.

## Backend/version clue

At **2026-09-04T16:02:12Z**, four board-served core assets were fetched and compared byte-for-byte
with the corresponding files in official CTFd tag `3.8.6`:

| Asset | Bytes | SHA-256 | Equal to upstream `3.8.6` |
| --- | ---: | --- | --- |
| [`main.e9ec7884.css`](https://hackathon.in-cypher.com/themes/core/static/assets/main.e9ec7884.css) | 345,067 | `e9ec7884c3e9a03123fff6d844f0b7269f508b01dc601adc495a5ea9694c6092` | yes |
| [`color_mode_switcher.52334129.js`](https://hackathon.in-cypher.com/themes/core/static/assets/color_mode_switcher.52334129.js) | 779 | `0d090af10a39efabf2f4eac35d9cccb19252b27181c59a6c60d74f30ecaafa77` | yes |
| [`index.94b01c79.js`](https://hackathon.in-cypher.com/themes/core/static/assets/index.94b01c79.js) | 215,536 | `9d1944f1a855f86d3df49fe81f41bae934c288dcb58f9ddffad81701ab04b395` | yes |
| [`page.9d188b19.js`](https://hackathon.in-cypher.com/themes/core/static/assets/page.9d188b19.js) | 88 | `1e910cebfbaf33f08b955b2dda6bb227082313b569b60a28061bcd25d71c157a` | yes |

The board also loads `challenges.ccee2541.js` and `scoreboard.bdd94812.js`. The complete observed
filename set matches the official core asset directories in CTFd tags
[`3.8.3`](https://github.com/CTFd/CTFd/tree/3.8.3/CTFd/themes/core/static/assets) through
[`3.8.7`](https://github.com/CTFd/CTFd/tree/3.8.7/CTFd/themes/core/static/assets), and differs from
tags 3.8.0–3.8.2. The latest official CTFd release at **2026-09-04T16:03:11Z** was
[`3.8.7`, published 19 August 2026](https://github.com/CTFd/CTFd/releases/tag/3.8.7).
Therefore:

- if the board uses an unmodified released core, its core is at least **3.8.3**;
- the assets cannot distinguish 3.8.3, 3.8.4, 3.8.5, 3.8.6, or 3.8.7;
- copied/cherry-picked assets or a custom backend remain possible, so the exact server version is
  still unknown;
- there has been no newer upstream CTFd release since the 29 August map facts.

The latest official `ctfd-chall-manager` release remains
[`v0.10.1`, published 9 June 2026](https://github.com/ctfer-io/ctfd-chall-manager/releases/tag/v0.10.1).
The board exposes no plugin version, so this says nothing about which plugin revision is deployed;
it only establishes that upstream has published no new plugin release since 29 August.

## Consequences and remaining unknowns

- There is **no official-surface reason to move the 14 September integration boundary**. A roadmap
  retargeting v2 to that date is an internal delivery decision, not a response to a newly announced
  organiser deadline.
- Recheck on 14 September at or just after **10:00 SGT**. That is the first stated moment at which
  the ADK, runnable demo, PoW helper implementation, and first batch should become observable.
- Treat an empty ledger as an observation, not orphan authority. Safe reclamation needs an
  ownership/Lease claim whose liveness can be reconciled against the Board; the public plugin
  behavior cannot establish that from inactivity or an empty table alone.
- Still unknown: scoring; exact CTFd and chall-manager revisions; competition-batch attempt and
  submission limits; mana total; Instance TTL; actual PoW algorithm; linked ADK/demo artifact; and
  whether competition-day runtime or network constraints differ from the practice surface.
- Public/API equivalence is only observed at this instant. The CDN or backend could change after
  the check, and authenticated empty collections remain deliberately non-authoritative.
