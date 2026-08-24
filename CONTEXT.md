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
_Avoid_: answer, solution, key (a "key" here is a credential — see Team key)

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
Our hold on an Instance — held from the deploy that creates it to the terminate that releases it,
and **not** the same span as an Attempt. The word was parked through v1's early design on the
grounds that a hold one-to-one with an Attempt names nothing Attempt does not already name; it is
coined here because that stopped being true. When the solving agent ends its turn with budget left
the orchestrator re-invokes, and that re-invocation is a new Attempt on the same Challenge — so a
single Lease outlives a run of consecutive Attempts, and "the Instance expired", "the Attempt was
cut" and "we let the Lease go" became three separate facts
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)). What
a Lease costs is Mana, which is why one nobody is working is not free, and why the boundary leak
sweep looks for a Lease nothing is using rather than an Instance nothing is using.
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
extracts.
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
monotone penalty on what has already been spent.
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

**Run**:
One unattended outing of the Solver at one Board — the whole competition window, several hours and
many Attempts, from the process starting to it terminating. A Run is the unit everything is
compared *across*: a threshold is calibrated over Runs, a version gate is a verdict on one, and its
`run_id` is what joins every record ADR-0009 writes. **A Run survives a restart.** If v2's
supervisor restarts a crashed process the Run continues and a boot counter increments, because the
thing being measured is hours against a Board rather than the life of a process — treating a
restart as a second Run would silently compare halves against wholes.
**A Run ends when the competition window closes, or when it crashes — never because the Board looks
finished.** Challenges drop mid-event, so "nothing left to work" is not something the Solver can
ever conclude; that is the Run-level counterpart of ADR-0005's rule that a Challenge is never marked
impossible. Running out of credential is not an ending either: quota windows roll, so capacity comes
back and the Solver backs off through it (ADR-0010).
_Avoid_: session, attempt (an Attempt is one Challenge inside a Run — see below), execution. Not
**Run state** either: that is what a Run *produces*, and it is a separate entry below.

**Attempt**:
One bounded run of the Solver at a single Challenge — from the recon that opens it to the moment it
is cut or a Flag is submitted. A Challenge may be attempted many times: **an Attempt ends, a
Challenge does not.** Keeping the two apart is what makes giving up cheap, because what is
abandoned is an Attempt and never the Challenge (ADR-0005). **Consecutive Attempts on one Challenge
are ordinary**, not a special case: when the solving agent ends its turn with budget left the
orchestrator re-invokes, and that is a new Attempt rather than a continuation of the last, because
treating an early stop as the end of the Attempt would hand the model the give-up button ADR-0005
removed. What carries across is the environment — the workdir is the memory — plus the approach
labels, never a conclusion
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)).
_Avoid_: run (see the Run entry above — the whole competition window, holding many Attempts),
session, try. Not **Lease** either — a Lease can outlive several Attempts, which is why it is a
separate word.

**Step**:
One cycle of an Attempt: a command is proposed, it runs, and its output is recorded as an
Observation. A Step is a *count*, never a duration — Steps differ by orders of magnitude in how
long they take, so a number of Steps says nothing about elapsed time.
_Avoid_: turn, iteration, action

**Observation**:
Real output from a real command, inseparable from the command that produced it. The model does not
write Observations; it only causes them. A tool that fails produces one too — the failure *is* the
output, and recording silence instead would leave the model to narrate what it thinks happened.
_Avoid_: finding, output, result, tool response

**Claim**:
Anything the model says — a hypothesis, a conclusion, a summary, "this looks like a spectrogram".
Claims are how an Attempt reasons, and they are never evidence of anything. Claim / Observation is
the most load-bearing pair here: it is what stops the model's account of what happened from being
mistaken for what happened (ADR-0005).
_Avoid_: note, assertion, conclusion-as-fact — and not *observation*, which is the other half of
the pair and deliberately a different word

**Checkpoint**:
An environment state change that can be re-verified by replaying a command: a shell that answers, a
route that was 403 and is now 200, an archive that extracted, a crash that reproduces. A Checkpoint
is a state *transition*, never an interpretation — a confident wrong turn produces Claims in
quantity and no Checkpoints at all.
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
Run's whole history with it. **Run state is output, never input**: the Solver reads no code and no
tool from it, so deleting it mid-run costs the record and not the ability
([ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)).
_Avoid_: cache, workspace, scratch, volume (a volume is how it is mounted, not what it is). Not
"state" bare either — that reads as the Solver's in-memory state, which is a different thing and
does not survive anything.

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

**Credential chain**:
The ordered credentials the Solver pays for inference with, tried in order and switched **without a
human** when one is exhausted — a subscription first, metered credit last. The order is the whole
meaning of the term: a fallback that waits for someone to reach for it is **Intervention**, which is
the penalised act, so "we hold a spare key" is not a chain and does not count as one. What the chain
*cannot* do is add headroom — a quota burned through a different door is burned the same, which is
why no proxy or shim sits in it (ADR-0010, ADR-0011).
The chain switches on **exhaustion and nothing else** — no Category, Tier or cost input picks the
model, because v1 runs one brain per Run and routing is v3's
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)). Which
credential leads is a config value rather than code, so a practice Run can lead with a different one
without a rebuild.
_Avoid_: failover, fallback key, provider list. Not **provider abstraction** either: that is the
seam the chain is expressed through, and a separate decision — settled as the Adapter below.

**Adapter**:
One concrete implementation behind one of the Solver's seams — `Board`, `Target`, or a credential in
the chain. Adapters share a *shape* and never a base class, which is a deliberate rule rather than
an oversight: an abstract base written before the second implementation exists encodes a guess about
what varies ([ADR-0008](docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)).
A credential's Adapter is asked to work an Attempt, not to answer a prompt — it is handed a Challenge,
a working directory and a deadline, and it emits Steps until it finishes or is killed
([ADR-0014](docs/adr/0014-the-vendors-agent-drives-the-loop-and-the-seam-runs-an-attempt.md)). What
it hides is the vendor's own agent loop; what it must not hide is anything that loop observed, since
the stall counters read those Observations.
_Avoid_: provider, driver, backend, plugin. Not **seam** either: the seam is the boundary and the
Adapter is what sits behind it, so a sentence naming both is usually confusing one of them.

**ADK**:
The competition's Agent Development Kit, released 14 September 2026, including the
`solver.connect(host, port, team_key)` helper that clears a PoW gate. The organisers also call it
the **Hackathon Starter Pack**; the two names mean one thing, and `ADK` is the one used here.
**The Solver is built to accept it, never on top of it** — it arrives eight days before the scored
run, so it sits behind the same adapter seam a Board does, and a Solver that cannot ship without it
has bet the competition on an unseen release (ADR-0006).
_Avoid_: SDK, framework

One term is about how this repository is governed rather than about the domain:

**Seed**:
The Jerome-Group private template this repository was generated from, and the org machinery that
came with it. The Solver owes the Seed nothing; the conventions do, and where a convention still
names its origin it means this — a historical source, not a live authority. ADR-0002 records
what was kept from it.
_Avoid_: Organisation, Baseline, template-as-authority
