# incypher-ctf — context

The domain: an autonomous agent that solves capture-the-flag challenges without human
intervention, for the IN-CYPHER hackathon.

## Language

The ubiquitous language of this repository: the words the code, the issues and the commits all
use for the same thing. An entry earns its place when two people — or a person and an agent —
could reasonably mean different things by the same word.

Each entry is the term, what it means **here**, and the near-synonyms to avoid so the wrong one
does not creep back in.

### The game

**Solver**:
The autonomous program this repository builds — the Docker container that receives a challenge
and works it unattended to a flag. "Agent" is the competition's word for the same thing and is
fine in prose; in code and issue titles, prefer **Solver** so it never collides with the AI
coding agents that *write* this repository.
_Avoid_: agent (ambiguous here), bot, script

**Challenge**:
One CTF task the Solver attempts — a single problem exposed by a Board, holding one Flag. The
kind of problem it is, is its Category; the fiction it is dressed in is its Scenario setting.
_Avoid_: problem, task (a `task` is a repository issue label — a different thing)

**Category**:
The Board's own label for what kind of problem a Challenge is — `web`, `pwn`, `crypto`, `rev`,
`forensics`, `misc`, and whatever else an event decides to ship. **It is an open string read from
the Board, never an enum in our code.** IN-CYPHER's practice Board namespaces its own as
`(Practice) forensics` and `(Practice) misc`; the organisers describe the set as the classic
categories *and more*. A Challenge's CTFd `type` is open in the same way — BrunnerCTF ships a
`flightops` alongside the standard ones. Code that switches on a fixed list does not fail loudly
when an event ships a category it has never met; it silently drops the Challenge.
_Avoid_: kind, track, genre. Not `type` either — that is CTFd's separate field for how a
Challenge is *served* (see Static Challenge).

**Scenario setting**:
The fiction a Challenge is dressed in. IN-CYPHER sets a number of its Challenges in medical and
healthcare scenarios — and that is a setting, not a Category: underneath, such a Challenge is
still web, pwn or forensics, and is solved with that Category's tools. Knowing the setting helps
*read* a Challenge; it never decides what to reach for.
_Avoid_: category, track, theme-as-category. In particular there is no "healthcare category" —
healthcare is a setting over the ordinary ones.

**Flag**:
The string that proves a Challenge is solved, submitted to the Board by the Solver. **The wrapper
is a property of the Board, not of the domain** — IN-CYPHER's is `flag{…}`, BrunnerCTF's is
`brunner{.*}` with any text as the body, and a practice Board we have not met yet will have its
own. So will its rules, its scoring, and its categories: each Board is read at the start of the
event, not assumed. A Solver that hardcodes one event's wrapper finds nothing at the next.

**A Board states its wrappers as a list, and a list of one is the ordinary case.** Where it states
more, every shape is matched **separately** over the same bytes and they are never joined into one
alternation — an alternative that starts earlier eats the bytes a later one would have matched, so
the join finds *fewer* Flags than the first shape alone (ADR-0020). The first entry is the Board's
primary shape and wins provenance on a tie.
_Avoid_: answer, solution, key (a "key" here is a credential — see Team key). Not *the* wrapper
either, where a Board states more than one — the singular is what invited the alternation.

**Board**:
One competition's CTFd instance — the thing the Solver enumerates, submits to, and is scored by.
A Board, not "the platform", because the Solver plays several: practice Boards like BrunnerCTF
Global and the IN-CYPHER Board itself. Which one it is pointed at is `CTFD_URL`, and getting that
wrong is a disqualification rather than a misconfiguration (`docs/competitions/`).
_Avoid_: platform, site, server, instance (an "instance" is one deployed Challenge — see below)

**Board profile**:
Everything the Solver knows about the Board it is pointed at: the Flag wrapper, whether
`ctfd-chall-manager` is installed, whether an unauthenticated read is answered, the submission
rate limit, and the prohibitions that Board's rules impose. **A profile is discovered at startup
wherever it can be, and configured only where it cannot** — a tracked
`docs/competitions/<event>.board.json` carries the URL and the rules-derived prohibitions, which
exist in prose and nowhere in the API, and the rest is read off the live Board
([ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)). This is why
a Board we have never met costs no code. A configured value we could have measured is a value that
goes stale without saying so.
_Avoid_: config, settings, board.json (that file is one input to a profile, not the profile)

### How a Board serves a Challenge

**Static Challenge**:
A Challenge the whole event shares — one deployment, one Flag, identical for every team. Which
shape a Challenge is, is the Board's `type` field, which is an open string exactly as Category is.
_Avoid_: shared challenge, normal challenge

