"""Pure truthful Order over one verified coherent Intake boundary.

The policy consumes facts and returns a complete decision.  It opens no Board, clock, model,
Recorder, or state path; publication and compatibility projection live outside this module.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Any, Mapping

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.intake_qualification import ChallengeSnapshot, IntakeSnapshot, PriorFence, TypedId
from solver.triage import FLOOR, STATED


def _digest(document: object) -> str:
    return digest_bytes(canonical_bytes(document))


def _key(challenge_id: TypedId) -> tuple[str, int | str]:
    return challenge_id.kind, challenge_id.value


def _fraction(value: Fraction) -> dict[str, int]:
    return {"numerator": value.numerator, "denominator": value.denominator}


@dataclass(frozen=True)
class PolicyDials:
    version: int = 2
    sample_min_seconds: int = 300
    crowd_max_age_seconds: int = 1800
    crowd_min_new_solves: int = 5
    crowd_min_moving_challenges: int = 3
    crowd_min_distinct_velocities: int = 3
    crowd_max_tie_numerator: int = 3
    crowd_max_tie_denominator: int = 4
    w_crowd: Fraction = Fraction(1, 1)
    w_value: Fraction = Fraction(1, 2)
    w_lease: Fraction = Fraction(3, 4)
    w_progress: Fraction = Fraction(5, 4)
    w_category: Fraction = Fraction(1, 1)
    w_spend: Fraction = Fraction(3, 2)
    knee_seconds: int = 600
    floor_seconds: int = 300
    tier_step: Fraction = Fraction(1, 4)
    ceiling_fraction: Fraction = Fraction(1, 5)
    attempts_full: int = 6
    checkpoints_full: int = 3
    tier_cap: int = 3
    explore_every: int = 4
    concurrency: int = 1

    @property
    def digest(self) -> str:
        return _digest(self.document())

    def document(self) -> dict[str, object]:
        return {
            name: _fraction(value) if isinstance(value, Fraction) else value for name, value in self.__dict__.items()
        }


@dataclass(frozen=True)
class CrowdSource:
    structurally_trusted: bool
    synthetic: bool
    control_digest: str

    def document(self) -> dict[str, object]:
        return {
            "structurally_trusted": self.structurally_trusted,
            "synthetic": self.synthetic,
            "control_digest": self.control_digest,
        }


@dataclass(frozen=True)
class OrderAuthority:
    snapshot: IntakeSnapshot
    fence: PriorFence
    history: tuple[IntakeSnapshot, ...]
    effective_max_challenges: int
    crowd_source: CrowdSource

    def document(self) -> dict[str, object]:
        return {
            "snapshot_digest": self.snapshot.digest,
            "fence": self.fence.document(),
            "history_digests": [item.digest for item in self.history],
            "effective_max_challenges": self.effective_max_challenges,
            "crowd_source": self.crowd_source.document(),
        }


@dataclass(frozen=True)
class AdmissionFact:
    challenge_id: TypedId
    admissible: bool
    reason: str = ""
    evidence_digest: str = ""
    safe_deadline: dt.datetime | None = None

    def document(self) -> dict[str, object]:
        return {
            "challenge_id": self.challenge_id.document(),
            "admissible": self.admissible,
            "reason": self.reason,
            "evidence_digest": self.evidence_digest,
            "safe_deadline": self.safe_deadline.isoformat() if self.safe_deadline else None,
        }


@dataclass(frozen=True)
class AttemptFact:
    challenge_id: TypedId
    attempt_id: str
    category: str
    seconds_ms: int
    checkpoints: int
    disposition: str
    source_event_digest: str
    checkpoint_event_digests: tuple[str, ...] = ()

    def document(self) -> dict[str, object]:
        return {
            "challenge_id": self.challenge_id.document(),
            "attempt_id": self.attempt_id,
            "category": self.category,
            "seconds_ms": self.seconds_ms,
            "checkpoints": self.checkpoints,
            "disposition": self.disposition,
            "source_event_digest": self.source_event_digest,
            "checkpoint_event_digests": list(self.checkpoint_event_digests),
        }


@dataclass(frozen=True)
class PriorCrowd:
    challenge_id: TypedId
    state: str
    consecutive_passes: int = 0
    consecutive_failures: int = 0
    velocity: Fraction | None = None
    last_qualified_tier: int | None = None
    source_event_digest: str = ""
    qualified_at: dt.datetime | None = None

    def document(self) -> dict[str, object]:
        return {
            "challenge_id": self.challenge_id.document(),
            "state": self.state,
            "consecutive_passes": self.consecutive_passes,
            "consecutive_failures": self.consecutive_failures,
            "velocity": _fraction(self.velocity) if self.velocity is not None else None,
            "last_qualified_tier": self.last_qualified_tier,
            "source_event_digest": self.source_event_digest,
            "qualified_at": self.qualified_at.isoformat() if self.qualified_at else None,
        }


@dataclass(frozen=True)
class DurableTier:
    challenge_id: TypedId
    tier: int
    source_event_digest: str

    def document(self) -> dict[str, object]:
        return {
            "challenge_id": self.challenge_id.document(),
            "tier": self.tier,
            "source_event_digest": self.source_event_digest,
        }


@dataclass(frozen=True)
class ActiveGrant:
    attempt_id: str
    generation_id: str
    challenge_id: TypedId
    tier: int
    budget_s: int
    deadline: dt.datetime
    order_event_digest: str

    def document(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id,
            "generation_id": self.generation_id,
            "challenge_id": self.challenge_id.document(),
            "tier": self.tier,
            "budget_s": self.budget_s,
            "deadline": self.deadline.isoformat(),
            "order_event_digest": self.order_event_digest,
        }


@dataclass(frozen=True)
class OrderFence:
    publication_id: str = ""
    decision_digest: str = ""
    boundary_event_digest: str = ""

    def document(self) -> dict[str, str]:
        return {
            "publication_id": self.publication_id,
            "decision_digest": self.decision_digest,
            "boundary_event_digest": self.boundary_event_digest,
        }


@dataclass(frozen=True)
class OrderRunFacts:
    boundary_id: str
    boundary_at: dt.datetime
    final_submission_cutoff: dt.datetime
    window_seconds: int
    next_generation: int
    window_digest: str = ""
    generation_projection_digest: str = ""
    intake_event_digest: str = ""
    submission_tail_seconds: int = 0
    boundary_clock_event_digest: str = ""
    leased: tuple[TypedId, ...] = ()
    solved: tuple[TypedId, ...] = ()
    active_grants: tuple[ActiveGrant, ...] = ()
    attempts: tuple[AttemptFact, ...] = ()
    prior_crowd: tuple[PriorCrowd, ...] = ()
    durable_tiers: tuple[DurableTier, ...] = ()
    admission: tuple[AdmissionFact, ...] = ()
    prior_order_fence: OrderFence = field(default_factory=OrderFence)
    boundary_fact_event_digest: str = ""

    def document(self) -> dict[str, object]:
        return {
            "boundary_id": self.boundary_id,
            "boundary_at": self.boundary_at.isoformat(),
            "final_submission_cutoff": self.final_submission_cutoff.isoformat(),
            "window_seconds": self.window_seconds,
            "next_generation": self.next_generation,
            "window_digest": self.window_digest,
            "generation_projection_digest": self.generation_projection_digest,
            "intake_event_digest": self.intake_event_digest,
            "submission_tail_seconds": self.submission_tail_seconds,
            "boundary_clock_event_digest": self.boundary_clock_event_digest,
            "leased": [item.document() for item in self.leased],
            "solved": [item.document() for item in self.solved],
            "active_grants": [item.document() for item in self.active_grants],
            "attempts": [item.document() for item in self.attempts],
            "prior_crowd": [item.document() for item in self.prior_crowd],
            "durable_tiers": [item.document() for item in self.durable_tiers],
            "admission": [item.document() for item in self.admission],
            "prior_order_fence": self.prior_order_fence.document(),
            "boundary_fact_event_digest": self.boundary_fact_event_digest,
        }

    def boundary_source_document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "record": "boundary-facts",
            "boundary_id": self.boundary_id,
            "boundary_at": self.boundary_at.isoformat(),
            "final_submission_cutoff": self.final_submission_cutoff.isoformat(),
            "window_seconds": self.window_seconds,
            "next_generation": self.next_generation,
            "window_digest": self.window_digest,
            "generation_projection_digest": self.generation_projection_digest,
            "intake_event_digest": self.intake_event_digest,
            "submission_tail_seconds": self.submission_tail_seconds,
            "boundary_clock_event_digest": self.boundary_clock_event_digest,
            "leased": [item.document() for item in self.leased],
            "solved": [item.document() for item in self.solved],
            "prior_order_fence": self.prior_order_fence.document(),
        }


@dataclass(frozen=True)
class OrderInput:
    authority: OrderAuthority
    run: OrderRunFacts
    dials: PolicyDials = field(default_factory=PolicyDials)

    @property
    def digest(self) -> str:
        return _digest(self.document())

    def document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "authority": self.authority.document(),
            "run": self.run.document(),
            "dials": self.dials.document(),
        }


@dataclass(frozen=True)
class CrowdDecision:
    state: str
    reason: str
    velocity: Fraction | None
    percentile: Fraction
    reliability: Fraction
    activity_digest: str = ""

    def document(self) -> dict[str, object]:
        return {
            "state": self.state,
            "reason": self.reason,
            "velocity": _fraction(self.velocity) if self.velocity is not None else None,
            "percentile": _fraction(self.percentile),
            "reliability": _fraction(self.reliability),
            "activity_digest": self.activity_digest,
        }


@dataclass(frozen=True)
class Components:
    crowd: Fraction
    value: Fraction
    lease: Fraction
    progress: Fraction
    category: Fraction
    spend: Fraction

    def document(self) -> dict[str, object]:
        return {name: _fraction(value) for name, value in self.__dict__.items()}


@dataclass(frozen=True)
class OrderRow:
    challenge_id: TypedId
    name: str
    category: str
    revision_digest: str
    rank: int
    order_value: Fraction
    components: Components
    crowd: CrowdDecision
    base_tier: int
    final_tier: int
    budget_s: int
    committed: bool
    demoted: bool
    deferred_reason: str
    deferred_evidence_digest: str
    tie_break: tuple[object, ...]

    def document(self) -> dict[str, object]:
        return {
            "challenge_id": self.challenge_id.document(),
            "name": self.name,
            "category": self.category,
            "revision_digest": self.revision_digest,
            "rank": self.rank,
            "order_value": _fraction(self.order_value),
            "components": self.components.document(),
            "crowd": self.crowd.document(),
            "base_tier": self.base_tier,
            "final_tier": self.final_tier,
            "budget_s": self.budget_s,
            "committed": self.committed,
            "demoted": self.demoted,
            "deferred_reason": self.deferred_reason,
            "deferred_evidence_digest": self.deferred_evidence_digest,
            "tie_break": list(self.tie_break),
        }


@dataclass(frozen=True)
class OrderDecision:
    boundary_id: str
    boundary_at: dt.datetime
    snapshot_digest: str
    intake_fence: PriorFence
    policy_digest: str
    input_digest: str
    input_document: Mapping[str, object]
    order_value_basis: str
    fallback_reason: str
    rows: tuple[OrderRow, ...]
    working_set: tuple[TypedId, ...]
    active_grants: tuple[ActiveGrant, ...]
    grant: ActiveGrant | None
    grant_exploring: bool
    interval: str
    final_interval_seconds: int
    history_digests: tuple[str, ...]

    def document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "boundary_id": self.boundary_id,
            "boundary_at": self.boundary_at.isoformat(),
            "snapshot_digest": self.snapshot_digest,
            "intake_fence": self.intake_fence.document(),
            "policy_digest": self.policy_digest,
            "input_digest": self.input_digest,
            "input_document": dict(self.input_document),
            "order_value_basis": self.order_value_basis,
            "fallback_reason": self.fallback_reason,
            "rows": [row.document() for row in self.rows],
            "working_set": [item.document() for item in self.working_set],
            "active_grants": [item.document() for item in self.active_grants],
            "grant": self.grant.document() if self.grant else None,
            "grant_exploring": self.grant_exploring,
            "interval": self.interval,
            "final_interval_seconds": self.final_interval_seconds,
            "history_digests": list(self.history_digests),
        }

    @property
    def digest(self) -> str:
        return _digest(self.document())


@dataclass(frozen=True)
class _Transition:
    passed: bool
    reason: str
    velocities: Mapping[tuple[str, int | str], Fraction]
    activity_digest: str


def decide_order(request: OrderInput) -> OrderDecision:
    """Return one total immutable Order decision from explicit verified facts."""

    authority, run, dials = request.authority, request.run, request.dials
    selected_history = _history(authority, dials)
    authority = replace(authority, history=selected_history)
    request = replace(request, authority=authority)
    if not 0 <= authority.effective_max_challenges <= 100_000:
        raise ValueError("Order Intake contract maximum is outside the supported bound")
    if authority.fence.snapshot_digest != authority.snapshot.digest:
        raise ValueError("Order Intake fence does not name its snapshot")
    if authority.fence.profile_digest != authority.snapshot.profile_digest:
        raise ValueError("Order Intake fence names another profile")
    if len(authority.snapshot.challenges) > authority.effective_max_challenges:
        raise ValueError("Order population exceeds the sealed Intake contract")
    if run.boundary_at.tzinfo is None or run.final_submission_cutoff.tzinfo is None:
        raise ValueError("Order boundary clocks must be timezone-aware")
    if run.boundary_at < authority.snapshot.observed_at:
        raise ValueError("Order boundary precedes its Intake observation")
    _validate_facts(authority, run, dials)
    population = _population(authority.snapshot.challenges, run.solved)
    basis, fallback, values = _value_basis(population)
    crowd = _crowd(population, selected_history, authority.crowd_source, run, dials)
    attempts = _attempts(run.attempts)
    categories, weak_categories = _category_components(population, run.attempts)
    leased = {_key(item) for item in run.leased}
    admission = {_key(item.challenge_id): item for item in run.admission}
    active = {_key(item.challenge_id) for item in run.active_grants}
    durable = {_key(item.challenge_id): item for item in run.durable_tiers}
    prior = {_key(item.challenge_id): item for item in run.prior_crowd}

    provisional: list[OrderRow] = []
    for challenge in population:
        key = _key(challenge.challenge_id)
        spent = attempts.get(key, ())
        base_tier = _base_tier(challenge, crowd[key], prior.get(key), durable.get(key))
        checkpoints = sum(item.checkpoints for item in spent)
        final_tier = min(
            4,
            max(1, base_tier + int(challenge.category in weak_categories) + min(dials.tier_cap, checkpoints)),
        )
        budget = _length(final_tier, dials)
        spend = _spend(spent, run.window_seconds, dials)
        category = categories.get(challenge.category, Fraction(1, 2))
        components = Components(
            crowd=_crowd_component(crowd[key]),
            value=values[key],
            lease=Fraction(int(key in leased), 1),
            progress=min(Fraction(1, 1), Fraction(checkpoints, dials.checkpoints_full)),
            category=category,
            spend=spend,
        )
        order_value = (
            dials.w_crowd * components.crowd
            + dials.w_value * components.value
            + dials.w_lease * components.lease
            + dials.w_progress * components.progress
            + dials.w_category * components.category
            - dials.w_spend * components.spend
        )
        fact = admission.get(key)
        deadline_too_short = bool(
            fact and fact.safe_deadline and (fact.safe_deadline - run.boundary_at).total_seconds() < dials.floor_seconds
        )
        deferred = (
            "active-grant"
            if key in active
            else fact.reason
            if fact and not fact.admissible
            else "safe-deadline-below-floor"
            if deadline_too_short
            else ""
        )
        evidence = fact.evidence_digest if fact and deferred else ""
        position = _position(challenge)
        provisional.append(
            OrderRow(
                challenge.challenge_id,
                challenge.name,
                challenge.category,
                challenge.revision_digest,
                0,
                order_value,
                components,
                crowd[key],
                base_tier,
                final_tier,
                budget,
                False,
                _demoted(spent, run.window_seconds, dials),
                deferred,
                evidence,
                _tie_break(position, challenge.challenge_id),
            )
        )
    provisional.sort(key=lambda row: (row.demoted, -row.order_value, row.tie_break))
    remaining = max(0, int((run.final_submission_cutoff - run.boundary_at).total_seconds()))
    admissible_provisional = [row for row in provisional if not row.deferred_reason]
    affordable = _attempts_affordable(remaining, [row.budget_s for row in admissible_provisional], dials.floor_seconds)
    committed_ids = {_key(row.challenge_id) for row in admissible_provisional[:affordable]}
    rows = tuple(
        replace(row, rank=index, committed=_key(row.challenge_id) in committed_ids)
        for index, row in enumerate(provisional, start=1)
    )
    working_set = tuple(row.challenge_id for row in rows if row.committed)
    interval = "closed" if remaining <= 0 else "final-interval" if remaining < dials.floor_seconds else "ordinary"
    grant = None
    grant_exploring = False
    if interval == "ordinary" and len(run.active_grants) < dials.concurrency:
        admissible = tuple(row for row in rows if row.committed)
        chosen = admissible[0] if admissible else None
        if admissible and (run.next_generation - 1) % dials.explore_every == dials.explore_every - 1:
            uncertain = tuple(row for row in admissible if row.crowd.state != "qualified")
            if uncertain:
                chosen = min(
                    uncertain,
                    key=lambda row: (
                        -(row.order_value - dials.w_crowd * row.components.crowd),
                        row.tie_break,
                    ),
                )
                grant_exploring = True
        if chosen is not None:
            fact = admission.get(_key(chosen.challenge_id))
            safe = (
                int((fact.safe_deadline - run.boundary_at).total_seconds())
                if fact and fact.safe_deadline
                else remaining
            )
            budget = min(chosen.budget_s, remaining, safe)
            if budget >= dials.floor_seconds:
                sequence = 1 + sum(1 for item in run.attempts if item.challenge_id == chosen.challenge_id)
                tag = "i" if chosen.challenge_id.kind == "integer" else "s"
                attempt_id = f"{tag}:{chosen.challenge_id.value}-{sequence}"
                grant = ActiveGrant(
                    attempt_id,
                    f"generation-{run.next_generation:06d}",
                    chosen.challenge_id,
                    chosen.final_tier,
                    budget,
                    run.boundary_at + dt.timedelta(seconds=budget),
                    "",
                )
    return OrderDecision(
        run.boundary_id,
        run.boundary_at,
        authority.snapshot.digest,
        authority.fence,
        dials.digest,
        request.digest,
        request.document(),
        basis,
        fallback,
        rows,
        working_set,
        run.active_grants,
        grant,
        grant_exploring,
        interval,
        remaining if interval == "final-interval" else 0,
        tuple(item.digest for item in selected_history),
    )


def order_input_from_document(value: object, snapshots: Mapping[str, IntakeSnapshot]) -> OrderInput:
    """Parse the exact bounded input sealed by an Order decision."""

    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise ValueError("Order input schema is unsupported")
    authority_value = _mapping(value.get("authority"), "Order authority")
    run_value = _mapping(value.get("run"), "Order run facts")
    dials_value = _mapping(value.get("dials"), "Order policy dials")
    snapshot_digest = str(authority_value.get("snapshot_digest", ""))
    try:
        snapshot = snapshots[snapshot_digest]
        history = tuple(snapshots[str(item)] for item in _list(authority_value.get("history_digests")))
    except KeyError as error:
        raise ValueError("Order input snapshot is absent from canonical Intake") from error
    fence_value = _mapping(authority_value.get("fence"), "Order Intake fence")
    authority = OrderAuthority(
        snapshot,
        PriorFence(
            str(fence_value["profile_digest"]),
            str(fence_value["event_id"]),
            str(fence_value["event_digest"]),
            str(fence_value["snapshot_digest"]),
        ),
        history,
        int(authority_value["effective_max_challenges"]),
        CrowdSource(**_typed_kwargs(CrowdSource, _mapping(authority_value.get("crowd_source"), "crowd source"))),
    )
    run = OrderRunFacts(
        boundary_id=str(run_value["boundary_id"]),
        boundary_at=dt.datetime.fromisoformat(str(run_value["boundary_at"])),
        final_submission_cutoff=dt.datetime.fromisoformat(str(run_value["final_submission_cutoff"])),
        window_seconds=int(run_value["window_seconds"]),
        next_generation=int(run_value["next_generation"]),
        window_digest=str(run_value.get("window_digest", "")),
        generation_projection_digest=str(run_value.get("generation_projection_digest", "")),
        intake_event_digest=str(run_value.get("intake_event_digest", "")),
        submission_tail_seconds=int(run_value.get("submission_tail_seconds", 0)),
        boundary_clock_event_digest=str(run_value.get("boundary_clock_event_digest", "")),
        leased=tuple(_typed_id(item) for item in _list(run_value.get("leased"))),
        solved=tuple(_typed_id(item) for item in _list(run_value.get("solved"))),
        active_grants=tuple(_active_grant(item) for item in _list(run_value.get("active_grants"))),
        attempts=tuple(_attempt_fact(item) for item in _list(run_value.get("attempts"))),
        prior_crowd=tuple(_prior_crowd(item) for item in _list(run_value.get("prior_crowd"))),
        durable_tiers=tuple(_durable_tier(item) for item in _list(run_value.get("durable_tiers"))),
        admission=tuple(_admission(item) for item in _list(run_value.get("admission"))),
        prior_order_fence=_order_fence(run_value.get("prior_order_fence")),
        boundary_fact_event_digest=str(run_value.get("boundary_fact_event_digest", "")),
    )
    dials = PolicyDials(
        **{
            name: _parse_fraction(raw) if isinstance(getattr(PolicyDials(), name), Fraction) else raw
            for name, raw in dials_value.items()
        }
    )
    parsed = OrderInput(authority, run, dials)
    if parsed.document() != dict(value):
        raise ValueError("Order input is not canonical or supported")
    return parsed


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} is not an object")
    return value


def _list(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError("Order input collection is invalid")
    return value


def _typed_id(value: object) -> TypedId:
    item = _mapping(value, "typed challenge identity")
    kind = str(item["type"])
    raw: int | str = int(str(item["value"])) if kind == "integer" else str(item["value"])
    return TypedId(kind, raw)


def _parse_fraction(value: object) -> Fraction:
    item = _mapping(value, "fraction")
    return Fraction(int(item["numerator"]), int(item["denominator"]))


def _active_grant(value: object) -> ActiveGrant:
    item = _mapping(value, "active grant")
    return ActiveGrant(
        str(item["attempt_id"]),
        str(item["generation_id"]),
        _typed_id(item["challenge_id"]),
        int(item["tier"]),
        int(item["budget_s"]),
        dt.datetime.fromisoformat(str(item["deadline"])),
        str(item["order_event_digest"]),
    )


def _attempt_fact(value: object) -> AttemptFact:
    item = _mapping(value, "attempt fact")
    return AttemptFact(
        _typed_id(item["challenge_id"]),
        str(item["attempt_id"]),
        str(item["category"]),
        int(item["seconds_ms"]),
        int(item["checkpoints"]),
        str(item["disposition"]),
        str(item["source_event_digest"]),
        tuple(str(v) for v in _list(item["checkpoint_event_digests"])),
    )


def _prior_crowd(value: object) -> PriorCrowd:
    item = _mapping(value, "prior crowd")
    velocity = item.get("velocity")
    return PriorCrowd(
        _typed_id(item["challenge_id"]),
        str(item["state"]),
        int(item["consecutive_passes"]),
        int(item["consecutive_failures"]),
        _parse_fraction(velocity) if velocity is not None else None,
        int(item["last_qualified_tier"]) if item.get("last_qualified_tier") is not None else None,
        str(item["source_event_digest"]),
        dt.datetime.fromisoformat(str(item["qualified_at"])) if item.get("qualified_at") else None,
    )


def _durable_tier(value: object) -> DurableTier:
    item = _mapping(value, "durable tier")
    return DurableTier(_typed_id(item["challenge_id"]), int(item["tier"]), str(item["source_event_digest"]))


def _admission(value: object) -> AdmissionFact:
    item = _mapping(value, "admission fact")
    return AdmissionFact(
        _typed_id(item["challenge_id"]),
        bool(item["admissible"]),
        str(item["reason"]),
        str(item["evidence_digest"]),
        dt.datetime.fromisoformat(str(item["safe_deadline"])) if item.get("safe_deadline") else None,
    )


def _order_fence(value: object) -> OrderFence:
    item = _mapping(value, "prior Order fence")
    return OrderFence(str(item["publication_id"]), str(item["decision_digest"]), str(item["boundary_event_digest"]))


def _typed_kwargs(cls, value: Mapping[str, Any]) -> dict[str, Any]:
    return {name: value[name] for name in cls.__dataclass_fields__}


def _population(
    challenges: tuple[ChallengeSnapshot, ...], solved: tuple[TypedId, ...]
) -> tuple[ChallengeSnapshot, ...]:
    removed = {_key(item) for item in solved}
    return tuple(
        challenge
        for challenge in challenges
        if _key(challenge.challenge_id) not in removed
        and not (challenge.solved.outcome == "answered" and challenge.solved.value is True)
        and not _undeployable(challenge)
    )


def _undeployable(challenge: ChallengeSnapshot) -> bool:
    shared = challenge.shared.state == "value" and challenge.shared.value is True
    instanced = challenge.challenge_type not in {"standard", "dynamic", ""}
    return shared and instanced


def _value_basis(population: tuple[ChallengeSnapshot, ...]):
    unsettled = [
        item
        for item in population
        if item.value.outcome != "answered"
        or isinstance(item.value.value, bool)
        or not isinstance(item.value.value, int)
        or item.value.value < 0
    ]
    if unsettled:
        first = min(unsettled, key=lambda item: item.challenge_id.sort_key())
        return (
            "unit-fallback",
            f"challenge-value-unsettled:{first.challenge_id.kind}:{first.challenge_id.value}",
            {_key(item.challenge_id): Fraction(1, 1) for item in population},
        )
    maximum = max((int(item.value.value) for item in population), default=0)
    values = {
        _key(item.challenge_id): Fraction(int(item.value.value), maximum) if maximum else Fraction(0, 1)
        for item in population
    }
    return "coherent-challenge-values", "", values


def _history(authority: OrderAuthority, dials: PolicyDials) -> tuple[IntakeSnapshot, ...]:
    current = authority.snapshot
    candidates = {item.digest: item for item in authority.history if item.observed_at <= current.observed_at}
    candidates[current.digest] = current
    selected = [current]
    before = current
    for _ in range(2):
        lower = current.observed_at - dt.timedelta(seconds=dials.crowd_max_age_seconds)
        upper = before.observed_at - dt.timedelta(seconds=dials.sample_min_seconds)
        possible = [item for item in candidates.values() if lower <= item.observed_at <= upper]
        if not possible:
            break
        before = max(possible, key=lambda item: (item.observed_at, item.digest))
        selected.append(before)
    return tuple(selected)


def _crowd(
    population: tuple[ChallengeSnapshot, ...],
    history: tuple[IntakeSnapshot, ...],
    source: CrowdSource,
    run: OrderRunFacts,
    dials: PolicyDials,
) -> dict[tuple[str, int | str], CrowdDecision]:
    prior = {_key(item.challenge_id): item for item in run.prior_crowd}
    if source.synthetic or not source.structurally_trusted:
        reason = "synthetic-source" if source.synthetic else "source-untrusted"
        return {
            _key(item.challenge_id): CrowdDecision("unavailable", reason, None, Fraction(1, 2), Fraction(0))
            for item in population
        }
    age = (run.boundary_at - history[0].observed_at).total_seconds()
    if age > dials.crowd_max_age_seconds:
        return {
            _key(item.challenge_id): CrowdDecision(
                "unavailable", "observation-expired", None, Fraction(1, 2), Fraction(0)
            )
            for item in population
        }
    transitions = tuple(
        _transition(history[index + 1], history[index], population, dials) for index in range(len(history) - 1)
    )
    newest = transitions[0] if transitions else None
    older = transitions[1] if len(transitions) > 1 else None
    states: dict[tuple[str, int | str], tuple[str, str]] = {}
    velocities: dict[tuple[str, int | str], Fraction] = {}
    for challenge in population:
        key = _key(challenge.challenge_id)
        previous = prior.get(key)
        if (
            previous
            and previous.last_qualified_tier is not None
            and previous.qualified_at is not None
            and (run.boundary_at - previous.qualified_at).total_seconds() > dials.crowd_max_age_seconds
        ):
            states[key] = ("unavailable", "last-qualified-expired")
            continue
        current_pass = bool(newest and newest.passed and key in newest.velocities)
        older_pass = bool(older and older.passed and key in older.velocities)
        if current_pass and (
            older_pass
            or (previous is not None and previous.state == "qualified")
            or (previous is not None and previous.consecutive_passes >= 1)
        ):
            state, reason = "qualified", "two-qualifying-transitions"
        elif previous and previous.state == "qualified" and not current_pass and previous.consecutive_failures < 1:
            state, reason = "qualified", "last-qualified-held"
        else:
            state = "provisional"
            reason = (
                newest.reason
                if newest and not newest.passed
                else "one-qualifying-transition"
                if current_pass
                else "sample-unavailable"
            )
        states[key] = (state, reason)
        if state == "qualified" and reason == "last-qualified-held" and previous and previous.velocity is not None:
            velocities[key] = previous.velocity
        elif newest and key in newest.velocities:
            velocities[key] = newest.velocities[key]
        elif previous and previous.velocity is not None:
            velocities[key] = previous.velocity
    percentiles = _percentiles(velocities)
    answer = {}
    for challenge in population:
        key = _key(challenge.challenge_id)
        state, reason = states[key]
        reliability = Fraction(1) if state == "qualified" else Fraction(1, 4)
        if state == "unavailable":
            reliability = Fraction(0)
        answer[key] = CrowdDecision(
            state,
            reason,
            velocities.get(key),
            percentiles.get(key, Fraction(1, 2)),
            reliability,
            newest.activity_digest if newest else "",
        )
    return answer


def _transition(
    earlier: IntakeSnapshot,
    later: IntakeSnapshot,
    population: tuple[ChallengeSnapshot, ...],
    dials: PolicyDials,
) -> _Transition:
    elapsed_us = round((later.observed_at - earlier.observed_at).total_seconds() * 1_000_000)
    earlier_by_id = {_key(item.challenge_id): item for item in earlier.challenges}
    later_by_id = {_key(item.challenge_id): item for item in later.challenges}
    keys = {_key(item.challenge_id) for item in population} & earlier_by_id.keys() & later_by_id.keys()
    if any(
        snapshot[key].solves.outcome != "answered"
        or isinstance(snapshot[key].solves.value, bool)
        or not isinstance(snapshot[key].solves.value, int)
        or snapshot[key].solves.value < 0
        for key in keys
        for snapshot in (earlier_by_id, later_by_id)
    ):
        return _Transition(False, "solve-values-unsettled", {}, "")
    velocities = {
        key: Fraction(
            max(0, int(later_by_id[key].solves.value) - int(earlier_by_id[key].solves.value)) * 1_000_000,
            elapsed_us,
        )
        for key in keys
        if elapsed_us > 0
    }
    deltas = {key: max(0, int(later_by_id[key].solves.value) - int(earlier_by_id[key].solves.value)) for key in keys}
    separated = dials.sample_min_seconds * 1_000_000 <= elapsed_us <= dials.crowd_max_age_seconds * 1_000_000
    activity = _scoreboard_activity(earlier, later)
    activity_digest = (
        _digest(
            {
                "earlier_at": earlier.observed_at.isoformat(),
                "later_at": later.observed_at.isoformat(),
                "earlier_scoreboard": list(earlier.scoreboard),
                "later_scoreboard": list(later.scoreboard),
            }
        )
        if activity
        else ""
    )
    counts = Counter(velocities.values())
    discrimination = (
        sum(deltas.values()) >= dials.crowd_min_new_solves
        and sum(delta > 0 for delta in deltas.values()) >= dials.crowd_min_moving_challenges
        and len(counts) >= dials.crowd_min_distinct_velocities
        and bool(counts)
        and max(counts.values()) * dials.crowd_max_tie_denominator < len(velocities) * dials.crowd_max_tie_numerator
    )
    reason = (
        "qualified-transition"
        if separated and activity and discrimination
        else (
            "sample-separation-invalid"
            if not separated
            else "activity-unproved"
            if not activity
            else "discrimination-unproved"
        )
    )
    return _Transition(separated and activity and discrimination, reason, velocities, activity_digest)


def _scoreboard_activity(earlier: IntakeSnapshot, later: IntakeSnapshot) -> bool:
    def rows(snapshot: IntakeSnapshot):
        return tuple(
            (item.get("rank"), item.get("name"), item.get("score"))
            for item in snapshot.scoreboard
            if item.get("source") == "scoreboard" and item.get("observed_at") == snapshot.observed_at.isoformat()
        )

    before, after = rows(earlier), rows(later)
    return bool(before and after and before != after)


def _percentiles(values: Mapping[tuple[str, int | str], Fraction]) -> dict[tuple[str, int | str], Fraction]:
    if len(values) == 1:
        return {key: Fraction(1, 2) for key in values}
    counts = Counter(values.values())
    cumulative = 0
    percentile = {}
    for value in sorted(counts):
        equal = counts[value]
        percentile[value] = Fraction(2 * cumulative + equal - 1, 2 * (len(values) - 1))
        cumulative += equal
    return {key: percentile[value] for key, value in values.items()}


def _base_tier(
    challenge: ChallengeSnapshot,
    crowd: CrowdDecision,
    prior: PriorCrowd | None,
    durable: DurableTier | None,
) -> int:
    if crowd.state == "qualified" and crowd.reason != "last-qualified-held" and crowd.velocity is not None:
        return (
            4 if crowd.velocity == 0 else max(1, 4 - (4 * crowd.percentile.numerator // crowd.percentile.denominator))
        )
    if prior and prior.last_qualified_tier is not None:
        return prior.last_qualified_tier
    if tier := _stated_tier(challenge.statement):
        return tier
    if durable and 1 <= durable.tier <= 4:
        return durable.tier
    return FLOOR


def _stated_tier(statement: str) -> int | None:
    found = re.search(r"difficulty\W{0,4}[:\-–—]\W{0,4}\s*([A-Za-z][A-Za-z \-]{0,24})", statement, re.I)
    if not found:
        return None
    word = re.sub(r"\s+", " ", found.group(1).strip().lower())
    return STATED.get(word)


def _crowd_component(crowd: CrowdDecision) -> Fraction:
    return Fraction(1, 2) + crowd.reliability * (crowd.percentile - Fraction(1, 2))


def _attempts(attempts: tuple[AttemptFact, ...]):
    answer = defaultdict(list)
    for item in attempts:
        answer[_key(item.challenge_id)].append(item)
    return {key: tuple(items) for key, items in answer.items()}


def _category_components(population: tuple[ChallengeSnapshot, ...], attempts: tuple[AttemptFact, ...]):
    grouped = defaultdict(list)
    for item in attempts:
        grouped[item.category].append(item)
    qualified = {
        category: items
        for category, items in grouped.items()
        if len(items) >= 3 and sum(item.seconds_ms for item in items) >= 1_200_000
    }
    if not qualified:
        return {item.category: Fraction(1, 2) for item in population}, frozenset()
    rates = {
        category: Fraction(
            sum(item.disposition == "complete" for item in items) * 4 + sum(item.checkpoints for item in items),
            4 * max(1, sum(item.seconds_ms for item in items)),
        )
        for category, items in qualified.items()
    }
    top = max(rates.values())
    components = {
        item.category: rates[item.category] / top if item.category in rates and top else Fraction(1, 2)
        for item in population
    }
    weak = frozenset()
    if len(rates) >= 4:
        ordered = sorted(rates.values())
        boundary = ordered[(len(ordered) - 1) // 4]
        weak = frozenset(category for category, rate in rates.items() if rate <= boundary)
    return components, weak


def _validate_facts(authority: OrderAuthority, run: OrderRunFacts, dials: PolicyDials) -> None:
    keys = [_key(item.challenge_id) for item in authority.snapshot.challenges]
    if len(keys) != len(set(keys)):
        raise ValueError("Order Intake contains duplicate Challenge identities")
    if any(item.profile_digest != authority.snapshot.profile_digest for item in authority.history):
        raise ValueError("Order history crosses Intake profiles")
    if run.final_submission_cutoff < run.boundary_at or run.window_seconds < 0 or run.next_generation < 1:
        raise ValueError("Order Run facts contain an invalid clock or generation")
    if (
        dials.floor_seconds <= 0
        or dials.knee_seconds <= 0
        or dials.attempts_full <= 0
        or dials.checkpoints_full <= 0
        or dials.explore_every <= 0
        or dials.concurrency <= 0
    ):
        raise ValueError("Order policy dials must be positive")
    fact_groups = (
        [(_key(item.challenge_id), item.attempt_id) for item in run.attempts],
        [_key(item.challenge_id) for item in run.prior_crowd],
        [_key(item.challenge_id) for item in run.durable_tiers],
        [_key(item.challenge_id) for item in run.admission],
    )
    for facts in fact_groups:
        if len(facts) != len(set(facts)):
            raise ValueError("Order Run facts contain duplicate identities")


def _spend(attempts: tuple[AttemptFact, ...], window_seconds: int, dials: PolicyDials) -> Fraction:
    seconds_ms = sum(item.seconds_ms for item in attempts)
    denominator = max(1, int(dials.ceiling_fraction * window_seconds * 1000))
    return min(Fraction(1), max(Fraction(seconds_ms, denominator), Fraction(len(attempts), dials.attempts_full)))


def _demoted(attempts: tuple[AttemptFact, ...], window_seconds: int, dials: PolicyDials) -> bool:
    return (
        any(item.disposition == "interrupt" for item in attempts)
        or max(
            Fraction(
                sum(item.seconds_ms for item in attempts),
                max(1, int(dials.ceiling_fraction * window_seconds * 1000)),
            ),
            Fraction(len(attempts), dials.attempts_full),
        )
        >= 1
    )


def _length(tier: int, dials: PolicyDials) -> int:
    weight = Fraction(1) + (tier - FLOOR) * dials.tier_step
    return max(dials.floor_seconds, int(dials.knee_seconds * weight))


def _position(challenge: ChallengeSnapshot) -> int:
    value = challenge.position.value
    return (
        value
        if challenge.position.state == "value" and isinstance(value, int) and not isinstance(value, bool)
        else 2**63 - 1
    )


def _tie_break(position: int, challenge_id: TypedId) -> tuple[object, ...]:
    return (
        (position, 0, int(challenge_id.value))
        if challenge_id.kind == "integer"
        else (position, 1, str(challenge_id.value))
    )


def _attempts_affordable(seconds: int, lengths: list[int], floor: int) -> int:
    if seconds < floor or not lengths:
        return 0
    mean = Fraction(sum(lengths), len(lengths))
    return max(1, int(Fraction(seconds, 1) // mean))


__all__ = [
    "ActiveGrant",
    "AdmissionFact",
    "AttemptFact",
    "CrowdDecision",
    "CrowdSource",
    "DurableTier",
    "OrderAuthority",
    "OrderDecision",
    "OrderInput",
    "OrderFence",
    "OrderRow",
    "OrderRunFacts",
    "PolicyDials",
    "PriorCrowd",
    "decide_order",
    "order_input_from_document",
]
