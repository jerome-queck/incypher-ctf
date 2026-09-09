# BrunnerCTF 2026 — Global

The Solver's first real test, now retained as historical evidence. This is our reading of the event;
the rules themselves are
[`brunnerctf-2026-global.rules.txt`](brunnerctf-2026-global.rules.txt), captured verbatim so a
change to them is a diff rather than a thing nobody noticed.

## The board

| | |
|---|---|
| Platform | CTFd at `https://global.brunnerctf.dk` |
| Window | 21 Aug 14:00 CEST → 23 Aug 14:00 CEST — **20:00 SGT Fri → 20:00 SGT Sun** |
| Current status | **Ended.** The authenticated Challenge surface returned 403 on 5 Sep 2026. |
| Flags | `brunner{.*}` — the body is *any* text, not just hex or alphanumerics |
| Scoring | Dynamic (value falls with solves); the Onboarding track is static |
| Teams | Team-based, no size limit |
| Categories | Web, Crypto, Forensics, Rev, Pwn, OSINT, and whatever else ships |
| Stakes | CTFtime points. No prizes on this platform. |

**The tracked profile is [`brunnerctf-2026-global.board.json`](brunnerctf-2026-global.board.json)**,
and it states no `closes_at`. It is preserved as the input to the promoted historical Brunner Runs,
whose scored window ended on 23 August. On 5 September a fresh browser login reached an authenticated
Challenge page stating that the event had ended and returning 403, so the Board cannot supply a
fresh Intake or release-candidate Run. Adding a close now would rewrite the input those retained
Runs used; the profile instead keeps the event's window *length* and must not be selected as a live
Gate venue. The IN-CYPHER profile does state a close, because there the moment scoring stops is the
fact that matters most.

**Play Global, never Danish.** `danmark.brunnerctf.dk` is a separate platform with its own
scoreboard, and it carries a strict no-AI policy that would disqualify everything this repository
builds. The two are one click apart in the site navigation — the Solver is pointed at a board by
`CTFD_URL` (see [`../credentials.md`](../credentials.md)) and that value is the whole guard.

Global's own position on us is explicit: AI use is not prohibited there, alongside a note that the
organisers would rather you solved the challenges yourself. We are entering an agent on the
platform that permits one.

## What the rules forbid the Solver

Four of the rules bind the *machine*, not just the operator, and two of them are live design
constraints rather than good manners:

- **No indiscriminate brute-forcing of flags or infrastructure.** Broad automated enumeration
  against event infrastructure — DirBuster is the named example — is disallowed unless a challenge
  says otherwise, and infrastructure abuse is an immediate ban rather than a warning. An
  unattended Solver is exactly the thing that does this by accident, so directory/parameter
  fuzzing is off by default and a wrong-flag retry loop is not an acceptable fallback.
- **No sandbagging.** Flags are submitted when found; the Solver may not hold them back.
- **One account, one team.** No second identity to parallelise across.
- **No help from outside the team.** Querying an LLM is explicitly fine. Posting challenge content
  somewhere public to solicit an answer is not — a distinction worth keeping if the Solver ever
  grows a web-search tool.

Violations can disqualify, and admins have the final word.

## Verified against the live board

Checked 21 Aug 2026, before the window opened:

- **The board is behind Cloudflare, and it screens on User-Agent.** A request announcing itself as
  `Python-urllib/3.x` is refused at the edge with a 403 that never reaches CTFd — and that 403
  looks exactly like a rejected token. Every HTTP client the Solver uses must send a browser
  User-Agent or it will see an empty board and blame its credentials. The probe checks this first,
  by name, for that reason.
- The two unauthenticated shapes differ, so neither can be treated as "the failure": **no
  `Authorization` header at all → 302 to `/login`**, while **a header carrying a bad token → 401**.
  A client that follows redirects turns the first into a 200 holding a login page, which parses as
  a board with no challenges. The probe never follows redirects.
- No `/api/v1/` schema root (404). The endpoints are the documented CTFd ones.

## Verified once the board opened

Checked 21 Aug 2026 at 20:10 SGT, ten minutes after the window opened, with a registered team:

- **74 challenges** across Boot2Root, Crypto, Forensics, Misc, Mobile, OSINT, Onboarding, Pwn,
  Reversing and Web.
- **Challenge files are not served by the board.** `/files/…` answers **302 to Hetzner object
  storage** with a presigned, expiring S3-style URL. Most forensics, reversing, pwn and misc
  challenges *are* a file, so a Solver that treats a redirect there as a failure cannot open a
  large share of the board. Two consequences: the file path must follow redirects even though the
  API path must not, and **the CTFd token must not travel with it** — the presigned URL carries
  its own credentials, and forwarding ours hands a working token to a host that never asked for
  one.
- **`type` is not a closed set.** Brunner returns `flightops` alongside `dynamic` — a custom CTFd
  plugin type. Anything switching on challenge type must tolerate one it has never seen rather
  than failing closed.
- Both earlier predictions held on the live board: dropping `Content-Type: application/json`
  answers `302 → /login`, and a deliberately wrong flag returns **HTTP 200 with
  `data.status='incorrect'`**.

Still open: the CTFd version, `incorrect_submissions_per_min`, and `max_attempts` — `/api/v1/configs`
is admin-only, so assume CTFd's default of 10 wrong submissions per minute.

## Verified after the board ended

Checked 5 Sep 2026 with the fresh account prepared for a new Run. The stored API token received 401
from both `/api/v1/users/me` and `/api/v1/challenges`. After a fresh browser login, the authenticated
Challenge page stated **“BrunnerCTF 2026 - Global has ended”** and returned 403. No Run opened and no
submission or Instance action was attempted.

Brunner is therefore historical real-Board and regression evidence only. The local CTFd plus
chall-manager competition rig is the only controllable live v2 Gate venue; a later suitable external
Board may supplement it but is never a prerequisite. Neither local nor historical evidence proves
official IN-CYPHER compatibility or discharges the Pending real-Instance clause.

### The Instance terms are *not exercised* here

Checked 26 Aug 2026 against the live board. **No chall-manager field reaches us on this board at
all**: a detail GET carries no `shared`, `timeout`, `destroy_on_flag` or `mana_cost` key, and the
only `type` values across all 74 Challenges are `dynamic` and `flightops`. The Solver records all
four every cycle and every one of the 74 rows reads the default — `null`, `false`, `false`, `0` —
which is the correct answer for a board without the plugin and **not** evidence that the recording
works.

So the chall-manager arm of that record is **not exercised**, through no fault of the Solver, and it
is proven on the first board we enter that runs one — the same resolution and the same tell
[#74](https://github.com/jerome-queck/incypher-ctf/issues/74) uses for the gate's Instance clause.
`position` is the same shape and worth noting beside it: CTFd sends the field, and Brunner sends `0`
for all 74, so Order's tie-break falls through to the id on this board every time.

The other half of the check *is* exercised, and passed: the fields are present on every row of every
cycle, so a board that does populate them will be read rather than silently dropped.

## Historical drift check

```bash
sh scripts/check-rules-drift.sh docs/competitions/brunnerctf-2026-global.rules.txt
```

Rules "may be updated at any time by the BrunnerCTF crew", so this ran before the event and each
morning of it. The command remains useful when auditing the preserved evidence or if the Board
unexpectedly reopens; it does not qualify Brunner as a current practice or Gate venue. `source
unchanged: <url>` and exit 0 means nothing changed; drift prints a diff and exits 1. The script is
never silent on success, so silence means it did not run.