**Isolated Challenge**:
A Challenge deployed per-team on demand, time-limited, and carrying a Flag unique to the team it
was deployed for — so a Flag lifted from another team's Instance does not grade. IN-CYPHER serves
these through the `ctfer-io/ctfd-chall-manager` plugin.
_Avoid_: dynamic challenge — CTFd's `dynamic` means a point value that falls as solves come in,
which is a different axis entirely — per-player challenge, instanced challenge

**Instance**:
One running deployment of an Isolated Challenge, belonging to one team and expiring on its own.
The Challenge is the thing on the Board; the Instance is the copy currently held. Keeping the two
apart matters because "the Instance expired" and "the Challenge is unsolved" are different facts
that want different responses.
_Avoid_: container, deployment, box, and **Lease** — which is our *hold* on an Instance and now has
its own entry below.

**Lease**:
The Solver's durable claim on an Instance — from reservation, deploy or recovery until a trusted
Board read proves expiry or the central Lease coordinator explicitly releases or terminates it —
and **not** the same span as an Attempt or Boot. The word was parked through v1's early design on
the grounds that a hold one-to-one with an Attempt names nothing Attempt does not already name; it
is coined here because a hold and the work done under it are different things: "the Instance
expired", "the Attempt was cut" and "we let the Lease go" are three separate facts, and only the
middle one is about the Challenge. A Lease may be active, reserved or recoverable while no command
runs. Only a positively attributed, durably ownerless Lease that survives a grace interval and
trusted recheck is orphaned; inactivity or an empty/unreadable ledger proves nothing. What a Lease
costs is Mana, which is why safe reconciliation matters even between Attempts and Boots.
_Avoid_: hold, reservation, session, and *Instance* — the Instance is the Board's running copy of a
Challenge, the Lease is our claim on it. The two end at different moments, which is the whole reason
for the second word.

**Mana**:
What chall-manager charges a team for holding Instances: every Isolated Challenge carries a mana
cost, every team a mana total, and destroying an Instance reclaims what it cost. Mana is why
holding an Instance is never free — an Instance nobody is working still costs what it cost to
deploy. **Mana is the concurrency cap**, not a currency beside one: a Board that permits two
Instances at a time expresses that as a total of two, and a Board that sets the total to zero has
switched the whole feature off. A deploy that cannot afford itself is **refused**, and the
Instances already held are left alone — chall-manager has no eviction path, which is what makes a
leaked Instance cost capacity for the rest of the run
([ADR-0007](docs/adr/0007-truth-about-an-instance-lives-on-the-board.md)).
_Avoid_: quota, credits, points (points are score — see Board)

**PoW gate**:
The proof-of-work a raw-TCP Challenge demands before its service will talk, bound to the Team key
so the work cannot be shared or farmed out. Web Challenges have no gate — they are reached at an
unguessable subdomain instead.
_Avoid_: captcha, rate limit, challenge (a PoW gate stands in front of a Challenge; it is not one)

**Target**:
A Challenge's own running service — the thing being attacked — and the name of the seam that
reaches it: a raw-TCP socket behind a PoW gate, or a web endpoint at an unguessable subdomain. A
Target is deliberately not a Board and not a Challenge: the Board is who scores us, the Challenge
is the problem on it, and the Target is the process at the other end of a connection. Its address
does not exist until an Instance is deployed
([ADR-0007](docs/adr/0007-truth-about-an-instance-lives-on-the-board.md)), and the ADK sits behind
this seam rather than the Board's, because clearing a PoW gate has nothing to do with submitting a
Flag ([ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)).
_Avoid_: host, endpoint, service, box, the challenge (the Challenge is the task; the Target is what
it exposes)

**Artefact**:
One file the Board ships with a Challenge — what the recon cascade opens onto, and what the model is
handed. 22 of 74 BrunnerCTF Challenges ship none at all, so three Challenges in ten are prose and
nothing else, and prose is then the only input an Attempt has.
_Avoid_: asset, download, sample, payload. Not *attachment* either — that is the Board's listing of
a file together with what our fetch made of it, so it exists for files we could not fetch and for
ones we decided not to; an Artefact is one we hold.

### How the Solver chooses what to work

**Intake**:
The Solver's copy of a Board — every Challenge's details and every file it ships, pulled to local
disk and refreshed on a fixed cycle for as long as the run lasts. Intake is a *sync*, not a fetch:
it exists because a Board moves underneath you, releasing Challenges mid-event, adding hints, and
replacing files. Re-running it is how the Solver notices. It touches no model and costs no tokens —
a Board's own file URLs carry a content hash, so what has changed is decided by comparing strings.
_Avoid_: enumeration (that is the one list call Intake begins with), scrape, download, crawl

