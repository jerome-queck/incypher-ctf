# The competition runtime lives on the Working volume with bounded growth

Issue [#371](https://github.com/jerome-queck/incypher-ctf/issues/371) moves the machine that builds
and runs the Solver without turning a larger disk into permission to accumulate indefinitely.

## Decision

The canonical Solver checkout is `/Volumes/Working/001 Projects/incypher-ctf`; the practice rig is
its sibling `incypher-practice-rig`. Colima data physically lives at sibling `incypher-colima` and
is reached through `~/.colima` so interactive commands, Docker contexts and the launchd service keep
one stable socket/config address. The VM explicitly mounts `$HOME` and `/Volumes/Working/001
Projects` writable. No other host root is a supported Solver bind source.

The pinned VM remains 8 CPUs, 24 GiB memory and **100 GiB disk**. The Working volume must be mounted
before Colima starts; a missing volume is a Refusal, never a fresh internal VM.

The VM envelope is divided before work begins:

| Class | Hard budget |
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

Development prunes BuildKit back toward 10 GiB before it reaches the 20 GiB hard limit. A scored
Run builds its immutable image, prunes BuildKit, then refuses unless cache is zero and the entire
image store is at most the 24 GiB Candidate-plus-rollback budget. The release process owns those
two identities. Repeated tags, dangling images and obsolete build references are reconstructible;
release identities, receipts and current Run authority are not.

`python3 scripts/check_host_storage.py development` is the pre-build refusal; `competition` is the
pre-Run refusal. When development cache crosses its limit, the bounded repair is `docker builder
prune --all --force --reserved-space 10GB` followed by `docker image prune --force` and another check.
Competition uses `docker builder prune --all --force`, then the check; neither command removes a
tagged Candidate or rollback image.

The host checker enforces the limits observable from the host: total and single-image size, cache,
active state size and Working-volume reserve. Resident/profile deltas are build-admission facts,
not values Docker's aggregate inventory can recover; their receipts must establish the smaller
limits before an image becomes either release identity.

Run state is a Working-volume bind mount rather than VM-disk consumption. Its active envelope is
60 GiB, with 100 GiB of physical-volume free space protected from project and VM growth. An amd64
artifact is exported and verified rather than retained beside both host-platform images.

The image budgets measure unpacked Docker size, not package count. Full Sage made issue #299's
strict image 10.6 GB with a 9.3 GB unique layer, exceeding the 4 GiB profile-delta budget. That
profile remains unadmitted unless a smaller locked closure passes its declared fixtures or this
budget is explicitly revised. Tool downloads remain build-time, locked and receipt-bearing; no
runtime download or larger VM silently widens that rule.

The old `state/work/157` directory is a bare-integer, pre-event namespace that current code reads
nowhere. It is retired under ADR-0025 after the migration copy is verified; promoted `runs/`, the
current credential store and issue #299's dirty worktree remain.

## Consequences

- Losing or renaming the external volume stops the runtime. This is preferable to silently starting
  an empty internal VM or empty `/state` mount.
- `~/.colima` is a compatibility address, not internal storage; its target is verified at preflight.
- A 1 TB physical volume does not make a 100 GiB image acceptable. Image and profile admission fail
  at their smaller budgets.
- Build caches improve iteration only inside their budget and carry no competition authority.

## Revisit when

- the official venue requires a different host filesystem or image transport;
- measured full-window state exceeds 60 GiB under the sealed retention policy; or
- Candidate/rollback host-platform storage cannot fit 24 GiB without dropping a proved Core
  capability.
