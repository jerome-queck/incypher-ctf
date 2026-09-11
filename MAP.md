# Map

An autonomous CTF-solving agent for the IN-CYPHER hackathon, and the conventions it is built
under.

Start here: `README.md`, then `AGENTS.md`.

| Area | What lives there | Entry point |
|------|------------------|-------------|
| Working here | Agent + contributor conventions, commit/attribution rules | `AGENTS.md` (= `CLAUDE.md`) |
| Contributing | How work flows here — issue first, then a pull request | `CONTRIBUTING.md` |
| Code standards | How code is written and reviewed | `CODING_STANDARDS.md` |
| Domain language | The glossary — ubiquitous language and the boundaries between Run, Attempt, Lane, Lease, final-chance admission, Submission epochs, Recovery, Tool profiles, Incidents and owned work | `CONTEXT.md` |
| Decisions | Architecture decision records | `docs/adr/` |
| Agent conventions | The routines an agent follows here, one file per topic | `docs/agents/` |
| Competitions | The boards we play — source snapshots, our provenance-labelled reading, and the tracked `<event>.board.json` a Board profile is configured from | `docs/competitions/` |
| Research | Dated primary-source findings that unblock decisions | `docs/research/` |
| Credentials | Every secret, where it comes from, and how it reaches the container | `docs/credentials.md` |
| The Solver | The agent's own code — what it refuses to start without, the Board profile it discovers, the seams it reaches the outside world through, the sync that keeps its copy of a moving Board current, the Tier it reads off what that Board states, the ranking and the clock that choose what to work next and for how long, the Board's own files as an Attempt holds them, the recon every Attempt opens with, the prompt it is worked under, the stall call that ends one, the Flag it submits only where its own work produced one, the record it leaves behind, and the reserved tail it ends on | `solver/`, starting at `__main__.py` |
| The image | The container the Solver ships as — the pinned base, the two package lists and the build-time probe that exercises each on real input, the pinned Codex CLI the adapter spawns, and the allowlist that keeps a secret out of a layer | `Dockerfile`, `.dockerignore` |
| Tool supply | Locked Tool-profile fragments and the generated image rootfs, inventory and receipt | `tool-supply/` |
| Scripts | Setup, pre-flight checks and the eval queries, all run by hand — the runtime this repo pins, the live board read through `solver/`, whether the model we would spend a Run on answers, which credentials this machine holds, the seven questions asked of a finished Run, and the promotion that puts one in `runs/` | `scripts/`, the seven queries at `eval_*.py` |
| Tests | What CI runs over `solver/` and `scripts/` — the seams that decide offline | `tests/`, configured in `pyproject.toml` |
| Installed skills | The mattpocock engineering skills, copied in for Claude and Codex | `.claude/skills/`, `.agents/skills/` |
| Conformance | The vendored convention checker and the manifest it reads | `conformance/` |
| Automation | The workflows that run on a pull request or on a new issue, and dependency updates | `.github/` |
| Tool caches | Disposable ruff and pytest scratch — gitignored, safe to delete | `.cache/` |
| Container state | The host mount a running container writes through — one `runs/<run_id>/` per Run, one `work/<event>/<challenge_id>/` per Challenge worked, and the Codex credential the container mints for itself — gitignored, never committed | `state/` |
| Promoted Runs | The Step stream of every Run that reached an Attempt, committed as written and one file per Run — data, so excluded from lint, outside the conformance checker's reach, and deliberately **not** excluded from the secret scan | `runs/`, written by `scripts/promote_run.py` |

Update this file in the same pull request whenever a top-level area is added, moved, or removed.
