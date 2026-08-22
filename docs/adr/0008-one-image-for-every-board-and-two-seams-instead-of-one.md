# One image for every Board, and two seams instead of one

The Solver has to play several Boards — BrunnerCTF or COMPFEST for v1, a local fixture for v2,
PatriotCTF for v3, the IN-CYPHER arena for v4, and the scored Board on 22 September — and the
obvious way to absorb that is a branch per event, cut from a `vN` tag, holding that event's
config and hacks and thrown away afterwards. That was this ticket's starting position. It loses,
and it loses for a reason that only became visible once
[ADR-0006](0006-a-version-is-a-capability-set-and-its-gate.md) made venue bindings movable.

Two decisions, and the second is the one that will still matter in a year.

**The Solver is one image, configured per Board.** `main` plus `vN` tags is the whole branching
story. An event is an `.env` file and a tracked facts file, never a branch.

**There are two seams, not one.** Talking to the Board and reaching a Challenge's own service are
different axes, and cutting one hole where there are two is how the ADK's arrival on 14 September
becomes a change to the Board code.

## Everything that varies between Boards is data

The variance is real and we have measured most of it. The Flag wrapper (`brunner{.*}` against
`flag{…}`), Category namespacing (`(Practice) forensics`), CTFd `type` strings (`flightops`,
`dynamic_iac`), whether `ctfd-chall-manager` is installed, whether the Board answers an
unauthenticated read, `incorrect_submissions_per_min`. Not one of those is a different algorithm.
Each is a different value in the same record.

So a Board is described by a **Board profile**, and the profile is *discovered at startup*
wherever it can be — the plugin's asset URL answers 200 or it does not, `/mana` returns a total
or a 403, the challenge list comes back or redirects to `/login`. A tracked
`docs/competitions/<event>.board.json` supplies only what cannot be read off the wire: the URL,
and the prohibitions derived from that Board's rules — Brunner's ban on broad automated
enumeration, its ban on sandbagging — which exist in prose and nowhere in the API.

The alternative is to configure what we could have measured, and a configured value goes stale
without saying so. `CONTEXT.md` already committed to this in the Flag entry: each Board is read at
the start of the event, not assumed. This record is that sentence turned into a mechanism.

## One image, not a branch per event

Branch-per-practice came with a discipline attached — any core improvement found during a practice
run is a separate pull request to `main`, never carried on the practice branch, or the improvement
dies with the branch. That discipline is the tell. It exists to stop the branch from accumulating
the thing that matters, which means the branch is only ever meant to hold the thing that does not.
If the event-specific part is a JSON file and an `.env`, there is nothing left for the branch to
carry.

ADR-0006 is what makes this urgent rather than merely tidy. A version is now bound to a venue, and
the binding moves: v1 is "Brunner if ready by 23 August 20:00, else COMPFEST", v3's binding
collides with v4's by a day and is the one expected to lose it. A branch per event is a branch per
*binding*, and bindings are the part of the plan designed to change. Meanwhile #13 established that
we almost certainly run the image ourselves on the venue network, so runtime `--env-file` injection
— the mechanism a configured split needs — survives to competition day.

A throwaway `practice/<event>` branch stays available for the case where an event forces code that
must not reach `main`. It is an escape hatch and never the plan, and needing one is evidence the
seam is in the wrong place rather than evidence the branch model was right.

## Two seams, not one

The ticket proposed a single platform-adapter module owning challenge listing, file fetch, Instance
deploy/renew/destroy, the PoW-gated socket helper, and Flag submission. Those split along a fault
line that runs straight through the middle of that list:

- **`Board`** — enumerate, detail, download, submit, deploy/renew/destroy/list Instances. This is
  scoring plumbing. It is HTTP, it is CTFd, it is authenticated with a token we mint.
- **`Target`** — open a connection to a Challenge's own service. This is the thing being attacked.
  It is a socket or an HTTP client, it is gated by proof-of-work on IN-CYPHER's raw-TCP Challenges,
  and the address for it does not exist until an Instance is deployed
  ([ADR-0007](0007-truth-about-an-instance-lives-on-the-board.md)).

