# A v2 Run requires the strict hostile-execution profile

[Reconcile accepted safety policy and ADR supersession
pointers](https://github.com/jerome-queck/incypher-ctf/issues/254) records the disposition already
accepted in [Which hostile-execution profile works on the pinned
runtime?](https://github.com/jerome-queck/incypher-ctf/issues/247#issuecomment-5614166506).
**One exact strict Isolation profile is sealed for a release candidate claiming v2. If it cannot
initialise or any required allow/deny preflight fails, the Solver Refuses before spending Board or
inference capacity. It never degrades automatically to weaker isolation.**

This sharpens ADR-0041's profile admission and failure semantics without changing its authority
ceiling. A different mechanism may belong to a different candidate only after it independently
proves the same complete principal, filesystem, process, network, syscall, credential and Resource
matrix. A running candidate does not switch profiles after its digest is sealed.

## The partial candidate is a separate final choice

At final go/no-go, the owner may explicitly select a separately sealed broker-only partial
candidate. It disables model-authored commands, Challenge executables, local parsers over hostile
bytes, tool-using Specialists and every Gate-qualified isolation claim. Its manifest records the
missing Core isolation obligation and states that the candidate is not v2.

That partial is neither a runtime fallback nor evidence that the strict profile works. It cannot
inherit a v2 pass, an Isolation receipt or a Gate result from a different profile. Selecting it is
an honest fielding decision under deadline pressure; it does not redefine Core or turn missing work
into v3.

## The prototype is feasibility evidence only

The pinned arm64 Colima prototype passed 19 hostile probes and enforced memory, PID, CPU, I/O and
temporary-storage limits with a UID-separated executor, empty capability bounding set,
`no_new_privs`, inner seccomp, private namespaces, a minimal read-only root, bounded writable work,
a dedicated cgroup v2 subtree and a peer-authenticated Target-broker socket. Those observations show
one direction is feasible on that measured host. They are not production or release proof.

Production must replace the prototype set-id `unshare` with a fixed-policy launcher; prove the full
Tool surface and both architectures; drop trusted-service capabilities; survive reboot; pass
hostile Target and full-rig probes; and receive the required outer runtime contract. Failure at any
required proof keeps v2 incomplete. If the exact sealed profile cannot initialise or pass its
startup preflight, the Solver Refuses before a Run begins.

## Consequences

- The release-candidate manifest has one isolation-profile digest and no silent reduced mode.
- Preflight failure spends no Board or inference capacity and produces no misleading Gate sample.
- The final go/no-go may still field a bounded partial while reporting exactly which v2 claim it
  lacks.
- Prototype observations, accepted policy, merged decision documentation, implementation and
  release proof remain separate facts. Merging this ADR records policy only.

## Revisit when

- Official runtime evidence changes the outer capability contract.
- A different full-strength mechanism proves the complete ADR-0041 authority matrix.
- The final go/no-go considers the explicitly non-v2 broker-only partial.
