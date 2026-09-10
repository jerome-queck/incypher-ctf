# One unknown submission fences Flags for sixty seconds

[How long may one indeterminate submission block unrelated
Flags?](https://github.com/jerome-queck/incypher-ctf/issues/244) closes the unbounded branch left by
ADR-0037. **Once any part of a Flag POST body may have left the Board broker, that exact Candidate
proposal is at most once. New Flag POSTs pause for at most sixty seconds from wire-send start while
the broker performs endpoint-specific reconciliation; then the Candidate proposal becomes
`unknown-and-spent`, a fresh Submission epoch fences late results, and unrelated submissions may
resume.** Absence from a Board read never proves that the POST was not graded.

This supersedes ADR-0037's permission to resend after purported positive proof of non-grading and
narrows ADR-0032, ADR-0045 and ADR-0046 only where an indeterminate Candidate proposal could leave
the scope or duration of its fail-closed state unbounded. The durable reservation, serial
account-wide authority, pacing, Candidate proposal provenance, minimum-safe-blast-radius rule and
broader freeze for lost broker or record ownership stand. This is a v2 planning contract; the
current v1 runtime does not implement it.

## A possibly sent Candidate proposal is never sent again

A failure before body admission is not an indeterminate POST. Local validation, serialization,
credential admission or a transport failure that proves no request-body byte left the broker may
release its unsent reservation. Once the broker cannot prove that boundary, the reservation is a
POST and its exact Candidate proposal is permanently at most once. A timeout, EOF, reset, proxy
error or missing Board row never authorises a resend.

The permanent deduplication key binds Board identity, Challenge revision, Instance provenance and
the exact Candidate proposal digest. Before a POST, one reservation id binds to that key; neither
the key nor reservation may be admitted twice. The full submission identity is that key plus its
reservation id. A materially new Challenge revision or Instance may produce a new key only from
fresh Evidence artifacts or a fresh Candidate derivation; it never reuses the old reservation or
inherits its authority.

## The barrier is submission-only and lasts sixty seconds

The crash-durable reservation records its absolute `wire_started_at`, current Submission epoch and
sixty-second reconciliation deadline before the POST. An indeterminate outcome immediately blocks
new Flag POST admission account-wide. The barrier ends on a conclusive correlated outcome, or at
the deadline after the final reconciliation cycle. The deadline includes transport timeout and
never restarts across a Boot. Run closure truncates the wait and seals the unresolved Candidate
proposal with the same `unknown-and-spent` transition and accounting as the deadline; there is no
successor epoch when no later submission can occur.

The minimum safe blast radius remains narrow. Intake, reconciliation reads, Target work, Instance
maintenance, local solving and unaffected Lanes continue while the Board broker, canonical writer
and their ownership are healthy. Only ambiguity in broker ownership, the live writer or durable
effect recording invokes ADR-0046's broader `run-shared` freeze over irreversible effects. An
unknown Candidate proposal by itself freezes only Flag POSTs.

Sixty seconds is a conservative default dial, not an organiser claim. It spans the existing
account pacing window and one stock CTFd challenge-cache horizon without pretending either is a
server-side completion bound. Waiting longer cannot make a missing row prove non-grading. Every Run
records the dial, and only matched fault evidence may tune it without weakening a published Board
rule or the at-most-once guarantee.

## Reconciliation trusts each endpoint separately

Before the reservation, the broker records the last trusted endpoint-specific baselines it has:
visible submission and solve identities, Challenge ownership, attempt count, Board and Challenge
revision, Instance provenance and the read outcome that supplied each value. An unavailable or
untrusted baseline remains unknown.

The broker reconciles immediately and no later than 15, 30 and 60 seconds after
`wire_started_at`. Each cycle stops as soon as a conclusive result is found and otherwise follows
this order:

1. accept a late original response only when its request identity and old Submission epoch match
   the reservation;
2. where a startup probe proved it available, read the exact self-submission ledger for the
   Challenge and match Board row, exact supplied value, submission identity, type and time to the
   reservation;
3. read authenticated user and, in team mode, team solve rows to learn whether the Challenge is
   now owned; and
4. read Challenge detail and list state for endpoint-controlled `solved_by_me` and `attempts`
   corroboration.

At the 60-second deadline the broker consumes only results already available; a started or pending
read cannot extend the barrier. Stock CTFd 3.8.5's [self submission-schema view includes the exact
supplied value](https://github.com/CTFd/CTFd/blob/3.8.5/CTFd/schemas/submissions.py#L20-L42), but its
[stock submission-list route is administrator-only](https://github.com/CTFd/CTFd/blob/3.8.5/CTFd/api/v1/submissions.py#L44-L47).
A Board-specific participant ledger is therefore optional: the Solver uses it only after startup
proves access, identity fields, freshness and exact-value semantics. Solve rows can prove current
Challenge ownership but omit the supplied value;
`solved_by_me`, attempt counts and public solve rows are weaker still. A positive, identity-matched
row may settle exactly what it states. An empty, unchanged, stale, interposed, disabled or
contradictory read settles nothing, and one endpoint's valid shape never certifies another.

## Outcomes preserve the difference between unknown and wrong

Every transition is append-only. Later exact evidence may refine a historical projection but
never erase the reservation, reactivate the Candidate proposal or authorise a resend.

| Evidence | Candidate proposal disposition | Challenge and accounting consequence |
| --- | --- | --- |
| Correlated `correct`, or an exact authoritative row matching the supplied value and `correct` type | `accepted` | Mark the Challenge solved and seal the Solve receipt. |
| Correlated `incorrect`, or an exact matching `incorrect` row | `rejected` | Increment Attempt and Challenge incorrect-verdict counters and start ADR-0037 diagnosis. |
| Exact refusal, pause or rate-limit result | `refused-and-spent` | Keep the account POST reservation; do not increment the Challenge attempt floor or incorrect counters. |
| Positive solve evidence without exact Candidate proposal attribution | `unknown-and-spent` | Mark the Challenge owned, create no Solve receipt for this Candidate proposal and retain the attribution gap. |
| No conclusive evidence at the sixty-second deadline | `unknown-and-spent` | Charge one account reservation and one conservative Challenge-attempt floor; increment no incorrect-verdict counter and create no Solve receipt. |

The conservative Challenge-attempt floor protects finite Board allowance without relabelling an
unknown outcome `incorrect`. If later exact evidence proves `correct`, `incorrect` or refused, a
new record reprojects the floor and counters to that outcome. Historical uncertainty remains
visible even where its operational projection becomes exact.

## A Submission epoch releases unrelated Flags

A **Submission epoch** is the durable monotonic generation of the account-wide serial submission
authority. At the first indeterminate outcome the current epoch remains active only for reads and
the pending reservation. A conclusive outcome or the durable `unknown-and-spent` transition closes
that barrier; before any later POST, the sequencer records the successor epoch and issues a fresh
submission capability.

Unrelated Challenges may then submit under the successor epoch. The exact Candidate proposal
remains barred forever. Same-Challenge claim and capacity release are left to [When may a Challenge
claim release productive capacity?](https://github.com/jerome-queck/incypher-ctf/issues/245); this
decision neither releases that claim nor stops local work on it.

A result from an older epoch is evidence only. It cannot satisfy a current reservation, consume a
second counter, close a current barrier or authorise a current effect. Exact late evidence may
append the historical correction described above and may create a Solve receipt only when it binds
the exact Candidate proposal and every receipt identity.

## Repeated ambiguity opens a bounded Submission breaker

One successor-epoch POST is the probation for a transient external fault. If two consecutive POSTs
become indeterminate without an intervening conclusive submission outcome, the Submission breaker
opens and Recovery records the repeated fingerprint. Local solving and safe Board work continue,
but no ordinary Flag POST is admitted.

After 120 seconds the Submission breaker admits exactly one real, provenance-admissible Candidate
proposal as soon as one has current submission authority. If none is admissible at the deadline,
the first that becomes admissible is admitted without a second interval. The probationary POST uses
the ordinary durable reservation, pacing and sixty-second barrier; no synthetic or known-wrong
probe is sent. A conclusive outcome closes the Submission breaker. Another indeterminate outcome
keeps it open and the next probation waits another 120 seconds. Account and Challenge bounds
remain in force.

Replay reconstructs the active epoch, reservation, absolute deadline, reconciliation attempts,
Candidate proposal disposition, counter floors, Submission breaker state and next probation time
before any new effect. No Boot, tail entry or Recovery attempt refreshes a deadline or allowance.

## The Gate proves every authority boundary

Controlled fault injection covers:

- exact self-submission view enabled, disabled and interposed; conclusive correct, incorrect,
  refusal, pause and rate-limit outcomes; solve ownership without Candidate proposal attribution;
  and stale, empty and contradictory reads;
- loss before body admission, after partial or complete send, before and after the CTFd commit, and
  during post-solve Instance cleanup;
- process death before reservation, after reservation, during POST, during each reconciliation
  cycle, before `unknown-and-spent`, before epoch advance and after the successor admits work;
- two Lanes racing the serial authority, a late old-epoch result, repeated ambiguity, a material
  Challenge revision and expiry inside the reserved tail; and
- exact counter, Candidate vault, receipt, Incident and sanitized-capsule projections after every
  replay point.

The proof fails on any duplicate Candidate proposal, overlapping submission authority,
stale-epoch effect, false incorrect verdict, false Solve receipt, reset deadline, account-bound
escape or any ambiguity blocking unrelated Flag POSTs beyond sixty seconds. The full-window Gate
then measures Flags, wall time, POST and read volume, ambiguous outcomes, Submission breaker time
and any Candidate proposal or solve left unattributed before the dial may change.
