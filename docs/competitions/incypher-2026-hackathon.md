# IN-CYPHER 2026 Hackathon

The event this repository exists for. This is our reading of it; the rules themselves are
[`incypher-2026-hackathon.rules.txt`](incypher-2026-hackathon.rules.txt), captured verbatim so a
change to them is a diff rather than a thing nobody noticed.

Two pages are canonical and they are not the same page. The [organisers' hackathon
page][official] carries the rules, prizes and eligibility. The platform's own [How to
play][how-to-play] carries the operational facts — where challenges live, how to connect, what
the team key is — and defers to the first for anything canonical. Both were read on 21 Aug 2026.

[official]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/
[agenda]: https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/hackathon-agenda/
[how-to-play]: https://hackathon.in-cypher.com/how-to-play

## The board

| | |
|---|---|
| Platform | CTFd at `https://hackathon.in-cypher.com`, with `ctfer-io/ctfd-chall-manager` |
| Practice window | Open since 1 Jul 2026, closing **22 Sep 00:00 SGT** — midnight before the run |
| Online build week | 14–20 Sep 2026; first batch opens **14 Sep 10:00 SGT** with the ADK |
| Scored run | **22 Sep 10:30–16:00 SGT — 5.5 hours**, on-site at CREATE Tower |
| Flags | `flag{…}` |
| Scoring | **Unannounced.** "Full scoring details will be announced closer to the event day." |
| Teams | One to four; university students. Score is recorded against the team. |
| Categories | Web, pwn, crypto, reversing, forensics "and more", several set in medical scenarios |
| Stakes | SGD 1,500 / 1,000 / 500, and the first-place team presents at the conference |

**The competition-day batch is challenges we have never seen.** Challenges are released in two
batches: the first opens 14 Sep online and is *the same batch* again on-site on day 1, and "the
remaining challenges open" on day 2. Day-1 tuning therefore cannot be tuning against the
challenges that will be scored — whatever the Solver learns on day 1 has to generalise.

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

**Inferred, high confidence.** The [agenda][agenda] describes a room full of teams running their
own machines, and never once describes a handover:

- Day 1 check-in is "connect to the venue network, confirm platform access" — *we* bring hardware
  onto *their* network.
- Day 1 is "Get your agent running against the live competition platform."
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
anyway: everything except secrets inside the image, and a documented set of environment variables
as the only thing it needs from outside. That costs nothing if we run it ourselves and saves the
entry if we are wrong.

### 2. Egress — outbound internet is available, because the challenges are on it

**Inferred, high confidence**, and this is the finding that most changes the picture. The
challenge infrastructure is not on the venue LAN:

- Raw-TCP challenges are at **`47.236.162.54`** — a public **Alibaba Cloud** address
  (`NetName: AL-3`, `OrgName: Alibaba Cloud LLC`), named on the How-to-play page.
- Web challenges are at **`https://<token>.in-cypher.com/`** — public DNS, public TLS.
- The board itself is `hackathon.in-cypher.com`, behind Cloudflare.

The Solver cannot reach a single challenge without routing to the public internet. A venue network
that blocked egress would block the competition, so egress exists.

**The residual risk is narrower than "no egress": it is an allowlist.** A venue could permit
`*.in-cypher.com` and the challenge IP while blocking `api.anthropic.com`. That would be a strange
choice — the organisers explicitly invite GPT, Claude and Gemini, and an allowlist would break
every team that took them up on it — but it is the one egress failure the evidence above does not
rule out.

**Consequence:** build for egress-available and treat the LLM API path as primary. Keep the
local-model path alive as the contingency, not as the plan. **Day 1 is the designed moment to
find out** — it is the same first batch on the live on-site environment, which makes it a dress
rehearsal for exactly this. A `curl` to each provider from inside the container, on the venue
network, on 21 September, converts this whole section from inference to fact with a day in hand.

### 3. Resource caps — unstated, and probably ours to choose

