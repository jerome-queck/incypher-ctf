# Decisions are recorded here, as ADRs

Any decision this repository makes that the code cannot state for itself is written down in
`docs/adr/` as a numbered, immutable record. `CODING_STANDARDS.md` §3 draws the line: the code
carries *what* it does, and this directory carries the *why* — the constraint that ruled an
option out, the trade-off accepted, the thing that will look like a mistake to whoever reads it
next without the reasoning.

The shape is the one used across the Organisation, and it is deliberately not a form to fill in:

- **The filename is `NNNN-a-sentence-saying-the-decision.md`**, four digits, never renumbered.
- **The title states the decision**, not the topic. "Sessions are stored in Redis" rather than
  "Session storage".
- **The body argues it.** What was chosen, what it was chosen over, and why the alternative
  loses. An ADR that only records the winner is a changelog entry.
- **`## Consequences`** — what this costs, including the parts nobody likes. A record with no
  costs was not a decision.
- **`## Revisit when`** — the condition that would make this the wrong answer. This is what
  stops a record from quietly becoming a rule.

A record is **immutable**. A decision that no longer holds is superseded by a new record that
links back to it; the old one stays, because the reasoning that was true at the time is what
makes the reversal legible.

## Consequences

- Small decisions do not earn a record, and the boundary is a judgement call. The test is
  whether a competent reader of the code would be surprised — if they would, it is an ADR.
- Organisation-wide decisions are **not** recorded here. They live in the management hub and
  reach this repository through the Baseline and the templates; duplicating one here creates a
  second copy that will drift.

## Revisit when

- This repository accumulates enough records that they need an index. Add one; do not start
  pruning.
