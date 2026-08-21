# Map

An autonomous CTF-solving agent for the IN-CYPHER hackathon, and the conventions it is built
under.

Start here: `README.md`, then `AGENTS.md`.

| Area | What lives there | Entry point |
|------|------------------|-------------|
| Working here | Agent + contributor conventions, commit/attribution rules | `AGENTS.md` (= `CLAUDE.md`) |
| Contributing | How work flows here — issue first, then a pull request | `CONTRIBUTING.md` |
| Code standards | How code is written and reviewed | `CODING_STANDARDS.md` |
| Domain language | The glossary — this repository's ubiquitous language | `CONTEXT.md` |
| Decisions | Architecture decision records | `docs/adr/` |
| Agent conventions | The routines an agent follows here, one file per topic | `docs/agents/` |
| Competitions | The boards we play — a rules snapshot and the platform facts, one file per event | `docs/competitions/` |
| Credentials | Every secret, where it comes from, and how it reaches the container | `docs/credentials.md` |
| Scripts | Setup and pre-flight checks, run by hand against a live board | `scripts/` |
| Installed skills | The mattpocock engineering skills, copied in for Claude and Codex | `.claude/skills/`, `.agents/skills/` |
| Conformance | The vendored convention checker and the manifest it reads | `conformance/` |
| Automation | The workflows that run on a pull request or on a new issue, and dependency updates | `.github/` |

Update this file in the same pull request whenever a top-level area is added, moved, or removed.
