# The container is the only sandbox v1 has

The Solver runs the vendor's CLI at `danger-full-access`, so challenge-supplied code executes as
root inside the container with nothing between it and `/state`. The container boundary is the whole
of the isolation, which is what
[ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md) already said it was — this
record is that sentence meeting the thing it implied and admitting the cost.

Nothing in ADR-0008 or [ADR-0011](0011-the-sanctioned-path-is-the-only-path.md) is reversed. What
changes is one default, `codex.Invocation.sandbox`, and one claim that has to be read more narrowly
afterwards.

## What was measured

`bubblewrap` is in the image because the CLI sandboxes with it, and the `Dockerfile` says so beside
the package. **It cannot build a sandbox inside an unprivileged container.** Measured on
26 August 2026 against a live login, inside the image, every configuration to hand:

| Configuration | What happened |
|---|---|
| `--sandbox workspace-write`, ordinary container | `bwrap: No permissions to create a new namespace` |
| `--security-opt seccomp=unconfined` | `bwrap: Failed to make / slave: Permission denied` |
| `+ --cap-add SYS_ADMIN`, `+ --security-opt systempaths=unconfined` | the same |
| `--enable use_legacy_landlock`, `--disable use_linux_sandbox_bwrap` | `legacy-landlock incompatibility` |
| `--privileged`, `--sandbox workspace-write` | **a command ran** |
| ordinary container, `--sandbox danger-full-access` | **a command ran** |

The Colima VM is not the obstacle — `kernel.unprivileged_userns_clone` is `1` and
`user.max_user_namespaces` is 63658. Docker's own confinement is, and only `--privileged` lifts
enough of it. So there are two working configurations and no third.

## Why this one

**`--privileged` spends a strong boundary to buy a weaker one.** A privileged container is close to
root on the VM: it keeps the CLI's inner sandbox and gives up most of what the outer one was worth.
The alternative keeps Docker's ordinary confinement — well understood, and the thing ADR-0008
already nominated as the isolation — and gives up a second boundary *inside* a boundary we are
keeping. Losing the inner one is the smaller loss.

It is also the more honest of the two. v1 already runs as root, already accepts that
`$CODEX_HOME/auth.json` is readable by challenge code (`docs/credentials.md`), and already names
**v2's uid separation** as the boundary that closes that. This is the same hole, seen from the other
side, closed by the same work.

## What it costs, exactly

- **The Observation log is no longer append-only as a matter of the filesystem.** Challenge code can
  write anywhere in the container, `/state/runs/<run_id>` included. `codex.run_attempt` still refuses
  to start where the Run's record sits inside the working directory, which holds the accidental case
  — a model unpacking an archive over its own evidence — and not a determined one.
  [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md)'s split stands as a discipline
  the Solver keeps; it stops being one the kernel enforces.
- **A leaked credential is a leaked credential either way.** Nothing here widens that, because
  bubblewrap's read-only view of `/state` never hid `auth.json` from a root process anyway.
- **The blast radius of a hostile Challenge is the container.** Which is what it was before, for
  every route that did not go through `bwrap`.

None of it is new exposure so much as exposure that was believed mitigated and is not.

## The alternatives, and why not

- **Run `--privileged`.** Rejected above. It also has to be remembered at the command line, and a
  Run that forgets it fails every command with nobody watching — the failure this whole record came
  out of.
- **Ship our own sandbox.** A seccomp profile or a uid split is v2's work
  (`docs/credentials.md`), and doing it here would be building v2 to unblock v1's gate.
- **Drop `bubblewrap` from the image.** No. The package stays: nothing is gained by removing it, and
  v2's uid separation is what brings it back into use.
- **Wait for a runtime that allows it.** The gate is bound to a venue and a date
  ([ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md)); a boundary we cannot get today
  is not a reason to spend a version.

## Consequences

- `codex.Invocation.sandbox` defaults to `danger-full-access`, and the reason is written at the
  constant rather than here alone, because that is where somebody will meet it.
- **A pre-flight has to run inside the image.** This failure and two others behind it —
  a model id the subscription refuses, and a CLI shipped in two halves — all pass a check run on the
  host, because the host has a working CLI and no container confinement.
  `scripts/codex_probe.py` runs in the image and proves a command executed, which is the only shape
  of check that catches any of them.
- **v2 owns the boundary.** Uid separation closes the record and the credential together, and this
  record is the second reason to do it.
