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
The Run coordinator's durable, epoch-fenced claim on an Instance, separate from the Attempt doing
work and the Lane providing capacity. It is **reserved**, **attempt-bound** (including while
paused) or **recoverable**; ADR-0044 separates those phases from reconciliation verdicts and
terminal causes ([ADR-0044](docs/adr/0044-one-coordinator-fences-every-instance-lease.md)).
_Avoid_: hold, reservation, session, and *Instance* — the Instance is the Board's running copy of a
Challenge, the Lease is our claim on it. The two end at different moments, which is the whole reason
for the second word.

**Mana**:
Chall-manager's optional capacity cap for held Instances. A positive total makes each Isolated
Challenge spend its stated cost until termination; total zero disables Mana entirely
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

**Crowd observation**:
The raw population evidence read from a Board at one trusted time — Challenge solve counts and
movement, recent solvers, and scoreboard or team activity with their source and read outcome. It is
an observation of what the Board exposed, never proof that the population is current or useful.
_Avoid_: crowd data, solve count, scoreboard (each names only one possible field)

**Crowd state**:
The derived quality of Crowd observations for one Challenge: `unavailable`, `provisional` or
`qualified`, according to source trust, freshness, population activity and discrimination. It is
recomputed from durable observations and a versioned policy; field presence alone never qualifies.
_Avoid_: crowd mode, crowd-less mode, has solves, live crowd

**Triage**:
Reading Intake's output to decide the Base Tier of every Challenge from qualified crowd, stated
difficulty, or one durable model judgement where the deterministic evidence remains ambiguous.
Triage is callable whenever Board evidence changes, not a one-shot pipeline stage; it never deploys
an Instance or opens an Artefact, and an unavailable input remains visibly unknown.
_Avoid_: ranking, scoring, classification, assessment

**Base Tier**:
The evidence-derived Tier before the Solver's Category weakness and confirmed Checkpoints add their
bounded uplift. It may rise or fall when a new qualified Crowd observation replaces an older prior;
a failed Attempt or model Claim never changes it.
_Avoid_: prior (it can be recomputed), difficulty, final tier

**Tier**:
The effort band that fixes how long one acquired Attempt may run before a Cut — Base Tier plus at
most one rung for a reliably weak Category and bounded confirmed-Checkpoint uplift, clamped to the
four-band scale. A later Tier may fall only because new qualified external evidence lowered Base
Tier; failed work and model Claims cannot lower it, and an acquired Attempt's Tier never changes.
_Avoid_: difficulty, priority, rank, score, weight

**Order**:
The sequence the Solver takes Challenges in — **a pure, deterministic, total function** over the
Board's own signals and the Run's, recomputed at every Attempt boundary and never stored. **There is
no queue**: a cut Challenge is not placed anywhere, it simply becomes eligible again, and where it
next ranks falls out of the function
([ADR-0015](docs/adr/0015-there-is-no-queue-and-the-clock-chooses-a-working-set.md)). The same formula
always reads crowd quality, trusted payoff, a live Lease, confirmed progress, Solver-specific
Category strength and monotone spend; unavailable crowd or Category evidence is neutral rather than
a second mode. Order favours tractability and strength to bank Flags early while Tier uses the same
evidence oppositely to fund harder work. A deterministic exploration share reaches the best
provisional or unavailable Challenge without ever banning anything
([ADR-0038](docs/adr/0038-one-order-reads-crowd-quality-and-tier-recomputes.md)).
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

**Challenge claim**:
The Run controller's durable, exclusive association between one Challenge and one open Attempt,
held from pre-effect admission through Turn gaps and Quota wait until the Attempt closes. At most one
exists per Challenge; it is neither a Lane identity nor a Lease, and admission candidates hold none.
_Avoid_: assignment, queue entry, lock, reservation, Lease

### How the Solver works a Challenge

**Agent role**:
The purpose-specific policy for bounded model work: **Triage Judge**, generalist **Solve Lead**,
**Specialist** or **Recovery Agent**. Intake, Order, verification, submission, resource governance
and lifecycle ownership are deterministic functions, not Agent roles.
_Avoid_: agent type, service, controller

**Engagement**:
One controller-owned use of an Agent role, from admission through durable close. A Solve Lead
Engagement spans one Attempt, a Triage Judge Engagement one snapshot batch, a Specialist Engagement
one delegated investigation and a Recovery Engagement one incident; each may hold one or more Turns.
_Avoid_: session, invocation, agent run, task — a repository issue is a task, and a Turn is one
vendor invocation inside the Engagement

