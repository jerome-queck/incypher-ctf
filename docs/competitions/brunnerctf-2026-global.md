# BrunnerCTF 2026 — Global

The Solver's first real test. This is our reading of the event; the rules themselves are
[`brunnerctf-2026-global.rules.txt`](brunnerctf-2026-global.rules.txt), captured verbatim so a
change to them is a diff rather than a thing nobody noticed.

## The board

| | |
|---|---|
| Platform | CTFd at `https://global.brunnerctf.dk` |
| Window | 21 Aug 14:00 CEST → 23 Aug 14:00 CEST — **20:00 SGT Fri → 20:00 SGT Sun** |
| Flags | `brunner{.*}` — the body is *any* text, not just hex or alphanumerics |
| Scoring | Dynamic (value falls with solves); the Onboarding track is static |
| Teams | Team-based, no size limit |
| Categories | Web, Crypto, Forensics, Rev, Pwn, OSINT, and whatever else ships |
| Stakes | CTFtime points. No prizes on this platform. |

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

Open until we hold an account — the probe answers all of them on its first run: the CTFd version,
`incorrect_submissions_per_min`, `max_attempts`, whether any per-player instance plugin is
installed (we expect not on Global), and whether challenge files download headlessly.

## Before playing

```bash
sh scripts/check-rules-drift.sh docs/competitions/brunnerctf-2026-global.rules.txt
```

Rules "may be updated at any time by the BrunnerCTF crew", so this runs before the event and again
each morning of it. Silence means nothing changed.