**Triage**:
Reading Intake's output to decide what each Challenge is worth. Triage runs over any set of
Challenges at any time — at the start of a run, again at its midpoint over what is still unsolved,
and on whatever appears in between — so it is one callable thing rather than a stage of a pipeline.
It **extracts and does not predict**: where a Board states a difficulty, Triage parses it, and a
model is asked to judge only what is left. Triage never deploys an Instance and never opens a file
it has downloaded; it reads the manifest, not the contents.
_Avoid_: ranking, scoring, classification, assessment

**Tier**:
The effort budget Triage assigns a Challenge — how much of the run it is worth spending before an
Attempt is cut. **A Tier is not a difficulty grade and not a rank.** It **rises on evidence and never
falls**: Checkpoints earned across a Challenge's Attempts raise it, capped, because attempting a
Challenge is the only real evidence of its difficulty we ever get and Triage's prior is a weak one.
Nothing lowers it — a Claim that a Challenge is easy buys nothing, exactly as it buys no time within
an Attempt (ADR-0005), and a barren Attempt is evidence about whether to come back rather than about
how long to stay, which is **Order**'s question
([ADR-0015](docs/adr/0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)).
The intended bias toward the Categories the Solver is *weakest* at is **deferred, not dropped**: it
needs a per-Category strength signal, and Category is an open string read from the Board, so v1 has
no entry for the categories that will actually be scored. Until a stable Category vocabulary and
real per-Category solve rates exist, a Tier is the difficulty a Board **states**, which Triage
extracts. A confirmed Checkpoint raises it by one rung only as far as the highest stated Tier; the
progress signal cannot manufacture effort bands above the scale Triage uses.
_Avoid_: difficulty, priority, rank, score, weight

**Order**:
The sequence the Solver takes Challenges in — **a pure, deterministic, total function** over the
Board's own signals and the Run's, recomputed at every Attempt boundary and never stored. **There is
no queue**: a cut Challenge is not placed anywhere, it simply becomes eligible again, and where it
next ranks falls out of the function
([ADR-0015](docs/adr/0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)). Order and Tier
are two words because they read the same inputs and weight them **oppositely** — Order goes where the
Solver is strongest to bank Flags early, Tier spends longest where it is weakest — and a design that
merges them silently picks one goal and loses the other. That opposition is the pair's *design
intent*; the term it turns on is the same deferred strength signal Tier's entry describes, so in v1
Order is what remains and all of it is measured: tractability, payoff, progress, a live Lease, and a
monotone penalty on what has already been spent. **Order is not the only thing that chooses the
pick**: roughly one Attempt in four is a **reserved exploration share**, spent on the best Challenge
nobody has solved regardless of where it ranks, because tractability weights high-solve Challenges
up and would otherwise leave the unsolved set starved by construction. Tractability itself is a
Challenge's **solve velocity** wherever two samples exist and its solve count before that — a
cumulative count is uniformly zero on a fresh Board and credits a Challenge for a rush that finished
before we arrived
([ADR-0017](docs/adr/0017-the-exploration-share-and-solve-velocity-are-reinstated.md)).
_Avoid_: priority, queue position, queue (there isn't one), tier (the other half of the pair, and
deliberately a different word)

**Working set**:
The Challenges the remaining clock is actually committed to — the top of **Order**, as many as the
time left can still afford Attempts for. It exists because the alternative allocation is the one
that looks fair and solves nothing: dividing the remaining hours by the number of unsolved
Challenges puts every Attempt below the length at which anything is ever solved. So the clock buys a
*number of Attempts*, and those go to the top of Order — which means the working set narrows on its
own as the Run burns down, and the end-of-Run scramble is a consequence of the same arithmetic
rather than a mode with rules of its own. A Challenge outside it is not banned and never becomes
banned; on a fixed clock, not being reached is simply what most of a Board does
([ADR-0015](docs/adr/0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)).
_Avoid_: queue, backlog, shortlist, and **eligible set** — that is every unsolved Challenge, which is
the thing the working set is a slice of

### How the Solver works a Challenge

**Lane**:
One place in the Solver's bounded capacity for running Attempts concurrently. A Lane carries at
most one active Attempt; several Lanes let several Attempts make progress at once. The Lane is
capacity, not durable identity — the Attempt identifies the work and the Lease identifies the
Instance hold across moments when no command is running.
_Avoid_: worker, thread, slot, agent (all name an implementation or collide with another domain
word rather than naming the concurrency boundary)

**Boot**:
One uninterrupted incarnation of the Solver process inside a **Run**. A Run has one Boot when
nothing fails and several when a supervisor replaces a failed process. A Boot failure is not by
itself a Run crash: the Run crashes only when no later Boot can safely continue it. Keeping the
two apart lets process uptime change without resetting the competition window, the work already
spent, or the evidence the Run produces.
_Avoid_: process (the operating-system mechanism rather than the domain boundary), restart (the
transition between Boots), run (the whole competition window)

