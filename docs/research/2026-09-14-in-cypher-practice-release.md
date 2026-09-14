# IN-CYPHER practice release — 14 September 2026

## Question and method

What did IN-CYPHER actually release at the stated 14 September boundary, and which repository
assumptions does the release confirm or contradict?

The read ran **2026-09-14T05:42–05:47Z** (**13:42–13:47 SGT**), after the advertised 10:00 SGT
warm-up opening. Sources were first-party only: the [official event page][official], [official
agenda][agenda], [platform landing page][landing], [platform guide][guide], and live participant
Board/API. The current `.env` credentials were used without printing or retaining them. All
challenge metadata and file checks below were reads. No Instance was deployed, renewed, restarted,
or destroyed; no attachment body was retained.

One method blemish must remain visible: `scripts/ctfd_probe.py` was initially run without its
`--no-attempt` switch. It submitted its built-in deliberately wrong probe string once to
**Parcelport**. CTFd answered HTTP 200 with verdict `incorrect`; Parcelport has `max_attempts: 0`,
so no finite attempt allowance was consumed. The probe stopped before any Instance lifecycle
action because the local probe recorder already had a live writer.

[official]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/
[agenda]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/hackathon-agenda/
[landing]: https://hackathon.in-cypher.com/
[guide]: https://hackathon.in-cypher.com/how-to-play

## Result

**The 14 September release is a 15-Challenge, explicitly non-scoring practice set. It is not the
previously assumed first competition batch, and no ADK or runnable demo was released with it.**

The platform guide now says:

- the 14 September set is a **warm-up/practice set which does not count toward competition score**;
- the practice set remains in use on 21 September;
- the **ADK moves to 21 September at 10:00 SGT**;
- the real competition set is revealed on 22 September at 10:00 SGT; and
- the competition runs until **23 September at 18:00 SGT**.

Those claims are internally supported by the platform theme's active countdown times and the
Board's changed `window.init.end=1790157600`, exactly 23 September 18:00 SGT. They contradict the
still-current [official agenda][agenda], which says the ADK and runnable demo arrive online on
14 September and the autonomous competition runs 22 September 10:30–16:00 SGT. The [official event
page][official] and tracked canonical rules span are unchanged. **There is therefore no single
authoritative duration at this observation:** the Board/guide say a 32-hour platform window;
Imperial's official agenda says a 5.5-hour scored Run. The repository must not silently choose one.

The platform itself is also internally stale: the [landing page][landing] still visibly says
“First challenges”, “21–22 Sep”, and “22 Sep · rest of challenges open”, while its injected
countdown script uses the new practice/ADK/competition/end milestones. The platform guide is the
coherent new operational statement.

## What was released

