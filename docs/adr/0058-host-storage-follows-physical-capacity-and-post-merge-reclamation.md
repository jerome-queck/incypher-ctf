# Host storage follows physical capacity and post-merge reclamation

Issue [#376](https://github.com/jerome-queck/incypher-ctf/issues/376) removes arbitrary host-size
refusals that stopped useful image builds while preserving the runtime's placement and execution
authority.

This record supersedes the host sizing and admission parts of
[ADR-0057](0057-the-competition-runtime-lives-on-the-working-volume-with-bounded-growth.md), which
links back here. ADR-0057 remains the historical record of why the Working volume and external
Colima data directory are authoritative.

## Decision

The Working volume measured 931 GiB total, 40 GiB used and 891 GiB free on 14 September 2026. The
initial Colima disk request is 700 GiB, leaving about 191 GiB of nominal host headroom at that
measurement. That request is a starting capacity choice, not a runtime identity or admission
limit: `runtime.py verify` holds CPU 8 and memory 24 GiB exact while reporting the observed disk.
The mount location, `~/.colima` link, and VM writability checks remain refusals.

`python3 scripts/check_host_storage.py development` and `competition` still inventory Docker
images, BuildKit cache, single-image size, Run state and physical free space. All of those values
are advisory observations in both modes and never block admission. Runtime placement and
availability remain the only host checks that can refuse a lifecycle phase.

The 4 GiB Crypto profile delta is also an advisory measurement retained in historical receipts.
`CryptoProfileSize` continues to bind baseline, candidate, arithmetic, provenance and measurement
method, while the signed Tool receipt still requires the exact image, isolation proof, capability
fixtures and per-worker Resource envelopes. Removing the host delta refusal does not widen a
worker's CPU, memory, filesystem, PID, wall-clock or network envelope.

Build, qualification, pre-Run and pre-merge paths do not prune Docker storage. After all in-flight
ticket work is merged, an operator first inspects the remaining images and cache for useful
unfinished work, then runs:

```sh
python3 scripts/reclaim_development_storage.py --pr <merged-pr>
```

The command verifies the named PR through `gh pr view` and requires `MERGED`, `mergedAt` and a
merge commit before it executes. It also requires no other open PRs, clean worktrees and no running
containers, so an unfinished ticket or unreviewed local change blocks reclamation. It then runs
`docker builder prune --all --force` and `docker image prune --force`; those operations target disposable BuildKit
cache and dangling images. Tagged Candidate/rollback images, Run state and authority records are
outside the command. An open PR, missing merge identity, dirty worktree or failed runtime
verification leaves storage untouched. If the first prune succeeds and the second fails, the
command reports partial reclamation, stops and never retries or broadens its scope.

## Consequences

- Builds can consume available image, cache and physical capacity; the checker reports old
  ADR-0057 benchmarks so growth is visible without turning them into refusals.
- Disk capacity can follow the host, while CPU/RAM and all strict worker Resource contracts remain
  bounded and reviewable.
- Reclamation is a deliberate lifecycle action after the work it could destroy has merged, rather
  than an admission side effect that can erase useful intermediate state.

## Revisit when

- the competition environment or Working-volume authority changes; or
- the runtime allocation or artifact policy changes.
