# An empty list is not an empty board

[#59](https://github.com/jerome-queck/incypher-ctf/issues/59) opened as a docs-drift ticket: the
IN-CYPHER practice arena had stopped listing its two practice Challenges to an unauthenticated
reader, and a standing fact on [map #11](https://github.com/jerome-queck/incypher-ctf/issues/11)
needed correcting. Resolving it turned up something the ticket did not ask about and that binds the
`Board` seam on every Board.

**On that board, `{"success": true, "data": []}` is not evidence of an empty collection.** It is not
CTFd's answer at all. `/api/v1/teams?field=bogusfield` returns **200 and the same empty body**, where
CTFd validates `field` against an enumeration before any handler runs and Brunner — the live control,
same CTFd generation — returns **400** naming the permitted values. `/api/v1/teams` carries no
`meta.pagination`, which `TeamList` returns unconditionally. Meanwhile
`/api/v1/challenges/8/solves` answers **200 with five dated solve rows**, naming a team through a
query that filters banned and hidden accounts *out*, while `/api/v1/teams/4` answers **404**, which
only a banned or hidden team does. Both cannot be true. Something that is not CTFd is composing the
replies to collection endpoints.

The decision: **a collection endpoint's empty reply is never taken as fact. It is corroborated by a
request the Board must refuse, or it is treated as unknown.**

This record **corrects [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md) and
[ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)** in one claim each, both
written on 2026-08-24 hours before this was found.

## Why this is worse than a 403

Every other way of not being shown a Board announces itself. An absent token answers **302** to
`/login`; a rejected one **401**; a request without `Content-Type: application/json` **302**; the
Cloudflare edge **403**. This repository has a named finding for each, and `scripts/ctfd_probe.py`
has a check for each. They are all loud.

This one is silent by construction. It arrives with `success: true`, HTTP 200, the right
`Content-Type`, no redirect and no error body — **every shape a client reads as an honest answer**.
A Solver enumerating that Board sees a competition with nothing on it and terminates cleanly, having
done nothing wrong by any check it runs.

It is also the sharpest instance of the failure
[ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md) already named when it made
the Board profile *discovered* rather than configured: **a discovered profile can discover the wrong
thing.** ADR-0008 had a transient `/mana` 403 in mind. This is the same failure with the error
removed.

## The control, and why it is this one

`GET /api/v1/challenges?field=<not-a-field>&q=a`. CTFd rejects it in `validate_args` before any
handler runs, so a **non-200 is CTFd-shaped and a 200 is proof the reply came from somewhere else.**

Three properties earn it the job:

- **It cannot be satisfied by accident.** An interposed layer returning a canned success answers 200
  to everything, which is exactly the thing being detected. A layer that reproduced CTFd's
  enumeration error would be reproducing CTFd.
- **It costs one request, and only on the path that already went wrong.** It runs where an empty list
  arrived, so a Board that lists Challenges never pays for it.
- **It needs no second Board.** The contradiction above was found by comparing against Brunner, but
  the control is *internal* — a Board is checked against CTFd's own contract, not against another
  Board we happen to hold credentials for.

**Any refusal counts.** A 400, a 403 and a 302 all pass it. The control is not certifying the stack;
it is catching the reply that is too agreeable, and widening it into a health check would make it
fail on Boards that are merely strict.

### Alternatives rejected

**Trust the empty list.** The status quo, and the reason this record exists. It is the cheapest
option right up to the moment it costs the whole Run, and it fails silently, which is the worst
combination available.

**Compare against a second Board.** How this was found, and useless in production: it needs a second
Board and credentials for it, and it answers "these two differ" rather than "this one is lying".

**Detect the interposed layer specifically** — fingerprint the missing `meta.pagination`, the 404 on
`/scoreboard/top/<n>`. Rejected as fitting one instance. The cause here is not determinable from
outside; an edge rule, a plugin and a fork all fit the evidence, and each would present differently.
The control asserts the *contract* rather than recognising the *symptom*, so it survives a cause we
have not seen.

## Consequences

- **`scripts/ctfd_probe.py` asks the control before it names any other cause**, and the ordering is
  load-bearing rather than tidy. The diagnosis added earlier in
  [#59](https://github.com/jerome-queck/incypher-ctf/issues/59) separates the clock, a stranger's
  view and a genuinely empty board — and every one of those is a statement about a Board that
  *answered*. Told about a closed Board that is also interposed, the unordered version reports the
  clock: true, and about a reply CTFd never composed. A test holds the ordering.
- **The Board seam inherits it, not just the probe.** ADR-0008 makes `scripts/ctfd_probe.py`'s
  `Board` *the* seam that moves into `solver/`, so the probe and the competition path are provably
  the same code. This is a property of that seam's read contract.
- **Corrects [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md).** Its scoreboard
  addition says logging the top-N *"costs one GET and no model"*, verified on Brunner. On the
  IN-CYPHER arena that GET returns a canned empty payload, and the empty scoreboard recorded there on
  21 August is the same effect rather than "no scores yet" — so it was never evidence of an empty
  scoreboard. The mechanism stands; **whether a given Board answers is a Board-profile discovery
  question**, and a Board that fails the control contributes no scoreboard rather than zero rows.
- **Corrects [ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)** in the same
  claim, which it carries as a consequence. It reaches nothing else there: Order reads Intake's
  copy, and a Board that lists nothing produces no Order to compute.
- **A `w_value` and `w_solves` term is only as good as the list it reads.** ADR-0015's ranking
  function consumes `solves` and `value` from the LIST payload. On a Board that fails the control
  those fields are not merely stale — they were never sent, and a scheduler ranking an empty set
  ranks nothing while reporting success.
- **This is a read-contract rule, not a retry rule.** Failing the control is not transient and must
  not be retried into a pass; the arena has behaved this way since at least 21 August. What the
  Solver does about a Board it cannot read is a separate question this record does not answer.
- **`CONTEXT.md` gains no term.** **Board profile** already names the thing being discovered and
  ADR-0008 already names the failure of discovering the wrong thing; a word for *"a reply that
  looks answered and is not"* would be a word for one mechanism rather than for a domain concept.

## What this record does not settle

**What the Solver does when a Board fails the control on competition day.** Refusing to start is the
obvious candidate and is the same shape as the missing-credential refusal the map already carries —
loud at 10:15 with a human present, rather than silent for 5.5 hours. But it is a Run-level policy,
it interacts with ADR-0010's boot checks, and it belongs with the orchestrator that does not exist
yet. Recorded here so `/to-spec` carries it.

**Whether the IN-CYPHER competition Board behaves this way.** Only the practice arena has been
measured. The arena and the scored Board may not share infrastructure, and the scored Board does not
exist to read yet.

**Why the arena does this.** Not determinable from outside, and deliberately not guessed at. The
Challenges themselves are `state: "locked"` — withdrawn from play, established from CTFd's own
handler filters and recorded in `docs/competitions/incypher-2026-hackathon.md` — which is an ordinary
organiser action and is a separate fact from the collection endpoints.

## Revisit when

- **The IN-CYPHER competition Board is first read**, on 14 September. It is the first chance to know
  whether this is an arena quirk or the platform.
- **The control fires on a Board we believe is healthy**, which would mean CTFd's own contract has
  moved and the control is asserting a version rather than a rule.
- **A Board answers the control but lies some other way.** The principle generalises; the single
  request does not, and a second silent shape would want its own.
