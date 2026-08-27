# The Board's statement of a Challenge is not evidence

**A Flag submission is authorised by what the Solver's own work produced, and by nothing it was
told.** A Challenge's description is the Board stating the puzzle, so a Flag-shaped string in it
authorises nothing — whatever channel the Solver happened to record it through.

[ADR-0009](0009-store-what-was-observed-derive-every-judgement.md) split the record into Claims and
Observations, and [#71](https://github.com/jerome-queck/incypher-ctf/issues/71) turned that into the
submission rule: *a command's output is the only thing that authorises*. The description is neither
half of that pair. The model did not write it, so it is not a Claim; nothing ran to produce it, so
calling it an Observation is an accident of how `solver/recon.py` records — uniformly, every probe
alike, which is the property that makes the cascade total. This record names the third thing and
says what it is worth.

## What was measured

Live Runs against `global.brunnerctf.dk`, from the built image, 26 August 2026
([#98](https://github.com/jerome-queck/incypher-ctf/issues/98)).

**A flag-format section is not universal, and where it appears it always shows an example.** Of the
nine distinct Brunner descriptions our Runs persisted, **four carry one — and all four of those spell
the wrapper out**, `brunner{…}` and all. That second number is the one that matters: the section is
not a hazard the Solver sometimes meets, it is a decoy the Solver meets *every time* the section is
there. #98 reports the first half as "every Challenge"; our own streams do not support that and do
support the worse half.

Challenge 94 *Blackboard* is one of the four. In `runs/brunner-gate-1.jsonl`, Attempt 94-1 read the
prose, swept it, ranked the example `observed` — the strength that authorises — and sent it:

```
step  2  description  [recon] the description as the Board gave it
step  3  flag-scan    [recon] flag-scan for brunner\{[^}]{1,256}\} — the description
step 14  flag-replay  [flag] replay — [recon] the description as the Board gave it
         → not replayed — the Solver's own probe, so this candidate stays observed
step 15  flag-submit  [flag] submit brunner{arthur_is_gone}
         → the Board graded it 'incorrect' (HTTP 200)
```

That Attempt's model never ran a command — the CLI errored at step 12 — so the whole submission is
the deterministic cascade's, spent in the Run's first cycle on a string the Board had simply shown
us. On Brunner that costs only the Board-wide `incorrect_submissions_per_min` limiter — which is
every *other* Challenge's budget. On a Board that limits attempts it costs a real slot on a decoy,
and `solver/flag.py` treats an unstated maximum as limited precisely because that is the expensive
direction. It also sits badly beside Brunner's own rule against indiscriminate submissions.

The replay at step 14 is worth reading, because #98 reports it as a second defect and it is not one:
`solver/flag.py` already asks `stall.replayable` before replaying, so nothing was handed to a shell.
The refusal is *why* the candidate stayed `observed` rather than a consequence of having run.

## The obvious fix, and why not

**Filter the sweep on `stall.replayable`** — skip any Observation whose command is a `[recon]`-style
line the Solver wrote about itself rather than a command anyone could run. It is one line, the
predicate already exists, and it would have caught this.

It would also throw away one of recon's most valuable probes. The flag-scan over a *shipped
artefact* is `[recon]`-marked for exactly the same reason the description probe is — it is
in-process arithmetic over bytes, not a shell command — and it is the probe that finds a Flag lying
in a file the Board handed us. A filter on "is this replayable" cannot tell those two apart, because
the property it reads is *how the Step was performed* and the property that matters is *where its
bytes came from*.

So the axis is provenance, not spelling. Two probes of an Attempt read bytes the Board stated — the
description, and the flag-scan over it. Every other probe of every Attempt reads the working
directory or a process's output. That line is knowable at the moment the Step is recorded and at no
point afterwards, which is what settles where the fix goes.

## The record carries the fact; `solver/flag.py` keeps the rule

A `source` on every Step: `solver` for what the Solver's own work produced, `board` for what the
Board stated. The recon cascade passes `board` for its two prose probes and defaults everywhere
else, and the sweep reads it.

That split is ADR-0009's own — **store the fact, derive the judgement**. "These bytes came from the
Board's prose" is a fact about what happened and does not change. "That may not authorise a
submission" is a policy, it lives in one module, and a replay of a stored stream at a different
policy has to be able to reach the other answer. A stored `authorises` boolean would freeze today's
rule into every Run ever promoted.

Three smaller choices fall out and are worth naming:

- **The sweep requires `solver` rather than refusing `board`.** A source nobody has thought of yet
  therefore arrives non-authorising, which is the cheap direction to be wrong in.
- **An absent `source` is `solver`.** ADR-0009's third stability rule is that readers access by name
  with a default, and the four Runs already in `runs/` were written before this field existed. They
  must replay exactly as they behaved, or the calibration they exist for is measuring this change.
- **The sweep says how many it passed over**, in its own Step. A silently skipped match reads
  afterwards as prose that held nothing.

## Two things deliberately not done

**The description is still read, and still scanned.** 22 of 74 Brunner Challenges ship no file at
all, so the prose is the only input three Challenges in ten ever have, and a password or a second
download host lives nowhere else. Both probes stay; only what they entitle a candidate to changes.

**A prose match is not demoted to `unverified` — it is not a candidate at all.** Demoting looks
gentler and is worse: `solver/flag.py` submits an `unverified` candidate outright where the Board
states unlimited attempts, which is every Challenge on Brunner, so the decoy would still be sent.
The case that demotion was meant to protect — a freebie Challenge whose Flag really is in the prose
— is already covered, and better: the model reads the description in its opening frame, states the
Flag, and it reaches the gate as `unverified`, which is the strength it deserves.

## Consequences

- **A Flag that appears only in a Challenge's description, and that the model never restates, is
  lost.** That is the trade, taken knowingly: the string in that position is a format example far
  more often than a Flag, and the Solver's opening frame puts the prose in front of the model
  precisely so the rare real one gets nominated.
- **A third provenance now exists in the record**, and the next Step that is neither the Solver
  working nor the Board stating will have to be placed. `solver/instance.py` was considered and is
  `solver`: a deploy is an action the Solver took, and the chall-manager's answer is what came back
  from it — the same shape as a `curl`.
- **`schema_version` does not move.** Adding a field is what the three stability rules were written
  to absorb; the integer is for a change they cannot.
- **Every promoted Run in `runs/` predates the field.** The four already committed still hold the
  decoy submissions this record removes, and that is correct — they are what happened.

## Revisit when

- **A Board is met whose description is machine-readable rather than prose.** The rule here is about
  a Challenge's *statement*; a Board that ships structured hints would want them placed rather than
  assumed.
- **A Run loses a Flag to this.** The trade above is an argument from Brunner's 74 descriptions and
  no counter-example. One real freebie missed is the evidence that would reopen it.
- **A fourth provenance is wanted.** Two values is a boolean wearing a vocabulary's clothes; a third
  is when the vocabulary starts earning it, and is also when the sweep's allowlist should be
  re-read rather than extended by reflex.
