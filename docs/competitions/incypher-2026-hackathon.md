# IN-CYPHER 2026 Hackathon

The event this repository exists for. This is our reading of it; the canonical rules are captured
as normalized rendered text and the platform guide as normalized rendered text plus link targets in
[`incypher-2026-hackathon.rules.txt`](incypher-2026-hackathon.rules.txt) and
[`incypher-2026-hackathon.how-to-play.txt`](incypher-2026-hackathon.how-to-play.txt), so a change
to either is a diff rather than a thing nobody noticed.

Two pages are canonical and they are not the same page. The [organisers' hackathon
page][official] carries the rules, prizes and eligibility. The platform's own [How to
play][how-to-play] carries the operational claims — where Challenges live, how to connect, what
the Team key is — and defers to the first for anything canonical. The rules baseline was captured
on 21 August; the complete How-to-play baseline was captured on 5 September.

[official]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/
[agenda]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/hackathon-agenda/
[how-to-play]: https://hackathon.in-cypher.com/how-to-play

## The board

| | |
|---|---|
| Platform | CTFd at `https://hackathon.in-cypher.com`, with `ctfer-io/ctfd-chall-manager` |
| Practice window | 15 practice Challenges released 14 Sep; Board now closes **23 Sep 18:00 SGT** |
| Online build week | Practice set from **14 Sep 10:00 SGT**; platform guide now moves the ADK to 21 Sep |
| Scored run | **Conflicting sources:** Imperial agenda says 22 Sep 10:30–16:00; platform guide/Board run to 23 Sep 18:00 |
| Flags | Guide says `INCYPHER{…}`; three Isolated descriptions still say `flag{…}`; final profile must accept both |
| Scoring | **Unannounced.** "Full scoring details will be announced closer to the event day." |
| Teams | One to four; university students. Score is recorded against the team. |
| Categories | Web, pwn, crypto, reversing, forensics "and more", several set in medical scenarios |
| Stakes | SGD 1,500 / 1,000 / 500, and the first-place team presents at the conference |

## Release observed on 14 September

The [practice-release research](../research/2026-09-14-in-cypher-practice-release.md) supersedes the
pre-release observations below where they conflict. The actual release is explicitly non-scoring:
15 practice Challenges across web, pwn, network, crypto, rev, forensics and misc; nine
`dynamic_iac` Isolated Challenges and six `standard` Static Challenges. The exact Solver transport
enumerates all 15 with the held token. Every Isolated Challenge states a 3,600-second TTL, zero mana
cost, `shared: false`, `destroy_on_flag: false` and no attempt cap; practice mana total remains zero.

The set is useful live compatibility evidence, not a hidden competition corpus. One description
publishes its exact Flag, others are stale or truncated, and the competition set remains hidden
until 22 September. ADR-0059 therefore binds the final v2 rehearsal to this Board while preserving
the claim boundary: it proves live operation, not hidden-set composition or official rank.

## How-to-play claims and provenance

This is the complete low-resolution ledger of claims in the 14 September How-to-play capture.
**Organiser-stated** means only that the platform page says it. **Observed** means a dated read of
the live Board. **Inferred** means our conclusion from a stated or observed fact. The exact wording
and order live in the [capture](incypher-2026-hackathon.how-to-play.txt), not in this summary.

