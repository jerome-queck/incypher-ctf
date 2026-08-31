# incypher-ctf

An autonomous CTF-solving agent for the [IN-CYPHER Agents-vs-CTF hackathon](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/)
(Imperial Global Singapore, September 2026). The deliverable is a Docker container pointed at a
whole **Board** and left for the window: it syncs every Challenge, judges what each is worth, ranks
what the remaining clock is committed to, and works that set to Flags — web, pwn, crypto, reversing,
forensics and whatever else the Board ships, a number of them set in healthcare scenarios. With no
human in the loop, which is not a stretch goal: the scoring penalises intervention.

Three files, in the order a newcomer wants them: [`MAP.md`](MAP.md) to find your way around,
[`AGENTS.md`](AGENTS.md) for how work is done here, and [`CONTEXT.md`](CONTEXT.md) for the
vocabulary the code and the documents both use. The plan is
[issue #11](https://github.com/jerome-queck/incypher-ctf/issues/11) and the shape being built is
[spec #63](https://github.com/jerome-queck/incypher-ctf/issues/63).

Born from the Jerome-Group private template and adapted to stand alone — see
[ADR-0002](docs/adr/0002-org-machinery-is-vendored-to-stand-alone.md) for what was kept, adapted,
and dropped.

## Status

🚀 **v1 has landed**, tagged `v1` against spec #63, and **its gate is closed with one clause
Pending**. The Solver takes a Board, works a window unattended and leaves its whole Step stream
behind, and the gate Runs are committed under [`runs/`](runs/) as the evidence rather than the
claim.

The Pending clause is *"≥1 Instance it deployed and terminated itself"*, and it is Pending rather
than failed because **neither Board v1 was bound to runs `ctfd-chall-manager`** — Brunner has no
instanced Challenge to deploy, so the Solver was never asked. The path ships complete and is
tested against a fake transport; what is missing is a real one.
[ADR-0026](docs/adr/0026-a-gate-clause-with-no-venue-is-pending-and-the-gate-closes-without-it.md)
is the verdict and its two rules: the first Board that can prove it discharges it, and it expires
at the final gate.

**Three versions remain, and the roadmap they follow was re-locked on 31 August 2026** —
[ADR-0030](docs/adr/0030-four-versions-remain-and-the-practice-board-is-one-we-build.md) replaces
ADR-0006's table and carries the mapping for any version number written before it. **v2 is in
progress**: the failsafes and the solve rate, gated on a 5.5-hour unattended Run against
BrunnerCTF Global and on the Instance path exercised against a board we build. That local board is
the part worth knowing about, because it is a reversal — the live practice calendar was measured and
only about 13% of it runs CTFd at all, so a roadmap that gates on somebody else's event is betting
on a coin-flip. **v3** integrates the ADK against the IN-CYPHER batch from 14 September, and **v4**
is the freeze and the only true go/no-go, on-site 21–22 September.

The ADK is the one part that waits on somebody else. It is released 14 September 2026, eight days
before the scored run, and
[ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md) puts it behind
`Target` rather than `Board`. The Solver is built to *accept* it, never on top of it — one that
could not ship without it would have bet the competition on an unseen release. `CONTEXT.md`'s **ADK**
entry is the rest.

## Getting started

Each command below is owned by a document, and that document is the copy to trust — what is here is
the shortest path to running something.

| To | Run | Owned by |
| --- | --- | --- |
| bring the container runtime up | `python3 scripts/runtime.py start` | [`AGENTS.md`](AGENTS.md) |
| build the image | `docker build -t solver .` | [`Dockerfile`](Dockerfile) |
| run the Solver at a Board | `docker run --rm --env-file .env -v "$PWD/state:/state" solver` | [`AGENTS.md`](AGENTS.md) |
| point it at a Board | `bash scripts/setup-board.sh` | [`docs/credentials.md`](docs/credentials.md) |
| see which credentials this machine holds | `python3 scripts/credentials_held.py` | [`docs/credentials.md`](docs/credentials.md) |

It refuses to start rather than run half-configured (`CONTEXT.md`, *Refusal*), so an unset variable
is a failure at 10:15 with somebody standing there instead of a spent window at 14:00. One thing
about that `-v` will bite you and is worth reading before you hit it: the runtime mounts `$HOME` and
nothing else, so a repository living outside it hands the container an empty directory in silence.

`ruff` and `pytest` are not installed here and the three checks to run before pushing are not the
ones you would guess — [`AGENTS.md`](AGENTS.md), *Getting it running*, has both.

Copy `.env.example` to `.env` and fill in the team key and the LLM keys. **Never commit the real
values**: [`docs/credentials.md`](docs/credentials.md) explains why one of them cannot be rotated,
which makes it the one secret here to handle as though the secret scan did not exist.