**Run**:
One unattended outing of the Solver at one Board — the whole competition window, several hours and
many Attempts, across one or more Boots. A Run is the unit everything is
compared *across*: a threshold is calibrated over Runs, a version gate is a verdict on one, and its
`run_id` is what joins every record ADR-0009 writes. **A Run survives a restart.** If v2's
supervisor starts a later Boot the Run continues, because the thing being measured is hours against
a Board rather than the life of a process — treating a
restart as a second Run would silently compare halves against wholes.
**A Run ends when the competition window closes, it is deliberately stopped, or recovery cannot
safely produce another Boot — never merely because one Boot failed, and never because the Board
looks finished.** Challenges drop mid-event, so "nothing left to work" is not something the Solver
can ever conclude; that is the Run-level counterpart of ADR-0005's rule that a Challenge is never
marked impossible. Running out of credential is not an ending either: quota windows roll, so
capacity comes back and the Solver backs off through it (ADR-0010).
_Avoid_: session, attempt (an Attempt is one Challenge inside a Run — see below), execution. Not
**Run state** either: that is what a Run *produces*, and it is a separate entry below.

**Attempt**:
One bounded run of the Solver at a single Challenge — from the recon that opens it to the moment it
is cut or a Flag is submitted. A Challenge may be attempted many times: **an Attempt ends, a
Challenge does not.** Keeping the two apart is what makes giving up cheap, because what is
abandoned is an Attempt and never the Challenge (ADR-0005). **Consecutive Attempts on one Challenge
are ordinary**, not a special case — a cut Challenge is never terminal, so Order can pick one it has
already worked. What carries across is the environment — the workdir is the memory — plus the
approach labels, never a conclusion
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)).
**An early stop does not produce a second Attempt.** When the solving agent ends its turn with
budget left the orchestrator re-invokes *inside the same Attempt*, and what that produces is a new
**Turn** — the entry below.
**If work continues after a Boot failure interrupted an Attempt, it opens a second Attempt.** The
interrupted Attempt ends with the spend and evidence it accumulated; a later Boot reconstructs that
carry, preserves the working directory, and opens a distinct Attempt. It never resumes or reuses
the identity of half-observed work. No synthetic Attempt is created when none was open, or when
Recovery cannot safely continue the Run.
_Avoid_: run (see the Run entry above — the whole competition window, holding many Attempts),
session, try. Not **Lease** either — a Lease is our hold on the Instance and an Attempt is the work
done under it, which is why it is a separate word.

**Turn**:
One invocation of the vendor's agent — the spawn, everything it does, and the moment it comes back
or is killed — and the unit the vendor **meters**. Turns exist because the vendor's agent drives its
own loop and can end one with budget left
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)); an
Attempt holds one or more of them, because the orchestrator answers an early stop by re-invoking
over the same working directory rather than letting that stop end the Attempt — which would be the
give-up button ADR-0005 removed, arriving by the back door.
**A Turn boundary resets no Attempt evidence.** Repetition, novelty and the current stall epoch
continue across it; ending a Turn early is neither progress nor failure.
**It is the middle of three units and never a synonym for either neighbour**: an Attempt is what a
Cut ends, a Turn is what a premature quit is about, and a Step is one command inside a Turn. Holding
one word for two of them is how a Run's own record gets misread — what a model *spent* is counted
per Turn and what it *did* is counted per Step, so the two are never the same number
([ADR-0022](docs/adr/0022-an-unmeasured-turn-is-marked-and-never-guessed.md)).
_Avoid_: iteration, round, cycle. **Invocation** is fine in prose about the CLI and is not the unit's
name. Not **Attempt** — one Attempt holds one or more Turns, and the two end for different reasons.

**Step**:
One cycle of an Attempt: a command is proposed, it runs, and its output is recorded as an
Observation. A Step is a *count*, never a duration — Steps differ by orders of magnitude in how
long they take, so a number of Steps says nothing about elapsed time.
_Avoid_: iteration, action, and **turn** — which is a unit of its own here, with the entry above, so
what to avoid is not the word but calling a Step one. Many Steps happen inside a single Turn.

**Observation**:
Real output from a real command, inseparable from the command that produced it. The model does not
write Observations; it only causes them. A tool that fails produces one too — the failure *is* the
output, and recording silence instead would leave the model to narrate what it thinks happened. Not
everything the record keeps in that channel is one: a Step carries a `source` saying whether its
bytes are the Solver's own work or a Board statement (ADR-0019).
_Avoid_: finding, output, result, tool response