| Claim | Provenance | Boundary |
| --- | --- | --- |
| The platform is where the agent runs; non-scoring practice opens on 14 Sep, the ADK follows on 21 Sep, and the hidden scored set opens on 22 Sep and runs to 23 Sep 18:00 SGT. | **Organiser-stated.** | “Where the agent runs” does not say who owns the host or starts the container; the end time conflicts with Imperial's agenda. |
| Teams contain one to four participants; one participant creates a named/passworded team, the others join it, and score belongs to the team. | **Organiser-stated; `userMode: "teams"` separately observed.** | Team mode and team scoring do not prove who owns a deployed Instance. |
| Every member can view the Team key under Settings → Access Tokens; it identifies the team, crosses the raw-TCP gate, and must remain inside the team. | **Organiser-stated.** | The page does not call the Team key an API token, and the two credentials are not interchangeable. |
| Static Challenges provide downloads or one shared service and one Flag for everyone. | **Organiser-stated; six practice Static Challenges observed.** | No Static competition-batch Challenge is visible yet. |
| An Isolated deployment is labelled “per-player” and gives the deployer a container and private address; all nine practice Instances state a 3,600-second TTL. | **Organiser-stated; practice TTL observed.** | Whether team mode makes the held Instance personal or team-owned remains unresolved until a real deployment or organiser clarification. |
| Raw-TCP Targets use `nc 47.236.162.54 <port>` and a provided Python `solver.connect` helper to cross a Team-key-bound PoW gate. | **Organiser-stated.** | The helper/ADK and PoW algorithm are not linked yet, so neither has been observed. |
| Web Targets use `https://<token>.in-cypher.com/`; the unguessable subdomain provides isolation and no PoW gate is used. | **Organiser-stated.** | No live Web Instance has been observed. |
| Flags match `INCYPHER{…}` and may be submitted on the Challenge page or through the platform API; an Isolated Flag is unique to the Instance, and another Instance's Flag does not score. | **Organiser-stated.** | Three released Isolated descriptions still state `flag{…}`. The final profile must tolerate both while the competition wrapper remains untested. |
| Agents are autonomous during the Run; only provided Challenge systems are in scope; defensive research only; do not attack the Board, other teams or shared infrastructure; do not share Flags or solutions. | **Organiser-stated**, with the page deferring canonical authority to the official hackathon page. | Drift in either source matters; both baselines are checked below. |
| The published Discord is the organiser discussion channel. | **Organiser-stated link; observed stale.** | The invite API returned expired on the 5 September recheck; publication does not prove usability. |
| The page says it is powered by CTFd. | **Organiser-stated page identity.** | This establishes platform family, not an exact CTFd or plugin version. |

### Current chall-manager observations

At **2026-09-05T14:16Z**, safe authenticated GETs preserved these current facts without retaining
identity fields or taking Instance lifecycle action:

- **Observed:** the mana API returned the exact 51-byte body summarized as `used: 0, total: 0`
  (SHA-256 `ab8674c7f58d7b0a5b3b785ee9293d1c37e073376958958d5531c6071d8584d1`).
- **Inferred from the observed response and upstream chall-manager semantics:** mana is disabled in
  this practice configuration. The organisers do not use the word “mana” on the How-to-play page;
  the competition-batch total remains unknown.
- **Observed:** the HTML ledger redirects a bare API-token request to login. After that token had
  established a session, the authenticated ledger was 37,931 bytes, declared `userMode: "teams"`,
  carried non-zero user and team markers, rendered its Instance table, and held zero rows. Its
  request-varying SHA-256 was
  `07c98d125611b066d7076ccb5c4b1ab415489a35d5afd7be14549facdbaca687`.
- **Boundary:** zero rows is an observation, not proof that the backend is healthy or authority to
  call any unexplained Instance orphaned. No Challenge was deployed, renewed or terminated, so
  per-Team ownership, Instance TTL and lifecycle behavior remain unproved on a real Instance.