**Unknown, leaning "no organiser-imposed cap".** No CPU, RAM, GPU or disk limit appears on any
published page, and if finding 1 holds the caps are simply our laptop's — a 48 GB M4 Pro. The only
budget-shaped limits the organisers do name are **mana** (chall-manager's charge for holding
Instances) and an **instance time limit** ("Instances have a time limit; redeploy if yours
expires"); neither total is published.

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
in, and plan on touching nothing. Ask anyway (question 4) — the answer costs one Discord message
and settles whether a crash-restart loop is defensible or merely plausible.

### 5. Live platform settings — partly answerable already

The practice board answers unauthenticated, so some of this did not need to wait for 14 September.
Read 21 Aug 2026:

- **chall-manager is live** — `/plugins/ctfd-chall-manager/instances` is in the board's own nav.
- **`max_attempts: 0`** on both practice challenges: no per-challenge attempt cap on this batch.
- **Scoring is static on the practice batch** — `function: "static"`, with `initial`, `decay` and
  `minimum` all null. Not evidence for the scored batch, which is unannounced.
- **Files are served on-platform**, `/files/<hash>/<name>` — *unlike* BrunnerCTF, whose
  `/files/…` 302s to object storage. The redirect-following seam built for Brunner is still
  needed, but this board does not exercise it.
- **The scoreboard reads empty** — `/api/v1/scoreboard` returns zero teams while both challenges
  show five solves. Scoreboard-driven triage cannot assume the endpoint carries anything.
- **CTFd is 3.7-era**: challenge JSON carries `rating`, `ratings`, `solution_id` and
  `solution_state`, and the theme is Alpine + Bootstrap 5.

Still unknown, and still ADK- or event-gated: the exact CTFd version,
`incorrect_submissions_per_min` (`/api/v1/configs` is admin-only — assume CTFd's default of 10
wrong submissions per minute), the mana total, and the instance TTL.

**One finding here belongs to the credential posture, not to this ticket.** How-to-play says
"Every member can see the team key under **Settings → Access Tokens**" — self-served from the
board, not issued by the organisers as [`.env.example`](../../.env.example) currently says, and
sitting in the same place CTFd mints an access token. Whether the team key and the CTFd API token
are one value or two that merely share a page is unresolved, and it matters to
[#20](https://github.com/jerome-queck/incypher-ctf/issues/20); it is question 6 below.

## Ask the organisers

**The Discord is live** — <https://discord.com/invite/MKUvNVE5>, linked from the board's own
footer. It was dead when [#13][issue13] was written; it is not dead now. It is the only channel
the organisers name for questions ("Have a question? Ask us in the official Discord channel"), and
no invite link appears on either Imperial page — the board is where it is published.

Ask these, in this order. The first two decide the most.

1. **On competition day, do we run our own container on our own machine on the venue network, or
   do you take the image and run it on your infrastructure?**
2. **Will the venue network allow the agent outbound HTTPS to third-party LLM APIs
   (api.anthropic.com, api.openai.com, generativelanguage.googleapis.com), or is egress restricted
   to the challenge infrastructure?**
3. **Are there CPU / RAM / GPU / disk limits on the agent container, and any cap on concurrent
   deployed instances beyond mana?**
4. **Does an automated restart count as human intervention?** If the container's supervisor
   restarts a crashed worker with nobody touching it, is that penalised?
5. **What are the mana total and the instance time limit?**
6. **Is the team key the same value as a CTFd access token minted under Settings → Access Tokens,
   or a separate secret that happens to live on that page?**
7. **What are the submission rate limits** — wrong-flag submissions per minute, and any
   per-challenge attempt cap on the competition batch?
8. **When will scoring be published?** Whether it is static, dynamic, or first-blood weighted
   changes how the Solver orders challenges.

Answers land in this file, and any that changes a decision goes back to the map as a new ticket.

## Before playing

```bash
sh scripts/check-rules-drift.sh docs/competitions/incypher-2026-hackathon.rules.txt
```

Silence means nothing changed. Run it before 14 September, again on the morning of 21 September,
and again before the run on 22 September — scoring is explicitly still to be announced, so this
page *will* change at least once and the change is one we must read.

The drift script watches the canonical rules page only. [How to play][how-to-play] is not under
it and carries the facts most likely to move — the batch schedule, the raw-TCP address, the
instance time limit — so read it by eye on the same three mornings.
