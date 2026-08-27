# The vendor's agent drives the loop, and the seam runs an Attempt

> **One section is superseded by
> [ADR-0023](0023-an-attempt-holds-the-turn-loop-and-order-is-not-asked-between-turns.md):
> *An early stop is a new Attempt, not a give-up*, below.** Its mechanism never ran — the
> orchestrator re-invokes *inside* the same Attempt, so the re-invocation is a **Turn** and Order is
> not asked between them. The concept this record declined to coin was coined anyway. Two of its
> claims are wrong rather than merely overtaken: repeated early stops do **not** feed the novelty
> counter (a new Attempt would reset every counter it has), and Order would not have gone on ranking
> the Challenge first (ADR-0015's spend term ranks it away by the sixth). **Everything else here
> stands** — the vendor's agent driving its own loop, the workdir as the memory, the rejection of
> `codex exec resume`, and the rule that an early stop must never become the give-up button ADR-0005
> removed. That last one is exactly what the turn loop delivers; only its location moves.

[#17](https://github.com/jerome-queck/incypher-ctf/issues/17) was chartered to pick v1's default
model and design a provider-abstraction seam behind it. It assumed the seam would be a completion
endpoint — [ADR-0011](0011-the-sanctioned-path-is-the-only-path.md) had already written the shape
down as *"roughly `complete(prompt) → text`"*. That assumption does not survive contact with the
CLI we are committed to.

The decision has two halves that stand or fall together:

**Codex drives its own loop, at full strength, with its own tools — and the orchestrator watches
the event stream, counts the stalls, and holds the kill switch.**

**The seam is not a completion. It is `run_attempt(prompt, workdir, deadline) → stream of Steps`.**

This record **supersedes [ADR-0011](0011-the-sanctioned-path-is-the-only-path.md) in part** — the
seam's shape and its claim that the vendor's loop does not reach us — and **amends
[ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md) and
[ADR-0007](0007-truth-about-an-instance-lives-on-the-board.md) in one bullet each**. It leaves
[ADR-0009](0009-store-what-was-observed-derive-every-judgement.md) untouched.

## `codex exec` has no door marked "just answer"

ADR-0011 asserted that `--output-last-message`, `--output-schema` and `-s read-only` *"reduce it to
prompt in, structured text out"*. Verified against `codex-cli` 0.147.0, that is wrong. Those flags
constrain the shape of the **final message** and the sandbox its shell may **write** to. None of
them removes the shell. There is no tool-disabling flag on `codex exec`, nothing in
`codex features list`, and no config key for it.

So the choice was never "which shape of completion". It was:

- **We drive** — ask Codex what to run, run it ourselves, feed the output back. Codex is trained and
  harnessed to act, and there is no supported way to stop it acting, so this is fighting the tool
  for the whole run and paying subscription rates to use a coding agent as a chat box.
- **Codex drives** — hand it the Challenge and let it work with the real Kali tools. What the
  product is for, and the strongest thing available to us.

The asymmetry that makes this a decision rather than a discovery: **Claude can do either.**
`claude -p --tools ""` is a genuine plain-completion door on a subscription credential, verified
working. Codex has no equivalent. Since [ADR-0010](0010-the-subscription-is-the-credential-and-nothing-waits-for-a-human.md)
puts Codex at slot 1 — it is the credential we are certain to hold on 22 September — the seam has to
fit the CLI that cannot bend, not the one that can.

## Watching is what keeps ADR-0005 alive

Letting Codex drive is not what would have broken ADR-0005. Letting it drive **unobserved** would
have. `codex exec --json` streams the internal loop as events, so every command it runs and every
output it gets is available to us live — and that is precisely the input ADR-0005's three counters
were specified against.

Audited bullet by bullet, six of ADR-0005's seven consequences survive **unchanged**: a Challenge is
never marked impossible; the three counters read only Observations; the reset is silent; nothing
asks the model to estimate its own budget; shadow mode replays offline over the stored stream; and
the deterministic recon cascade runs before Codex is invoked, with its results in the prompt.

The title survives too, which is the point. **The stall call still lives outside the solving
model.** What ADR-0005 chose as a *mechanism* — that our orchestrator issues each command — moves.
What it argued as a *principle* — that the model's account of its own progress never buys it time —
does not.

One bullet genuinely coarsens. *Time is bought by state transitions* still holds: we still detect a
Checkpoint in the stream, and extensions are still capped at K. But Codex takes its next turn
without asking, so the unit being granted is no longer a Step — it is the **kill deadline**. A
confident rabbit-hole still gets less time than a run that is moving; the grain is coarser.

The residual cost, named rather than hidden: Codex compacts its own context when it fills, and that
compaction can replace real Observations with the model's summary of them **inside** a long Attempt.
ADR-0005's rule governs what crosses an Attempt *boundary* and is not breached — each Attempt is a
fresh `codex exec` carrying nothing — but the effect is real, and it is a standing argument for
short Attempts, which the counters and the budget already produce.

## An early stop is a new Attempt, not a give-up

Way B creates a state Way A could not: Codex ends its turn, finds no Flag, and budget remains.

Treating that as the end of the Attempt would hand the model the give-up button ADR-0005 spent its
whole argument removing — the model would be ending its own Attempt, by the back door. So the
orchestrator **re-invokes**, and the re-invocation **is a new Attempt**: Order simply ranks the same
Challenge first again and takes it back to back. No new concept is coined for it, because
`CONTEXT.md`'s definition of an Attempt — one bounded run of the Solver at a single Challenge —
already describes it exactly.

`codex exec resume` is rejected. It continues the previous session with its prose intact, carrying
hypotheses and conclusions across, which is the carry ADR-0005 restricts to a single approach label.
A fresh invocation over the **same working directory** carries the right things and carries them for
free: **the workdir is the memory.** A Checkpoint *is* an environment state transition, so the
extracted archive, the shell that now answers and the route that went 403→200 are still on disk. A
fresh Codex opens onto an environment that has already moved, without inheriting anyone's theory
about why it moved.

ADR-0005 anticipated the obvious objection — that a deterministic starting state gives the next
Attempt *"every reason to repeat the last"* — and its answer, the model-authored approach label, is
the answer here unchanged.

**The closed Cut vocabulary holds.** A re-invocation producing no new Checkpoint feeds the novelty
counter rather than being a free retry, so repeated early stops trip `cut:novelty` on their own; a
volunteered "impossible" already has `cut:self-reported-impossible`. Way B adds no cause.

## The seam, and what stays ours

`run_attempt(prompt, workdir, deadline) → stream of Steps`. The orchestrator owns the counters, the
deadline, the kill and the Board. The adapter owns one thing: how this vendor's CLI is spawned, and
how its output becomes Steps. **No abstract base** — the same rule
[ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md) applies to `Board` and
`Target`, so this is a shape two files happen to share rather than a hierarchy.

Every adapter returns the **same deterministic schema**, and that uniformity is load-bearing rather
than tidy: #17 exists partly to measure per-model refusal and premature-quit behaviour from real
runs, and two adapters emitting differently-shaped streams cannot be compared at all.

**Codex never touches the Board.** ADR-0008 already made `Board` — enumerate, submit, Instance
lifecycle — a deterministic seam of ours with no model in it, and three decisions already made
force it to stay that way: ADR-0010's invariant that no secret sits in the environment of a process
running challenge-supplied code (Codex's subprocess *is* that process, so it cannot hold the CTFd
token); the board-wide `incorrect_submissions_per_min` limiter, which a model submitting on impulse
would burn for every Challenge; and ADR-0007's rule that a correct Flag is followed by a terminate
and a late one grades `incorrect`, both of which need the Instance deadline the orchestrator holds.

That leaves a Flag arriving by two routes, and the Claim/Observation pair decides between them. A
Flag Codex **states** is a Claim. The same string sitting in real command output is an
**Observation**. So the schema carries the candidate, the recorded Observations are swept for the
Board's wrapper, and a candidate that traces to an Observation is what authorises a submit. One the
model asserts that appears in no Observation is still submitted while attempts remain — but recorded
as **unverified**, because MIRAGE-Bench measures agents fabricating an action 46–65% of the time in
unachievable states and that rate is a per-model number worth having.

## Consequences

- **We do not choose Codex's toolset.** #17's brief said *"expose only Bash+Python as tools"*; that
  premise dies with Way B. The standing principle replacing it is **prefer the vendor's tooling
  unless it does not make sense** — we are paying for a tuned harness and reimplementing it is how
  we get a worse one.
- **Web search is on, and it is a Board profile value.** Every advantage counts on a 5.5-hour
  clock, and a Challenge shipping an image or an audio clip is often solvable only by looking
  something up. ADR-0008 already makes a Board's rules-derived prohibitions a tracked field in
  `docs/competitions/<event>.board.json`, and this is one: default on, a Board's profile turns it
  off, and `scripts/check-rules-drift.sh` is what catches a Board that bans it.
- **An Instance hold now spans consecutive Attempts on one Challenge**, which amends ADR-0007's
  1:1 line. Tearing down and redeploying between back-to-back Attempts would preserve the 1:1 and a
  fresh TTL, at the cost of any foothold established *on the Target* — usually the whole game — plus
  mana, which is the concurrency cap. Losing a foothold to preserve a naming convention is the wrong
  trade. **`CONTEXT.md` gains Lease**, the word it had parked for exactly the condition of a hold
  outliving an Attempt.
- **`--tools ""` is demoted, not discarded.** The Claude adapter drives like Codex so the streams
  compare. But the flag is the one way to ask a model something without it acting, which is what a
  Triage call that must not touch the Board wants.
- **v1 has one brain per Run.** The chain switches on exhaustion and on nothing else; no Category,
  Tier or cost input picks the model. v1's quota mitigation is therefore **rate discipline, not
  routing** — [#20](https://github.com/jerome-queck/incypher-ctf/issues/20) closed noting the lever
  v1 does not have, and this records that v1 still does not have it. Chain **order** is a config
  value rather than code, so a practice Run can put Claude first without a rebuild.
- **The local model is cut from v1.** The Colima VM is pinned at 16 GiB, not the machine's 48
  ([`scripts/runtime.py`](../../scripts/runtime.py)), so the headroom that justified it is *host*
  headroom — which makes a local model a second long-lived host process, the exact shape ADR-0011
  refused for the proxy. It costs nothing to defer, because `codex exec --oss --local-provider`
  means the local tier needs no adapter of ours whenever we want it.
- **Refusal and premature-quit are queries, not fields.** Per
  [ADR-0009](0009-store-what-was-observed-derive-every-judgement.md), the Step carries the response
  text, the model and the credential slot; "refused" and "quit early" are derived offline and joined
  to Category. Nothing is added to the closed Cut vocabulary.
- **Credential exhaustion mid-Attempt hands the Attempt over**, and requeues only if the handover
  itself fails. ADR-0010 already ruled exhaustion *a stall, not an ending* and explicitly not a Cut;
  holding for the rolling window is worse arithmetic, since the window is 5 hours against a
  5.5-hour Run. The handover is not clean — the next model gets the Challenge, the Instance and the
  Observations, not Codex's internal context — and that is acceptable, because it is the reset
  ADR-0005 designed for.

## What this record does not settle

**How the 5.5 hours divides across the Board.** Whether an Attempt gets a fixed budget or a share of
what remains is Tier's question and requeue is
[#47](https://github.com/jerome-queck/incypher-ctf/issues/47)'s; both are routed there rather than
answered here. The map already notes there is no academic source on allocating a fixed budget
*across* a Board — every benchmark caps per-Challenge independently — so it is ours to measure on
day 1.

The budget being **strict** is not open, and was re-examined here rather than reopened: #15 measures
that most solves land in the first ~20 steps and that spend anti-correlates with success, so the
expensive mistake on a 5.5-hour clock is grinding a dead Challenge rather than cutting a live one.
The case against cutting something that was progressing is what the Checkpoint rule already answers.

## Revisit when

- **The ADK lands on 14 September** and turns out to impose a loop of its own. ADR-0006 already
  makes it an adapter rather than a foundation, and this seam is where that promise gets tested.
- **A practice Run has produced enough stream data** to say whether counters computed over a
  vendor's event stream trip at the same places as counters computed over commands we issued
  ourselves. This record assumes they are the same signal; nothing has measured it.
- **A vendor ships a supported no-tools mode for a coding CLI**, which would reopen Way A at no
  cost to strength.
