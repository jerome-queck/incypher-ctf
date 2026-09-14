# v2 is the final competition candidate

The official practice batch is now live and only eight days remain before the scored Run. We will
finish one **v2** rather than preserve a speculative v3 boundary: official Board, Target, Flag,
Instance, ADK/PoW and freeze work all belong to v2, and its final 5.5-hour rehearsal runs against
the official practice Board rather than treating a local hidden rig as the release gate.

This supersedes ADR-0033's v2/v3 split, ADR-0048 and ADR-0049's local-rig Gate binding,
ADR-0051's mandatory Core/Pack ledger, and ADR-0055's six-arm selection prerequisite.

Scope is selected by competition usefulness, not by the old proof ledger. First audit the current
Solver against the released surface; then implement only gaps that block the released categories,
the exact Board/Target path or unattended operation. Keep existing safety and working behavior, but
drop optional Packs, unused tool families, six-arm policy selection, legacy-code contraction and
receipt work that does not change the fielded Solver. Historical ADRs and issues remain evidence of
how the current code was built; they no longer block the final candidate.

A scope cut removes a release prerequisite; it does not delete landed behavior, its safety
invariants, or the detailed design record. Remaining work extends the existing Board, Target,
Tool, Candidate, submission, Lease, Recovery and canonical-state seams rather than creating
parallel paths.

The final Gate is operational: the exact image must enumerate the official Board through the
Solver transport, exercise real standard and `dynamic_iac` Challenges, and complete one unattended
19,800-second practice Run with truthful records and cleanup. Any official fact exposed before the
scored Run updates v2 directly. There is no planned v3.
