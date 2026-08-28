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

🚀 **v1 has landed**, tagged `v1` against spec #63. The Solver takes a Board, works a window
unattended and leaves its whole Step stream behind, and the gate Runs are committed under
[`runs/`](runs/) as the evidence rather than the claim.

One part is outstanding and it waits on somebody else. The competition's **Agent Development Kit**
is released 14 September 2026, eight days before the scored run, and
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