**Resource envelope**:
The controller-admitted capacity bound to one owned Attempt, Engagement or service: its purpose and
deadline plus measured soft targets and hard ceilings. It may borrow unused productive capacity
globally while remaining outside the protected control and Recovery reserves; touching them is a
calibration or admission failure.
_Avoid_: quota, Lane budget, static share, resource limit

**Specialist profile**:
The changeable expertise and tool emphasis of a Specialist Engagement: web, pwn, cryptography,
reverse engineering, forensics, steganography/media, OSINT, AI/ML, misc/protocols or an on-demand
profile for another open Category. A Board Category may seed the profile but never locks it, because
one Challenge may span several techniques.
_Avoid_: category agent, fixed specialist, category lock

**Tool capability**:
A distinct kind of Challenge work the Solver can make available, independent of which installed
thing supplies it and whether one Engagement may use it now (ADR-0047).
_Avoid_: package, binary, tool name, Capability handle

**Tool component**:
A binary, library, runtime, corpus or sysroot which supplies one or more Tool capabilities.
_Avoid_: Artefact (a Board-supplied file), package (too narrow), dependency, tool

**Tool profile**:
A selection of Tool components offered together to an Engagement; membership does not itself grant
authority. One Specialist profile may compose several Tool profiles.
_Avoid_: Specialist profile, Category image, runtime install, toolbox

**Tool view**:
The Engagement-specific projection stating separately which Tool capabilities are installed,
proved, enabled and currently authorised (ADR-0047).
_Avoid_: PATH inventory, package list, Tool profile, permission list

**Lane**:
One Run-scoped, ordinal-named place in the Solver's bounded capacity for one active Attempt. The
Attempt identifies the work and the Lease identifies the Instance hold; a child Specialist
Engagement consumes no additional Lane.
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
One bounded run of the Solver at a single Challenge — from the controller's durable admission,
before any effect, to the moment it is cut or a Flag is submitted. A Challenge may be attempted
many times: **an Attempt ends, a Challenge does not.** Keeping the two apart is what makes giving
up cheap, because what is abandoned is an Attempt and never the Challenge (ADR-0005).
**Consecutive Attempts on one Challenge
are ordinary**, not a special case — a cut Challenge is never terminal, so Order can pick one it has
already worked. What carries across is the environment — the workdir is the memory — plus the
approach labels, never a conclusion
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)).
**An early stop does not produce a second Attempt.** When the solving agent ends its turn with
budget left the controller continues its persistent Engagement *inside the same Attempt*, and what
that produces is a new **Turn** — the entry below.
**If work continues after a Boot failure interrupted an Attempt, it opens a second Attempt.** The
interrupted Attempt ends with the spend and evidence it accumulated; a later Boot reconstructs that
carry, preserves the working directory, and opens a distinct Attempt. It never resumes or reuses
the identity of half-observed work. No synthetic Attempt is created when none was open, or when
Recovery cannot safely continue the Run.
_Avoid_: run (see the Run entry above — the whole competition window, holding many Attempts),
session, try. Not **Lease** either — a Lease is our hold on the Instance and an Attempt is the work
done under it, which is why it is a separate word.

**Turn**:
One bounded period of model work inside an Engagement — from invocation or continuation until the
model returns or is killed — and the unit the vendor **meters**. Turns exist because the vendor's
agent drives its own loop and can end one with budget left
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)); an
Engagement holds one or more of them, and the controller answers an early stop by continuing the
persistent Solve Lead rather than letting that stop end the Attempt — which would be the give-up
button ADR-0005 removed, arriving by the back door.
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
Real output from a real command or tool, inseparable from the act that produced it. The model does
not write Observations; it only causes them. A tool that fails produces one too — the failure *is*
the output, and recording silence instead would leave the model to narrate what it thinks happened.
Not everything the record keeps in that channel is one: a Step carries a `source` saying whether
its bytes are the Solver's own work or a Board statement (ADR-0019).
_Avoid_: finding, output, result, tool response

**Claim**:
A model-authored hypothesis, conclusion, summary, interpretation or plan. A Candidate derivation is
a structured Claim over Evidence artifacts and remains an interpretation rather than an Observation
(ADR-0005, ADR-0037).
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

**Evidence artifact**:
An immutable, source-identified input to a Candidate derivation: an Artefact or picture with its
digest, an Observation, or a captured web or tool result. Model prose and a search query alone are
not Evidence artifacts; the recorded bytes, source, time and identity are what let later work inspect
the same thing (ADR-0037).
_Avoid_: evidence (too broad), source (missing identity), input (missing provenance)

