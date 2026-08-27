# A Board states a list of Flag wrappers, never an alternation

**A Board profile carries `flag_wrappers`, a non-empty list, and every scan site compiles each entry
separately and runs all of them over the same bytes.** The patterns are never joined into one
`a|b` regex — because that join finds *fewer* Flags than the first pattern alone.

## The trap this closes

Two modules tell anyone meeting an unfamiliar Board the same thing, in almost the same words:

> the pattern comes from the Board profile at runtime, so a wrapper this code has never seen
> **costs a config value** — `solver/recon.py`, and again at `solver/flag.py`

With one string in the profile, the config value that route invites is an alternation. Both scan
sites use non-overlapping `finditer`, which takes the leftmost match and moves past it — so an
alternative that starts *earlier* consumes the bytes a later one would have matched:

```
board  = flag\{[^}]{1,256}\}          second = FLAG-[0-9a-f]{1,64}
bytes  = b"noise FLAG-abcflag{the_real_flag} more"

one alternation  : [b'FLAG-abcf']                                  <- the real Flag is gone
two, run apart   : [b'flag{the_real_flag}', b'FLAG-abcf']
board alone      : [b'flag{the_real_flag}']
```

Spending the documented config value therefore left the Solver **worse than not spending it**. No
error, no log line: the Flag is simply never a candidate, in recon or in the sweep, at any strength.

That is the expensive shape of failure. A Flag that does not match is invisible to both scan sites,
so the only route left is the model noticing and stating it — which lands `unverified` and is refused
a slot on any Board that limits attempts.

## The decision

`Rules.flag_wrappers` is a `tuple[str, ...]`, read from a tracked `<event>.board.json` as a list.
`solver/wrapper.py` compiles them and matches them, and is a leaf module importing nothing from
`solver` — `solver/profile.py` reaches `solver/flag.py` through `solver/intake.py`, so the rule
cannot live in the profile without a cycle. Its two readers are the two scan sites, which is
`CODING_STANDARDS.md` §6's own test for a seam: *the moment two callers would otherwise each keep
their own copy of a rule.*

Four choices fall out, each because the obvious alternative is worse:

- **Both scan sites read the same field.** No call site can widen recon's scan and leave the sweep
  narrow, so the half-configured Board — finding a Flag in recon that the sweep then refuses to
  make a candidate — is structurally unreachable rather than merely unlikely.
- **One flag-scan Step per subject, however many shapes.** The bytes are read once and every matcher
  applied to each block. The recon cascade shares a single 180-second deadline, so a re-read per
  pattern would spend a later artefact's budget on bytes this one has already seen.
- **A broken pattern costs only itself.** One wrapper that will not compile is reported and the
  others are still scanned for — a Run that gave up on every shape because one was malformed would
  find nothing for a reason nobody chose. At **boot** the rule is the opposite and stays as it was:
  any wrapper that will not compile refuses the Run, because a profile stating two shapes wants both
  and starting on one of them is starting on a Board we have half-read.
- **The prompt names every shape.** A model told only the primary one would read a Flag in the
  second shape as not a Flag.

## The singular key is retired, not aliased

`flag_wrapper` stops being written and its name is never reused — ADR-0009's second stability rule,
applied to a config file rather than to the record. A profile still carrying it fails twice over,
missing required `flag_wrappers` and carrying an unknown `flag_wrapper`, which is what
`solver/profile.py`'s own reader asks for: *"the failure mode of a permissive reader is a mistyped
key silently taking its default"*. An alias would make the old spelling keep working, and the old
spelling is the one that invited the alternation.

There are two tracked profiles and both are migrated in this change. The run-open record now carries
`flag_wrappers` as a list; a Run promoted before this carries `flag_wrapper` and is read by name with
a default, as every reader here is.

## What is deliberately not done

**A wrapper is not extracted from a Challenge's description.** It was designed, judged and rejected
([#108](https://github.com/jerome-queck/incypher-ctf/issues/108)), on two grounds:

- **It gains nothing measurable.** Of the nine distinct Brunner descriptions our Runs persisted, four
  carry a flag-format section and **all four state the Board's own `brunner{…}`** — what they
  describe is the *body*, `brunner{<lake_name>_<substation_name>}` and the like. A per-Challenge
  different wrapper occurs zero times in the data we hold.
- **It would re-open [ADR-0019](0019-the-boards-statement-of-a-challenge-is-not-evidence.md)'s
  problem from the other side.** A prose extractor yields spurious `word{…}` prefixes — a
  `struct header{int magic;}` in a description gives `header\{…\}` — and a spurious pattern matching
  inside an *artefact* scan is the Solver's own output, so it lands `observed`, the strength that
  authorises a submit. That is a new decoy path in the module that just closed one.

The description already reaches the model verbatim in its opening frame, which is where a stated
format belongs.

## Consequences

- **A Board with two shapes costs two full passes over every artefact the cascade reads.** Bounded
  by the same deadline as one pass and measured against no artefact yet; a profile stating a shape
  it does not need is now a cost as well as noise, which is why a repeated entry is refused at boot.
- **`schema_version` does not move.** A retired name and a new one is what the three stability rules
  were written to absorb.
- **The four promoted Runs in `runs/` carry `flag_wrapper`.** They are read by name with a default
  like everything else, and they are what happened.

## Revisit when

- **A Board is met that really does vary the wrapper per Challenge.** The rule here is per Board.
  Reopening it means reopening prose extraction, and the objection above is the thing to answer:
  where would a wrongly-extracted pattern's match land, and what would stop it authorising.
- **A profile wants more than a handful of shapes.** Two passes are free; twenty over a 64 MB
  artefact are not, and the cost is paid inside the cascade's shared deadline.
