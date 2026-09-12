"""The Instance path: deployed at the start of an Attempt, released when it is cut.

Thirty-two of Brunner's seventy-four Challenges cannot be reconned at all without a running
Instance — `connection_info` is empty on every `dynamic` one and absent from every `flightops` one,
and no description carries a host:port. **The address does not exist until an Instance is
deployed** ([#15](https://github.com/jerome-queck/incypher-ctf/issues/15)).

[ADR-0007](../docs/adr/0007-truth-about-an-instance-lives-on-the-board.md) governs everything here,
and it is one rule: **the Solver keeps no record of record.** Two reads on the Board are
authoritative, each for one question, and neither is ever asked the other's — **existence** is the
team-scoped ledger, and **the deadline** is the `until` the deploy answered with. So this module
holds no dictionary of what is deployed: it hands back a `Lease` and asks the Board again.

Three refusals shape the rest:

- **Expiry is computed, never detected.** The per-Challenge GET is served from a sixty-second
  CTFd-side cache that can report an Instance alive a minute after it died, so a poll built on it
  is worse than no poll. Liveness *is* checked — but only on cause, which is why `liveness` cannot
  be called without naming the command whose connection was refused.
- **Renewal is late or not at all.** A renew sets `until = now + timeout`, discarding whatever
  remained, so renewing early throws time away.
- **No failure is anonymous.** Every one arrives as a named Observation from the closed vocabulary
  below, because an orchestrator handed a tool error has to guess between *never retry*, *retry
  after terminating* and *retry in a moment* — which is the same 403 three times over.

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from solver.board import ABSENT, ANSWERED, DENIED, LOCKED, REFUSED, UNREACHABLE, Board, Mana, Reply
from solver.board_broker import BoardBrokerClient
from solver.instance_ledger import AuthenticatedIdentity, read_profiled_instance_ledger
from solver.board_broker_contracts import BoardOutcome
from solver.record import NO_MODEL, Recorder
from solver.instance_lease import LeaseCoordinator
from solver.instance_lease_contracts import LeaseCloseCause, LeaseIdentity, LeasePhase
from solver.work_generation import GenerationIdentity

# Every line this module writes about itself opens with this, for the reason `solver/recon.py`
# gives: a reader of a stream can tell what the Solver said from what a tool said, and never takes
# one of these for a shell command they could replay.
MARK = "[instance]"

# Hardcoded in the plugin's SQLAlchemy `polymorphic_identity`, so it is baked into the database
# rows and an organiser cannot rename it without forking. Anything else falls to the default branch
# — treated as static and attempted with no deploy at all.
INSTANCED_TYPE = "dynamic_iac"

# Isolated Challenges whose Instances a **different plugin** serves. `container` is the CTFd
# containers plugin, measured on COMPFEST on 29 August 2026: `/plugins/containers/`, with `image`,
# `internal_port`, `timeout_minutes` and its own deploy endpoint, and nothing this module can call.
#
# Named rather than inferred from the absence of chall-manager's fields, because the two silences
# are opposite: a `dynamic` Challenge carrying none of them is static and workable, and a
# `container` carrying none of them has a target that does not exist until somebody deploys it —
# its `connection_info` says so outright and its Flag is generated per Instance, so there is
# nothing to reach and nothing to submit. A set rather than a second constant: a third plugin at a
# third venue is a line here (#138 is where the seam that would replace this gets decided).
INSTANCED_ELSEWHERE = frozenset({"container"})

# The closed vocabulary. Every failure on this path is one of these ten, so nothing reaches the
# orchestrator as an anonymous tool error (ADR-0007). Read them as instructions rather than as
# labels: the first three are *never retry* / *retry after terminating* / *retry in a moment*.
DEPLOY_REFUSED_SHARED = "deploy-refused-shared"
DEPLOY_REFUSED_MANA = "deploy-refused-mana"
DEPLOY_REFUSED_TRANSIENT = "deploy-refused-transient"
DEPLOY_COLLISION = "deploy-collision"
DEPLOY_ALREADY_HELD = "deploy-already-held"
INSTANCE_DIED_EARLY = "instance-died-early"
INSTANCE_EXPIRED_AT_SUBMIT = "instance-expired-at-submit"
CHALL_MANAGER_DOWN_AT_SUBMIT = "chall-manager-down-at-submit"
INSTANCE_DESTROYED_ON_FLAG = "instance-destroyed-on-flag"
SHARED_NOT_DEPLOYED = "shared-not-deployed"

FAILURE_SHAPES = (
    DEPLOY_REFUSED_SHARED,
    DEPLOY_REFUSED_MANA,
    DEPLOY_REFUSED_TRANSIENT,
    DEPLOY_COLLISION,
    DEPLOY_ALREADY_HELD,
    INSTANCE_DIED_EARLY,
    INSTANCE_EXPIRED_AT_SUBMIT,
    CHALL_MANAGER_DOWN_AT_SUBMIT,
    INSTANCE_DESTROYED_ON_FLAG,
    SHARED_NOT_DEPLOYED,
)

# What CTFd's `attempt()` says when it GETs the Instance before grading and does not find one, and
# what it says when chall-manager answered it with an error. Both grade `incorrect` and spend a
# slot of the Board-wide incorrect-submission budget, and the two want opposite responses — so the
# message is matched rather than the status, which is identical.
EXPIRED_AT_SUBMIT_SAYS = "the instance must be running to submit"
CHALL_MANAGER_DOWN_SAYS = "contact admins"


@dataclass(frozen=True)
class Reserves:
    """Time held back inside a deadline, as parameters rather than constants.

    Neither number has a source yet: they are calibrated from a replayed Run like every other
    number in v1, and a constant is a number nobody can move when it turns out to be wrong.
    """

    # Held inside `until` so a Flag found at the last moment can still be submitted. A Flag
    # submitted after the Instance is gone is graded *incorrect*, not errored — it scores nothing
    # and spends a slot of the Board-wide budget anyway.
    submission_seconds: float = 60.0
    # One PATCH round-trip, which is what makes "renew at the last moment" late rather than too
    # late.
    renew_round_trip_seconds: float = 10.0


@dataclass(frozen=True)
class Terms:
    """What the Board says about deploying this Challenge, **read before anything is deployed**.

    `type` comes free with the LIST payload and is the detector; the rest is what the detail GET is
    for, and `shared` is in no LIST payload at all. Reading `timeout` here rather than after a
    deploy is what makes `min(budget, TTL)` computable at Triage, which spends no mana and starts
    no clock.
    """

    challenge_id: int | str
    challenge_type: str
    shared: bool = False
    timeout: int | None = None
    destroy_on_flag: bool = False
    mana_cost: int = 0

    @classmethod
    def of(cls, challenge: Mapping[str, Any]) -> Terms:
        return cls(
            challenge_id=challenge["id"],
            challenge_type=str(challenge.get("type", "")),
            shared=bool(challenge.get("shared", False)),
            timeout=challenge.get("timeout") or None,
            destroy_on_flag=bool(challenge.get("destroy_on_flag", False)),
            mana_cost=int(challenge.get("mana_cost") or 0),
        )

    @property
    def instanced(self) -> bool:
        return self.challenge_type == INSTANCED_TYPE

    @property
    def elsewhere(self) -> bool:
        """An Isolated Challenge some other plugin deploys, so we cannot — and, unlike a static
        Challenge, there is no target to work without one."""
        return self.challenge_type in INSTANCED_ELSEWHERE

    @property
    def renewable(self) -> bool:
        """`check_source_can_patch_instance` is exactly `if not challenge.timeout: return False`, so
        a Challenge with only an `until` ceiling answers every PATCH with a 403."""
        return bool(self.timeout)


@dataclass(frozen=True)
class Lease:
    """Our hold on one Instance — the address it answers at, and the moment it stops answering.

    A hold is a resource and an Attempt is work, which is why it has a word of its own even though
    the two now share a span (`CONTEXT.md`, *Lease*; ADR-0023). The deadline is the deploy response's
    `until` and nothing else ever writes it.
    """

    challenge_id: int | str
    connection_info: str
    until: dt.datetime | None
    terms: Terms
    reserves: Reserves = field(default_factory=Reserves)
    identity: LeaseIdentity | None = None
    epoch: int = 0
    attempt_id: str = ""
    generation_id: str = ""

    def attempt_deadline(self, budget_deadline: dt.datetime) -> dt.datetime:
        """The earlier of the Attempt's own budget and what the Instance leaves us, minus the
        submission reserve — so a Flag is never found with no time left to submit it."""
        if self.until is None:
            return budget_deadline
        return min(budget_deadline, self.until - dt.timedelta(seconds=self.reserves.submission_seconds))

    def due_for_renewal(self, now: dt.datetime) -> bool:
        """Late, deliberately: a renew sets `until = now + timeout`, so every second renewed early
        is a second discarded."""
        if self.until is None or not self.terms.renewable:
            return False
        margin = self.reserves.submission_seconds + self.reserves.renew_round_trip_seconds
        return (self.until - now).total_seconds() <= margin


@dataclass(frozen=True)
class Answer:
    """What one operation on this path did: the Lease if we now hold one, the shape if something
    has to be named, and the Observation as the model is shown it.

    `released` is a separate fact from either: a terminate that came back 429 names no shape and
    hands back no Lease, and reading that as a release is how a leak is recorded as reclaimed.
    """

    shape: str = ""
    lease: Lease | None = None
    shown: str = ""
    released: bool = False


@dataclass(frozen=True)
class Swept:
    """What a leak sweep reclaimed, and what it did not.

    Three outcomes rather than two, because the two failures want different responses and neither
    may read as a release. `still_held` is a terminate the Board refused — the per-team lock 429s a
    DELETE immediately, and back-to-back DELETEs in one sweep are the likeliest way to meet it, so
    the next boundary comes back for it. `unresolved` is the ledger's own lossiness: it keys rows by
    challenge name and a terminate takes an id, so a row nothing on the Board answers to is capacity
    that stays spent. Both are reported rather than dropped, because chall-manager never evicts.
    """

    terminated: tuple[str, ...] = ()
    still_held: tuple[str, ...] = ()
    unresolved: tuple[str, ...] = ()
    shown: str = ""


def affordable(terms: Terms, mana: Mana) -> bool:
    """Whether this Challenge can be deployed on the mana the Board reported.

    Read once at Intake and branched on rather than tracked: a total of zero or less is the feature
    switched off, and a Challenge that costs nothing is free whatever the total says — both
    short-circuit before any arithmetic (`CONTEXT.md`, *Mana*).
    """
    if mana.total <= 0 or terms.mana_cost == 0:
        return True
    return terms.mana_cost <= mana.total - mana.used


def _work_id(challenge_id: int | str) -> str:
    kind = "integer" if isinstance(challenge_id, int) else "string"
    return f"{kind}:{challenge_id}"


def submission_shape(verdict: Mapping[str, Any]) -> str:
    """Name what a rejected submission said about the Instance, or nothing where it said nothing.

    Both messages arrive as a plain `incorrect` verdict at HTTP 200, so a Solver reading the status
    learns that a Flag was wrong when what happened is that its Instance was gone.
    """
    message = str(verdict.get("message", "")).lower()
    if EXPIRED_AT_SUBMIT_SAYS in message:
        return INSTANCE_EXPIRED_AT_SUBMIT
    if CHALL_MANAGER_DOWN_SAYS in message:
        return CHALL_MANAGER_DOWN_AT_SUBMIT
    return ""


class Instances:
    """The Board's Instance surface, worked the way ADR-0007 says to work it.

    Deliberately stateless about what is deployed. Everything it returns is a `Lease` the caller
    holds and every question about existence goes back to the ledger, because a private copy goes
    stale in precisely the situations it would exist for — and a stale copy reports a leak as
    absent.
    """

    def __init__(
        self,
        board: Board,
        recorder: Recorder,
        *,
        now: Callable[[], dt.datetime] | None = None,
        reserves: Reserves = Reserves(),
        step_numbers: Callable[[], int] | None = None,
        ledger_identity: AuthenticatedIdentity | None = None,
        ledger_broker: object | None = None,
        coordinator: LeaseCoordinator | None = None,
    ) -> None:
        self._board = board
        self._recorder = recorder
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._reserves = reserves
        # A deploy is a Step of the Attempt it opens, and it happens *before* the recon cascade's
        # first — so the numbers come from whoever owns the Attempt, and two counters would put two
        # Steps at the same address.
        self._step_numbers = step_numbers or itertools.count(1).__next__
        self._ledger_identity = ledger_identity
        self._ledger_broker = ledger_broker
        self._coordinator = coordinator

    def deploy(
        self,
        terms: Terms,
        *,
        attempt_id: str,
        recovery_reader: BoardBrokerClient | None = None,
        generation_id: str = "",
    ) -> Answer:
        """Take a Lease on this Challenge, at the start of the Attempt that will work it.

        Never at Triage: Triage reads the manifest and spends no mana and starts no clock. A
        Challenge that is not `dynamic_iac` needs no Instance and costs no call — that default
        branch is what makes the path total over a `type` we have never met.
        """
        if not terms.instanced:
            return self._answer(
                "deploy",
                f"no deploy — {terms.challenge_type!r} is not {INSTANCED_TYPE}, so it is attempted as static",
                attempt_id,
            )
        if self._coordinator is not None:
            generation = GenerationIdentity(generation_id, _work_id(terms.challenge_id), attempt_id)
            grant = self._coordinator.acquire(terms.challenge_id, generation=generation)
            if grant.phase is not LeasePhase.ATTEMPT_BOUND:
                return self._answer(
                    "deploy",
                    f"{DEPLOY_REFUSED_TRANSIENT} — Lease create remains {grant.phase.value}",
                    attempt_id,
                    shape=DEPLOY_REFUSED_TRANSIENT,
                )
            lease = Lease(
                terms.challenge_id,
                grant.target,
                grant.until,
                terms,
                self._reserves,
                grant.identity,
                grant.epoch,
                attempt_id,
                grant.generation_id,
            )
            return self._answer("deploy", f"deployed at {grant.target}", attempt_id, lease=lease)
        reply = self._board.deploy_instance(terms.challenge_id)
        if reply.outcome == ANSWERED and reply.connection_info:
            return self._leased("deploy", terms, reply, attempt_id)
        if reply.outcome in (ANSWERED, DENIED):
            return self._recover(terms, reply, attempt_id, recovery_reader)
        if reply.outcome == LOCKED:
            return self._answer("deploy", f"{DEPLOY_COLLISION} — {reply.detail}", attempt_id, shape=DEPLOY_COLLISION)
        if reply.outcome == REFUSED:
            return self._refused(terms, reply, attempt_id)
        return self._answer(
            "deploy",
            f"{DEPLOY_REFUSED_TRANSIENT} — the deploy was answered {reply.outcome}: {reply.detail}",
            attempt_id,
            shape=DEPLOY_REFUSED_TRANSIENT,
        )

    def renew(self, lease: Lease, *, attempt_id: str, generation_id: str = "") -> Answer:
        """Push the deadline out, and compute the new one rather than reading it back.

        The PATCH response carries no `until`; `update_instance` sets it to `now + timeout`, so the
        arithmetic here *is* the deadline. A renew the Board refuses is not a new shape: the Lease
        is unchanged, the deadline already computed still governs, and the Attempt is cut by the
        clock rather than by this call.
        """
        if not lease.terms.renewable:
            return self._answer("renew", "not renewed — the Challenge defines no timeout", attempt_id, lease=lease)
        if self._coordinator is not None and lease.identity is not None:
            asked_at = self._now()
            until = asked_at + dt.timedelta(seconds=int(lease.terms.timeout or 0))
            generation = GenerationIdentity(generation_id, _work_id(lease.challenge_id), attempt_id)
            grant = self._coordinator.renew(lease.identity, lease.epoch, generation=generation, until=until)
            renewed = Lease(
                lease.challenge_id,
                grant.target,
                grant.until,
                lease.terms,
                lease.reserves,
                grant.identity,
                grant.epoch,
                attempt_id,
                grant.generation_id,
            )
            return self._answer("renew", f"renewed under Lease epoch {grant.epoch}", attempt_id, lease=renewed)
        # Read before the call rather than after it: the `now` chall-manager used is the moment it
        # processed the PATCH, so a clock sampled on the reply puts the deadline one round-trip
        # later than the Board's own — which is the direction that submits a Flag after expiry.
        asked_at = self._now()
        reply = self._board.renew_instance(lease.challenge_id)
        if reply.outcome == ANSWERED:
            until = asked_at + dt.timedelta(seconds=int(lease.terms.timeout or 0))
            renewed = Lease(lease.challenge_id, lease.connection_info, until, lease.terms, lease.reserves)
            return self._answer("renew", f"renewed until {until.isoformat()}", attempt_id, lease=renewed)
        if reply.outcome == ABSENT:
            return self._answer(
                "renew", f"{INSTANCE_DIED_EARLY} — the renew found no Instance", attempt_id, shape=INSTANCE_DIED_EARLY
            )
        return self._answer(
            "renew",
            f"not renewed — the renew was answered {reply.outcome}: {reply.detail}. "
            f"The deadline already computed still governs",
            attempt_id,
            lease=lease,
        )

    def terminate(
        self, challenge_id: int | str, *, attempt_id: str, after_flag: bool = False, generation_id: str = ""
    ) -> Answer:
        """Release the hold. **A 404 is success** — `destroy_on_flag` may already have done it, and
        reading that field to decide whether to bother is a branch that gets it wrong when the
        field is absent."""
        if self._coordinator is not None and (grant := self._coordinator.active(challenge_id)) is not None:
            generation = GenerationIdentity(
                generation_id or grant.generation_id,
                _work_id(challenge_id),
                attempt_id if generation_id else grant.attempt_id,
            )
            closed = self._coordinator.release(grant.identity, grant.epoch, generation=generation)
            return self._answer(
                "terminate",
                f"released challenge {challenge_id}",
                attempt_id,
                released=closed.close_cause is LeaseCloseCause.TERMINATED,
            )
        reply = self._board.terminate_instance(challenge_id)
        if reply.outcome == ABSENT:
            shape = INSTANCE_DESTROYED_ON_FLAG if after_flag else ""
            return self._answer(
                "terminate",
                f"{shape or 'released'} — there was no Instance to destroy",
                attempt_id,
                shape=shape,
                released=True,
            )
        if reply.outcome == ANSWERED:
            return self._answer("terminate", f"released challenge {challenge_id}", attempt_id, released=True)
        # Not a named shape and not fatal: the boundary sweep reads the ledger and comes back for
        # whatever this left behind, which is what makes terminate-on-cut survive its own failure.
        return self._answer(
            "terminate",
            f"not released — the terminate was answered {reply.outcome}: {reply.detail}. The sweep will return for it",
            attempt_id,
        )

    def liveness(self, lease: Lease, *, attempt_id: str, because: str) -> Answer:
        """One read, bought by one cause. There is no timer behind this and there must not be:
        the per-Challenge GET is cached for sixty seconds, so a poll can report an Instance alive a
        minute after it died. `because` is the command whose connection was refused, and it is
        required so that asking without a cause is not a thing this seam can express."""
        reply = self._board.read_instance(lease.challenge_id)
        if reply.outcome == ABSENT:
            return self._answer(
                "liveness", f"{INSTANCE_DIED_EARLY} — asked because {because}", attempt_id, shape=INSTANCE_DIED_EARLY
            )
        if reply.outcome == ANSWERED:
            return self._answer("liveness", f"alive — asked because {because}", attempt_id, lease=lease)
        # Neither alive nor dead. Saying "alive" here would answer the question the caller asked
        # with the one thing this read cannot establish, and there is no second read: liveness is
        # bought by a cause and a cause buys exactly one.
        return self._answer(
            "liveness",
            f"not established — the read was answered {reply.outcome}: {reply.detail}. Asked because {because}",
            attempt_id,
            lease=lease,
        )

    def sweep(self, *, attempt_id: str, keeping: str | None, known: Mapping[str, int | str]) -> Swept:
        """Read the ledger and release everything held that this Attempt is not working.

        Runs at every Attempt boundary and again at Run close, where `keeping` is `None` and
        nothing survives. chall-manager never evicts, so an Instance still held when the process
        exits is capacity nobody reclaims for the rest of the event.

        `keeping` is a challenge *name* because the ledger keys its rows by name; `known` is what
        Intake already holds, and turns those names back into the ids a terminate takes.
        """
        if self._ledger_identity is not None and self._ledger_broker is not None:
            ledger = read_profiled_instance_ledger(self._ledger_identity, self._ledger_broker)
            if ledger.outcome not in {"empty", "populated"}:
                told = f"the authenticated instance ledger is {ledger.outcome}: {ledger.reason}"
                return Swept(shown=self._record("sweep", told, attempt_id, ok=False))
            by_id = {challenge_id: name for name, challenge_id in known.items()}
            held = [by_id.get(row.challenge_id, f"#{row.challenge_id}") for row in ledger.owned]
        else:
            held = [record.challenge_name for record in self._board.instances_held()]
        terminated, still_held, unresolved = [], [], []
        for name in (name for name in held if name != keeping):
            if name not in known:
                unresolved.append(name)
            elif self.terminate(known[name], attempt_id=attempt_id).released:
                terminated.append(name)
            else:
                still_held.append(name)
        told = (
            f"the ledger holds {held or 'nothing'}; released {terminated or 'nothing'}"
            + (f"; still held after a refused terminate: {still_held}" if still_held else "")
            + (f"; no id on the Board for {unresolved}" if unresolved else "")
        )
        left_behind = still_held + unresolved
        return Swept(
            tuple(terminated),
            tuple(still_held),
            tuple(unresolved),
            self._record("sweep", told, attempt_id, ok=not left_behind),
        )

    def _recover(
        self,
        terms: Terms,
        reply: Reply,
        attempt_id: str,
        recovery_reader: BoardBrokerClient | None,
    ) -> Answer:
        """Trap 1 — a POST for a Challenge that already has an Instance answers **HTTP 200** with
        `success: false` and no `connectionInfo`. The status is not evidence a deploy happened, and
        the recovery is a read: what we are holding is the ledger's question, not the POST's."""
        standing = self._brokered_read(recovery_reader, terms.challenge_id)
        if standing.outcome == ANSWERED and standing.connection_info:
            return self._leased("deploy", terms, standing, attempt_id, shape=DEPLOY_ALREADY_HELD)
        return self._answer(
            "deploy",
            f"{DEPLOY_REFUSED_TRANSIENT} — the deploy answered without an address ({reply.detail}) "
            f"and no Instance stands: {standing.outcome}",
            attempt_id,
            shape=DEPLOY_REFUSED_TRANSIENT,
        )

    def _brokered_read(self, reader: BoardBrokerClient | None, challenge_id: int | str) -> Reply:
        if reader is None:
            return self._board.read_instance(challenge_id)
        result = reader.read_instance(challenge_id)
        if result.outcome is not BoardOutcome.ANSWERED or not isinstance(result.value, Reply):
            return Reply(UNREACHABLE, detail=f"Board broker classified the read {result.outcome.value}")
        return result.value

    def _refused(self, terms: Terms, reply: Reply, attempt_id: str) -> Answer:
        """Trap 2 — one 403 body for three situations, meaning *never* / *after a terminate* / *in a
        moment*. Disambiguated by the `shared` flag we already read, and only then by **one**
        `GET /mana`: a second would take the same per-team lock a deploy holds and block rather than
        fail."""
        if terms.shared:
            return self._shared(terms, attempt_id)
        mana = self._board.mana()
        if mana.outcome != ANSWERED:
            return self._answer(
                "deploy",
                f"{DEPLOY_REFUSED_TRANSIENT} — the refusal could not be attributed: mana answered {mana.outcome}",
                attempt_id,
                shape=DEPLOY_REFUSED_TRANSIENT,
            )
        if not affordable(terms, mana):
            return self._answer(
                "deploy",
                f"{DEPLOY_REFUSED_MANA} — {mana.used}/{mana.total} spent and this costs {terms.mana_cost}",
                attempt_id,
                shape=DEPLOY_REFUSED_MANA,
            )
        return self._answer(
            "deploy",
            f"{DEPLOY_REFUSED_TRANSIENT} — refused with {mana.total - mana.used} mana to spare, "
            "so chall-manager errored while computing it",
            attempt_id,
            shape=DEPLOY_REFUSED_TRANSIENT,
        )

    def _shared(self, terms: Terms, attempt_id: str) -> Answer:
        """Trap 3 — a `shared` Challenge is undeployable by us entirely: POST, PATCH *and* DELETE
        all fail, and only an admin deploys one. They are ineligible either way; one read decides
        which of the two names says so, because the two mean opposite things to whoever picks the
        next Challenge — *never* against *not yet*."""
        standing = self._board.read_instance(terms.challenge_id)
        if standing.outcome == ANSWERED and standing.connection_info:
            return self._answer(
                "deploy",
                f"{DEPLOY_REFUSED_SHARED} — shared, and an admin's Instance is standing at "
                f"{standing.connection_info}. Never ours to deploy, renew or release",
                attempt_id,
                shape=DEPLOY_REFUSED_SHARED,
            )
        return self._answer(
            "deploy",
            f"{SHARED_NOT_DEPLOYED} — shared, and none is standing; an admin may yet deploy one",
            attempt_id,
            shape=SHARED_NOT_DEPLOYED,
        )

    def _leased(self, tool: str, terms: Terms, reply: Reply, attempt_id: str, *, shape: str = "") -> Answer:
        lease = Lease(terms.challenge_id, reply.connection_info, reply.until, terms, self._reserves)
        deadline = reply.until.isoformat() if reply.until else "no deadline the Board would state"
        return self._answer(
            tool,
            f"{shape or 'deployed'} at {reply.connection_info} until {deadline}",
            attempt_id,
            shape=shape,
            lease=lease,
        )

    def _answer(
        self,
        tool: str,
        told: str,
        attempt_id: str,
        *,
        shape: str = "",
        lease: Lease | None = None,
        released: bool = False,
    ) -> Answer:
        # A Step's exit code is the operation's own verdict: it came away with what it asked for —
        # a Lease, a release, or nothing that had to be named — or it did not.
        went_well = lease is not None or released or not shape
        return Answer(shape, lease, self._record(tool, told, attempt_id, ok=went_well), released)

    def _record(self, tool: str, told: str, attempt_id: str, *, ok: bool) -> str:
        """One Step per operation, as a `step-begin` / `step-end` pair like any other — a deploy
        that hung is invisible without the pair, and at a crash it is the prime suspect."""
        step = self._recorder.step_begin(
            attempt_id=attempt_id,
            step_index=self._step_numbers(),
            command_raw=f"{MARK} {tool}",
            command_normalised=f"{MARK} {tool}",
            tool=tool,
        )
        return step.end(exit_code=0 if ok else 1, output=f"{MARK} {told}".encode(), usage=NO_MODEL).shown
