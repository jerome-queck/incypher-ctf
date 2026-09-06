# An unsettled read is not an empty fact

A read that times out, fails authentication, meets an unrecognised response or cannot be parsed says
what happened at that attempt and moment. It does not say that the Board has no rows, no schedule,
no Challenge detail, no Artefact or no prior work. Today seven reads collapse those two claims, two
of them on the live IN-CYPHER Board. This decision gives them one contract without pretending one
endpoint-specific control or one universal enum fits every read.

Decided on [The reads whose empty answer is not a
fact](https://github.com/jerome-queck/incypher-ctf/issues/174), under wayfinder map
[#153](https://github.com/jerome-queck/incypher-ctf/issues/153). This is a planning decision. Its
production changes and proofs belong to the map's `/to-spec` -> `/to-tickets` handoff.

## The contract stands beside ADR-0016

An ordinary empty value is settled only where structurally valid data or an endpoint-specific,
positively identified response proves that emptiness is what the source said. A timeout, connection
fault, generic status, malformed document or unexplained empty body remains unsettled. Each reader
keeps the outcome vocabulary and corroboration its own contract needs; no universal status enum is
forced across unrelated protocols.

Three axes remain separate in the record:

- the value or bytes returned;
- the read outcome and freshness that say whether they are trustworthy; and
- the source or provenance that says where they came from.

One field never impersonates another. In particular, Board provenance does not prove a successful
read, and `stale` continues to mean that a Sighting carried detail from an earlier cycle. A new
sibling outcome records whether this cycle's detail read settled. Changing `stale` to mean both
would violate ADR-0009's rule that a written field does not silently change meaning.

This ADR stands beside [ADR-0016](0016-an-empty-list-is-not-an-empty-board.md). It generalises the
principle that an uncorroborated empty collection is unknown; it does not generalise ADR-0016's
particular invalid-query control. That control remains the right positive evidence for the
Challenge LIST and does not prove anything about another endpoint.

## Recovery orients; an unsettled read never ends the Run

No read in this decision introduces a boot Refusal or terminal Run verdict merely because its
answer is unsettled. Before the competition window closes, Recovery owns it, preserves the exact
response and time, tries deterministic alternate sources, and schedules retries across Intake
cycles with bounded backoff. A materially different response resets the backoff because the world
has changed. A known outage is therefore *unreadable now, retry scheduled*, never *the Board has
nothing*. Unaffected Challenges and capabilities continue. If nothing useful is presently safe,
the Solver remains alive in Recovery or wait and never reports a clean finish. The actual window
boundary, not frustration or an empty read, enters the reserved tail.

Deterministic code handles known shapes, validation, alternate endpoints, retry clocks and durable
records. A persistent or novel shape activates the Recovery LLM, which autonomously assesses the
preserved evidence and chooses what to do next: probe differently, wait, retry, isolate work,
redirect capacity or propose a repair. No human is in the competition loop.

That authority is adaptive, not historical. A solving or Recovery model never authors the Run's
facts about Attempts, submissions, Instances or ownership. A repair model may change candidate code
in isolation; deterministic machinery tests, promotes, rolls back and records it. When two
structurally valid sources disagree, both observations survive. A predetermined authority rule may
select one; otherwise Recovery decides the next diagnostic or reversible act while the fact stays
unsettled. Deterministic guards still require positive authority before a submission, deployment,
renewal, ownership change or destructive action. Choosing not to perform one unproved irreversible
act while pursuing another route is an autonomous decision, not a human escalation.

Existing Refusals for a missing credential, an unknown Board profile or another pre-Run
misconfiguration remain outside this decision. ADR-0032's diagnosed terminal recovery verdict also
remains available where no sanctioned safe Boot can exist; a transient or novel Board read alone
never earns it.

## The seven dispositions

### 1. Scoreboard — fix, live

On the IN-CYPHER Board, reverified with the team token on 6 September 2026,
`GET /api/v1/scoreboard/top/10` returns 404 while `GET /api/v1/scoreboard` returns 200 with
`{"success": true, "data": []}`. The current Solver catches the first fault and serialises
`scoreboard: []`, erasing the difference.

The Board seam deterministically discovers the compatible scoreboard endpoint, validates its
shape, and records the selected endpoint and read outcome beside the rows. Compatible alternatives
may be tried, but a failed one is retained as an observation rather than replaced by a fictional
empty table. Only a settled empty payload becomes an empty scoreboard. What Order does on a
crowd-less Board remains the separate decision in [Order and Tier when the board has no
crowd](https://github.com/jerome-queck/incypher-ctf/issues/163).

### 2. Board window — fix, live

On the same 6 September recheck, `/api/v1/configs` returns 403 to the team token while the landing
page's `window.init` states `2026-06-30 16:00 UTC` to `2026-09-21 16:00 UTC`. The current profile
records `board_window: {}` with no outcome or source.

Discovery reads both available sources, records each source and outcome, and preserves any
disagreement. The tracked competition rules remain authoritative for scored Run timing: the CTFd
window can cover practice time and is not the scored window. An unsettled Board-window read starts
Recovery and later retry; it never ends the Run or silently becomes no published window.

### 3. First-cycle Challenge detail — fix

A failed detail GET with an earlier trusted Sighting carries that detail and remains `stale`. With
no earlier detail, the Challenge records an unsettled detail outcome, receives no invented
`shared=False`, `timeout=None`, empty prose or attachment manifest, and waits for another Intake
cycle while other Challenges continue. It is not eligible for an Attempt until the facts required
to interact with it safely settle.

### 4. Zero-byte Artefact — fix

A zero-byte response is `HELD` only where the Board's content digest or an independent reread
corroborates that zero bytes are the Artefact. Without that evidence it is an explicit unsettled
download, is not staged as ordinary input, and is retried. This preserves a legitimate empty file
without trusting a degraded 200 response.

### 5. Anonymous read — fix

`unauthenticated_read` becomes at least `answered`, `refused` or `unreadable`. It remains diagnostic
and grants no solving authority. `unreadable` may be retried; it never impersonates a Board that
definitively refused anonymous access.

### 6. Missing Flag stream — fix under ADR-0032

The current sweep treats a missing stream as zero Observations and records `ok=True`. The ticket's
older premise that `/state` may disappear harmlessly was superseded by
[ADR-0032](0032-a-run-survives-its-boots-and-recovery-owns-the-first-fault.md): recovery-critical
Run state is authoritative input to later work.

A missing stream is therefore an operational fault, not another empty read. Recovery isolates the
affected Attempt, preserves the damaged state, reconstructs authority from durable records and
trusted Board facts, and continues unrelated safe work. It never invents whether a Candidate was
observed or submitted, and the sweep never reports success over a stream it could not read.

### 7. Probe window — fix

The operator probe distinguishes `published`, `not-published`, `unreadable` and
`unrecognised-page`, preserves the response, and tries deterministic alternate sources. A failed
schedule read cannot become an instruction to repair credentials: it may be a pre-live gate, a
temporary Board outage or a theme the parser does not yet recognise.

## Handoff and proof

The post-map specification gives each read a typed outcome, durable observation, retry state,
alternate-source order and positive settlement test. Tests cover settled empty, explicit absence,
authentication refusal, transient transport failure, malformed content, source disagreement,
retry after recovery and the two measured IN-CYPHER shapes above. Fault injection proves that an
unsettled read neither fabricates a fact nor closes the Run, that unaffected work continues, and
that irreversible effects remain deterministically gated while Recovery chooses the next action
without a human.
