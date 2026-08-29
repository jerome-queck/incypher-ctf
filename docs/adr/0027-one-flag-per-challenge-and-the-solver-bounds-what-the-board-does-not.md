# One Flag per Challenge, and the Solver bounds what the Board does not

**A command that emits more Flags than a Challenge has authorises none of them — a sixth strength,
`crowded`. And where a Board states no attempt budget, the Solver supplies the two bounds the Board
is not supplying: everything its own work did not produce waits for the reserved tail, and an
Attempt stops once the Board has graded it wrong `WRONG_CEILING` times.**

`compfest-2026-seg1` put 51 incorrect Flags on a live Board in fourteen minutes and was stopped over
it. COMPFEST names a wrong-Flag retry loop beside DDoS, so this is a rules exposure and not only
wasted slots.

## What the incident actually showed

**43 of the 51 arrived `reproduced`** — the strength [ADR-0021](0021-a-string-the-board-stated-waits-for-the-reserved-tail.md)
reserves for a Board's last attempt. Seven were `unverified` and one `observed`.

That number is the whole record. Nothing believed a Claim; the Claim/Observation split held exactly
as designed. What failed is that **`reproduced` is cheap**: it means the emitting command was run a
second time and the same string came back, which is automatic when the command is reading a file.
Both leak paths were file reads — an `rg` over a CTF training repository the model cloned into
`/tmp`, whose `README.md` and `flag.txt` carry dozens of example Flags for unrelated past
challenges, and an `rg` over the Run's own `stream.jsonl`, which carries every earlier
`[flag] submit` line.

A per-candidate rule cannot see either. Each individual string is genuinely well-evidenced: a real
command really did emit it, and a replay really did bring it back.

## Why the obvious fix is not the fix

`NEVER_SWEPT` already refuses this module's own reports where they arrive as their own Step. The
obvious repair is to refuse them in a **body** too, so a grep of the record cannot launder them
back. That was written first, and it is kept — but it is defence in depth, and recording it as the
fix would be recording something false.

It matches the marker on the line. `rg -n` carries the marker and is closed; `rg -o` emits the bare
matches and is not. Measured against the real record, **47 rejected strings survive it**. The rule
closes a command's formatting choice, never the record.

## One Flag per Challenge

A Challenge has exactly one Flag. So a command answering with twenty-seven has read a *list* of
Flags rather than solved anything, and the twenty-seven are twenty-six wrong ones at best. They are
individually well-evidenced and collectively self-refuting, and the collective reading is the true
one.

**Demoted, not refused.** The set is untrustworthy rather than proven wrong, and a Challenge whose
Flag really is in there is one the tail can still win. `crowded` sits with the strengths that wait
for the reserved tail, so nothing is dropped.

The second effect is the one that matters more: a `crowded` candidate is not `authorised`, so **the
replay never runs on it**. That is the step that minted `reproduced` off a second read of the same
file, and it is now unreachable for exactly the shape that was abusing it.

### Per command, not per sweep

Judged over the matches of one Observation, never over the Attempt's whole candidate set.

An Attempt that works for twenty turns legitimately accumulates Flag-shaped strings across them — a
decoding tried, a decoy found, a guess printed — and one each from twenty commands is a model
working. Twenty from one command is a model grepping. A per-sweep rule cannot tell those apart, and
the first thing it broke was an integration test in which a model iterates guesses honestly.

The replay is exempt for a reason of the same kind: it re-runs a command the sweep already read, so
everything in its output was counted once at the command that first emitted it. Counting it again
would let one candidate's confirmation nominate a crowd. Where that first command really was reading
a list, the demotion has already happened there.

## Where the Board states no budget, the Solver supplies one

Both remaining rules live in the `slots.unlimited` branch alone, and that placement is itself the
decision.

Where a Board states a maximum, **the maximum is the bound**: the loop cannot outrun it, and the
reserve already keeps the last slot for a `reproduced` candidate. Where a Board states none, nothing
in `_refuses` ever said stop — it returned the empty string for every strength. Submitting was free,
so the gate waved everything through.

*Free to submit* was read as *free to be wrong*. COMPFEST prices the second one. So:

- **`unverified`, `guessed` and `crowded` wait for the reserved tail.** None is the Solver's own
  work saying anything, and none is worth a slot while a candidate the work *did* produce is
  unspent.
- **An Attempt stops after `WRONG_CEILING` incorrect gradings.** Only `incorrect` counts —
  `ratelimited`, `paused` and `unread` are the Board declining to answer, and a bad minute must not
  close a Challenge the Solver never answered wrongly. Held per Attempt rather than per call,
  because `solver/run.py` submits once a turn and a local count resets with it.

This does not disturb [ADR-0021](0021-a-string-the-board-stated-waits-for-the-reserved-tail.md),
whose `stated` rule still sits *above* the branch and so holds on every Board. The contrast is
deliberate: `stated` is a string there is evidence against anywhere, where these three are strings
that merely have nothing for them, which only matters where nothing else is doing the bounding.

## What this reverses

On an unlimited Board an `unverified` candidate used to be submitted on the spot. That was
deliberate, tested, and read as free — the wrong answers bought a per-model number worth having,
and ADR-0021 relied on the same fact when it argued that `unverified` was "not a holding pen — it is
a submission".

That premise is what COMPFEST falsified. The number is still recorded; it is now paid for in the
tail rather than during the Run.

## Consequences

- **The strength vocabulary is six, not five**, in `CONTEXT.md` and in the `STRENGTHS` ladder —
  which is written as a sequence precisely so that adding one is an ordering decision.
- **A Run cut before its tail submits none of the four held strengths.** The same exposure every
  held candidate has had since ADR-0021, now covering more of them.
- **A Challenge whose Flag genuinely sits in a crowd is slower to win**, and wins only in the tail.
  The trade is deliberate: that Challenge was going to cost twenty-six wrong submissions to find by
  exhaustion, which is the loop the Board prohibits.
- **The ceiling can cost a solve** where an Attempt is wrong five times and right on the sixth. It
  binds only where the Board states no budget of its own.
- **Containment is untouched and still open.** The solving model can still read `/state/runs/**`;
  this makes the content non-authorising rather than unreachable. Taking the record out of reach is
  the boundary v2's uid separation draws, beside the credential-on-disk problem ADR-0011 accepted.

## Revisit when

- **A Board is met that limits attempts.** Neither bound here applies there, on the argument that
  the Board's maximum is doing the work. One real limited Board is when that stops being free.
- **`CROWD_LIMIT` fires on an honest command.** Three is set against what a right command looks like
  — one Flag, deduped, plus a decoy — and not against the twenty-seven and forty-seven that were
  wrong. The first legitimate demotion is evidence the number is too low.
- **A tail submits a `crowded` candidate and the Board grades it correct.** That is the Challenge
  this demotion was built to keep winnable, and it would be the first evidence the trade was real.
- **`WRONG_CEILING` is reached in a Run that was solving well.** Five is a guess at where an Attempt
  stops answering and starts guessing, and nothing has measured it.