**Claim**:
Anything the model says — a hypothesis, a conclusion, a summary, "this looks like a spectrogram".
Claims are how an Attempt reasons, and they are never evidence of anything. Claim / Observation is
the most load-bearing pair here: it is what stops the model's account of what happened from being
mistaken for what happened (ADR-0005).
_Avoid_: note, assertion, conclusion-as-fact — and not *observation*, which is the other half of
the pair and deliberately a different word

**Board statement**:
The Board's own words about a Challenge — its description — as opposed to anything the Solver did.
The third thing beside Claim and Observation, and the one the pair could not name: the model did not
write it, and nothing ran to produce it. It is recorded, because 22 of 74 Brunner Challenges ship no
file at all and a password or a second download host lives nowhere else, and it **authorises
nothing** — a Board's prose commonly ends in a flag-format section, and every Brunner one that does
spells the wrapper out, so a Flag-shaped string there is the Board showing its shape (ADR-0019).
_Avoid_: prose alone (the Solver's own `[recon]` lines are prose too), and not *Observation* — the
record keeps both in one channel and tells them apart by `source`

**Candidate**:
A string that might be a Challenge's Flag, and **how strongly it is known** — which is the whole of
what decides whether it may spend a submission slot. Seven strengths, and the ordering between them
is the policy: **reproduced** (the exact command that emitted it was replayed and the same string
came back), **observed** (a real command's output carried it and the replay did not confirm it),
**unverified** (no Observation carries it — the model said it and nothing else did), **guessed**
(an Observation did carry it, and then the Instance that minted it expired underneath it),
**stated** (the Board's own prose carried it and the model repeated it — evidence *against*, and so
held back until the reserved tail on every Board at all, ADR-0021), **crowded** (one command emitted
it alongside more Flags than a Challenge has, so that command was reading a list rather than solving
— ADR-0027), and **template** (the string is a Flag's *shape* rather than a Flag — a regular
expression or a placeholder token like `FAKE_FLAG` — so it is held whether or not this is the tail,
ADR-0029). A candidate is not a Flag until the Board says so; keeping the two words apart is what
stops "we found the Flag" from meaning seven different things.
_Avoid_: found flag, the flag (before a verdict), guess — *guessed* is one of the seven strengths
rather than the word for all of them

**Approach label**:
The one model-authored field that crosses an Attempt boundary — a short line **declaring what the
model is about to try**, written at the start of a turn and lifted out of a Claim by its marker.
It is a statement of intent and never a report of what happened: nothing checks it against the
commands that followed, because its value is *differential* — a label that changes is a new
approach, a label that repeats is not
([ADR-0031](docs/adr/0031-the-label-is-declared-before-the-work-and-nothing-is-called-impossible.md)).
It is declared before the work because a Cut kills the model without warning, so anything asked for
at the end of a turn is asked for at the one moment a Cut prevents.
_Avoid_: summary, conclusion, finding — the model may name what it is trying and may never state
what it concluded (ADR-0005). Not **Claim** either: a Claim is the prose, and the label is the one
line lifted out of it.

**Checkpoint**:
An environment state change that can be re-verified by replaying a command: a shell that answers, a
route that was 403 and is now 200, an archive that extracted, a crash that reproduces. A Checkpoint
is a state *transition*, never an interpretation — a confident wrong turn produces Claims in
quantity and no Checkpoints at all. A model may nominate the safe probe, but that nomination is a
Claim: only the observed replay or verified before/after state creates the Checkpoint. Novel output
alone is weaker evidence and never one.
_Avoid_: milestone, breakthrough, progress. Not a saved state to return to either — nothing is ever
rolled back to a Checkpoint; it records that the environment moved.

**Cut**:
The end of an Attempt that is not a Flag, and the **cause** that ended it — the orchestrator's
decision, never the model's (ADR-0005). The vocabulary is closed and each name is one of the three
stall counters, the budget, an Instance's expiry, a crash, or the model's own volunteered
"impossible": `cut:repetition`, `cut:novelty`, `cut:step-cliff`, `cut:budget`,
`cut:instance-expired`, `cut:self-reported-impossible`, `crashed`. A cut Challenge is requeued, so
a Cut says what stopped this Attempt and never that the Challenge is out of reach. The last of those
causes is recorded **because the aim is for it never to fire** — a cause nobody records is a defect
nobody can watch (ADR-0009).
_Avoid_: abandoned, gave up, failed, timeout, no-flag. "No flag" in particular is the *absence* of a
cause rather than one, and naming it hides which counter actually fired — which is the only thing
calibration needs to know.

**Run state**:
Everything one Run produces and the image could not contain, because none of it exists until the
Run happens: Intake's copy of the Board, every attachment downloaded, the Steps, Observations and
Checkpoints of every Attempt, and the telemetry. It lives at `/state`, host-mounted, so it outlives
the container that wrote it — a container's own filesystem dies with the container, taking the
Run's whole history with it. It starts as output and becomes authoritative input to every later
Boot: the append-only record rebuilds the control state that makes continuing safe. Losing it
mid-Run therefore loses the authority to continue, never merely the evidence. The Solver still
reads no code or tool from it. A Challenge's Working directory — the entry below — is separate:
the model reads it for carry, but because the model can write it, it can never prove identity,
submission or Lease ownership ([ADR-0032](docs/adr/0032-a-run-survives-its-boots-and-recovery-owns-the-first-fault.md)).
_Avoid_: cache, workspace, scratch, volume (a volume is how it is mounted, not what it is). Not
"state" bare either — that reads as the Solver's in-memory state, which is a different thing and
does not survive anything.

**Working directory**:
The one directory a Challenge's Attempts write in — `/state/work/<event>/<challenge_id>/`, holding
the Board's own files copied in and everything the model makes beside them. The sandbox makes it the
only path the vendor's agent may write, and it is **never cleared between Attempts**: a Turn is a
fresh spawn with no memory of the last, so this directory is what carries what was learned across
that reset. It is therefore the one part of Run state that is also Run *input*, and it is keyed by
event rather than by `challenge_id` alone — that id is a per-installation auto-increment integer, so
two Boards mint the same ones and one address would hold two Challenges
([ADR-0025](docs/adr/0025-the-event-namespaces-the-working-directory-and-it-is-run-input.md)).
_Avoid_: workspace, scratch, sandbox (the sandbox is the container itself, ADR-0018). Not the Run's
own record either: that lives under `/state/runs/`, deliberately outside this directory, so the
model cannot rewrite the file its own stall is judged from.

**Landing**:
Where an Artefact's copy sits in a Challenge's Working directory — the path the model is told about
and the one it can open. It carries the Board's own name for the file. Where staging finds that name
already taken by something that is not this same file — another file the Board ships under it, or
whatever an earlier Attempt left there — the copy lands under a name minted from its own digest
instead, and the Attempt prompt says whose name it is. Overwriting what holds the name is the one
thing staging may not do — that is the memory the Working directory exists to keep.
_Avoid_: destination, drop, staged file. Not *our copy* bare — Intake keeps one too, under
`/state/runs/`, and that one is change detection's evidence rather than anything the model is
pointed at. Not *Target* either: a Target is an Instance's address, not a file.

**Refusal**:
The Solver declining to start, before anything is spent — a missing or empty credential, no tracked
profile for the Board `CTFD_URL` names, a Board that fails ADR-0016's read-contract control, a first
Intake that could not be believed, or a `RUN_ID` nobody set. It is **not a Cut**: a Cut ends an
Attempt and says what stopped it, and at a Refusal nothing has been attempted. Its whole value is
its timing — the failure being designed against is a container that comes up at 10:30 with one
variable quietly unset and runs the full window on a credential that was never there, silent from
inside and afterwards indistinguishable from bad luck. **A loud refusal at 10:15, with a human
standing there, is setup rather than Intervention.**
_Avoid_: error, crash, validation failure, precondition. A terminal **crash** ends a Run only after
Recovery cannot produce another safe Boot; one Boot failing is neither a crash nor a Refusal. A
Refusal is a Run that never began. A **model refusal** — the solving model declining to work a
Challenge it was handed — is a third thing again, derived offline from a stream, joined to Category,
and never a Cut cause (ADR-0014).

**Promotion**:
Copying a finished Run's Step stream out of `/state` and into `runs/<run_id>.jsonl`, where it
survives the laptop. It is a separate step run by an agent afterwards and never part of a Run — the
Solver has no git binary and never commits while it is working (ADR-0009) — and it copies the stream
**as written**, never a projection of it, because every number v1 turns on is uncalibrated and a
projection freezes the one reading that was live when it was taken. Observation and Claim bodies
stay behind in `/state`, so a promoted stream keeps its digests and can never be resolved back to
the content they were taken over. Because nothing human stands between the file and the push,
promotion **re-scans for a declared credential and refuses on a hit**.
_Avoid_: export, archive, publish, upload. Not **Run state** either — promotion moves one file out
of it and leaves the rest where it is.

**Reserved tail**:
The last stretch of a Run's window, held back by the scheduler rather than found at the end, with
four jobs and no Attempts: submit the candidates the gate held back, destroy every Instance, flush
telemetry, exit clean. `Dials.tail_seconds` is its length and `acquire` never returns a budget that
eats into it, so the tail is about **doing** the four jobs rather than making room for them. It is
where the submission reserve is released — there is no later Attempt for it to be reserved *for* —
and where the leak sweep runs with nothing kept, because chall-manager never evicts and an Instance
still held when the process exits is capacity nobody reclaims.
Only window closure or deliberate shutdown enters it; a Boot failure does not. The tail is one
durable, restartable Run phase with a hard deadline: an interrupted tail resumes safe unfinished
work but never blindly repeats a submission whose outcome is unknown.
_Avoid_: shutdown, cleanup, teardown, grace period. Not the **submission reserve** inside an
Instance's own deadline either (`instance.Reserves.submission_seconds`) — that is a different
reserve, held for a different reason, and the two are deliberately separate numbers.

**Recovery**:
The owner of an operational fault from its first detection until either a materially changed,
probationary Boot is safe or the Run can no longer continue safely. Recovery can diagnose beside
declared long-running work; it does not infer a hang merely from age. It never waits for blind
restarts to run out, repeats an unchanged Solver-owned failure, deletes evidence or relaxes Board,
identity, submission or Lease safeguards to make progress look possible.
During an unattended Run, Recovery's model autonomously chooses the next diagnostic, wait, retry,
isolation, redirection or candidate repair when deterministic handling cannot orient a novel
failure. It may interpret preserved evidence and propose code, but never authors the Run's history
or bypasses the deterministic guards that authorise an irreversible effect.
_Avoid_: retry, restart, crash loop, repair. A replacement Boot is one action Recovery may permit;
a Repair Agent is one possible mechanism whose scored-Run authority is separately decided.

### How practice proves the Solver

**Known-answer regression**:
A public or otherwise answer-exposed practice Challenge, including an exact or merely re-flagged
copy. It can prove deterministic mechanisms and calibration, never discovery solve rate (ADR-0034).
_Avoid_: benchmark, clean challenge, holdout

**Execution holdout**:
A practice Challenge whose fresh parameters make the unchanged reference exploit fail while
preserving its known solution class. It measures adaptation and execution, separately from discovery.
_Avoid_: variant, mutated challenge, discovery holdout

**Discovery holdout**:
A private derived or original practice Challenge whose solution-critical structure is fresh and
whose generator, oracle and reference exploit are unreachable to the Solver. Only this corpus role
may contribute to discovery solve rate, though model-training recall can never be proved absent.
_Avoid_: unseen challenge, clean challenge, benchmark

**Gate-qualified solve**:
An accepted practice Flag with Evaluator-attested Target evidence, a Discovery holdout role and a
complete passing Isolation receipt. The qualification states what the Gate controlled; it never
claims model training contained no relevant knowledge (ADR-0034).
_Avoid_: uncontaminated solve, accepted flag, clean solve

**Retrieval-contaminated solve**:
An accepted practice Flag whose Attempt observed answer-bearing material from an external or prior
source. It remains a solve for Board mechanics and is excluded from discovery solve rate.
_Avoid_: cheated solve, invalid flag

**Unqualified solve**:
An accepted practice Flag whose provenance or isolation is incomplete. It supports no discovery
claim, and a successful boundary breach fails the Gate rather than silently shrinking its denominator.
_Avoid_: uncertain solve, probably clean

**Evaluator**:
The trusted practice-rig component outside Solver and Target authority which owns the corpus
manifest, private oracle, isolation evidence and Gate receipt. It observes or replays evidence and
never solves a Challenge or exists in a scored Run (ADR-0034).
_Avoid_: judge, Observer, control plane

**Run controller**:
The trusted part of the Solver which owns Board operations, scheduling, submission and canonical
control state. It never exposes those capabilities directly to an Attempt executor (ADR-0034).
_Avoid_: orchestrator, scheduler, worker

**Attempt executor**:
The separately confined runtime identity through which the solving model uses tools, one Challenge
working directory, its assigned Target proxy and an inference route. It is not a Lane: the Lane is
capacity and the executor is the mechanism occupying it (ADR-0034).
_Avoid_: worker, agent, Lane

**Isolation receipt**:
The Evaluator-authenticated practice record binding exact images, rig and runtime controls, fresh
state and adversarial probe results. A Gate-qualified solve cannot exist without one (ADR-0034).
_Avoid_: log, report, attestation

### Secrets and tooling

**Team key**:
The secret that identifies and gates this team on the IN-CYPHER Board, including its
proof-of-work gate. It never enters the repository — it lives in an untracked env file and reaches
the Solver at runtime. The Board **shows** it rather than minting it, and offers no way to replace
it, so it is the one secret here whose leak cannot be undone.
_Avoid_: token, API key. A **CTFd access token** is a different secret — per-Board, minted by us,
revocable — and the **LLM credential** is a third. All three follow the same rule and none of
them is the Team key (`docs/credentials.md`).

**Intervention**:
A human acting on a Run **while it is running** — the act the competition penalises. The timing is
the whole definition: preparing the Run is not intervention, so starting the container, injecting
an env file, and logging a credential in beforehand are all setup, however manual they are. The
same act inside the window is intervention, which is why every fallback has to be pre-armed rather
than reachable (ADR-0011).
_Avoid_: manual step, human-in-the-loop, babysitting. A **supervisor** restarting a crashed process
is not intervention either — nobody is present for it, which is the point of building one.

**Inference route**:
One tested path by which Codex works an Attempt: native Codex first, or a private single-owner CPA
path after it proves the same tool, deadline, record and safety contract. The two routes are not two
credentials or two pools of capacity: both spend the same personal subscription and reach the same
backend. A route changes only on a classified route-specific failure. Shared quota exhaustion causes
a declared wait or a separately pre-authorised saved reset; it never causes proxy rotation around a
limit. Claude, local models and metered API inference are outside the set
([ADR-0032](docs/adr/0032-a-run-survives-its-boots-and-recovery-owns-the-first-fault.md)).
Which model, effort, harness and Agent role receives work is a routing decision still being measured;
"Codex-only" does not mean one model, one Lane or one tool loop.
_Avoid_: credential chain, provider list, fallback key. Not **Adapter** either: an Adapter implements
the route's seam, while the route is the end-to-end way an Attempt gets worked.

**Adapter**:
One concrete implementation behind one of the Solver's seams — `Board`, `Target`, or an Inference
route. Adapters share a *shape* and never a base class, which is a deliberate rule rather than
an oversight: an abstract base written before the second implementation exists encodes a guess about
what varies ([ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)).
An inference Adapter is asked to work an Attempt, not to answer a prompt — it is handed a Challenge,
a working directory and a deadline, and it emits Steps until it finishes or is killed
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)). What
it hides is the vendor's own agent loop; what it must not hide is anything that loop observed, since
the stall counters read those Observations.
_Avoid_: provider, driver, backend, plugin. Not **seam** either: the seam is the boundary and the
Adapter is what sits behind it, so a sentence naming both is usually confusing one of them.

