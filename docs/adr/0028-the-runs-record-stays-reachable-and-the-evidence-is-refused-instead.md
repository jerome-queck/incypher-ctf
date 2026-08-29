# The Run's record stays reachable, and the evidence is refused instead

**v1 does not take the Run's own record out of the solving model's reach. All three boundaries that
would were measured and each costs more than it buys, so the record stays readable and the Flag
sweep refuses to treat anything read out of it as evidence.**

[#144](https://github.com/jerome-queck/incypher-ctf/issues/144) was filed to build the boundary.
This records why it is not built, because *"we could not"* and *"we chose not to"* are different
facts and the next person to plan this needs the second one.

## The access is real and it is the model's, not a Challenge's

On `compfest-2026-seg1` the model ran `rg -n -i 'cookie|session=|…'` over
`/state/runs/*/stream.jsonl`, hunting for session material to reach a Discord-gated Flag. It found
the Solver's own record — every earlier submission, every command, the Board profile — and the sweep
read those Flags back as Observations of a real command in the current Attempt.

Three documents described this exposure as *challenge code runs as root and can read a file*
(ADR-0011, `docs/credentials.md`, `solver/credentials.py`). All three were corrected in
[#146](https://github.com/jerome-queck/incypher-ctf/issues/146): the reader is the agent that
drives the Run, and a boundary drawn around challenge-supplied code would not have been near it.

## Three boundaries, all measured

**Uid separation does not reach `/state`.** The remedy all three records named. `/state` is
`virtiofs`, and it does not enforce file modes:

    container filesystem, root-owned 0700, read as uid 1000 -> blocked
    /state bind mount,    root-owned 0700, read as uid 1000 -> succeeds, stat says mode=700 uid=0

The container-filesystem line is the control, and it is what makes this a fact about the mount
rather than about the test. `chmod` reports success, `ls` shows `drwx------`, and the read goes
through. Everything sensitive — the record, the working directory, `CODEX_HOME` — is under `/state`,
so a non-root uid buys none of it.

**A mount namespace needs a capability worth less than it costs.** Masking `/state/runs` for the
agent works regardless of modes, and it is unavailable: `unshare -m` answers `Operation not
permitted`, so it would take `--cap-add SYS_ADMIN` on the run command.

That is the option to reject on its merits rather than on its availability. `CAP_SYS_ADMIN` is the
broadest capability there is, and **this container executes arbitrary binaries a Challenge handed
it**. Granting it to hide a log file from the model would widen the boundary that matters — the one
around challenge-supplied code — to close the one that does not. It also makes the handover ask a
fourth thing, where `docs/credentials.md` already records that it is three and not one.

**Taking the record off the mount breaks the crash it exists for.** Writing the stream to the
container filesystem, where modes do work, and mirroring it to `/state` would contain it. But
`solver/record.py` reads that file back — `_resume_after_a_crash` — so a restarted container resumes
its sequence numbering from it, and `--rm` takes the container filesystem with it. Mirroring per
Attempt keeps the restart working and re-opens the leak for every completed Attempt, which is
exactly the `28-1` → `28-2` path that started this.

## So the evidence is refused

The access stays; what changes is that nothing read out of the record can authorise a submission.
A command that names the runs directory produces no candidates at all.

**On the command, not in the body.** A body rule was tried first
([ADR-0027](0027-one-flag-per-challenge-and-the-solver-bounds-what-the-board-does-not.md)'s
`NOT_EVIDENCE`) and it matches this module's `MARK` on a line — which `rg -o` defeats by printing
the matches and nothing else, measured at 47 strings walking through. A command cannot hide the path
it reads in the same way.

**Any Run's record, not only this one.** `compfest-2026-seg1` read `ctfd-probe`'s stream beside its
own, and a Flag from a Run that finished last week is no more this Challenge's answer.

It complements ADR-0027's crowding rule rather than repeating it. Crowding is a threshold and
catches the record being read *wholesale*; this catches one Flag lifted out of it, which is under
any crowd limit and exactly as much not-the-Solver's-work.

## What is knowingly left open

- **The record is still readable.** A model that wants the Board profile, or the list of commands an
  earlier Attempt ran, can have them. What it cannot do is turn any of it into a submission.
- **The rule is a heuristic and is named as one.** A model that `cd`s into the directory first, or
  reaches it through a variable, is not caught by a path match. Crowding is the backstop for the
  shape that matters, and neither is a boundary.
- **`$CODEX_HOME/auth.json` is unaffected.** ADR-0011's accepted exposure stands, now with the
  correction that no file mode closes it either.

## Revisit when

- **v2 draws the boundary for real** — a separate container for the agent, or a supervisor that owns
  the record. Both are ADR-0008's shape and neither is a v1 change.
- **The mount stops being `virtiofs`.** The measurement above is about Colima's mount and not about
  Docker; a runtime whose mount enforces modes makes uid separation the cheap answer again.
- **The path rule fires on an honest command.** It refuses evidence, so a false positive costs a
  solve silently. Nothing has measured how often a solving model touches `/state/runs`, and the
  answer should be never.