**Candidate derivation**:
A structured model-authored Claim that produces exactly one Candidate proposal from cited Evidence
artifacts and records the locator, fragments, ordering or transformation that relates them. It may
express visual recognition, semantic recognition, OSINT inference or multi-source assembly; source
completeness and blind re-derivation measure it, never the model's confidence score (ADR-0037).
_Avoid_: proof, Observation, confidence, chain of thought

**Candidate proposal**:
The exact string offered to the submission authority as a possible Flag, together with how it was
obtained and its current disposition. It may be observed, derived, unsupported, Board-stated,
crowded, templated or tied to an expired Instance; the Board alone turns it into a Flag (ADR-0037).
_Avoid_: found flag, the flag (before a verdict), guess, candidate flag

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

**Closing barrier**:
The durable, sequenced point after which an Attempt and its descendants may no longer change
canonical state or cause an external effect. Pre-barrier results may join; later bytes survive only
as inert quarantined evidence, and the Lane and Challenge claim release only after bounded join or
kill completes.
_Avoid_: Attempt close, cancellation, timeout, kill

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
[ADR-0045](docs/adr/0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)
classifies that state's reachability, retention and publication boundary.
_Avoid_: cache, workspace, scratch, volume (a volume is how it is mounted, not what it is). Not
"state" bare either — that reads as the Solver's in-memory state, which is a different thing and
does not survive anything.

**Working directory**:
The Challenge-scoped home of its **Work generations** — the entry below — keyed by event and
Challenge because a `challenge_id` is a per-installation integer and two Boards mint the same ones.
It is not itself one mutable directory carried forever. Each Attempt receives a fresh generation
assembled from the Board's files and one bounded carry manifest over sealed earlier work; a
Specialist receives a private generation and only controller-accepted artifacts cross back. The
model therefore sees the memory deliberately handed forward, never every stale file a predecessor
made. Working-directory material may help later work but, because a model can author it, never
proves identity, submission, Lease ownership or another control fact
([ADR-0025](docs/adr/0025-the-event-namespaces-the-working-directory-and-it-is-run-input.md)).
_Avoid_: workspace, scratch, sandbox (the sandbox is the container itself, ADR-0018). Not the Run's
own record either: that lives under `/state/runs/`, deliberately outside this directory, so the
model cannot rewrite the file its own stall is judged from.

**Work generation**:
One producer-owned body of mutable Challenge work while its Attempt or Specialist Engagement is
open. Its closing barrier seals it; selected artifacts may enter a successor's bounded carry, while
unselected, stale or late material remains inspectable only under its retention class and never
silently enters later work. A generation is the unit the storage governor may quarantine or retire;
the Working directory is the Challenge-level lineage containing those units
([ADR-0045](docs/adr/0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)).
_Avoid_: Attempt (the work period), Working directory (the lineage), checkpoint, snapshot

**Artifact disposition**:
The declared purpose governing one file in a Work generation. New files begin `ephemeral`; the
model may nominate them as `carry`, `solve-evidence`, `training-evidence` or `discard`, and trusted
control validates and applies that request within its count, byte, provenance and sensitivity
bounds. An interrupted generation maps remaining ephemeral files to `quarantine`, so no file is
unclassified and no crash promotes a directory into later context by default
([ADR-0045](docs/adr/0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)).
_Avoid_: useful file, keep, cleanup status, model verdict

**Landing**:
Where an Artefact's copy sits in a Challenge's Working directory — the path the model is told about
and the one it can open. It carries the Board's own name for the file. Where staging finds that name
already taken in the fresh generation by something that is not this same file — another Board file
or an explicitly selected carry artifact — the copy lands under a name minted from its own digest
instead, and the Attempt prompt says whose name it is. Earlier Attempt material enters only through
the carry manifest; an old generation does not create a collision merely by surviving retention.
Staging never overwrites a selected input.
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
Moving a finished Run's sanitized **Run capsule** — the entry below — out of live Run state and
across its publication boundary. It is a trusted post-Run operation, never part of solving, and
preserves canonical facts rather than freezing one current metric projection. Promotion refuses a
live, shrinking, incomplete or secret-bearing package and scans both raw and decoded values against
current and retained historical credentials. A promoted capsule is independently durable before
raw Run state may enter its retention clock
([ADR-0045](docs/adr/0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)).
_Avoid_: export, archive, publish, upload. Not **Run state** either — promotion deliberately
excludes private or disposable classes.