**ADK**:
The competition's Agent Development Kit, due for release 14 September 2026, including the
`solver.connect(host, port, team_key)` helper that clears a PoW gate. The organisers also call it
the **Hackathon Starter Pack**; the two names mean one thing, and `ADK` is the one used here.
**The Solver is built to accept it, never on top of it** — it arrives eight days before the scored
run, so it sits behind the same adapter seam a Board does, and a Solver that cannot ship without it
has bet the competition on an unseen release (ADR-0006).
_Avoid_: SDK, framework

Three terms are about how this repository is governed rather than about the domain:

**Version**:
A capability set and the **Gate** that closes it — never a date. A date and a venue are *bindings*
to a version, re-bound whenever the world moves, and re-binding one is not a change to the roadmap
(ADR-0006's thesis, restated by
[ADR-0033](docs/adr/0033-v2-is-the-complete-pre-final-solver-and-v3-is-only-the-official-delta.md),
which carries the live table). A version number written before this record resolves through its
mapping, because earlier ADRs deliberately remain records of their own moment. v2 is the complete
pre-final Solver; v3 is only the evidence-backed official competition delta and freeze.
_Avoid_: milestone, release, phase, sprint. Not **tag** either: `v1` is a git tag *because* the
version closed, and the tag is the receipt rather than the thing.

