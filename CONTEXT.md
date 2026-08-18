# incypher-ctf — context

The domain: an autonomous agent that solves capture-the-flag challenges without human
intervention, for the IN-CYPHER hackathon.

## Language

The ubiquitous language of this repository: the words the code, the issues and the commits all
use for the same thing. An entry earns its place when two people — or a person and an agent —
could reasonably mean different things by the same word.

Each entry is the term, what it means **here**, and the near-synonyms to avoid so the wrong one
does not creep back in.

**Solver**:
The autonomous program this repository builds — the Docker container that receives a challenge
and works it unattended to a flag. "Agent" is the competition's word for the same thing and is
fine in prose; in code and issue titles, prefer **Solver** so it never collides with the AI
coding agents that *write* this repository.
_Avoid_: agent (ambiguous here), bot, script

**Challenge**:
One CTF task the Solver attempts — a single web, pwn, crypto, reversing, forensics, or
healthcare problem exposed by the competition platform, holding one flag.
_Avoid_: problem, task (a `task` is a repository issue label — a different thing)

**Flag**:
The `flag{…}` string that proves a Challenge is solved, submitted to the platform by the Solver.
_Avoid_: answer, solution, key (a "key" here is a credential — see Team key)

**Team key**:
The secret that identifies and gates this team on the competition platform. It never enters the
repository — it lives in `.env` and reaches the Solver at runtime. See `.env.example`.
_Avoid_: token, API key (those are the LLM provider credentials — different secrets, same rule)

**ADK**:
The competition's Agent Development Kit, released 14 September 2026, including the
`solver.connect(host, port, team_key)` helper for proof-of-work-gated challenges. The Solver is
built against it.
_Avoid_: SDK, framework

One term is about how this repository is governed rather than about the domain:

**Seed**:
The Jerome-Group private template this repository was generated from, and the org machinery that
came with it. The Solver owes the Seed nothing; the conventions do, and where a convention still
names its origin it means this — a historical source, not a live authority. ADR-0002 records
what was kept from it.
_Avoid_: Organisation, Baseline, template-as-authority