**Run capsule**:
The smallest sanitized, durable package from which one Run's accepted evidence and later metric
views can be reconstructed: its canonical stream, configuration and provenance manifests, Solve
receipts and only the selected Evidence artifacts they require. Raw Observation bodies, general
work, caches, credentials and vendor rollouts remain outside it. A trend table is a derived view of
capsules, never a replacement truth
([ADR-0045](docs/adr/0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)).
_Avoid_: summary, report, training row, archive, Run state

**Solve receipt**:
The immutable machine-readable record sealed for one accepted Flag, binding the Board verdict to
its Run, Boot, Lane, Attempt and Step identities; Instance and Target provenance; exact
credential-free commands or scripts; selected Evidence-artifact references; and image, model,
prompt and tool versions. It supports later writeup reconstruction without authoring narrative
during the Run or copying credentials and raw bulk into the receipt
([ADR-0045](docs/adr/0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)).
_Avoid_: writeup, flag file, submission log, Isolation receipt

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

**Incident**:
One Recovery-owned fault lifecycle, from deterministic detection and containment through a
resolved, contained or terminal outcome. A materially changed cause or operating context opens a
linked successor rather than silently changing the identity of the original Incident.
_Avoid_: error, alert, retry, Recovery Engagement (the Engagement is one mechanism working it)

**Failure fingerprint**:
The stable, normalized signature used to recognise the same operational failure across processes
and Boots: scope, component, operation, failure class, relevant authority state and image/config
identity, excluding volatile timestamps, process ids and prose. Matching fingerprints coalesce
recurrence only within an open Incident; recurrence after closure or a material field change opens
a linked Incident.
_Avoid_: error string, stack trace, incident id, deduplication key

**Recovery probe**:
One fixed, typed and bounded diagnostic read that deterministic control may execute for an Incident.
It has an explicit authority scope, timeout, output limit and redaction policy; it is never an
arbitrary model-authored command or general shell.
_Avoid_: diagnostic command, investigation, shell access, tool call

**Remedy**:
One versioned, prebuilt state-changing action that deterministic control may authorise for matching
Failure fingerprints after checking its typed inputs, preconditions, authority mode and bounded-use
rule. A Recovery Agent may request a Remedy but cannot execute or widen it.
_Avoid_: fix, retry, repair, model action

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

**Competition rig**:
The private sibling product which supplies the local Board, real isolated Targets, dynamic
scenarios, qualifying corpus, fault injection and hidden Evaluator used for v2 evidence. It
reproduces pinned confirmed semantics, not unpublished official infrastructure, human strategy or
production load ([ADR-0048](docs/adr/0048-one-hidden-competition-rig-produces-every-local-gate-receipt.md)).
_Avoid_: mock Board, test server, official replica, benchmark

**Scenario manifest**:
The versioned declarative plan sealed before a practice Run, binding its seed, population,
Challenge revisions and splits, releases, clocks, faults, resources, images, model/prompt/tool policy
and expected observations. The Evaluator records deviations; neither Solver output nor a result may
rewrite the plan (ADR-0048).
_Avoid_: test config, Run state, event log, Gate receipt

**Population profile**:
One declared range and schedule for Board-native simulated registered and active teams and their
solves. Its hidden simulated origin is Evaluator metadata; the Solver observes only ordinary Board
surfaces. It tests reaction to a distribution and never claims to reproduce human strategy
(ADR-0048).
_Avoid_: fake scoreboard, synthetic Crowd source, load test

**Practice catalogue**:
The Evaluator-owned Challenges eligible to appear on the local Board. Every entry has a verified
answer or reference result and reproducible Challenge material before admission; incomplete,
answerless, dead-link and redundant candidates do not enter merely because an old workspace names
them. Catalogue membership says a Challenge can be exercised, not that the Solver has seen it
([ADR-0045](docs/adr/0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)).
_Avoid_: training set, archive, old workspace, attempted corpus

**Attempt corpus**:
The compact historical record that grows when the Solver actually attempts a Practice-catalogue
Challenge: its Run capsule, outcome and corpus role, never the old workdir or answer material. An
unattempted catalogue entry is absent even where the Evaluator already holds it as a validation or
Discovery holdout. Using an entry to choose a model, prompt, tool or dial makes it calibration data;
repeating it thereafter is regression evidence rather than a fresh discovery test
([ADR-0045](docs/adr/0045-canonical-state-is-sealed-classified-and-governed-by-reachability.md)).
_Avoid_: training set, Board catalogue, challenge archive, model training