**Gate**:
The scheduled review that closes a **Version**. It reopens the prior version's decisions feature by
feature, and it reaches one of three verdicts: **pass**; **fail**, which leaves the Version open and
records what remains; or **Pending**, for a clause no binding of that Version could produce
evidence for
([ADR-0026](docs/adr/0026-a-gate-clause-with-no-venue-is-pending-and-the-gate-closes-without-it.md)).
**A Gate does not block the calendar**, but the calendar does not make a failed Version complete or
move known work forward. The final Gate is also a real go/no-go (ADR-0033).
_Avoid_: milestone, review, sign-off. Emphatically not **Checkpoint**, which is a Run-level record
this glossary already defines — a Gate is a verdict on a Run, a Checkpoint is a thing inside one.

**Observer CLI**:
The host-side, canonical-record-derived view of a Run used for development, rehearsal and
postmortem inspection. It owns no state; scored mode is read-only, while any control authority is
an explicit non-scored mode unless written competition rules later permit more (ADR-0033).
_Avoid_: dashboard, control plane, human interface, Recovery Agent

**Seed**:
The Jerome-Group private template this repository was generated from, and the org machinery that
came with it. The Solver owes the Seed nothing; the conventions do, and where a convention still
names its origin it means this — a historical source, not a live authority. ADR-0002 records
what was kept from it.
_Avoid_: Organisation, Baseline, template-as-authority
