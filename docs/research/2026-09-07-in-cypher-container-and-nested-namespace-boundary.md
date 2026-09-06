# IN-CYPHER container and nested-namespace boundary — 7 September 2026

## Question

For [**The Worker, control, and credential trust boundary**](https://github.com/jerome-queck/incypher-ctf/issues/190): does IN-CYPHER require one Solver container, and why might one container still need inner mount, PID, and network namespaces for hostile Attempt executors?

## Result

**Design for one top-level submitted/running Solver container, but do not call “exactly one” an
express rule yet.** The organiser consistently describes one agent submitted “as a Docker
container” and running “inside that container” ([current captured rules](../competitions/incypher-2026-hackathon.rules.txt#L14-L23)). That strongly supports one top-level container containing multiple supervised processes. The published material does **not** specify an image count, container count, sidecars, nested containers, Docker socket, runtime flags, capabilities, or host kernel.

Both committed official-text baselines were refreshed against their live sources on 7 September
SGT with `scripts/check-rules-drift.sh`; both were unchanged. The promised Starter Pack, ADK, and
runnable demo remain scheduled for 14 September at 10:00 SGT, and no link is yet published on the
[official agenda](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/hackathon-agenda/).

**“Docker blocks nested namespaces” was too broad.** Linux supports nested namespaces, including
an unprivileged user-namespace route. The precise repository fact is narrower: on the Solver image
and Colima/Docker configuration measured on 26 August, Bubblewrap failed in every ordinary or
partially relaxed configuration tested and succeeded only with `--privileged`; the unsandboxed
Codex invocation also succeeded ([measurement](../adr/0018-the-container-is-the-only-sandbox-v1-has.md#what-was-measured)). This research did not reproduce that experiment: the local Colima profile currently reports `Broken, not Running`.

## What the organiser has and has not said

### Confirmed text

- The canonical page says the team submits its autonomous agent as a Docker container and that it
  runs inside that container on competition day. It also grants broad freedom in tools and
  frameworks ([official page](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/); [verbatim capture](../competitions/incypher-2026-hackathon.rules.txt#L14-L23)).
- How to play says the platform is where the agent runs and that it runs fully autonomously. Its
  later references to “your own container” and “someone else’s container” concern per-player
  **Challenge Target instances**, not the Solver ([capture](../competitions/incypher-2026-hackathon.how-to-play.txt#L13-L27), [Target wording](../competitions/incypher-2026-hackathon.how-to-play.txt#L40-L60)).
- The agenda promises an ADK and runnable demo through the hackathon website on 14 September. As of
  this check, the agenda phrase is text rather than a download link, and the official landing page
  exposes no Starter Pack, ADK, or demo link.

### Strong inference, not quotation

The singular submission/run wording makes **one top-level Solver container** the safest intended
deployment unit. Several processes inside it are compatible with the text. A sibling Solver,
sidecar, Compose stack, nested Docker daemon, or replacement container is not expressly forbidden,
but neither is it promised to be available.

Still unstated: whether submission means an image, registry reference, tarball, Dockerfile, or
running container; who runs it and with which flags; whether more than one image/container is
accepted; and whether `--privileged`, added capabilities, user namespaces, seccomp changes, or
host-side orchestration are available. Those require the ADK/runtime contract or an organiser
ruling.

## Why inner namespaces help within one container

These are kernel compartments between a trusted supervisor and hostile Challenge-derived code;
they do not create a second Docker container.

| Boundary | What Linux isolates | Benefit for an Attempt executor | What it does not solve |
| --- | --- | --- | --- |
| Mount namespace | The mounts and filesystem view; later mount changes normally do not affect another namespace ([Linux man-pages](https://man7.org/linux/man-pages/man7/mount_namespaces.7.html)) | Construct a minimal root exposing one workdir and selected read-only tools, while omitting `/state`, control sockets, credentials, other workdirs, and `/opt/solver` | A careless bind mount still exposes data; pathname Unix sockets are protected by filesystem visibility/permissions, not the network namespace |
| PID namespace | Process-ID space; its PID 1 adopts orphans, and killing that PID 1 kills the namespace ([Linux man-pages](https://man7.org/linux/man-pages/man7/pid_namespaces.7.html)) | The Attempt cannot enumerate or signal control/credential processes; the supervisor can tear down the Attempt process tree as one unit | Resource limits belong to cgroups; identity/permission checks still need separate UIDs and dropped capabilities |
| Network namespace | Interfaces, protocol stacks, routes, firewall rules, ports, and abstract Unix sockets ([Linux man-pages](https://man7.org/linux/man-pages/man7/network_namespaces.7.html)) | Give an Attempt no direct egress, or only a narrow path to Target and inference proxies, while hiding control listeners | It does not hide pathname Unix sockets or enforce application-level broker authority |

UID separation, seccomp/Landlock policy, cgroups, broker authorization, and namespace teardown remain
separate controls. No one namespace makes hostile execution safe.

## Why creation is blocked, and why broad privilege is a poor fix

Linux normally requires `CAP_SYS_ADMIN` to create mount, PID, or network namespaces. A user
namespace is the exception: an unprivileged process can create one and receives capabilities
*inside that new user namespace*; creating the user namespace and other namespaces together can
therefore avoid `CAP_SYS_ADMIN` in the original namespace
([`unshare(2)`](https://man7.org/linux/man-pages/man2/unshare.2.html)). Bubblewrap uses that mechanism,
always creates a mount namespace, and can add PID/network namespaces
([Bubblewrap README](https://github.com/containers/bubblewrap/blob/main/README.md)).

An ordinary Docker container adds more gates. Docker's default seccomp policy denies `unshare`
(apart from its user-namespace exception) and `setns`; Docker also omits `SYS_ADMIN` and
`NET_ADMIN` from its default capability set
([seccomp](https://docs.docker.com/engine/security/seccomp/), [runtime capabilities](https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities)). On this repository's measured runtime, removing seccomp changed Bubblewrap's failure but did not make its required mount operation work; adding `SYS_ADMIN` plus relaxed system paths still did not suffice. That result shows layered Docker confinement, not a universal Linux ban.

`SYS_ADMIN` is unusually broad: besides namespace creation, it covers mounts, `pivot_root`, many
filesystem/device operations, and more ([`capabilities(7)`](https://man7.org/linux/man-pages/man7/capabilities.7.html)). `NET_ADMIN` permits interface, firewall, routing, and promiscuous-mode changes. Network-namespace **creation** needs `SYS_ADMIN`; `NET_ADMIN` is for configuring the resulting network domain. These capabilities should never remain in the hostile executor. A trusted launcher could create the boundary and then irrevocably drop capabilities before `exec`, but granting them at the outer container level enlarges what a compromised launcher can do and must be prototype-proven on the actual competition runtime.

`--privileged` is much larger again: Docker gives the container all capabilities, all host devices,
and reconfigures AppArmor/SELinux so it has nearly the host access of an outside process
([Docker](https://docs.docker.com/engine/containers/run/#runtime-privilege-and-linux-capabilities)). In
the local Colima setup that means spending much of the **outer container-to-VM boundary** merely to
recover an inner sandbox; on an organiser host it could expose that host/shared infrastructure and
may not be offered at all. It is therefore neither a justified rule assumption nor a safe default.

## Decision consequence

Keep the one-top-level-container requirement for issue 190. Treat the exact inner isolation
mechanism as a prototype gate, not as already settled:

1. first test rootless user+mount+PID+network namespace creation under the real ADK/runtime;
2. if unavailable, test a trusted minimal launcher with only the smallest required outer authority,
   followed by verified capability drop before hostile code;
3. reject any design that requires hostile executors to retain `SYS_ADMIN`/`NET_ADMIN`, or the
   whole Solver to run `--privileged`;
4. combine namespaces with UID, cgroup, syscall/filesystem, broker, and teardown controls;
5. ask organisers explicitly whether one top-level container is the only allowed runtime unit and
   which Docker flags/capabilities/user-namespace features they provide.

Thus the user is right about the design posture—**assume one competition container**—while the
literal rule claim remains: singular intended form, exact count and privileges not yet specified.
