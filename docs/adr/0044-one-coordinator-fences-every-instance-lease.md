# One coordinator fences every Instance Lease

[Who owns an Instance across Lanes and
Boots](https://github.com/jerome-queck/incypher-ctf/issues/189) resolves the ownership detail left
open by ADR-0032 and ADR-0043. **The Run controller's central Lease coordinator owns every Lease;
Attempts receive fenced bindings, Lanes never own Instances, and only corroborated Board existence
plus durable Solver provenance can authorise adoption or destruction.**

The Board remains authoritative about whether an Instance exists. The canonical Run stream is
authoritative about why this Solver may use or terminate it. Neither authority can impersonate the
other, and a model-writable file proves neither.

## One Lease identity, successively fenced

A Lease has a Run-unique `(run_id, lease_seq)` identity and immutable Board, team/source and
Challenge identity. Every bind, rebind or authority-changing recovery advances a monotone
`lease_epoch`. An Attempt executor and each typed effect capability carry the Lease identity,
epoch and Attempt identity; the broker accepts an operation only when all three match the current
replay-derived projection. A late process therefore cannot deploy, renew, use or terminate through
an epoch a successor has fenced off.

The coordinator owns the Lease throughout. An Attempt may be **attempt-bound** to it, including
while paused, between Turns or in Quota wait. A Lane is stable Run-scoped capacity and never an
owner. There is no queued Attempt: an admission candidate owns no Challenge claim, Lane or Lease.
A Lease temporarily retained without an Attempt is **reserved**, not evidence of queued work.

The open Lease phases are deliberately a separate vocabulary from Board reconciliation and close
causes:

- **reserved** — authority is durably retained while deploy, planned handoff or cleanup is not yet
  settled; it grants no executor an effect capability;
- **attempt-bound** — one open Attempt holds the current fenced binding, whether or not a command is
  running; and
- **recoverable** — a Boot or owner ended abnormally, so only reconciliation may rebind or close it.

`orphaned` and `unattributed` are reconciliation verdicts, not extra Lease phases. `expired`,
`terminated` and `never-deployed` are terminal close causes, not open phases. A generic `released`
close is forbidden because it hides whether the Board may still hold the Instance.

## Reservation precedes effect; Order precedes reattachment

Before deploy, the sequencer writes a crash-durable reserved Lease and deploy intent. A confirmed
deploy binds the exact Target and deadline to the already-open Attempt and advances the epoch. If
the response is lost, the intent plus a newly appearing, uniquely matching team ledger row may
recover cleanup authority. It does not recover solving authority unless a trusted read also
recovers the exact Target and deadline. Ambiguous rows remain unattributed.

An Attempt's closing barrier revokes its binding before the Lane and Challenge claim release. The
coordinator may retain the Lease briefly, but that retention neither reserves future work nor
bypasses Order. Reattachment happens only when Order independently admits a fresh Attempt for the
same Challenge before grace expires, the Board still proves the Instance ours, no external effect
is uncertain, the exact Target and deadline are recoverable, and enough TTL remains for the
Attempt plus its submission reserve. Reattachment uses a fresh Attempt identity and epoch.
Otherwise the attributed Instance moves toward termination.

A Boot loss makes its open Leases recoverable. A successor Boot never resumes an Attempt identity:
it proves the predecessor and descendants dead, replays the stream, closes the interrupted Attempt
as crashed, then may reattach the Lease only through the fresh-Order rule above. No Lane-local sweep
exists.

## Presence, absence and orphanhood need different proof

A Board row is **unattributed** when no durable claim can safely identify it. It is never adopted or
destroyed. Same Challenge name, silence, age, no command, a paused Lane, an absent in-memory object,
local TTL, or an empty, stale, `NOT_OURS` or `UNREADABLE` ledger cannot strengthen attribution.

An Instance is **orphaned** only when it is positively attributable to this Run, has no current
reserved, attempt-bound or recoverable Lease after a durable quarantine, and remains present after
two ownership and Board reconciliations. The second reconciliation occurs no earlier than fifteen
seconds after the first. Orphanhood authorises termination only; it never authorises adoption.

Fifteen seconds is a minimum grace, not absence proof. ADR-0007 records that chall-manager can
render an authenticated empty ledger when its backend is unavailable, particularly while Mana is
disabled. A Lease therefore closes for a settled terminal cause only after either:

- an authenticated successful DELETE or 404 proves the Instance gone; or
- an authenticated ledger absence is corroborated by a per-Challenge 404 no earlier than sixty
  seconds after the last possible presence.

Repeated empty ledger pages, local deadline arithmetic and a 404 still inside that cache horizon
remain unsettled. Corroborated natural absence closes as `expired`. Recovery retries while
unrelated safe work continues. Termination writes a
crash-durable intent under the current epoch before DELETE. A 2xx or 404 closes it as `terminated`;
a lost or failed response remains pending and may be retried because absence is success. A deploy
intent closes as `never-deployed` only under the same corroborated-absence rule.

## Startup reconciles globally before admission

Every Boot freezes new effects, replays canonical state, proves the predecessor and descendants
dead, reads the authenticated team ledger, classifies every Lease and visible Instance, closes
interrupted Attempts, performs safe reattachment or termination, and only then admits work. A
contradictory stream, uncertain identity or disputed epoch stops all new effects and enters
Boot-level Recovery; no unaffected Lane proceeds under ambiguous shared authority.

A nonterminal stored Run against this Board continues as another Boot rather than opening a new
Run. A new Run never adopts an earlier Run's Instance for solving. It may discharge an earlier
Run's cleanup obligation only when that Run's durable records conclusively identify the Instance;
otherwise the row is unattributed. Board identity prevents records from one event authorising an
effect against another.

## Mana is optional; ownership is not

Mana is chall-manager's optional capacity mechanism. A positive total makes held Instances spend
that configured capacity; total zero means Mana is disabled. The authenticated IN-CYPHER reading
on 8 September 2026 remains `used: 0, total: 0`, so avoiding current Mana spend is not a premise of
this decision. Unneeded Instances are still closed promptly for ownership correctness, bounded
external state and compatibility with Boards that enable capacity later.

## Handoff and proof

The post-map specification gives reservation, epoch change, binding, recovery, quarantine,
termination intent and terminal cause crash-durable records whose projection rejects gaps,
duplicates and contradictory owners. [What persists in `/state`, and how it stays
bounded](https://github.com/jerome-queck/incypher-ctf/issues/175) owns their representation and
retention.

Fault injection covers death before and after deploy intent, a lost deploy or terminate response,
Boot replacement, paused and Quota-wait Attempts, simultaneous Lane completion, stale-epoch output,
ambiguous same-Challenge rows, backend failure rendered as an empty ledger, natural expiry, cached
per-Challenge reads, earlier-Run residue and reserved-tail entry. The proof admits no overlapping
binding, stale effect, Order bypass, unsafe destroy or Instance left falsely classified as closed.
