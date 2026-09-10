# Native Codex leads, and one small loop owns the CPA route

> **The fixed native-primary selection is superseded by
> [ADR-0051](0051-v2-has-a-finite-core-and-proof-earned-capability-packs.md).** Native and CPA are
> both Core and a sealed release-candidate qualification selects either as primary. The minimal
> Solver-owned CPA Harness, shared-capacity boundary and rejected alternative Harnesses stand.

[Which secondary harness survives a Solver-shaped
trial?](https://github.com/jerome-queck/incypher-ctf/issues/184) compared native Codex, Oh My Pi
(OMP), OpenCode and a minimal Solver-owned Responses loop under the same shell task and deterministic
success, malformed-stream and quota responses. The trial then exercised a closed-schema non-shell
tool, a process death after a durable checkpoint, context growth and a live native-Codex baseline.

The decision: **native Codex remains the primary Inference route; the private single-owner CPA route
uses one minimal Solver-owned Responses Harness; OMP and OpenCode leave the design.** The CPA route
is a candidate, not extra capacity or a scored fallback, until its installation and rehearsal ticket
proves the real transport, containment, redaction, route-change and shared-quota contract.

This resolves the open Harness choice in ADR-0014 and ADR-0032. It does not replace their
Attempt-shaped Adapter seam, Codex-only inference set, route-failure classification or prohibition
on treating CPA as another quota pool.

## Why the full Harnesses lose

All three secondary candidates executed the exact shell tool and preserved its bytes against the
fake Responses endpoint. Success-path compatibility therefore did not decide the trial; failure
ownership did.

OMP 18.1.10 returned exit zero after a malformed event stream. On a quota response it made five
requests until its own deadline and again exited zero. That hides failure from Recovery and makes
the Solver infer success from a process that produced neither a completed response nor a Step.

OpenCode 1.18.29 repeated the same malformed request 454 times before the prototype's ten-second
process-group kill. It made six requests on quota before the same external cut. A Harness inside the
Solver must own its request and retry bound; an outer kill cannot make an internal request storm an
acceptable default.

The two binaries also added 135,031,680 and 144,107,234 bytes respectively before their plugins,
state and integration seams. Their broader capabilities buy nothing after the decisive failure
contract is lost.

## Why the small loop advances

The standard-library prototype stopped after one malformed or quota response with a non-zero exit,
executed both shell and non-shell strict tool schemas, and exposed every request, event, tool result,
usage record and context-size change. After a deliberate process exit 75, its checkpoint restored
the completed model event and executed the tool without repeating the first request. The branch
[`prototype/secondary-harness-trial`](https://github.com/jerome-queck/incypher-ctf/tree/prototype/secondary-harness-trial)
retains the
throwaway executable report as the primary source; none of that prototype enters production.

Native Codex 0.153.2 with `gpt-5.6-sol` completed the same shell task in 14.63 seconds and reported
33,277 input tokens, of which 27,008 were cached, plus 185 output and 28 reasoning-output tokens.
The account's coarse weekly usage remained 91 percent before and after, so the run proves event-level
usage recording but not an exact subscription decrement. Native remains primary because it is the
supported, mature Harness; the second loop exists only to remove its CLI/tool-loop failure domain.

## Consequences

- v2 specifies only two Harnesses: native Codex and the minimal CPA Responses Harness.
- The CPA Harness owns a closed tool allowlist, strict event classification, request/deadline bounds,
  durable Steps and checkpoints, explicit usage, and fail-closed recovery without duplicate tools.
- No OMP or OpenCode binary, configuration, plugin, state or licence enters the Solver image.
- [Install and rehearse the CPA recovery
  path](https://github.com/jerome-queck/incypher-ctf/issues/185) owns the pinned real-CPA proof. A
  failed rehearsal removes the CPA route; it does not revive either rejected Harness.
- The route changes only for an independently classified native-Harness failure. Shared account
  exhaustion still waits or spends a separately pre-authorised reset.

## Revisit when

- Native Codex exposes a supported machine interface that survives the CLI/tool-loop failures this
  second Harness exists to remove; or
- controlled Runs show the second Harness reduces Flags or recovery safety enough to outweigh its
  independence; or
- a future Harness proves the same closed failure contract with materially less owned complexity.
