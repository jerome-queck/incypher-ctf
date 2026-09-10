# Submission is speed-first, provenance-backed and serial

> **The ambiguous-response branch is resolved by
> [ADR-0052](0052-one-unknown-submission-fences-flags-for-sixty-seconds.md).** It supersedes the
> retry-after-non-grading clause below: any Candidate proposal whose body may have left the broker
> is never resent. Endpoint-specific reconciliation runs inside one sixty-second Flag-POST barrier,
> then `unknown-and-spent` and a fresh Submission epoch release unrelated submissions.

[Bounding the submission path so the Solver cannot be mistaken for a
brute-forcer](https://github.com/jerome-queck/incypher-ctf/issues/164) found that v1's reproduction
gate proves repeatable output rather than correctness, suppresses legitimate model recognition, and
keeps every Boot- and Lane-wide guard in memory. We decide to **submit one provenance-admissible
Candidate proposal immediately, reproduce only after an incorrect verdict to diagnose the approach,
and put every POST through one crash-durable account-wide authority with conservative finite
bounds**. This is a planning decision whose production changes and proofs belong to wayfinder map
153's `/to-spec` -> `/to-tickets` handoff.

## The policy is ours, not an organiser quotation

The published [IN-CYPHER rules](https://www.imperial.ac.uk/about/global/singapore/research/in-cypher/in-cypher-hackathon/)
forbid attacking the platform, other teams and shared infrastructure, while the Board's
[How to play](https://hackathon.in-cypher.com/how-to-play) explicitly permits API submission. Neither
publishes a numeric submission rate, wrong-attempt ceiling, account scope or parallel-submission
rule, and neither says "never brute-force Flags."

The exact competition allowance is therefore unsettled. The tracked profile's anti-exhaustion rule
is a conservative Solver invariant derived from ordinary competition conduct and the need not to
look like a brute-forcer; it is not presented as the organiser's words. A measured or published
Board rule may tighten the invariant. It never silently loosens it.

Stock CTFd is supporting evidence, not authority over the deployed Board. Upstream calls its guard
anti-bruteforce, defaults to ten incorrect submissions per minute, and counts recent Fails by
`account_id` across Challenges; in team mode that account is the team. IN-CYPHER's exact revision
and configuration are unknown, and its participant token cannot read `/api/v1/configs`. The v2
default deliberately keeps half of that inferred allowance rather than treating all ten as ours.

## Provenance admits model recognition without admitting pure guesses

The old Claim/Observation distinction stands: a model's interpretation is never relabelled as a
fact. Literal-output-only admission does not stand. A picture can carry a Flag visible in its
pixels but absent from its bytes; an OSINT Challenge can require recognising what several sources
mean; and fragments may have to be ordered or transformed before the Flag exists anywhere
verbatim. Those are legitimate model contributions, not Observations and not automatically guesses.

The v2 path separates three things:

- An **Evidence artifact** is immutable and source-identified: an Artefact or attached picture with
  a digest, an Observation, or a captured web, MCP or browser result with its source and time. A
  model-authored query is not its result, and a mutable workdir path is not an immutable identity.
- A **Candidate derivation** is a structured Claim that cites those artifacts, produces exactly one
  string, and records the relationship the model recognised: a visual region or frame, the source
  of every fragment and its ordering or transformation, or the captured OSINT material and the
  inference drawn from it. It carries its Claim reference. Mechanical validation proves the cited
  sources exist and are admissible; it does not pretend the interpretation is objectively true.
- A **Candidate proposal** is that exact string plus its provenance and disposition. `observed`
  means a legitimate tool result carried it. `derived` means a mechanically complete Candidate
  derivation produced it. Both may be submitted immediately. Model-rated confidence is telemetry
  only and never grants authority.

Unsupported prose, a Board-stated example, a crowded set that has not been disambiguated, a
template, or a Candidate proposal whose Isolated Instance has expired does not
authorise a POST. None is released merely because the Run entered its tail. This replaces the
v1 rule that weak Candidate proposals eventually spend otherwise-unused slots: an unused slot is still not
permission to guess.

The precise capture mechanism belongs at the seams already mapped. [The complete per-Category tool
inventory](https://github.com/jerome-queck/incypher-ctf/issues/183) owns OCR, viewers, browser and
media capture; [The Worker, control, and credential trust
boundary](https://github.com/jerome-queck/incypher-ctf/issues/190) owns who may seal an artifact; and
[What persists in /state, and how it stays
bounded](https://github.com/jerome-queck/incypher-ctf/issues/175) owns digests, retention, Solve
receipts and workdir generations. This decision consumes their evidence identities and decides what
they authorise.

## Speed first, diagnosis after rejection

The first admissible Candidate proposal is sent without replay. Replaying before the POST costs
time and only proves repeatability, not correctness. One submission Step reserves and sends exactly
one Candidate proposal. Any Board response—correct, incorrect, refused, rate-limited, paused or
unread—ends that Step's decision; the current batch fall-through that can send every remaining
Candidate proposal stops.

An `incorrect` verdict starts diagnosis and never a resend:

- where a command or tool emitted the Candidate proposal, replay it once. The same string returning
  means the method is repeatably wrong; absence means the evidence was unstable;
- where a Candidate derivation produced it, run one blind re-derivation in a fresh context over the
  same sealed Evidence artifacts without showing the rejected string. The same rejected string
  means the derivation path is repeatably wrong; a different result is a new Candidate proposal and
  receives no inherited authority.

The rejected string is never submitted again in the Run. Repeatability is a diagnostic fact about
the approach, not a Candidate proposal's strength. The authority durably marks the Approach label
and evidence or derivation fingerprint that produced a repeatably rejected string as spent, and
refuses another proposal from that same path until a different Approach label or materially new
Evidence artifact proves the path changed. `reproduced` therefore leaves the pre-submit strength
ladder; ADR-0024's false-confidence case cannot be repaired by printing model prose through a file.

## One authority owns every Lane's allowance

Attempt executors and Lanes produce Candidate proposals and never hold the Board credential or
submit directly. The trusted Run controller has one serial submission authority for the account. It owns
Candidate proposal admission, atomic reservations, pacing, per-Challenge counts and every POST
across all Lanes and Boots. A Lane has no private allowance. The same canonical stream and sequencer
settled by ADR-0032 persist every authority-changing record before its side effect.

Before a POST, the authority writes a crash-durable reservation binding Run, Boot, Lane, Attempt,
Step, Challenge, Candidate proposal digest, evidence references and budget counters. Afterward it
writes the wire outcome and Board verdict. A lost response leaves one indeterminate side effect. All account
submissions freeze while the authority reconciles trusted solved state and the Board's attempt
count. It retries only where positive Board evidence proves the POST was not graded; otherwise it
counts the reservation as spent and never resends that Candidate proposal. A timeout message cannot
claim a request "never reached" when the transport cannot prove that.

## The initial v2 bounds

The bounds are conservative dials whose exact values are recorded on every Run. Evidence may tune
them later without weakening a published Board rule.

| Scope | Bound | What happens at the bound |
| --- | --- | --- |
| Step | One POST | The Step ends on every outcome; another Candidate proposal requires another decision. |
| Attempt | Two `incorrect` verdicts | The Attempt is Cut and the next work must change approach. |
| Challenge | Five `incorrect` verdicts across Attempts and Boots | Another submission requires a new replay-confirmed Checkpoint; the Challenge remains visible and eligible. |
| Lane | No independent allowance | Every Lane waits on the same authority and counters. |
| Account window | Five POST reservations in any rolling 60 seconds, at least 12 seconds apart | The reservation waits durably; correct, refused and indeterminate POSTs count because they still create traffic or uncertainty. |
| Run | The account-window rate over the fixed absolute Run window | The finite window derives a finite maximum; no second unpaced Run begins after a Boot. |

The reserved tail uses the same admission, Challenge and account bounds and keeps ADR-0032's hard
300-second deadline. It releases neither weak Candidate proposal classes nor an exhausted
allowance. Hitting a bound delays or changes work: a window reopens, an Attempt changes, or a
Challenge needs new confirmed progress. It never silently bans a Challenge.

## What this moves

- ADR-0019's rule that a Board statement authorises nothing stands.
- ADR-0024's picture attachment stands, while its print-through-command workaround and the false
  `reproduced` confidence it permits are replaced by Candidate derivations.
- ADR-0027's need to bound what an unlimited Board does not remains; its five-wrong per-Attempt
  ceiling becomes the hierarchy above, and a batch no longer falls through multiple Candidate proposals.
- ADR-0029's permanent refusal of templates stands.
- ADR-0021's tail release is superseded. A Board-stated or otherwise unsupported Candidate proposal
  is not submitted merely because no later Attempt can use the slot.
- ADR-0032's serial, durable, at-most-once submission authority stands and gains the exact admission,
  ambiguity and pacing policy above.

## Handoff and proof

The post-map specification replaces `Candidate`'s one command/ref pair with provenance that can
reference one or many Evidence artifacts and a Candidate derivation Claim. It records offered,
pending, rejected and spent identities durably; rebuilds pacing and every counter from the stream;
removes pre-submit replay and batch fall-through; and makes the tail deadline effective over
derivation, waiting and POST time.

Tests cover literal tool output, visual reading, OSINT recognition, fragment assembly, unsupported
prose, Board examples, crowds, templates, expired Instances, every bound transition, and blind
post-rejection diagnosis. Composition tests kill the process before reservation, after reservation,
during POST and before outcome persistence while two Lanes race the account window. They prove one
POST per reservation, no Candidate proposal resend, no allowance reset across Boots, no Lane-local
race, an account-wide freeze on ambiguity, and no tail exemption. Matched practice Runs measure
Flags, wall time, POSTs, incorrect verdicts and subscription use before any dial changes.
