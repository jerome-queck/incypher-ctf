# Truth about an Instance lives on the Board, split by question

> **[ADR-0044](0044-one-coordinator-fences-every-instance-lease.md) resolves the parallel Lease
> lifecycle and corrects Mana's scope.** Mana limits held Instances only when the Board configures
> a positive total; total zero disables it. Ownership reconciliation and prompt cleanup remain
> required whether or not that optional capacity mechanism is enabled.

> **Amended for restart and parallel ownership by
> [ADR-0032](0032-a-run-survives-its-boots-and-recovery-owns-the-first-fault.md).** The Board remains
> authoritative about whether an Instance exists. A durable Solver claim is separately
> authoritative about who owns it, and only one global Lease coordinator may reconcile the two.
> Presence alone cannot prove an orphan; inactivity or an empty, stale or unreadable ledger cannot
> authorise termination.

> **Amended by [#156](https://github.com/jerome-queck/incypher-ctf/issues/156), which measured the
> ledger instead of reasoning about it.** Everything about *where* truth lives stands, and the one
> word this record spends on authentication — **"Authed"** — turns out to have been exactly right
> for a reason nobody had checked: **the Solver's API token authenticates CTFd *page* routes, not
> only `/api/v1/*`.** Measured 2026-08-31 against the IN-CYPHER board, through `Board`'s own
> no-redirect transport, `GET /plugins/ctfd-chall-manager/instances` answers our token **200** with
> the plugin's own table and a logged-in nav, a corrupted token **401**, and no token **403** —
> CTFd's permission page, carrying no table at all. The ticket opened on the premise that the route
> serves a *login page at 200* which `instances_held()` reads as an empty ledger. **That premise is
> false.** The empty answer measured is the correct answer: nothing has ever been deployed there,
> and `/mana` agrees at `used: 0`.
>
> Three things change anyway, because the *risk* the premise described is real at a different
> address, and one of them is live today.
>
> **The read proves the page is ours before its rows are trusted.** An empty `<tbody>` is what we
> see for *"we hold nothing"* and equally for *"this page was rendered for nobody"*, and no
> measurement can separate them while we hold nothing to render. So `instances_held()` requires the
> theme's inline `window.init` object to carry a `userId` **equal to `data.id` from
> `GET /api/v1/users/me`** for the same token, read once and cached for the life of the `Board`.
> Presence is not the signal — the block is emitted on all three response shapes, at `userId: 0`
> when unauthenticated; the *value* is. Not the `/logout` link, which is refuted by measurement:
> the IN-CYPHER landing page fetched **with** our token carries `/logout`, `/login` and `/register`
> together, because the organisers ship both hero states and hide one in CSS. Not `teamId` either,
> which is `null` for every user on a `userMode: "users"` board and would turn a legitimate
> configuration into a permanent refusal to sweep. The two-source form rather than `userId > 0`
> because the asymmetry is not symmetric: a falsely *empty* ledger leaks our own capacity, but a
> falsely *populated* one has us **terminate another team's Instances**, and the sweep destroys
> what it finds. `scripts/ctfd_probe.py` already parses this same block for the Board's window, so
> this is an existing idiom rather than a new dependency, and Brunner emits the identical block
> from a fully custom theme.
>
> **Two corrections from the adversarial pass, which are the reason this paragraph is not the one
> first written.** `userId` proves who is *viewing*; the rows are *team* property. The plugin's own
> route reads `source_id = current_user.get_current_user().team_id` in team mode, so an admin moving
> our user between teams leaves `userId` matching while the ledger renders another source's rows —
> and this is the direction that destroys rather than merely leaks. So `teamId` is asserted against
> `users/me`'s `team_id` **conditionally, when `window.init` says `userMode: "teams"`**, which was
> the whole objection to gating on it: a `users` -mode board renders `teamId: null` legitimately, and
> a mode-aware assertion never fires there. And the marker must not become a second single point of
> failure — a transient blip on `/api/v1/users/me` at boot would otherwise make every instanced
> Challenge undeployable for the whole Run. Where that read is unavailable, fall back to the
> **identity observed at boot**: nothing is held at boot, so an identity established then leaks
> nothing, and the read is lazy rather than eager so a plugin-less Board never pays for it.
>
> **What this deliberately does not catch, recorded because it is the failure that most resembles
> the one the marker exists to stop.** When chall-manager itself is down, the plugin catches
> `ChallManagerException` and renders *the genuine ledger template, authenticated as us*, with
> `instances=[]`. That response is 200, carries the table, and carries our real `userId` — **every
> guard here passes and it reads as "we hold nothing."** The template's `mana_remaining="unknown"`
> would be the tell, and it is **not rendered on IN-CYPHER**, because `mana_enabled` is
> `mana_total > 0` and mana measures disabled there. Two smaller ones share the shape: a response
> truncated between the block and the table passes both guards and returns a *short* row list, which
> reads as a successful sweep rather than a fault; and `Board.request` sends
> `Accept: application/json` on this HTML route, so a CTFd or plugin release that honours it would
> serve a healthy board a JSON body carrying neither `window.init` nor a table. The marker is
> defended against a page that is not ours. It is not defended against a page that is ours and
> lying, and no read of this page can be.
>
> **There is a fourth state, and it is the opposite of the one the ticket predicted.** It expected
> *plugin installed but the page unauthenticatable*; that state does not exist on this Board. What
> does exist is the split the marker creates: **not ours** — `userId` present and `0` or somebody
> else's — is unambiguous, the rows are real and belong to another team, and they must never be
> terminated. **Unreadable** — no `window.init` at all, a shape we do not recognise — means we
> cannot tell, and there are no rows worth acting on either way. `ABSENT` (a 404, no plugin) is
> unchanged. Collapsing the two new ones would recreate in miniature the conflation this whole
> ticket turned out to be about.
>
> **An unreadable ledger stops refusing the Run, and stops silently skipping the sweep.** Both
> failure modes hang off one boolean today. `Profile.instances_reachable` collapses `ABSENT` and
> `UNREADABLE` into a single `False`, which `solver/run.py` turns into a whole-Run sweep skip whose
> own docstring calls it "genuinely nothing to report" — after which the Run exits **clean**. And
> where the boot list *does* hold an instanced Challenge, the Refusal fires and the Run never
> starts at all. On 22 September IN-CYPHER will have `dynamic_iac` Challenges, so tightening the
> ledger read without touching this would convert an observation about a board that is not yet live
> into a **competition-day kill switch**: a theme edit between now and then would refuse the boot
> and score zero. So a ledger that is unreadable or not ours makes every instanced Challenge
> **undeployable** — excluded exactly as a `shared` one already is, and for the same kind of reason,
> a fact about the Board rather than a judgement about the Challenge. We deploy nothing, so we leak
> nothing and cannot deadlock on mana, and every static Challenge is still worked.
>
> This is not a retreat from
> [ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md)'s rule that discovery
> *"fails the run rather than guessing"*. We do not guess: we decline to deploy what we could not
> reclaim, which is the conservative act the Refusal was reaching for by the bluntest available
> means. The Refusal was written for a Board that might not have the plugin; the case here is a
> Board that has it and answers us in a shape we do not recognise, and the two deserve different
> answers. Where the ledger reads **not ours**, that is said out loud in the record — it is the one
> reading that means somebody else's capacity is on the page.
>
> Two corrections this measurement forces elsewhere. **ADR-0026's Pending clause is not discharged
> by any value of the `chall_manager` field** — the clause is *"≥1 Instance it deployed and
> terminated itself"*, and the field only says the question can be asked; map #153 said otherwise.
> And ADR-0026:83-86 removes Pending as an option the moment we enter a venue that *has* a
> chall-manager, so a silently-empty ledger on 14 September converts a carried clause into a
> **failed** one. **The Instance path has never executed**: measured across every surviving stream,
> 31 `[instance] deploy` steps, all of them short-circuits, and **zero** sweeps, terminates or
> liveness reads, ever. The widely-repeated "49 deploy steps" is not re-derivable from anything
> still on this machine.

> **Extended in one line by [ADR-0015](0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md).**
> The leak sweep runs at **Run close** as well as at every Attempt boundary. chall-manager never
> evicts, so a Lease still held when the process exits is capacity nobody reclaims. ADR-0015 also
> rejected a late-run gate on new deploys: `min(budget, TTL)` here already prevents an Attempt
> outliving its Instance, so the surviving rule is cause-neutral — do not start an Attempt shorter
> than `L_min`, whether or not it needs a deploy.

> **Amended in one line by [ADR-0014](0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md).**
> Everything about where truth lives stands: existence is the ledger page, the deadline is the deploy
> response, renewal is late, the leak sweep runs at every Attempt boundary. Only **the hold being 1:1
> with an Attempt** is overtaken. When the solving agent ends its turn with budget left the
> orchestrator re-invokes, and that re-invocation is a *new* Attempt on the same Challenge — so one
> Instance now spans several consecutive Attempts rather than one. Tearing down and redeploying
> between them would keep the 1:1 and buy a fresh TTL, at the cost of any foothold on the Target and
> of mana, which is the concurrency cap. `CONTEXT.md` gains **Lease** accordingly: this is the
> condition the word was parked for.
>
> **That amendment's premise is withdrawn by
> [ADR-0023](0023-an-attempt-holds-the-turn-loop-and-order-is-not-asked-between-turns.md), and its
> conclusion is not.** The re-invocation is a Turn inside the Attempt, so the hold spans exactly one
> Attempt after all and `_end_lease` releases it at every close — **the 1:1 this record wrote down
> was right, and was never actually overtaken.** `Lease` still earns its entry, on the ground
> ADR-0023 gives: a hold is a resource and an Attempt is work, and one-to-one in span is not
> one-to-one in meaning.

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
