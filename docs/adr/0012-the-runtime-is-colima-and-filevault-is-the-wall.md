# The runtime is Colima, pinned here, and FileVault is what an unattended reboot runs into

> **Superseded in part by [ADR-0013](0013-filevault-is-off-and-the-reboot-recovers-unattended.md).**
> The runtime, the pin, the allocation and the three silent container facts all stand. The
> **FileVault** section below does not: it describes a machine on which automatic login was
> refused, and the Owner turned FileVault off on 23 August 2026. The unattended reboot this record
> calls unreachable was proven the same day — 41 seconds, no human — and what it costs is recorded
> in ADR-0013.
>
> **The allocation was amended on 5 September 2026.** The Owner accepted the observed 8 CPU,
> 24 GiB memory and 100 GiB disk VM as the pin. Runtime choice, version pins and restart conclusions
> are unchanged.
>
> **The location, mount set and disk allocation are superseded by
> [ADR-0057](0057-the-competition-runtime-lives-on-the-working-volume-with-bounded-growth.md).**
> ADR-0057 increased the sparse external VM disk to 200 GiB on 13 September 2026 without widening
> its independent storage budgets.
>
> **Host disk sizing and host storage admission are now superseded by
> [ADR-0058](0058-host-storage-follows-physical-capacity-and-post-merge-reclamation.md).** The
> current `start` command requests 700 GiB initially; CPU and memory remain exact, while actual
> disk capacity is observed rather than treated as runtime drift.

