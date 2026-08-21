# incypher-ctf — context

The domain: an autonomous agent that solves capture-the-flag challenges without human
intervention, for the IN-CYPHER hackathon.

## Language

The ubiquitous language of this repository: the words the code, the issues and the commits all
use for the same thing. An entry earns its place when two people — or a person and an agent —
could reasonably mean different things by the same word.

Each entry is the term, what it means **here**, and the near-synonyms to avoid so the wrong one
does not creep back in.

### The game

**Solver**:
The autonomous program this repository builds — the Docker container that receives a challenge
and works it unattended to a flag. "Agent" is the competition's word for the same thing and is
fine in prose; in code and issue titles, prefer **Solver** so it never collides with the AI
coding agents that *write* this repository.
_Avoid_: agent (ambiguous here), bot, script

**Challenge**:
One CTF task the Solver attempts — a single problem exposed by a Board, holding one Flag. The
kind of problem it is, is its Category; the fiction it is dressed in is its Scenario setting.
_Avoid_: problem, task (a `task` is a repository issue label — a different thing)

**Category**:
The Board's own label for what kind of problem a Challenge is — `web`, `pwn`, `crypto`, `rev`,
`forensics`, `misc`, and whatever else an event decides to ship. **It is an open string read from
the Board, never an enum in our code.** IN-CYPHER's practice Board namespaces its own as
`(Practice) forensics` and `(Practice) misc`; the organisers describe the set as the classic
categories *and more*. A Challenge's CTFd `type` is open in the same way — BrunnerCTF ships a
`flightops` alongside the standard ones. Code that switches on a fixed list does not fail loudly
when an event ships a category it has never met; it silently drops the Challenge.
_Avoid_: kind, track, genre. Not `type` either — that is CTFd's separate field for how a
Challenge is *served* (see Static Challenge).

**Scenario setting**:
The fiction a Challenge is dressed in. IN-CYPHER sets a number of its Challenges in medical and
healthcare scenarios — and that is a setting, not a Category: underneath, such a Challenge is
still web, pwn or forensics, and is solved with that Category's tools. Knowing the setting helps
*read* a Challenge; it never decides what to reach for.
_Avoid_: category, track, theme-as-category. In particular there is no "healthcare category" —
healthcare is a setting over the ordinary ones.

**Flag**:
The string that proves a Challenge is solved, submitted to the Board by the Solver. **The wrapper
is a property of the Board, not of the domain** — IN-CYPHER's is `flag{…}`, BrunnerCTF's is
`brunner{.*}` with any text as the body, and a practice Board we have not met yet will have its
own. So will its rules, its scoring, and its categories: each Board is read at the start of the
event, not assumed. A Solver that hardcodes one event's wrapper finds nothing at the next.
_Avoid_: answer, solution, key (a "key" here is a credential — see Team key)

**Board**:
One competition's CTFd instance — the thing the Solver enumerates, submits to, and is scored by.
A Board, not "the platform", because the Solver plays several: practice Boards like BrunnerCTF
Global and the IN-CYPHER Board itself. Which one it is pointed at is `CTFD_URL`, and getting that
wrong is a disqualification rather than a misconfiguration (`docs/competitions/`).
_Avoid_: platform, site, server, instance (an "instance" is one deployed Challenge — see below)

### How a Board serves a Challenge

**Static Challenge**:
A Challenge the whole event shares — one deployment, one Flag, identical for every team. Which
shape a Challenge is, is the Board's `type` field, which is an open string exactly as Category is.
_Avoid_: shared challenge, normal challenge

**Isolated Challenge**:
A Challenge deployed per-team on demand, time-limited, and carrying a Flag unique to the team it
was deployed for — so a Flag lifted from another team's Instance does not grade. IN-CYPHER serves
these through the `ctfer-io/ctfd-chall-manager` plugin.
_Avoid_: dynamic challenge — CTFd's `dynamic` means a point value that falls as solves come in,
which is a different axis entirely — per-player challenge, instanced challenge

**Instance**:
One running deployment of an Isolated Challenge, belonging to one team and expiring on its own.
The Challenge is the thing on the Board; the Instance is the copy currently held. Keeping the two
apart matters because "the Instance expired" and "the Challenge is unsolved" are different facts
that want different responses.
_Avoid_: container, deployment, lease, box

**Mana**:
What chall-manager charges a team for holding Instances: every Isolated Challenge carries a mana
cost, every team a mana total, and destroying an Instance reclaims what it cost. Mana is why
holding an Instance is never free — an Instance nobody is working still costs what it cost to
deploy.
_Avoid_: quota, credits, points (points are score — see Board)

**PoW gate**:
The proof-of-work a raw-TCP Challenge demands before its service will talk, bound to the Team key
so the work cannot be shared or farmed out. Web Challenges have no gate — they are reached at an
unguessable subdomain instead.
_Avoid_: captcha, rate limit, challenge (a PoW gate stands in front of a Challenge; it is not one)

### Secrets and tooling

**Team key**:
The secret that identifies and gates this team on the IN-CYPHER Board, including its
proof-of-work gate. It never enters the repository — it lives in `.env` and reaches the Solver at
runtime. See `.env.example`.
_Avoid_: token, API key. A **CTFd access token** is a different secret — per-Board, minted by us,
revocable — and the **LLM credential** is a third. All three follow the same rule and none of
them is the Team key (`docs/credentials.md`).

**ADK**:
The competition's Agent Development Kit, released 14 September 2026, including the
`solver.connect(host, port, team_key)` helper that clears a PoW gate. The organisers also call it
the **Hackathon Starter Pack**; the two names mean one thing, and `ADK` is the one used here.
The Solver is built against it.
_Avoid_: SDK, framework

One term is about how this repository is governed rather than about the domain:

**Seed**:
The Jerome-Group private template this repository was generated from, and the org machinery that
came with it. The Solver owes the Seed nothing; the conventions do, and where a convention still
names its origin it means this — a historical source, not a live authority. ADR-0002 records
what was kept from it.
_Avoid_: Organisation, Baseline, template-as-authority
