# The event namespaces the working directory, and it is Run input

**`/state/work` gains one directory per event — `/state/work/<event>/<challenge_id>/` — and this
record writes down the thing that made the missing namespace dangerous: the working directory is the
one path under `/state` that a later Attempt reads back as *input*.** Both halves answer
[#120](https://github.com/jerome-queck/incypher-ctf/issues/120). The path is a two-line change in
`solver/run.py`; the exception is a correction to three documents that all say `/state` is
write-only, and it is the half a reader should expect to argue with.

The alternative was to leave the key alone and purge `/state/work` between Boards. It is argued
against below, because it is the cheaper option and it is the one somebody will suggest again.

## Why a `challenge_id` is not an address

CTFd's `challenge_id` is a **per-installation auto-increment integer**. It is unique on the Board
that minted it and means nothing anywhere else, so two Boards are not two ranges — they are two
sequences from the same low numbers. Measured on this machine on 28 August 2026: the working
directories are `13, 37, 43, 55, 66, 69, 94, 145, 157`, all Brunner's, and COMPFEST mints its own
from 1. The overlap is a certainty rather than a risk.

Keyed by that integer alone, `/state/work/13` is one address holding two Challenges from two events.
Which is a *problem* only because of the second half.

## The working directory is read back, and nothing said so

[ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md) is explicit that `/state`
is *"a one-way pipe for output"* — *"the Solver never reads code or tools from it. Delete the
directory mid-run and the Solver keeps solving."* [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md)'s
*Two homes* rests its whole weight on that, and `CONTEXT.md` states it as a rule: *"Run state is
output, never input."*

The working directory is the deliberate exception, and it has been since the loop was written.
`solver/run.py` says so in `_staged` — *"the memory that crosses an Attempt boundary, so it is
[…] never cleared between Attempts"* — and `solver/prompt.py` tells the model *"Your working
directory is {workdir}. It is yours, it already holds this Challenge's files"*. An Attempt that is
Cut leaves its half-unpacked archives, its notes and its scripts there, and the next Attempt on that
Challenge is handed them. That is the design: [ADR-0023](0023-an-attempt-holds-the-turn-loop-and-order-is-not-asked-between-turns.md)
makes every turn a fresh spawn with no memory of the last, so the directory is *the* thing carrying
what was learned across the reset.

Say exactly how far the exception reaches, because it is narrower than it first sounds:

- **The Solver still reads nothing from `/state`.** Not code, not tools, not its own record. The
  read-back is the *model's*, of the model's own prior output, inside a directory the sandbox makes
  the one place it may write.
- **Deleting it mid-Run still costs the record and not the ability.** A Challenge whose working
  directory vanished is re-staged from Intake's copy on the next Attempt and worked from scratch. It
  loses memory, not capability, which is the claim ADR-0008 was making.

So ADR-0008's sentence survives as written and `CONTEXT.md`'s does not — *never input* is one word
too strong, and it is the word that made an unnamespaced key look harmless.

## Why not purge between Boards

Purging is genuinely cheaper today: `rm -rf state/work/*` before a cutover, no path change, no
orphans. It loses on one property, and it is the property this repository keeps choosing.

**It makes a manual step load-bearing for correctness, and the failure when it is skipped is
silent.** Nobody is at the keyboard at 13:00 ([ADR-0010](0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)),
and a skipped purge does not crash: Intake fetches COMPFEST's file correctly and records that it
did, `_staged` finds a same-named file already in `/state/work/13` and skips the copy, and the
prompt tells the model the Brunner artefact in front of it is this Challenge's own. The Run exits
clean. This is [#119](https://github.com/jerome-queck/incypher-ctf/issues/119)'s bug and the same
shape as [#99](https://github.com/jerome-queck/incypher-ctf/issues/99)'s *Blackboard*: the whole
verification ladder working perfectly on an input that was never what it claimed to be.

A namespace is not a better version of the purge — it removes the step. Two Boards under
`/state/work/brunnerctf-2026-global/` and `/state/work/compfest-2026/` cannot collide whether or not
anyone remembered anything, and a cutover needs no operator action at all.

The second reason is smaller and worth naming: the purge destroys evidence. 21G of `state/work` is
what four gate Runs and the model's own working left behind, and a post-mortem that wants to see
what the model built for Challenge 43 wants the directory still there.

## The event is a path segment, so it is checked at boot

`event` is a free string in a tracked `docs/competitions/<event>.board.json` and now names a
directory under `/state`. `solver/profile.py` refuses at boot anything that is not a plain segment —
a separator or a `..` would put a Board's memory outside the root that is namespacing it, which is
this record's own failure arriving by another door. Refused where the file is read rather than where
the path is built: `Rules` is what leaves that module, and a name rejected at boot costs a Run
nothing.

## Consequences

- **The nine existing directories are orphaned.** They are Brunner's, they are 21G, and nothing
  reads them after this change. That is now a **disk** question rather than a correctness one, which
  is the whole point: reclaim them with `rm -rf state/work/<id>` for the bare-integer directories
  when the volume needs it — 28 GiB free against a 22G `state/` — and skipping it costs space and
  never a wrong answer. Nothing moves them: re-keying them by hand under an event they were never
  labelled with is a guess, and a Run that wanted them can be pointed at a restored copy.
- **`CONTEXT.md`'s *Run state* entry changes**, and a **Working directory** term joins it. The
  glossary asserting *never input* is the same defect as ADR-0009 asserting it, and one PR fixes
  both or neither.
- **#119 is not fixed by this.** A same-named file that is not the Board's is still staged and
  handed over in silence; namespacing removes the cross-Board way of reaching that state and leaves
  the within-Board ones — a model that writes `clue-1.txt` into its own working directory, a Board
  that replaces a file mid-event. That is its own ticket and stays open.
- **A test now pins the shape.** `tests/test_run_loop.py` runs two Boards that both mint
  `challenge_id` 1 through one `workdirs` root and asserts each was handed its own bytes. It fails
  on the flat key, which is what makes it worth having.
- **An event rename orphans that event's directories**, exactly as a re-keying would. Accepted:
  the tracked file's `event` is the Board's identity across the image, the `runs/` promotion and now
  `/state`, and renaming it is already a bigger act than a path.

## Revisit when

- **A Board's `challenge_id` stops being an integer, or two events share an `event` string.** The
  first is a CTFd change and the second is ours to prevent; either makes the key underneath the
  namespace worth re-reading.
- **v2 bounds what accumulates in `/state`.** ADR-0009 left that open and named Observation bodies
  as the bulk of it. 21G says the working directories are a second bulk, and a cap that knows about
  the event boundary can expire a finished event's memory rather than a live Board's.
- **Concurrency at v3.** Two Attempts on one Challenge at once share this directory, and *the memory
  that crosses a boundary* stops having one writer.
