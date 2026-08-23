# The subscription is the credential, and nothing waits for a human

[#20](https://github.com/jerome-queck/incypher-ctf/issues/20) was written to decide secret handling
and credential posture. It opened with a rule — *"no subscription as the sole credential path"* —
and deferred its production half until [#13](https://github.com/jerome-queck/incypher-ctf/issues/13)
settled where the container runs.

#13 answered that half: we run the image ourselves, so runtime `--env-file` injection survives. But
the premise underneath the rule did not survive. **We hold subscriptions, not metered credit.** The
scored run goes on a **Codex Pro 20×** subscription; a metered key exists only as break-glass, and
the aim is that it never fires. So the rule is not wrong — it is *unsatisfiable*, and this record
replaces it.

The decision: **the subscription is the credential path, the quota is the cap, and every fallback
fires without a human.** What follows is what that costs and what it forces.

## The quota is the cap, and it hard-blocks inside our own run

This is the fact the ticket was missing, and it reframes everything else. Codex meters subscription
usage on **two** limits at once — a **5-hour rolling window** and a separate **weekly cap** — both
active since 30 July 2026. Three properties matter:

- **It hard-blocks.** At the cap, Codex refuses new turns until the window rolls over. It finishes
  the turn it is mid-way through and stops.
- **It meters by tokens, not messages** (since April 2026), so heavy requests drain it faster. The
  Solver is about as heavy a client as exists: long Observations, large contexts, hours unbroken.
- **One pool across every surface** — local CLI, IDE extension, and cloud tasks all draw from the
  same budget.

**The scored run is 5.5 hours and the window is 5.** Rolling means old usage ages out, so this is
not a wall at hour five; the constraint is that our *sustained rate* stays under cap ÷ 5h for the
whole run. A Pro 5× user reported burning a full window in 1.5–2 hours of heavy use, and 20× is
roughly four times that headroom — which puts us somewhere near 1.3–1.6× the sustainable rate.
**That is inside the noise, unattended, with a hard block at the end of it.** Not obviously fatal,
and not something to hope past.

Two consequences follow that are not obvious from the outside:

- **Subscription usage is what spends the allowance.** There is no separate pot that only metered
  API calls touch. Practice on the subscription draws from the same budget the scored run needs.
- **The operator shares the pool with the Solver.** Anything run personally on Codex on 21–22
  September comes out of the run's headroom. The day-1 rehearsal is *spending from the run's
  allowance*, not free, and the weekly cap rolls ~7 days from when it opened — so the 21st and the
  22nd are unambiguously the same weekly window.

## The break-glass key has to break its own glass

A metered key exists for the scored run and the aim is that it is never used. That aim is right and
it stays. But **a fallback that needs a human to reach for it is human intervention**, which is the
penalised act — the rule the whole repository is built around.

So the chain is **pre-armed and automatic**: Codex subscription first, metered second, and the seam
switches on exhaustion with nobody present. "Never used" then becomes a property we *measure* after
the run — we check whether it fired — rather than a plan resting on someone being awake at 13:00.

Two rules keep that honest:

- **Credential exhaustion is a stall, not an ending.** The window is *rolling*, so capacity comes
  back. A Solver that terminates at 13:00 on a quota block throws away the run it would have had at
  14:00. It backs off and retries.
- **A switch is recorded in the Step stream.** *"Why did solve quality fall off after 13:00"* is a
  question the eval will ask, and an invisible provider switch makes it unanswerable.

Failing over is never a **Cut**. [ADR-0005](0005-the-stall-call-lives-outside-the-solving-model.md)
is explicit that a Cut says what stopped *this Attempt*, and a credential dying is not evidence
about the Challenge — cutting here would blame the Challenge for our billing.

### The chain is two links with a socket for a third

On 22 September the chain is **Codex subscription → metered**. The Claude 20× subscription we hold
today is not assumed present on the day, so any additional subscription — ours, or a teammate's —
**slots in ahead of metered if it is in the environment at boot**. A teammate arriving with Claude
is a socket the design already has; its absence is not a hole. The Claude path is built regardless,
because training runs use it and because building it is what makes the socket real.

### The metered key lives in the scored board's overlay

The automatic chain creates a failure it would be easy to miss: a practice run left going overnight
switches to paid the moment the subscription caps, and spends real money with nobody watching.

`docs/credentials.md` already has the mechanism — **one board at a time, with an overlay for the
others**. The metered key goes in the scored board's overlay, exactly like `TEAM_KEY` and for the
identical reason: a Brunner practice run then *structurally cannot* spend money, because the
credential is not in its environment at all. No flag to set, no mode to remember.

That is also why the scored key carries a **budget alert and not an enforcing spend limit**. A
console limit that blocks requests is the same hard stop we are designing around, arriving at the
same unwatched hour. The only run that can reach the key is the one we mean to pay for, and the run
is bounded by 5.5 hours of wall-clock either way.

## No secret is in the environment of a process that runs challenge-supplied code

This is the gap no ticket owned, and it is the highest-value control available.
[ADR-0008](0008-one-image-for-every-board-and-two-seams-instead-of-one.md) puts the ReAct loop's
Bash and Python **inside the same container as the credentials, as root**. A challenge that gets
code execution there reads `os.environ` and takes everything — which is not exotic, it is pwn's
entire premise, and a malicious archive is cheaper still. Brunner alone ships 52 zip attachments.

**The orchestrator reads credentials once at boot, and every Step is spawned with an explicit
allowlist environment** — `PATH`, `HOME`, `TERM`, `LANG` — never an inherited one. It is one `env=`
argument on the spawn. It costs nothing at runtime, and it is the difference between *a challenge
stole a key we revoke* and *a challenge stole the one secret we can never replace*.

That last clause is the point. The **team key** is displayed by the Board rather than minted, with
no rotate control and no working channel to the organisers
([#41](https://github.com/jerome-queck/incypher-ctf/issues/41),
[#36](https://github.com/jerome-queck/incypher-ctf/issues/36)) — its leak is the one we could not
undo. So it is passed as an **argument**, held only in the `Target` seam, and read off the Board on
the day rather than stored long-term, since re-reading it is free. When the ADK arrives on 14
September and `solver.connect(host, port, team_key)` is handed it, that is unseen code holding an
unrotatable secret — accepted, because it is the sanctioned path and there is no alternative, but
it is exactly why the invariant above is worth having.

A heavier boundary — a separate uid, a nested container — is real defence-in-depth and belongs with
v2's supervisor. Not v1, where ADR-0008 makes PID 1 the Solver itself on purpose.

## Redaction covers a declared set, and promotion refuses

[ADR-0009](0009-store-what-was-observed-derive-every-judgement.md) redacts *"exact known values plus
base64/URL-encoded forms, no heuristics"* at write time, and never says where the list of values
comes from.

It comes from a **declared set of variable names in one place**, values read from the environment at
boot — not from "everything the env file supplied", which sweeps in `PATH` and `HOME` and would
shred every Observation into noise. A test asserts the declared set covers every secret variable in
`.env.example`, so adding a credential to the template and forgetting the redactor is **a failing
check rather than a leaked run**.

**Promotion re-scans, and refuses rather than warns.** ADR-0009 has a script promote the stream to
`runs/<run_id>.jsonl` as a pull request; that script is **agent-run, never during a live run** — the
ADR calls it "human-run", which this record corrects. The invariant ADR-0009 actually cared about is
untouched: the Solver has no git binary and never commits during a Run. But with an agent running
the promotion and opening the PR, **there is no human between the file and the push**; the human
sees it at merge, which is afterwards.

The two scans are not redundant. ADR-0009 correctly keeps `runs/` inside the secret scan — but
`CONTRIBUTING.md` is blunt that conformance fires *after* the push, so a hit there means the value
already reached GitHub and the credential is burned. The promotion-time refusal is the only control
that fires while a leak is still recoverable; the secret scan is the backstop that tells you to
start rotating.

## The image stays handover-shaped, and we refuse to bake a key

#13 rates "we run it ourselves" as **inferred, high confidence** — not stated, and the channel to
confirm is dead. The ticket held three options against being wrong: a runtime-fetched short-lived
key, a key expiring right after the event, or a local-model-only build. **All three are ruled out.**

The contingency is that the image is *handover-shaped* — everything inside except secrets, a
documented set of environment variables as the only thing it needs from outside — which costs
nothing because we already build that way. If we are wrong on the day we ask for `-e` or
`--env-file`, and **if that is refused we do not compete rather than bake a key into a layer.** A
layer is readable by anyone holding the image, survives a later `RUN rm`, and travels with every
push — unrecoverable in precisely the way the team key already is. The refusal is recorded here so
it is not relitigated at 10:00 on competition day by someone who wants to compete.

## Consequences

- **A restart must re-authenticate without a human.** `docker run --env-file` is *expected* to keep
  the resolved values in the container config, so `docker start` and `--restart unless-stopped`
  re-authenticate with no file present. **Unverified** — there is no container runtime on the build
  machine — and marked so deliberately, in the style #13 established. If it holds, the values live
  where `docker inspect` prints them, which `docs/credentials.md` now names.
- **Codex authenticates by file, not environment,** and `--device-auth` needs a human the Solver
  will not have. Whether a piped `CODEX_ACCESS_TOKEN` survives 5.5 hours is a **day-1 rehearsal
  item**; if it does not, the run dies on auth rather than quota and every fallback above is aimed
  at the wrong failure. The Codex shim ([#37](https://github.com/jerome-queck/incypher-ctf/issues/37))
  is the fallback for that — which makes its real job **auth durability**, not protocol uniformity.
- **v1's quota mitigation rests on a lever v1 does not have.** Not running the top tier at every
  Attempt is a genuine defence, but v1 has **one default brain** and routing is a v3 decision. Either
  [#17](https://github.com/jerome-queck/incypher-ctf/issues/17) pulls a thin slice of routing forward
  *as a credential measure*, or v1's exposure is top-tier-on-everything for 5.5 hours and the
  automatic chain carries the whole risk.
- **A lent credential is rotated by its owner.** A teammate's subscription token is their personal
  credential; it rides in the event overlay, and rotation is their call, on request.
- **The ToS posture is deliberate, not inherited.** The plan is the local-subscription route, with a
  Codex shim built and tested but held in reserve. Recorded as a decision made with the cost visible:
  an account action mid-run is a harder stop than quota, hits the whole team's credential, and has
  no appeal on a Saturday afternoon. The posture itself remains
  [#37](https://github.com/jerome-queck/incypher-ctf/issues/37)'s to settle.
- **`docs/credentials.md` loses its metered/practice split.** "Practice on a subscription; compete on
  metered billing" described credit we do not hold, and is rewritten by this record.