Authenticated [`GET /api/v1/challenges`](https://hackathon.in-cypher.com/api/v1/challenges)
returned **15 visible Challenges**, 4,181 bytes, SHA-256
`678920df71f6a4a9d6e281e378ac91052ac0292ec586e571681f474f4d1a284e`. Categories are Board-owned
open strings and all carry a `(Practice)` prefix.

| Category | Count | Challenges |
| --- | ---: | --- |
| `(Practice) web` | 4 | Parcelport, Mustache Trap, Schema Ghost, SSTI |
| `(Practice) pwn` | 2 | Overflow Ward, Handoff |
| `(Practice) network` | 2 | Handshake, Pacemaker Protocol |
| `(Practice) crypto` | 1 | Trust Anchor |
| `(Practice) rev` | 1 | Combination |
| `(Practice) forensics` | 3 | Dear Diary, Zip, Second Opinion |
| `(Practice) misc` | 2 | Oracle's Riddle, Telltale Beacon |

The set is deliberately balanced by displayed value: **five each at 100, 250, and 500 points**.
All 15 have `max_attempts: 0`. None is solved by this team at observation time.

### Every Challenge

The “shape” column summarizes only the Challenge's own statement; it is not a solution claim.
API detail links require a participant identity.

| ID | Challenge | Category | Type / value | Stated shape | Attachment |
| ---: | --- | --- | --- | --- | --- |
| [42](https://hackathon.in-cypher.com/api/v1/challenges/42) | Parcelport | web | `dynamic_iac` / 500 | smart-locker web authorization | none |
| [106](https://hackathon.in-cypher.com/api/v1/challenges/106) | Overflow Ward | pwn | `dynamic_iac` / 250 | remote memory corruption | `overflow-ward-handout.zip` (974,856 B) |
| [72](https://hackathon.in-cypher.com/api/v1/challenges/72) | Handoff | pwn | `dynamic_iac` / 500 | heap use-after-free | `handoff` (17,144 B) |
| [109](https://hackathon.in-cypher.com/api/v1/challenges/109) | Handshake | network | `dynamic_iac` / 250 | custom TCP protocol | none |
| [80](https://hackathon.in-cypher.com/api/v1/challenges/80) | Pacemaker Protocol | network | `dynamic_iac` / 500 | medical-device length-framed binary TCP | none |
| [19](https://hackathon.in-cypher.com/api/v1/challenges/19) | Mustache Trap | web | `dynamic_iac` / 100 | restricted template evaluation | none |
| [24](https://hackathon.in-cypher.com/api/v1/challenges/24) | Schema Ghost | web | `dynamic_iac` / 100 | undocumented GraphQL API | none |
| [68](https://hackathon.in-cypher.com/api/v1/challenges/68) | SSTI | web | `dynamic_iac` / 250 | filtered Jinja2 SSTI | none |
| [17](https://hackathon.in-cypher.com/api/v1/challenges/17) | Trust Anchor | crypto | `standard` / 100 | fictional signed firmware packages | 7 files; 7,518 B total |
| [7](https://hackathon.in-cypher.com/api/v1/challenges/7) | Combination | rev | `standard` / 250 | sequence-checked binary | `combination` (14,472 B) |
| [90](https://hackathon.in-cypher.com/api/v1/challenges/90) | Dear Diary | forensics | `standard` / 100 | ext4 journal recovery | `dear-diary.img.gz` (68,303 B) |
| [94](https://hackathon.in-cypher.com/api/v1/challenges/94) | Zip | forensics | `standard` / 100 | ZIP metadata/CRC | `zip.zip` (219 B) |
| [11](https://hackathon.in-cypher.com/api/v1/challenges/11) | Second Opinion | forensics | `standard` / 500 | DICOM discarded-image data | `study.dcm` (13,034 B) |
| [33](https://hackathon.in-cypher.com/api/v1/challenges/33) | Oracle's Riddle | misc | `dynamic_iac` / 250 | five-trial TCP oracle | none |
| [15](https://hackathon.in-cypher.com/api/v1/challenges/15) | Telltale Beacon | misc | `standard` / 500 | detected-envelope radio signal | `capture.wav` (37,604 B) |

All 14 attachment URLs answered HEAD 200 from the Board host, without a redirect. The largest is
under 1 MiB. That confirms the existing inline-download path on this set but does not retire
off-host redirect support needed by other Boards.

## Live Board and runtime interfaces

### Authentication and collection behavior

The current `.env` CTFd token **is valid when used through the Solver's exact transport contract**:
browser User-Agent, `Accept: application/json`, `Content-Type: application/json`, `Authorization:
Token …`, and redirects disabled. [`GET /api/v1/users/me`](https://hackathon.in-cypher.com/api/v1/users/me)
answered 200 JSON for the expected participant/team, and the same client enumerated all 15
Challenges and read every detail.

A generic `Mozilla/5.0` urllib client was instead redirected to login; because that client followed
the redirect, it appeared as HTTP 200 HTML. The public/generic challenge collection still returned
the exact synthetic 30-byte empty JSON seen before. **That is a client-profile/authentication
difference, not evidence that the stored token is invalid.** The release positively re-validates
the Solver's strict User-Agent/content-type/no-redirect rules.

For the valid Solver client, the deliberately invalid collection query is rejected by CTFd and
challenge enumeration is real. Participant enumeration remains interposed:

| GET | Observed result |
| --- | --- |
| `/api/v1/challenges` | 200 JSON, 15 Challenges |
| `/api/v1/challenges/types` | 403 JSON |
| `/api/v1/scoreboard` | 200, exact synthetic empty collection |
| `/api/v1/scoreboard/top/10` | 404 |
| `/api/v1/teams` | 200, exact synthetic empty collection |
| `/api/v1/users` | 200, exact synthetic empty collection |

Thus Intake's challenge path is usable now, but empty scoreboard/team/user data still cannot be
treated as an empty fact.

### Static and Isolated Challenges

The release contains **nine `dynamic_iac` Isolated Challenges and six `standard` Static
Challenges**. Every Isolated detail says:

- `shared: false`;
- `timeout: 3600` — the previously unknown practice Instance TTL is exactly one hour;
- `mana_cost: 0`;
- `destroy_on_flag: false`; and
- no `connection_info` until deployment.

The authenticated [mana endpoint](https://hackathon.in-cypher.com/api/v1/plugins/ctfd-chall-manager/mana)
still returns `used: 0, total: 0` (51 bytes; SHA-256
`ab8674c7f58d7b0a5b3b785ee9293d1c37e073376958958d5531c6071d8584d1`), so mana remains disabled
for practice. Safe GETs against all nine Instance resources returned 404 absent, and the HTML
ledger contained zero held rows. No lifecycle mutation was made, so actual deploy/renew/restart/
destroy replies, connection-address shapes, PoW behavior, and per-player versus team ownership
remain unverified.

The [platform guide][guide] still promises raw TCP at `47.236.162.54:<port>` behind a Team-key-bound
PoW gate and web Instances at `https://<token>.in-cypher.com/`. It still shows `from solver import
connect`, but no helper, ADK, demo-agent repository, archive, or download is linked from the official
page, agenda, landing page, or guide at observation time.

### Points and solve counts

All six Static Challenges use CTFd `function: static`. All nine Isolated Challenges report
`function: logarithmic`, but each has `initial == minimum == current value`, so their displayed
score cannot decay under the current configuration. Practice scoring is therefore effectively
static. This proves nothing about the hidden competition set, whose scoring is still unpublished.

Every Challenge already shows one to five solves. Most solve timestamps predate the public release
by weeks; three gained a solve around 12:32–12:58 SGT on 14 September. These totals include
pre-release/test activity and cannot be treated as participant difficulty or velocity evidence.
The participant scoreboard remains suppressed.

## Content-quality findings

The set is official practice material, but it is visibly rough and answer-bearing:

- **Zip publishes a full write-up and an exact accepted `INCYPHER{…}` flag in its own description.**
  It is an Exposed practice Challenge, not discovery evidence.
- **Dear Diary says its attachment is pending**, despite the attachment being present and
  downloadable.
- **Pacemaker Protocol ends mid-wire-format at `payload:`**, so its advertised binary protocol is
  incomplete.
- The platform guide changes the wrapper to `INCYPHER{…}`, and five Static descriptions agree,
  but three Isolated descriptions still say `flag{…}`. The remaining Challenges state no wrapper.
  The release therefore does not support a one-wrapper `flag{…}` profile; it also does not support
  trusting every Challenge's prose over the guide.
- Medical framing appears in network, crypto, forensics, and misc Challenges, confirming that a
  healthcare **Scenario is not a Category**. `network` and `rev` also confirm that Categories must
  stay open strings.

## Assumption ledger and specification consequences

| Repository assumption before release | 14 September evidence | Consequence |
| --- | --- | --- |
| The 14 September batch is the first competition batch, reused on-site. | Guide now explicitly calls it non-scoring practice; the real set is hidden until 22 September. | Separate official-practice qualification from competition claims. Do not infer hidden-set composition from these 15. |
| ADK and runnable demo arrive 14 September. | Nothing is linked; guide moves ADK to 21 September while official agenda still says 14 September. | ADK/PoW integration remains gated. Record the source conflict and recheck continuously rather than implementing an imagined kit. |
| The scored Run is 22 September 10:30–16:00, 5.5 hours. | Official agenda still says this; guide, countdown, and Board end now say 23 September 18:00. | The configured 5.5-hour deadline remains defensible only from Imperial's agenda, not as a settled cross-source fact. Seek organiser clarification before changing scored duration. |
| Practice Board closes 22 September 00:00. | Board end moved to 23 September 18:00. | The released set remains available for a full live 5.5-hour rehearsal/Gate window; update any close-time guard that treats the old Board time as truth. |
| Flags use `flag{…}`. | Guide says `INCYPHER{…}`; several released descriptions agree, several stale descriptions conflict. | The IN-CYPHER profile/wrapper must recognize the canonical new form. Preserve challenge-prose inconsistency as evidence rather than treating `flag{…}` as authoritative. |
| Practice Instance TTL, mana and per-challenge limits are unknown. | TTL 3,600 s; mana total/cost zero; `max_attempts: 0`; `destroy_on_flag: false`. | These values can parameterize official-practice runs only. Hidden competition settings remain unknown and must still be discovered at Intake. |
| Challenge collections may be synthetically empty. | Correct Solver transport now gets 15 real rows and passes the collection-control check; generic/public clients still see login/empty behavior. | Existing strict transport and read-contract control are confirmed. Do not generalize the success to scoreboard or participant collections. |
| Healthcare may need a dedicated category route. | Medical scenarios span four ordinary categories. | Existing Scenario-versus-Category domain separation holds; healthcare knowledge can assist routing without becoming a hard category enum. |
| The local rig alone supplies a real chall-manager Gate surface. | Official practice now supplies nine real `dynamic_iac` Challenges with one-hour terms. | The released official dataset can exercise live Intake, files, scheduling, and real Instance semantics. It cannot replace hidden/evaluator-controlled isolation evidence, and its exposed answers cannot qualify discovery. |

For the user's stated planning decision—**v2 is the final competition candidate**—this evidence is
best treated as a late compatibility and scope-pruning input to v2, not deferred into a fictional
later version. A live 5.5-hour run over this official practice dataset can now test the real Board
and category/tool surface without losing solving functionality. It must remain labelled a
practice compatibility run: the public set is answer-bearing, the competition set and scoring are
hidden, and real Instance lifecycle/PoW were deliberately not mutated during this research read.

## Still unknown / next observations

- Which duration controls the scored competition: Imperial's 5.5 hours or the platform's 32-hour
  end window.
- When the ADK and runnable demo will actually be linked, and their hashes/interfaces.
- Real deploy, renew, restart, destroy, address, PoW, and ownership behavior on these nine
  Isolated Challenges.
- Hidden competition category/type/value mix, scoring, TTL, mana, attempt/rate limits, and wrapper
  enforcement.
- Whether the published Discord is repaired. Its still-published invite API answered 404 expired
  at observation time.

Recheck the guide, agenda, Board window, Challenge list, and Discord before every live run and at
the 21 and 22 September boundaries. The disagreement is active external state, not documentation
cleanup.