They are swapped by different events, they fail differently, and they are read in different moods —
a `Board` failure means we are blind to the competition, a `Target` failure means one Attempt is
stuck. Most of all, **the ADK is a client for the second and not the first.** ADR-0006's consequence
that the ADK "integrates behind the same seam the Board sits behind" is overtaken here: it sits
behind `Target`, where a PoW gate lives, and it never touches the code that submits a Flag.

Both seams are **one concrete implementation each, with no abstract base.** The seam is the module
boundary and its narrow public surface — nothing above it imports `urllib` or learns what wire
format an answer arrived in — not an interface hierarchy. `CODING_STANDARDS.md` §2 bans speculative
generality and asks for deep modules; a concrete `Board` delivers the second without the first. For
`Target` the argument is stronger still: the ADK's shape is unknown until 14 September, so a
Protocol written today is a guess we would have to break. v4 writes the interface with both
implementations in front of it, which is the only condition under which an interface is right.

The seam's real test is `GET /plugins/ctfd-chall-manager/instances`, which ADR-0007 made
authoritative for Instance *existence* and which answers in HTML — rows keyed by challenge name,
`until` truncated to whole seconds, no timezone. That parsing lives **inside** `Board`, behind a
method returning records. If it leaks upward the seam is decorative, and the caller ends up having
to remember which of two reads answers which question.

`scripts/ctfd_probe.py` already holds the three transport rules that cost a day to find — a browser
User-Agent or Cloudflare 403s us at the edge, never follow an API redirect or a 302 to `/login`
parses as an empty Board, and `Content-Type: application/json` on every call including GETs. Its
`Board` class is not a rehearsal for the seam; it **is** the seam, and moves into `solver/`. The
probe becomes a script that imports it and asserts against a live Board, which makes the pre-flight
check and the competition path provably the same code and finally gives `tests/` something real to
cover.

## The image, and what is inside it

`kalilinux/kali-rolling` is 79 packages with no `python3`, `file`, `strings`, `unzip`, `curl` or
`ca-certificates` — so `CODING_STANDARDS.md` §6's "standard library only" currently presumes an
interpreter the base does not ship, and ADR-0005's recon cascade dispatches on `file`, which is
absent. The image installs them explicitly, from a short list written one package per line in the
`Dockerfile` itself, and pins the base by its **multi-arch index digest** so the same file builds
natively on an arm64 Mac and an amd64 runner. A metapackage such as `kali-tools-top10` would answer
the same need by pulling several hundred packages that are named nowhere a reviewer reads.

Kali now, rather than Debian now and Kali at v3 when the Category tool inventory lands, because a
base swap invalidates every tool assumption made before it, and paying that at v3 costs a whole
version's confidence in exchange for a slightly smaller v1 image.

**Everything the Solver needs before a run starts is baked in**: the tools, `solver/`, and the
event's `.board.json`. Two things are not, and both are deliberate. Secrets arrive by `--env-file`
at runtime, because baking a credential into a layer is banned outright. And there is exactly one
writable path, `/state`, host-mounted, holding what the run *produces* — Intake's copy of the Board,
downloaded attachments, the Step and Observation records, telemetry.

`/state` is not a dependency and it is worth being exact about why: the Solver never reads code or
tools from it. Delete the directory mid-run and the Solver keeps solving; it loses the record, not
the ability. It is a one-way pipe for output, and the reason it is mounted rather than left in the
container's own layer is that a container's layer dies with the container — taking the whole run's
history with it, including the telemetry #16 exists to produce.

That is the exact opposite of the development bind mount, which genuinely does read code off a
laptop. Mount the working tree freely while developing; **every ADR-0006 gate run is from a built
image with no source mount.** Nothing else makes a gate's "zero keystrokes" a claim about the image
rather than about the working tree, and a mount-only habit ships an image missing a file that
nothing notices until the scored run.

