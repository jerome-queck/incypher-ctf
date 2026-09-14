# The competition runtime lives on the Working volume with bounded growth

Issue [#371](https://github.com/jerome-queck/incypher-ctf/issues/371) moves the machine that builds
and runs the Solver without turning a larger disk into permission to accumulate indefinitely.

> **Host disk sizing and host storage admission are superseded by
> [ADR-0058](0058-host-storage-follows-physical-capacity-and-post-merge-reclamation.md).** The
> location, mount, CPU/RAM and execution-authority decisions here remain context; the image, cache,
> state, single-image and Crypto profile-size caps are historical advisory benchmarks.

## Retained placement decision

The canonical Solver checkout is `/Volumes/Working/001 Projects/incypher-ctf`; the practice rig is
its sibling `incypher-practice-rig`. Colima data physically lives at sibling `incypher-colima` and
is reached through `~/.colima` so interactive commands, Docker contexts and the launchd service keep
one stable socket/config address. The VM explicitly mounts `$HOME` and `/Volumes/Working/001
Projects` writable. No other host root is a supported Solver bind source.

The VM keeps 8 CPUs and 24 GiB memory. The Working volume must be mounted before Colima starts;
a missing volume is a Refusal, never a fresh internal VM. Disk capacity follows ADR-0058.

## Superseded storage policy — historical only

On 13 September 2026 this decision requested a 200 GiB VM and divided storage into the following
budgets. ADR-0058 removes every size refusal and all pre-merge reclamation from this policy.
These values describe the former decision and must not be used as admission or cleanup rules.

| Class | Former budget, now advisory |
| --- | ---: |
| Candidate plus rollback on the host platform | 24 GiB |
| Total development image store | 40 GiB |
| One Candidate platform | 12 GiB |
| Resident Tool floor | 3 GiB |
| Any one Tool profile delta | 4 GiB |
| Rig, trusted control and Recovery | 20 GiB |
| Development BuildKit cache | 20 GiB |
| Docker/runtime overhead and scratch | 20 GiB |
| Untouchable VM headroom | 16 GiB |
| Additional unallocated VM capacity | 100 GiB |
| Active Run state on the Working volume | 60 GiB |
| Working-volume free-space reserve | 100 GiB |

The former policy pruned development cache toward 10 GiB and demanded zero cache before a scored
Run. It also refused images and Crypto profile deltas above their budgets. None of those actions
or refusals remains authorized. Both host-checker modes now report advisory size observations;
use the post-merge reclamation workflow in ADR-0058 only after ticket completion is verified.

Full Sage originally produced a 10.6 GB image with a 9.3 GB unique layer, exceeding the former
4 GiB profile-delta budget. Size alone no longer rejects such a profile. Tool downloads remain
build-time, locked and receipt-bearing, with fixtures and execution authority verified independently.

The old `state/work/157` directory is a bare-integer, pre-event namespace that current code reads
nowhere. It is retired under ADR-0025 after the migration copy is verified; promoted `runs/`, the
current credential store and issue #299's dirty worktree remain.

## Consequences

- Losing or renaming the external volume stops the runtime. This is preferable to silently starting
  an empty internal VM or empty `/state` mount.
- `~/.colima` is a compatibility address, not internal storage; its target is verified at preflight.
- Image, profile, state and cache sizes follow available physical capacity under ADR-0058.
- Useful build material remains until the ticket's squash merge is confirmed.

## Revisit when

- the official venue requires a different host filesystem or image transport;
- the physical volume or runtime storage arrangement changes.