**Dataset snapshot**:
One immutable content-addressed training view over qualifying Attempt-corpus records, binding exact
Run/Attempt/Challenge identity, time-causal features, outcome, provenance, split, receipt and schema,
policy and image hashes. A correction supersedes it; a later observation never becomes an earlier
feature. Automatic fitting consumes snapshots only between sealed Runs (ADR-0048).
_Avoid_: live memory, mutable training set, model state, Practice catalogue

**Model candidate**:
One automatically fitted but unpromoted model package binding a Dataset snapshot, feature schema,
coefficients, normalisation, policy, metrics and rollback target. Eligibility may start its trials;
only repeated untouched Gate evidence may make it the Model champion (ADR-0048).
_Avoid_: Candidate, experiment, new model, champion

**Model champion**:
The reviewed model package currently admitted behind the bounded Order input. It remains fixed for a
Run, ships with its prior champion and transparent fallback, and changes only between Runs through a
versioned Gate (ADR-0048).
_Avoid_: Model candidate, production model, live model, best model

**Run controller**:
The trusted part of the Solver which owns Board operations, scheduling, submission and canonical
control state. It never exposes those capabilities directly to an Attempt executor (ADR-0034).
_Avoid_: orchestrator, scheduler, worker

**Supervisor**:
The trusted PID-1 authority inside the Solver container which owns Boots, process lifecycles and
isolation profiles while handing children capabilities rather than credentials (ADR-0041).
_Avoid_: init, Run controller, Recovery, process manager

**Capability handle**:
An identity-bound permission to request one typed Solver operation without receiving the credential,
endpoint or control authority behind it (ADR-0041).
_Avoid_: token, credential, socket, permission flag

**Attempt executor**:
The separately confined runtime identity through which the solving model uses tools, one Challenge
working directory, its assigned Target proxy and an inference route. It is not a Lane: the Lane is
capacity and the executor is the mechanism occupying it (ADR-0034).
_Avoid_: worker, agent, Lane

**Attempt service**:
A descendant explicitly adopted past its spawning Step while remaining owned, bounded and removed
with the same Attempt (ADR-0041).
_Avoid_: daemon, background process, orphan, Worker

**Isolation receipt**:
The Evaluator-authenticated practice record binding exact images, rig and runtime controls, fresh
state and adversarial probe results. A Gate-qualified solve cannot exist without one (ADR-0034).
_Avoid_: log, report, attestation

**Isolation profile**:
One pretested set of identity, filesystem, process, network, syscall and resource controls that
enforces the Attempt executor's fixed authority ceiling on a particular runtime (ADR-0041).
_Avoid_: sandbox mode, runtime flags, best effort

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
Native Codex is primary; the CPA route uses a separate Solver-owned Harness so a Codex CLI or
tool-loop failure does not take both routes down. Model, effort and Agent role remain measured
routing decisions; "Codex-only" does not mean one model, one Lane or one tool loop
([ADR-0039](docs/adr/0039-native-codex-leads-and-one-small-loop-owns-the-cpa-route.md)).
_Avoid_: credential chain, provider list, fallback key. Not **Adapter** either: an Adapter implements
the route's seam, while the route is the end-to-end way an Attempt gets worked.

**Harness**:
The agent loop inside an Inference route that turns model events into bounded tool calls and Steps.
Native Codex supplies its own Harness; the CPA route uses the Solver's minimal Responses Harness so
the two routes do not share the Codex CLI, its tool loop or its event schema
([ADR-0039](docs/adr/0039-native-codex-leads-and-one-small-loop-owns-the-cpa-route.md)).
_Avoid_: model, Agent, Adapter, Inference route

**Codex Control**:
The Supervisor-owned, non-Harness client to the pinned Codex App Server that reads native account,
model and limit state and spends an earned reset only under pre-authorised, journalled authority.
Its pipe and `CODEX_HOME` never reach a Worker; absence or failure makes the corresponding fact
unknown and never grants more authority
([ADR-0040](docs/adr/0040-one-owner-one-subscription-and-one-observed-limit-state.md)).
_Avoid_: control plane, Observer, Harness, quota monitor

**Quota wait**:
The live Run state entered when shared account capacity is exhausted: new Inference pauses while
the Supervisor, deterministic work and Recovery continue until observed capacity returns or the
Run reaches its reserved tail. It is neither a Cut nor a route change
([ADR-0040](docs/adr/0040-one-owner-one-subscription-and-one-observed-limit-state.md)).
_Avoid_: quota failure, retry loop, fallback, Run close

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
