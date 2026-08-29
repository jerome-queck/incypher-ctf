# A candidate that describes a Flag is not one

**A candidate whose text is a Flag's *shape* — a regular expression or a placeholder token — is a
seventh strength, `template`, and it is the one strength the reserved tail does not release: a
template is wrong whenever it is sent, where every other held candidate is merely unproven. And the
rule that refuses the Run's own record as evidence ([ADR-0028](0028-the-runs-record-stays-reachable-and-the-evidence-is-refused-instead.md))
now catches a command that names an *ancestor* of the record, not only the record itself.**

`compfest-2026-seg2` sent a live Board five Flags. Three were correct. **Both wrong ones were
strings the Board itself supplied, and neither is a value.**

## What the two were

`COMPFEST18{FAKE_FLAG}` — swept `observed` from `strings -a -n 6 …/public.zip`, the Board's own
handout read exactly as downloaded. It is the string a challenge author ships where the Flag will
go.

`COMPFEST18{[A-z0-9_-]+}` — swept `observed` from an `rg` over `/state`. It is CTFd's `placeholder=`
attribute on the submission box, captured in a challenge-detail HTML body the sweep found in the
**previous segment's** record. It is a regular expression describing the Flag's shape.

Both were `observed` off a genuine command, and that is the whole difficulty:
[ADR-0027](0027-one-flag-per-challenge-and-the-solver-bounds-what-the-board-does-not.md)'s crowding
rule, the confusable guard, the wrong-Flag ceiling and the reserved-tail reserve all turn on
*provenance* or *count*, and the provenance here was real and the count was one. The sweep read
where each string came from correctly. What no provenance rule can see is that the string itself
describes a Flag rather than being one.

## Two holes, one incident

**The record rule matched a substring.** ADR-0028 added `_reads_the_record` for exactly the shape
that produced the placeholder — a command going to the Run's own `stream.jsonl` for its output — and
it did not fire, because it asked whether the command's text *contained* the runs directory's path.
The command named `/state`, an ancestor that contains the record and reads every file under it while
naming none of them. A substring test catches a command at or below the directory and misses one
above it, and an ancestor is neither exotic nor rare: `rg PATTERN /state` and `grep -r X /` both read
the record. The rule now asks whether the two paths lie on one branch, in either direction, and `/`
is a legitimate answer — a sweep over `grep -r … /` is reading this record along with everything
else. It stays lexical over the string the command spelled: a resolve against the filesystem would
follow symlinks and answer about the machine the sweep runs on, and the sweep also runs offline over
a stored stream where the paths a finished Run named no longer exist (ADR-0009).

**Nothing weighed the candidate's own text.** This is the new rule. A candidate is `template` when
it is a **pattern** — it carries the regex fragments a literal Flag does not, a character class in
brackets or an escape or a quantifier bound to a class — or a **placeholder** — its wrapped body is
nothing but the words a handout puts where the Flag goes, joined by separators. Both read the string
alone, because that is the only thing that separates these two from a real Flag with the same
provenance.

## Why the handout itself is not the signal

The stronger rule the incident suggests is *the real Flag is never in the handout* — the Solver
stages the Board's files at Intake and knows their digests, so a candidate found in a shipped file
exactly as downloaded is a decoy by construction. It is rejected for v1, and named here so the next
person does not have to re-derive why:

**A forensics Challenge's Flag is in the file the Board shipped.** A stego image, a pcap, a
memory dump, a zip with a comment — the answer to each is inside the handout, and a rule that refused
every wrapper match in a downloaded file would refuse the answer to a whole category of Challenge.
The handout is where the Flag *often* is, not where it never is, so *seen in the handout* is not
evidence against a candidate the way *is a regular expression* is. Whatever bounds the placeholder
case has to be about the candidate's own text, which is what `template` is.

The digest-of-staged-files boundary is a real v2 improvement — it would catch a placeholder that is
a plausible value, which the text rule cannot — and it belongs with the staging rework, not bolted
onto the sweep.

## What is knowingly left open

- **The placeholder list is a heuristic and a parameter**, like every threshold in the module. A
  handout that writes `COMPFEST18{solve_me}` where the Flag goes is not caught, because `solve` and
  `me` are not placeholder words. The rule catches the shapes two live submissions actually were,
  and is named as a floor rather than a proof.
- **The pattern half keys on brackets and escapes, not on quantifiers alone.** A base64 Flag holds
  `+` and `/` and `=` legitimately, so a bare `+` is not enough; only a quantifier bound to a class
  or group counts. A pattern written without a character class — `COMPFEST18{.+}` — is caught by the
  `.+` sequence, but an adversarially minimal one need not be. This is a decoy filter, not a parser.
- **`template` is refused even at `last_call`**, which is the one property it shares with the
  wrong-Flag ceiling and no other held strength. The tail argument — a slot nothing else will spend —
  does not rescue it, because the slot would be spent being wrong on a Board that prices a wrong Flag
  (ADR-0027). A pattern submitted at the end of a Run is a wasted slot and a rules exposure both.

## Revisit when

- **A real Flag is refused as a template.** The rule costs a solve silently where it fires wrongly,
  exactly as the record rule does. Nothing has measured how often a real Flag looks like a pattern,
  and the answer should be never.
- **v2 stages files with digests the sweep can reach.** Then *found in the handout unchanged* becomes
  available as the principled boundary this record rejected for v1, and it subsumes the placeholder
  half of the text rule.
