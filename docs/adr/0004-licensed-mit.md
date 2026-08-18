# Licensed MIT, replacing the template's proprietary default

The `Jerome-Group` template ships an all-rights-reserved proprietary `LICENSE` — a deliberate
default whose own footer says a repository that should be open replaces it wholesale. This
repository is a hackathon entry that may be opened after the competition (writeups, reuse of the
Solver), and the IN-CYPHER hackathon publishes **no** requirement to open-source, keep proprietary,
or license the work any particular way — so the choice was ours to make.

The decision: **license this repository MIT**, `Copyright (c) 2026 Jerome Queck`.

MIT is the least-friction permissive license: it lets the four of us, and anyone after the event,
reuse the Solver with no obligation beyond preserving the notice. The copyright line names the
single account that owns the repository rather than enumerating four contributors, which would need
maintaining as the team changes.

## Why now, and why not stay proprietary

The template's argument for its proprietary default is sound in general — withholding rights is
reversible, granting them is not, so a repository unsure of its stance should stay closed. This
repository is *not* unsure: it is a collaborative hackathon entry among four people who all want it
reusable, and choosing MIT at setup means every commit is contributed under a known, permissive
license from the first one. Relicensing later would require every contributor's agreement over the
whole history — cheap to decide now, expensive to decide after four people have committed.

Left unconfirmed: the hackathon's registration terms behind the platform login were not visible at
setup, so the "no open-source requirement" finding is an absence of a rule rather than a rule
permitting it. If those terms turn out to constrain licensing, this is the record to revisit.

## Consequences

- The Solver and everything in this repository is reusable by anyone under MIT terms — including
  competitors, once the repository is public. That is acceptable for a hackathon entry and is the
  point of choosing it.
- The team key and LLM credentials are *not* covered by this or any license question — they never
  enter the repository (`.env.example`, ADR none needed; it is a rule in `AGENTS.md`).

## Revisit when

- The hackathon's registration terms are read and say anything about IP, licensing, or
  confidentiality that contradicts a permissive license.
- The repository is about to be made public and the team wants to reconsider the license against
  what is by then in it.
