# incypher-ctf

An autonomous CTF-solving agent for the [IN-CYPHER Agents-vs-CTF hackathon](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/)
(Imperial Global Singapore, September 2026). The deliverable is a Docker container that solves
CTF challenges — web, pwn, crypto, reversing, forensics, healthcare — with no human in the loop.

Born from the Jerome-Group private template and adapted to stand alone — see
[`MAP.md`](MAP.md) to find your way around, [`AGENTS.md`](AGENTS.md) for how work is done here,
and [ADR-0002](docs/adr/0002-org-machinery-is-vendored-to-stand-alone.md) for what was kept,
adapted, and dropped.

## Status

🌱 Repository scaffolding is in place; no solver code yet. The competition's Agent Development
Kit lands 14 September 2026, and the solver is built against it.

## Getting started

*(How to run it, build it, or test it — filled in with the first solver code.)*

Copy `.env.example` to `.env` and fill in the team key and LLM keys. Never commit the real
values.
