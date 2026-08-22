# Truth about an Instance lives on the Board, split by question

Forty-three per cent of a Brunner-shaped Board cannot be reconned at all without a running
Instance — `connection_info` is empty on every `dynamic` Challenge and absent from every
`flightops` one, and no description carries a host:port. The address does not exist until an
Instance is deployed, which is why v1 deploys ([#15](https://github.com/jerome-queck/incypher-ctf/issues/15)).
Something then has to know what we are holding and how long we hold it, and the obvious answer is
the wrong one: keep our own record of every Instance and poll it.

The decision: **we keep no record of record.** Two reads on the Board are authoritative, each for
exactly one question, and neither is ever asked the other's.

- **Existence** — what do we hold right now — is `GET /plugins/ctfd-chall-manager/instances`.
  Authed, team-scoped, and it queries chall-manager directly rather than through CTFd's cache, so
  it is both complete and current.
- **The deadline** — how long this Instance lasts — is the `until` on the deploy response, or
  `now + timeout` after a renew. Every piece of arithmetic reads that and only that.

The two never mix. The ledger renders `until` through a template that truncates it to whole seconds
and strips the timezone, and keys its rows by challenge *name* rather than id. It is exact about
what exists and lossy about everything else, so a deadline read from it would be a deadline quietly
wrong by up to an hour.

## Why not our own ledger

Because it could only ever be a copy, and it would go stale in precisely the situations it was
built for. A record written at deploy is correct until the moment something unplanned happens to
the Instance — a crash mid-Attempt, a terminate that returned 429, a container evicted underneath
us — and those are the only moments anybody consults it.

What makes that expensive rather than merely untidy is that **chall-manager never evicts.** There
is no janitor, no reclaim-the-oldest, no eviction path anywhere in the plugin: a deploy that
exceeds the team's mana is refused with a 403, and the Instance already held stays held. So a
leaked Instance is not self-healing. It costs us capacity for the remainder of the run, and a
private ledger that has drifted is worse than no ledger at all, because it reports the leak as
absent.

## Why not poll

Polling looks cheaper than it is, and the cheap-looking call is the wrong one. The per-Challenge
`GET .../instance` is served from a sixty-second CTFd-side cache — and so is the liveness check
inside `attempt()`, which is why a Flag submitted just after expiry sometimes still grades. A poll
built on it can report an Instance alive a full minute after it died. The ledger page bypasses that
cache entirely, which is the whole reason it is the one we read.

## Consequences

- **Every Attempt boundary runs a leak sweep.** The ledger is read, and anything held that is not
  this Attempt's Challenge is terminated. That is what makes terminate-on-cut survive its own
  failure, and given no eviction it is the only thing that does.
- **A correct Flag is always followed by a terminate, and a 404 is success.** `destroy_on_flag` may
  already have destroyed the Instance server-side; reading the field to decide whether to bother is
  a branch that buys nothing and gets it wrong when the field is absent.
- **Expiry stays computed, never detected** — the inherited rule from
  [ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md)'s ticket, and the cache above is
  why. Liveness, by contrast, *is* checked, but only on cause: a connection refused by the
  Solver's own command buys exactly one read, never a timer.
- **Renewal happens late or not at all.** A renew sets `until` to `now + timeout`, discarding
  whatever remained, so renewing early throws time away. It is permitted only where the Challenge
  defines a `timeout`, which is readable before we ever deploy.
- **A reserve is held inside `until` for submission.** A Flag submitted after the Instance is gone
  is graded **incorrect**, not errored — it spends a slot of the Board-wide incorrect-submission
  budget and scores nothing. The message distinguishes it, so it is an Observation with a name
  rather than a mystery.
- **Mana is read once and branched on, never tracked.** It is chall-manager's concurrency cap
  rather than a separate currency, and where it is disabled the check short-circuits before any of
  it applies.

## Revisit when

- **v2 introduces concurrency.** Several holds outliving several Attempts is what would earn
  `Lease` its own entry in `CONTEXT.md`, and it is what turns the boundary sweep from a safety net
  into a scheduler.
- **Mana is switched on.** It reads disabled on the IN-CYPHER practice arena today, from two
  independent code paths, but no instanced Challenge exists there yet — so that reading is about
  today's configuration and not about competition day.