[#49](https://github.com/jerome-queck/incypher-ctf/issues/49) opened on a fact rather than a
question: **there was no container runtime on the build machine at all** — no `docker` binary, no
`Docker.app`, an empty `~/.docker`. The deliverable is a container and
[ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md) gates v1 on one unattended run, so
this blocked the gate rather than the design.

The runtime is **Colima 0.10.3** with the **Docker CLI 29.7.2**, both pinned in
[`scripts/runtime.py`](../../scripts/runtime.py), the VM's allocation with them, and
`brew services` starting it at login. That is the smaller half of the record. The larger half is
what proving the restart path turned up, and it does not have a happy ending.

## Colima, on grounds that survive being checked

The ticket's three reasons held, and two of them are now measured rather than quoted:

- **The CPU and memory allocation are reviewable lines, not sliders.** `PIN` in
  `scripts/runtime.py` names 8 CPUs and 24 GiB; its disk field is only the initial start request,
  and `runtime.py verify` reports the observed capacity without rejecting its size.
  Docker Desktop's equivalent is GUI state on one laptop that no diff can read. This is the same
  argument [ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md) made for the
  event being a config file, and it is the reason the pin is worth more than the runtime choice.
- **About 190 MB resident, idle, with two containers up** — measured on this machine, better than
  the ~400 MB the ticket estimated and far under Docker Desktop's 2 GB+. That headroom is the
  point while [#13](https://github.com/jerome-queck/incypher-ctf/issues/13) keeps a local model
  alive as the egress contingency.
- **MIT, so there is no licensing question to answer** rather than one whose answer is probably
  fine.

The launchd caveat the ticket raised did not bite. `brew services` runs `colima start -f`, which
stays in the foreground — the shape launchd wants — and the service brought the VM up on the pinned
allocation with `verify` passing against it. `runtime.py enable-at-login` stops a hand-started VM
first, because against a running VM `colima start -f` returns immediately and
`keep_alive successful_exit` turns that into a restart loop ([colima#490][490]).

## What the restart path proved, and where it stops

Three of the four links hold, unattended and by measurement rather than by eye:

- **`--restart unless-stopped` survives the VM going down.** Both probe containers came back on
  their own after `colima stop` and a fresh start.
- **`--env-file` keeps its values with the file deleted.** ADR-0010 marked this *expected,
  unverified*; it is now verified. Docker resolves the file at `docker run` into the container's
  config, so `docker start` re-authenticates with nothing on disk — and `docker inspect` prints
  every value, which is the same fact wearing its other face.
- **`codex login --device-auth` reaches a one-time code inside the container**, with no browser and
  no localhost callback, exactly as [ADR-0011](0011-the-sanctioned-path-is-the-only-path.md)
  requires. The flag exists but is undocumented in `--help`.

The fourth link is the one that matters and it is broken, on this machine, for a reason no amount
of scripting reaches:

**FileVault is on, and macOS refuses automatic login outright while it is** — `sysadminctl
-autologin status` says so in those words. The VM starts from a *user* LaunchAgent, and a
LaunchAgent runs at user login rather than at boot. So a Mac that loses power at 03:00 comes back
to a disk-unlock screen and starts **nothing**: no VM, no daemon, no container, no run. Auto-login
is not a setting anybody forgot to enable here; it is unavailable.

`fdesetup supportsauthrestart` returns true, so a **planned** reboot has a path —
`sudo fdesetup authrestart` skips the unlock screen once. That covers rebooting the machine
deliberately before the event. It does nothing for the unplanned reboot, which is the only kind
that happens during a scored run.

**The trade is named here and deliberately not taken.** Turning FileVault off would buy unattended
recovery on the machine that holds the **team key** — the one secret in `docs/credentials.md` that
cannot be rotated, because the Board displays it rather than minting it. Trading full-disk
encryption for reboot resilience, on that machine, is the Owner's call and not a script's; nothing
in this repository turns it off, and `scripts/setup-runtime.sh` presents the choice rather than
making it.

## Consequences

- **The unattended-reboot criterion is open, not met, and the instrument for closing it is
  committed.** `scripts/restart_probe.py` arms a container before a reboot and reaches a verdict
  after one by clock: a container whose last start is later than the host's boot came back by
  itself. Two distinctions in it are the whole reason it is a script rather than a glance at
  `docker ps`. It separates *unattended* from *recovered* — on a machine that cannot log itself
  in, a container that came back came back **after somebody typed a password**, which is not what
  #49 asks for, and the verdict says so rather than passing. And it separates *not yet* from
  *no*: a host that has not rebooted is pending, a container that did not return is a failure,
  and only the first re-arms — because re-arming after a real failure destroys the only evidence
  of it.
- **Three container facts, each of which fails silently.** All three would look like working setups
  and then lose a credential or die mid-run:
  - **Colima mounts only declared host roots.** A `-v` from any path outside them does not
    error — the container gets an *empty directory*. ADR-0011 puts `CODEX_HOME` on that mount, so a
    repository living outside `$HOME` would take the login, appear to work, and lose it on the next
    `docker rm` with nothing said.
  - **`CODEX_HOME` must exist, not merely be writable.** Against a missing path the CLI refuses to
    load configuration at all and never reaches the login.
  - **The image must ship `ca-certificates`.** `node:*-slim` carries no system trust store; Node
    has its own roots so `npm install` succeeds and hides it, while the Codex CLI is a native
    binary that reads the system store and dies on `error sending request for url` with working
    egress and nothing else to go on. This binds the Solver's `Dockerfile` when it arrives.
- **`state/` is gitignored, and that is a credential control rather than tidiness.** The container
  mints its own `auth.json` there (ADR-0011). It is the `.env` rule applied to a directory.
- **The pin is only true while something checks it.** Colima keeps whatever allocation it was last
  started with, so a hand-run `colima start --cpu 2` would stick silently. `runtime.py verify` is
  what makes the committed number the real one, and `brew pin` stops `brew upgrade` moving either
  tool underneath it.
- **Being pinned means updates are now a decision.** A pinned formula does not move, including for
  a fix we want. That is the trade taken knowingly: the machine matching the repository is worth
  more than being current.
- **`colima start` registered qemu emulators for `linux/amd64` and `linux/386`.** Unlooked-for and
  worth knowing on an arm64 build machine, since a pwn challenge ships x86-64 ELFs.

## Revisit when

- **The FileVault question is answered either way.** If it is turned off for the event, the
  unattended-reboot path becomes reachable and `restart_probe.py check` is what closes it. If it
  stays on, the accepted risk should be written down as accepted rather than left implied.
- **A run needs more than 8 CPUs or 24 GiB** — one line, a `colima stop`, and a start.
- **The Solver stops being the only thing in the container.** ADR-0008 makes it PID 1 with no
  supervisor until v2; a supervisor changes what "came back" has to mean, and the probe's verdict
  with it.
- **The build machine stops being this Mac.** Every number here is measured on a 14-core, 48 GiB
  laptop, and the FileVault wall is that machine's, not Colima's.

[490]: https://github.com/abiosoft/colima/issues/490
