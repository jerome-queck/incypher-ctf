# FileVault is off on the build machine, the reboot recovers unattended, and the disk is the cost

[ADR-0012](0012-the-runtime-is-colima-and-filevault-is-the-wall.md) ended on a trade it named and
deliberately did not take: FileVault was on, macOS refuses automatic login while it is, and so a
Mac that lost power came back to a disk-unlock screen and started nothing. That record left the
decision with the Owner because it is a security decision about the machine holding the one secret
nobody can rotate.

**The Owner took it on 23 August 2026.** FileVault is off, automatic login is on, and this record
**supersedes ADR-0012 in part** — its FileVault section describes a machine that no longer exists.
Everything else in it stands: the runtime, the pin, the allocation, and the three container facts
that fail silently.

## The reboot path is now proven rather than argued

A real reboot, with `scripts/restart_probe.py` armed beforehand and nobody touching the machine:

- The host booted at **14:25:41**. `restart-probe` came back at **14:26:22** — **41 seconds**, on
  `--restart unless-stopped`, with no human in between.
- The VM came up **by itself** from its LaunchAgent, and `runtime.py verify` passed against it, so
  the machine that came back is the machine this repository pins rather than a default one.
- The **Codex login survived**. A container built fresh from the image, with no login of its own,
  reports `Logged in using ChatGPT` — the credential came off the host mount exactly as
  [ADR-0011](0011-the-sanctioned-path-is-the-only-path.md) designed, across a full host reboot
  rather than a container recreate.

The probe's verdict is what it is because the machine can now log itself in: the same clock
comparison on the old machine would have returned *recovery with a human in it*, and did.

## What this costs, stated plainly

**The disk is no longer encrypted at rest, and the threat model changed twice over.** Full-disk
encryption is gone, and automatic login means the machine boots straight to an unlocked desktop —
so physical possession is now full access, with no password anywhere in the path.

What is on that disk:

- **`.env.incypher`, carrying `TEAM_KEY`** — displayed by the Board rather than minted, with no
  rotate control and no working channel to the organisers. Its leak is the one this repository
  cannot undo.
- **`state/codex/auth.json`** — a live subscription credential, and one the container refreshes and
  rewrites during a run.
- **`.env`**, with the CTFd token and whichever inference credential is in play.

Before, losing the laptop meant losing hardware. Now it means losing all three, and one of them
permanently. That is the actual price of the 41 seconds above, and it is worth being able to say
out loud rather than discovering after the fact.

## Consequences

- **The exposure is physical now, so the control is physical.** Nothing in software mitigates an
  unlocked machine in someone else's hands. The machine's whereabouts on 21–22 September is the
  control, and there is no second one.
- **FileVault should go back on after the event.** Nothing here needs it off outside a scored run,
  and the recommendation is recorded so that turning it back on is a step somebody remembers rather
  than a thing that never comes up again.
- **`docs/credentials.md`'s leak column is now optimistic about `TEAM_KEY`.** It reasons about a
  credential leaking through a challenge or a commit; a stolen laptop reaches it directly, and no
  row in that table covers a machine rather than a value.
- **ADR-0012's FileVault section is history rather than guidance**, and is marked so at its head.
  Its reasoning was correct for the machine it was written about, which is why it stays.
- **The Solver image owes `bubblewrap` alongside `ca-certificates`.** `codex exec` warned it could
  not find `bubblewrap` on `PATH` and fell back to a bundled copy. It worked, and depending on a
  vendored fallback for the sandbox that runs challenge-supplied code is not a thing to leave to
  chance.

## Revisit when

- **The event is over.** Turning FileVault back on is the revisit, and it invalidates the reboot
  path recorded here — which is the correct trade once nothing is running unattended.
- **The scored run moves to a machine that is not this laptop.** Every number above is this
  machine's, and so is the physical-access argument.
- **The run stops being the only thing this machine does.** An unlocked desktop is a different
  proposition on a machine that also holds personal accounts, and that is a judgement the Owner
  makes with the exposure in view rather than one this record settles in advance.