The preceding day's wider receipts and the chall-manager empty-ledger failure mode are preserved in
the [live-surface recheck](../research/2026-09-05-in-cypher-live-surface-recheck.md#board-and-api-receipts)
and [How the Solver learns which Instances it holds](https://github.com/jerome-queck/incypher-ctf/issues/156).

**The tracked profile is [`incypher-2026-hackathon.board.json`](incypher-2026-hackathon.board.json)**:
5.5 hours, closing 22 Sep 16:00 SGT. That follows the Imperial agenda and the selected rehearsal
duration; the Board now advertises 23 Sep 18:00 SGT instead. The Solver records both rather than
silently replacing one authority with the other.

**The competition-day batch is Challenges we have never seen.** The 15 released Challenges are
explicitly practice-only and remain available on site; the scored set is revealed on 22 September.
Practice tuning must therefore generalise rather than become a claim about the hidden set.

## What the rules forbid the Solver

Four rules, and three of them bind the machine rather than the operator:

- **Any human intervention during the run is penalised.** Stated on the rules page, in the key
  information block ("no human intervention is allowed"), and again on the agenda against the
  10:30–16:00 block. It is the design constraint the whole repository is built around.
- **Only the provided challenge systems are in scope; responsible, defensive research only.**
- **No attacking the platform, other teams, or shared infrastructure.** This is IN-CYPHER's
  version of the constraint BrunnerCTF spells out as a ban on broad automated enumeration, and it
  binds the same way: the Solver's tool inventory may not point scanning or fuzzing at anything
  that is not the challenge it was handed, and the board itself is never a target.
- **No sharing flags or solutions during the event.**

Note what is *not* forbidden: the model. "There are no restrictions on how. You can use any Large
Language Model (LLM) such as GPT, Claude, Gemini, or a local model, and any tools or frameworks
you like."

## Competition day: where the container runs

This section is the resolution of [#13][issue13]. Each finding is marked **stated** (the
organisers say it), **inferred** (it follows from what they say, but they never say it) or
**unknown**. Nothing here is a stated answer to the question as asked — the organisers have
published no runtime-environment spec — so the inferences carry the weight, and the questions in
[Ask the organisers](#ask-the-organisers) are how they get confirmed.

[issue13]: https://github.com/jerome-queck/incypher-ctf/issues/13

### 1. Who runs the image — we do, on our own hardware, on the venue network

**Inferred, high confidence.** The organisers describe a room full of teams running their own
machines, and never once describe a handover:

- Day 1 check-in is "connect to the venue network, confirm platform access" ([agenda][agenda]) —
  *we* bring hardware onto *their* network.
- Day 1 is "Get your agent running against the live competition platform" — this one is on the
  [official page][official], under *How it works*, not on the agenda.
- Day 2, 09:30–10:00, is "Doors open and final setup — Arrival and attendance check. **Perform
  final checks on the agents you built.**" You cannot perform final checks on an image you handed
  over a week earlier.
- There is **no submission deadline anywhere in the agenda** — no upload step, no image drop, no
  registry push.
- The day-1 workshop covers "how the platform, team key, interface connection, and demo agent
  work" — the things you configure on a run you operate yourself.

The one sentence pointing the other way is "You submit your agent as a Docker container." Read
against the agenda, that names the *form* the deliverable takes, not a transfer of custody — it
is the sentence that makes the container mandatory, not the sentence that says who starts it.

**Consequence:** runtime `--env-file` injection is available, so the credential posture in
[`../credentials.md`](../credentials.md) survives intact and no secret needs to be baked into a
layer. Build for that. But the inference is not a guarantee, so keep the image *handover-shaped*
anyway: everything except secrets inside the image, and a documented set of what it needs from
outside. That costs nothing if we run it ourselves and saves the entry if we are wrong.

**Handover-shaped stopped meaning "environment variables only" when
[ADR-0011](../adr/0011-the-sanctioned-path-is-the-only-path.md) landed**, and the ask on the day has
to match. Codex no longer authenticates by an environment variable but by a **file** under
`CODEX_HOME`, written by an interactive login. So the image needs **three** things from outside, all
three verified in [#49](https://github.com/jerome-queck/incypher-ctf/issues/49):

1. the documented environment variables, by `--env-file` or `-e`;
2. a **writable host mount** for `CODEX_HOME`, or the login does not survive a restart;
3. **one interactive login inside the container before the run** — `codex login --device-auth`,
   approved on a phone, which is setup rather than **Intervention**.

Ask for all three, not just the first. Without the native login, native inference refuses; it never
falls through to metered billing. [#54](https://github.com/jerome-queck/incypher-ctf/issues/54)
holds the contingency for the case where a shell is refused.

### 2. Egress — outbound internet is available, because the challenges are on it

**Inferred, high confidence**, and this is the finding that most changes the picture. The
challenge infrastructure is not on the venue LAN:

- Raw-TCP challenges are at **`47.236.162.54`** — a public **Alibaba Cloud** address
  (`NetName: AL-3`, `OrgName: Alibaba Cloud LLC`), named on the How-to-play page.
- Web challenges are at **`https://<token>.in-cypher.com/`** — public DNS, public TLS.
- The board itself is `hackathon.in-cypher.com`, behind Cloudflare.

The Solver cannot reach a single challenge without routing to the public internet. A venue network
that blocked egress would block the competition, so egress exists.

**The residual risk is narrower than "no egress": it is an allowlist.** A venue could permit only
challenge infrastructure while blocking the ChatGPT/Codex backend needed by both native Codex and
CPA. Day 1 must probe the exact selected routes from inside the container; no local-model or
third-party API contingency is part of the Solver.

### 3. Resource caps — unstated, and probably ours to choose

**Unknown, leaning "no organiser-imposed cap".** No CPU, RAM, GPU or disk limit appears on any
published page, and if finding 1 holds the caps are simply our laptop's — a 48 GB M4 Pro. The one
budget-shaped limit the organisers name is an **instance time limit** — "Instances have a time
limit; redeploy if yours expires" — with no value given. **Mana** is *not* theirs: the word appears
on none of the four canonical pages, and we know of it only because the board runs
`ctfd-chall-manager`, whose model charges it. So treat mana as a property of the plugin we have
inferred is in play, not as a published rule — and add its total to the questions below.

If we are wrong about finding 1 and organisers run the image, a cap becomes likely and the
local-model path dies with it — a 48 GB working set is not something a shared runner grants. That
coupling is why question 1 below is the one to ask first.

### 4. A supervisor is not human intervention — but confirm it

**Unknown as stated; defensible on a plain reading.** The rule penalises *human* intervention. A
PID-1 process that restarts a crashed worker involves no human, and the agenda all but requires
the container to survive alone: the run is 10:30–16:00 with **"Agents keep running"** printed
against both the 12:30–13:30 lunch and the 14:30–15:00 tea break. Roughly an hour and a half of
the 5.5-hour run is unattended *by the organisers' own schedule*.

**Consequence, and it holds either way:** the container restarts itself and never needs a
keyboard. The risk is not really the rule — it is that humans are in the room for the whole run,
so a hand on a keyboard at 13:00 is visible and judged whatever it was doing. Build the supervisor
in, and plan on touching nothing. Ask anyway (question 4) as soon as there is a channel to ask on
— see [Ask the organisers](#ask-the-organisers), where the published Discord invite turns out to
be expired — since it settles whether a crash-restart loop is defensible or merely plausible.

### 5. Live platform settings — partly answerable already

The practice board answered unauthenticated, so some of this did not need to wait for 14 September.
Read 21 Aug 2026 — and see [what changed on 24 August](#what-changed-on-24-august-2026), which
took the two practice Challenges out of the list:

- **chall-manager is live** — `/plugins/ctfd-chall-manager/instances` is in the board's own nav.
- **`max_attempts: 0`** on both practice challenges: no per-challenge attempt cap on this batch.
- **Scoring is static on the practice batch** — `function: "static"`, with `initial`, `decay` and
  `minimum` all null. Not evidence for the scored batch, which is unannounced.
- **Files are served on-platform**, `/files/<hash>/<name>` — *unlike* BrunnerCTF, whose
  `/files/…` 302s to object storage. The redirect-following seam built for Brunner is still
  needed, but this board does not exercise it.
- **The scoreboard reads empty** — `/api/v1/scoreboard` returns zero teams while both challenges
  show five solves. Scoreboard-driven triage cannot assume the endpoint carries anything.
- **CTFd is 3.8.0 or later** — challenge JSON carries `rating`, `ratings`, `solution_id`,
  `solution_state` and `logic`, and the theme is Alpine + Bootstrap 5. Upstream's changelog adds
  `solution_id` and `logic` to `/api/v1/challenges/<id>` in **3.8.0 (2025-09-04)**, so those
  fields put a *floor* under the version, not a ceiling: every later release carries them too, and
  3.8.2 was out in Feb 2026. Treat it as "3.8+, exact version unknown".

Still unknown, and still ADK- or event-gated: the exact CTFd version,
`incorrect_submissions_per_min` (`/api/v1/configs` is admin-only — assume CTFd's default of 10
wrong submissions per minute), the mana total, and the instance TTL.

#### What changed on 24 August 2026

`GET /api/v1/challenges`, browser UA and `Content-Type: application/json`, unauthenticated, now
answers **200 with `{"success": true, "data": []}`**, and `/api/v1/challenges/8` answers 404. The
two practice Challenges are gone from the list. Resolution of [#59][issue59]; everything below was
read on 24 Aug 2026, with BrunnerCTF Global as a live control and CTFd's own source at tag
`3.8.0` as the reference for what each handler does.

**Nothing below rests on the empty list, or on the 404.** The next section shows that this board
cans both: its collection endpoints do not reliably reach CTFd, and its 404s are byte-identical
across routes that should answer differently. Those are the two weakest readings here, not the
strongest. **One endpoint carries the whole section** — `/api/v1/challenges/8/solves`, which answers
**200 with five dated solve rows**, a body no interposed layer invents. Findings are marked
**measured** (read off the wire) or **inferred** (it follows from CTFd's source, but the board never
said it), as the rest of this section marks its own; where a reading is measured but its *meaning*
comes from the source, it says both.

- **Not an event-state change — measured 200, inferred meaning.** `ChallengeSolves.get` is decorated
  `@during_ctf_time_only`, so its 200 places the reading inside the board's own event window.
  `window.init` agrees: `start` 1782835200 and `end` 1790006400 are **1 Jul 2026 00:00 SGT** to
  **22 Sep 2026 00:00 SGT**, the window recorded at the top of this file, unchanged.
- **Not a `challenge_visibility` flip to private — measured 200, inferred meaning**, and that is
  what map [#11][issue11]
  guessed when it corrected itself in place. `ChallengeSolves.get` also carries
  `@check_challenge_visibility`, `@check_account_visibility` and `@check_score_visibility`, each
  of which aborts **403** for an unauthenticated request carrying `Content-Type:
  application/json`. It answered 200 with real rows, so all three visibilities are still
  **public**. Brunner is the live control on the same CTFd generation: its challenge list *is*
  private, and it answers exactly that 403.
- **Not deleted, and not `hidden` — measured 200, inferred meaning.** `ChallengeSolves.get` aborts 404 when the
  Challenge's `state` is `hidden`. Both id 8 and id 15 answer 200, so both rows are still in the
  database and neither is hidden.
- **`state: "locked"` — a hypothesis, and no longer the only one.** `Challenge.get` selects a
  non-admin's Challenge with `state != "hidden" AND state != "locked"` and 404s otherwise, and
  `hidden` is ruled out above, so `locked` fits. **But the 404 it rests on is not CTFd's.** This
  board returns a *byte-identical* body for `/api/v1/challenges/8`, `/api/v1/challenges/999999`,
  `/api/v1/teams/4` and `/api/v1/scoreboard/top/10` — one MD5 across all four, including a route
  taking no id — while CTFd's `first_or_404` appends the requested URI and a suggested rule, which
  Brunner returns and this board never does. The interposed layer of the next section explains that
  404 as readily as `locked` does, and it is the simpler cause because we have already proven the
  layer exists. **Both fit; the state is undetermined.**

**Why that reads as a consistent answer here and as a contradiction for teams**, which is the
first objection to raise against it. CTFd's three *challenge* handlers filter differently:
`get_all_challenges` and `Challenge.get` exclude `hidden` and `locked`, while `ChallengeSolves`
excludes only `hidden`. A single value of `state` therefore satisfies all three observations at
once. CTFd's three *team* handlers filter **identically** — `hidden` and `banned` in every one —
so the team readings in the next section cannot be satisfied by any value at all. Same shape of
evidence, opposite verdicts, and the asymmetry in the handlers is the whole reason.

**They were live until days before.** Breadcrumbs' fifth solve is dated **2026-08-21T04:19Z** and
Telltale Beacon's **2026-08-20T09:21Z** — and [#12][issue12] and [#14][issue14] recorded
Breadcrumbs at *four* solves on 21 Aug, so it gained one after that reading.

**The team account does not reopen the board — measured, 24 Aug 2026.** We hold a registered
account on this arena (`.env.incypher`), and it authenticates: `/api/v1/users/me` and
`/api/v1/teams/me` both answer **200**, naming user 108 on team 63 with four members. Authenticated,
against the same board in the same minute:

| Request | Authenticated result |
| --- | --- |
| `/api/v1/challenges` | 200, **0 items** |
| `/api/v1/challenges?field=probe-is-not-a-field&q=a` | **200** — CTFd would answer 400 |
| `/api/v1/challenges/8` | 404 |
| `/api/v1/challenges/8/solves` | 200, **5 rows** |
| `/api/v1/scoreboard` | 200, 0 items |
| `/api/v1/teams` | 200, 0 items |

**The suppression is not about being logged out.** A fully authenticated team member gets the same
canned empty collections and the same failing control as a stranger, which rules out the reading
that this is a visibility setting or an edge rule aimed at anonymous traffic. It also settles the
practical question: **an account does not make this arena readable**, so the calibration the map
planned against it cannot happen here today, and the practice window closes 22 Sep 00:00 SGT.

It does not separate `locked` from the layer — an authenticated non-admin is shown nothing under
either — so the Challenges' state stays undetermined.

#### Participant enumeration is suppressed by something that is not CTFd's config

The empty scoreboard noted above is the visible corner of a larger one. On this board
`/api/v1/teams`, `/api/v1/users`, `/api/v1/scoreboard` and `/api/v1/scoreboard/top/<n>` do not
behave like CTFd 3.8's handlers at all:

| Request | IN-CYPHER arena | Brunner, same CTFd generation |
| --- | --- | --- |
| `/api/v1/teams` | `{"success": true, "data": []}`, no `meta` | `meta.pagination`, 1521 teams |
| `/api/v1/teams?field=bogusfield&q=a` | 200, the same empty body | **400** from `validate_args` |
| `/api/v1/teams/4` | **404** | 200 |
| `/api/v1/scoreboard/top/10` | **404** | 200 |

One contradiction is internal and needs no control: `/api/v1/challenges/8/solves` lists team 4 by
name, and `get_solves_for_challenge_id` filters `banned` and `hidden` accounts *out* — while
`TeamPublic.get` 404s for a banned or hidden team — and, being `first_or_404()`, for an id that
does not exist at all, so it is not *only* those two. Team 4 demonstrably exists, which is what
makes it a contradiction.
`TeamList` returns a `PaginatedAPIListSuccessResponse` unconditionally, so a body carrying no
`meta` is not its output either. The application is up behind the same prefixes —
`/api/v1/challenges/types` and `/api/v1/teams/me` answer with CTFd's own 403 — and both
`account_visibility` and `score_visibility` are **public**, because `ChallengeSolves.get` carries
both guards and answers 200.

The cause is not determinable from outside: an edge rule, a plugin and a fork all fit. The
consequence is what binds the Solver — **on this board `{"success": true, "data": []}` is not
evidence of an empty collection.** It arrives with `success: true`, the right `Content-Type`, no
redirect and no 403: every shape a client would read as an honest answer. **When it started is
not established.** It is tempting to read the empty scoreboard of 21 Aug as the same effect and
conclude this is not a competition-week regression — but `/api/v1/challenges` is a collection
endpoint too and it carried two real Challenges that day, so the suppression was either absent then
or was never uniform across collections. Both readings fit, and nothing here separates them.

[issue11]: https://github.com/jerome-queck/incypher-ctf/issues/11
[issue12]: https://github.com/jerome-queck/incypher-ctf/issues/12
[issue14]: https://github.com/jerome-queck/incypher-ctf/issues/14
[issue59]: https://github.com/jerome-queck/incypher-ctf/issues/59

**One finding here belongs to the credential posture, not to this ticket — and it is now
settled.** How-to-play says "Every member can see the team key under **Settings → Access
Tokens**", and the page was read on 22 Aug 2026. The team key and the CTFd API token are **two
different values that merely share a page**, and they are opposites on the properties that matter:
the CTFd token is minted by us, per-board and revocable; the team key is **displayed rather than
minted, with no generate control and no rotate control**. So it is not "issued by the organisers"
as [`.env.example`](../../.env.example) said — you read it off the board — but nor can we replace
it, which makes it the one secret in `docs/credentials.md` whose leak cannot be undone. Corrected
in [#41](https://github.com/jerome-queck/incypher-ctf/issues/41); the posture itself is
[#20](https://github.com/jerome-queck/incypher-ctf/issues/20).

## Ask the organisers

**The Discord invite the organisers publish is expired, so there is currently no working channel
to ask on.** <https://discord.com/invite/MKUvNVE5> is the link, and it is the only Discord URL the
board carries — in the logged-in hero block on the landing page, and under *Join the discussion* on
[How to play][how-to-play]. It does not resolve:

```console
$ curl -s https://discord.com/api/v10/invites/MKUvNVE5
{"message": "Invite is expired.", "code": 50270}   # HTTP 404 — re-checked 24 Aug 2026
```

**Check it with the API, never by opening the URL.** `https://discord.com/invite/<code>` returns
**HTTP 200 whatever the code's state** — Discord serves its app shell to every invite URL and
renders the invalid notice client-side — so fetching the page proves the link is *published* and
nothing more. That is exactly how this doc got it wrong on the first pass: an earlier revision
said "the Discord is live" on the strength of the link appearing in the board's markup, and
[#13][issue13]'s original finding that the invite was dead was correct all along.

This matters because it is the only channel the organisers name for questions — "Have a question?
Ask us in the official Discord channel" — and no invite appears on either Imperial page. **Until a
fresh invite is published, the questions below have nowhere to go.** Three fallbacks, in order:

- **Imperial Global Singapore's events team**, `eventsigsingapore@imperial.ac.uk`, or the general
  `igsingapore@imperial.ac.uk`. Neither is hackathon-specific and neither is offered as a support
  channel, so a reply is not owed — but a dead invite in the organisers' own footer is worth
  reporting whatever else is asked.
- **The practice/build week**, which the agenda says runs "with online support" — whatever channel
  that turns out to be is the one these questions belong in.
- **The day-1 workshop**, 11:30–12:15 on 21 Sep, "a walkthrough of how the platform, team key,
  interface connection, and demo agent work". Questions 1, 2 and 5 are answerable there by
  observation alone, even if nobody answers them out loud.

Re-run the API check on the same three mornings as the drift script (see [Before
playing](#before-playing)).

Ask these, in this order, on whichever channel opens first. The first two decide the most.

1. **On competition day, do we run our own container on our own machine on the venue network, or
   do you take the image and run it on your infrastructure?**
2. **Will the venue network allow the selected native Codex and private CPA routes, or is egress
   restricted to the challenge infrastructure?**
3. **Are there CPU / RAM / GPU / disk limits on the agent container, and any cap on concurrent
   deployed instances beyond mana?**
4. **Does an automated restart count as human intervention?** If the container's supervisor
   restarts a crashed worker with nobody touching it, is that penalised?
5. **What are the instance time limit and — if chall-manager's mana is in play at all — the mana
   total?** Mana is our inference from the plugin, not a term the organisers have used.
6. **What are the submission rate limits** — wrong-flag submissions per minute, and any
   per-challenge attempt cap on the competition batch?
7. **When will scoring be published?** Whether it is static, dynamic, or first-blood weighted
   changes how the Solver orders challenges.
8. **The Discord invite on the platform is expired — what is the current one?** Ask this first if
   the route you found was email, because it is what unblocks the other seven.

Answers land in this file, and any that changes a decision goes back to the map as a new ticket.

## Before playing

```bash
sh scripts/check-rules-drift.sh docs/competitions/incypher-2026-hackathon.rules.txt
sh scripts/check-rules-drift.sh docs/competitions/incypher-2026-hackathon.how-to-play.txt
```

`source unchanged: <url>` and exit 0 means that source's normalized span matches its committed
baseline; drift prints a diff and exits 1. The script is never silent on success, so **silence
means it did not run** — check each exit status rather than reading nothing as reassurance. Run
both commands at each build-session start, on the morning of 21 September, and again before the Run
on 22 September. Scoring and duration now conflict across first-party sources, so at least one
source should change and the change is one we must read.

To accept a reviewed change, rerun the drifting source with `--update`, then inspect the exact
baseline diff before committing it:

```bash
sh scripts/check-rules-drift.sh docs/competitions/incypher-2026-hackathon.how-to-play.txt --update
git diff -- docs/competitions/incypher-2026-hackathon.how-to-play.txt
```

The update intentionally exits 1 because drift existed. The post-update command without
`--update` must then print `source unchanged` and exit 0. For baselines with `Links: yes`, the
same diff also watches normalized link destinations; unchanged anchor text cannot hide a changed
Challenge route, Discord invite or external destination.

Re-check the Discord invite on the same mornings — the API call, not the URL:

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://discord.com/api/v10/invites/MKUvNVE5
```

`200` means it finally works; `404` means still expired (see [Ask the
organisers](#ask-the-organisers)).

Any ADK, scoring, duration or Challenge delta now enters final v2 directly under ADR-0059. These
commands detect the boundary crossing; the current-code audit decides whether it requires behavior.
