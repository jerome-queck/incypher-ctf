# A string the Board stated waits for the reserved tail

**A candidate the model states that the Board's own prose also carried is `stated` — a fifth
strength, weakest of the five — and it is held back from every live submission, including where the
Board says attempts are unlimited. The reserved tail releases it.**

It is the one place `solver/flag.py` does not let speed win, and the one candidate the Solver holds
positive evidence *against*.

## What ADR-0019 left open

[ADR-0019](0019-the-boards-statement-of-a-challenge-is-not-evidence.md) closed the deterministic
route: a Flag-shaped string in a Challenge's description is recorded `source: board` and the sweep no
longer authorises it. It argued the model's route needed nothing, in the section *A prose match is
not demoted to `unverified` — it is not a candidate at all*:

> The case that demotion was meant to protect — a freebie Challenge whose Flag really is in the
> prose — is already covered, and better: the model reads the description in its opening frame,
> states the Flag, and it reaches the gate as `unverified`, which is the strength it deserves.

**That last clause is wrong, and this record supersedes it.** `_refuses` returns the empty string for
every strength where `slots.unlimited` holds, and Brunner reports `max_attempts` 0 on all 74
Challenges. So on the one Board we have played, `unverified` is not a holding pen — it is a
submission. (That last sentence stopped being true on 29 August 2026: ADR-0027 makes `unverified`
exactly the holding pen it was not here, on a Board that states no budget. The argument below is
unaffected — it turns on `stated` being the one strength there is evidence *against*.) A model that restates the example while narrating what it tried spends a live slot on the
Board's own decoy, which is the defect ADR-0019 was written to remove, arriving by the other door.

Everything else in ADR-0019 stands. The `source` fact it added is what makes this record's rule
possible at all.

## Why not simply refuse it

Refusing the nomination outright is the obvious fix and it is the wrong one, because the two errors
are not the same size. A decoy submitted costs **one submission**, against the Board-wide
`incorrect_submissions_per_min` limiter. A freebie Challenge refused costs **the Challenge** — and a
Flag sitting in a description is a real pattern, the welcome or sanity Challenge that most events
ship.

So the question is not *whether* to submit a string the Board stated. It is *when*.

A submission made during the Run competes with every other Challenge for the limiter — that is the
whole reason `Pace` is held per Board rather than per Challenge. A submission made in the Run's
reserved tail competes with nothing: `last_call` exists precisely because there is no later Attempt
for a slot to be saved for, and a candidate carried out of a Run unsubmitted is one the Solver found
and never tried.

The tail is therefore where a `stated` candidate belongs, and the machinery already exists —
`_refuses` returning a reason puts a candidate in `Outcome.held`, `solver/run.py` carries it to
`_pending`, and the tail submits it with `last_call=True`.

## Where the refusal sits, and why that matters

Above the `slots.unlimited` branch, which no other refusal is. That ordering *is* the decision: an
unlimited Board still has the Board-wide limiter, and unlimited attempts on this Challenge are not
unlimited attempts on the Board.

It is not sandbagging, which Brunner bans outright and `solver/flag.py` respects everywhere else. The
candidate **is** submitted — at the one moment holding it back costs nobody anything. What is being
declined is spending another Challenge's budget on a string we have good reason to think is an
example.

## What makes it derivable

`solver/flag.py`'s sweep already reads every Observation's `source`. Where it meets a Board statement
it now reads the body rather than skipping it, and keeps the wrapper matches it finds. A `said` match
that appears in that set is `stated`; one that does not is `unverified`, unchanged.

Two properties fall out and are worth naming:

- **A string a real command produced is untouched.** The body loop runs first and `setdefault` keeps
  it, so a Flag the Board happened to state *and* a command produced stays `observed`. The Board
  stating a string says nothing about whether the Solver later found it.
- **It costs one extra body read per Board statement per Attempt** — two, in practice, since that is
  how many prose probes recon spends. Bodies it was already choosing not to sweep.

## Consequences

- **The strength vocabulary is five, not four**, in `CONTEXT.md` and in `solver/flag.py`'s ladder.
  `STRENGTHS` is written as a sequence precisely so that adding one is an ordering decision rather
  than an edit in five places. It became six in
  [ADR-0027](0027-one-flag-per-challenge-and-the-solver-bounds-what-the-board-does-not.md), which
  is the sequence earning that.
- **A Run cut before its tail never submits a `stated` candidate.** A crash, or a window that closes
  during an Attempt, loses it. That is the same exposure every held candidate already has.
- **Nothing measures how often this fires.** The four gate Runs cannot show how often a model
  restates the format section, because the CLI errored on the Attempts that would have. The Step
  stream records the hold, so the first Run that reaches its tail answers it.

## Revisit when

- **A Run's tail submits a `stated` candidate and the Board grades it correct.** That is the freebie
  this record exists to preserve, and it would be the first evidence the trade was real rather than
  hypothetical.
- **A Board is met that limits attempts.** The reserve already holds the last attempt for a
  `reproduced` candidate, so a `stated` one and the reserve now compete for the same slot at
  `last_call`. Nothing orders them today beyond `STRENGTHS`, and one real limited Board is when that
  ordering stops being free.
- **The limiter turns out not to bind.** If a Run never approaches `incorrect_submissions_per_min`,
  the cost this record is avoiding is zero and the holding is pure loss.
