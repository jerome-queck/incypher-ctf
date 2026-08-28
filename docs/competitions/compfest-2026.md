# COMPFEST 18 — 2026

ADR-0006's else-branch binding for v1, and the first Board this Solver plays that is not Brunner.
This is our reading of the event; the rules themselves are
[`compfest-2026.rules.txt`](compfest-2026.rules.txt), captured verbatim so a change to them is a
diff rather than a thing nobody noticed.

## The board

| | |
|---|---|
| Platform | CTFd at `https://mirror-ctf.compfest.id` |
| Window | 29 Aug 00:00 UTC → 31 Aug 00:00 UTC — **08:00 SGT Sat → 08:00 SGT Mon**, 48 hours |
| Flags | `COMPFEST18{[A-z0-9_-]+}` as the Board states it, "unless specified otherwise" |
| Divisions | **Human** and **AI Assistant** — see below. This is the entry that decides whether we may play at all. |
| Hints | Released only for Challenges on 0 solves, announced by the organisers |
| Stakes | Prizes are advertised on the site |

**The tracked profile is [`compfest-2026.board.json`](compfest-2026.board.json)**, and unlike
Brunner's it *does* state a `closes_at`: this is a live scored event with a real end, and a Run
must not outlive it. Brunner's profile omits one because that board stays up as our practice
board and a stamped close would refuse every practice Run.

**The wrapper we register is deliberately wider than the Board's.** The Board says
`COMPFEST18{[A-z0-9_-]+}`; the profile carries `COMPFEST18\{[^}]{1,256}\}`. A sweep pattern that is
narrower than the truth *misses a Flag we already earned* and reports a clean Run, which is the
expensive direction of this error — and the Board's own `[A-z0-9_-]` is a character class with a
known off-by-one anyway (`A-z` spans `[`, `\`, `]`, `^`, `_`, backtick). Wider costs a false
candidate the verification ladder then rejects. Narrower costs the Flag.

## The AI policy is a gate, not a footnote

COMPFEST runs **two divisions**, and the rules are explicit:

- **Human Division** — "No AI assistance is allowed besides browser AI overview and IDE code
  suggestions and autocomplete", and even the overview may not be prompted. Everything this
  repository builds is disqualified here, in the same way `danmark.brunnerctf.dk` disqualifies it.
- **AI Assistant Division** — "Use any AI/LLM you want." This is the only division the Solver may
  play in.

**Switching is one-way and needs a human.** "If you want to switch from the Human Division to the
AI-Assisted Division, open a ticket on our Discord server and we will review your case. Switching
from the AI-Assisted Division to the Human Division is not allowed." So the division must be
settled — by a person, through Discord, subject to review — *before* a Run, and it cannot be
undone afterwards. There is no API for this and no way for the Solver to check it: the tracked
profile cannot encode which division an account is in, so this page is the record that it was
verified.

## What the rules forbid the Solver

Six prohibitions are in the profile; three of them are live design constraints rather than good
manners, and two are sharper here than they were on Brunner:

- **No automated scanners.** SQLMap, Burp Scanner and Dirb are named, and *"or similar tools"*
  reaches any sweep that enumerates rather than reasons. An unattended Solver is exactly the thing
  that does this by accident.
- **Brute-forcing Flags on the platform is listed as harm**, in the same clause as DDoS and
  deleting flag files — not as a style violation. This is the one to watch: across the four
  promoted gate Runs the Solver made **74 submissions and had 1 accepted**. On Brunner most of
  those were masked as `already_solved` because our account had already solved 65 of 74. Here
  nothing masks them, the account is fresh, and a rejected-submission stream is visible to the
  organisers. Submission discipline is a live risk on this Board in a way it never was on the last
  one.
- **No Flag hoarding** — stockpiling and submitting in bulk near the close. This is Brunner's
  no-sandbagging rule wearing a different name, and the Solver already satisfies it by submitting
  as soon as a candidate is reproduced.

## What this Board cannot prove

The Instance clause of v1's gate — "≥1 Instance it deployed and terminated itself" — needs a board
running `ctfd-chall-manager`. Whether COMPFEST runs one is **unverified at the time of writing**
and is a question for the first authenticated read: check the plugin's asset URL and whether any
Challenge carries a `dynamic_iac` type. If it does not, this Board closes the same gate clause
Brunner could not, and v1's gate stays open on the same line.