The remaining runtime shape follows from what v1 is for. **PID 1 is the Solver process itself**, no
supervisor — v2 wraps it, and adding one at v1 would make a clean exit indistinguishable from a
restart loop, which is the one behaviour v1's gate is trying to observe. **It runs as root**,
because the container boundary already provides the isolation a non-root user would buy, and CTF
tooling that needs a privilege at 14:00 on competition day has nobody to ask for it.

## Where things live

- **`solver/`** — the Solver's own code, a plain package at the repository root. Not `src/solver/`:
  `src/` exists to solve a packaging problem, and there is no distribution here, only a container.
- **`scripts/`** — unchanged in purpose. Human-run checks and setup that now `import solver`.
- **`Dockerfile`** — at the root, package list inline. No `image/` directory; one fewer top-level
  area, and the list is readable where it is used.
- **`docs/competitions/<event>.board.json`** — a third file per event, beside our reading of it and
  the verbatim rules snapshot. §6 already establishes one-file-per-event there, and a top-level
  `boards/` would buy nothing.

None of these exist yet, so `MAP.md` and `CODING_STANDARDS.md` §6 are not edited by this record —
they are updated by the pull request that creates each one, which is what the same-PR rule means.

A `vN` is an **annotated tag, `v1` through `v5`, cut after the gate run on the commit that actually
ran**, its message carrying the one-line verdict; the evidence and any re-scope live in a gate issue
closed at the same time. A tag cut before the run is a prediction, and ADR-0006's gates are
diagnostic precisely because some of them are expected to come back negative.

## Consequences

- **A Board we have never met needs no code.** It needs a URL, a token, and a `.board.json` holding
  what its rules forbid. That is the payoff, and it is what makes a slipped venue binding cheap.
- **A discovered profile can discover the wrong thing.** Startup probing trades a stale configured
  value for a misread live one — a transient 403 on `/mana` reading as "mana disabled" is the shape
  already seen on the IN-CYPHER arena (ADR-0007). Discovery has to distinguish "absent" from
  "failed", and where it cannot, it fails the run rather than guessing.
- **Two seams cost a decision at every call site** — which of them owns this? The cases that will
  be argued are the ones where an Instance's address crosses from one to the other: `Board` deploys
  and returns a `connectionInfo`, `Target` connects to it. The address is data passing between them,
  never a shared module.
- **CI proves amd64 and we compete on arm64.** A package present on one and missing on the other
  passes the build and fails locally. This is not solved, it is *placed*: the locally built gate run
  catches it, days before it matters. The alternative — QEMU cross-builds in CI — buys the same
  answer slower and inside a ten-minute bound (§5) that a Kali build is already close to.
- **`/state` grows and nothing bounds it.** Brunner alone ships 52 zip attachments, and 5.5 hours of
  Observations is not nothing. Host-mounting means it consumes the Mac's real free space rather than
  Docker Desktop's fixed VM disk, which removes the failure where a full VM disk quietly kills a run
  — but rotation and a cap on stored Observation size are v2's endurance work and are not solved
  here.
- **Promoting the probe's `Board` couples a pre-flight script to the Solver package.** That is the
  intent — one home for the transport rules — but it means `scripts/ctfd_probe.py` can no longer be
  copied to another machine on its own.
- **No abstract base means the second platform is a refactor, not a plug-in.** Accepted: we play
  CTFd for every venue on ADR-0006's table, and the two non-CTFd platforms in the practice window
  were ruled out as venues for exactly this reason.

## Revisit when

- **14 September**, when the ADK lands. If it turns out to want the Board as well as the target
  service, the two-seam split is wrong and this record is the thing to reverse.
- **A venue that is not CTFd** becomes worth playing — that is when `Board` earns an interface.
- **v2's gate**, which is the first time crash-restart makes `/state` load-bearing rather than
  merely convenient, and the first time its unbounded growth is a real endurance question.
- **An event forces a `practice/<event>` branch.** Once is an escape hatch working as designed;
  twice means the seam is in the wrong place and the branch model deserves re-arguing.
