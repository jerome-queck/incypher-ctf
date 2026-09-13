"""Canonical Order controller with a one-way v1 Pick projection."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Collection
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

from solver.event_store import WORK_GENERATION_RECORDED, GenerationRecord
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.final_interval import FinalIntervalController
from solver.order_contracts import ORDER_PUBLICATION_RECORDED
from solver.order_input_contracts import ORDER_INPUT_RECORDED, OrderInputRecord, OrderInputRecorded
from solver.order_journal import OrderJournal, replay_order_publication
from solver.order_policy import (
    ActiveGrant,
    AdmissionFact,
    AttemptFact,
    OrderAuthority,
    OrderFence,
    OrderInput,
    OrderRunFacts,
    PolicyDials,
    PriorCrowd,
    DurableTier,
    decide_order,
)
from solver.record import Recorder
from solver.schedule import WINDOW, Dials, Ended, Pick, Window
from solver.triage import JUDGED, UNJUDGED, Judge, judgeable_ids, parse_judged_tiers, triage, unasked
from solver.triage_judgement_contracts import TRIAGE_JUDGEMENT_RECORDED, TriageJudgementRecorded
from solver.triage_judge import TriageJudgeController, evidence_digest as triage_evidence_digest
from solver.triage_judge_contracts import TriageEvidence, TriageJudgeRequest, TriageProposal


class CanonicalScheduler:
    """Publish one truthful decision before exposing its derived v1 scheduling view."""

    def __init__(
        self,
        window: Window,
        recorder: Recorder,
        authority: Callable[[], OrderAuthority],
        *,
        dials: Dials = Dials(),
        judge: Judge = unasked,
        now: Callable[[], dt.datetime] | None = None,
        triage_judge: TriageJudgeController | None = None,
        final_interval: FinalIntervalController | None = None,
        final_lane_ids: tuple[str, ...] = ("lane-1",),
    ) -> None:
        self.window = window
        self.dials = dials
        self._recorder = recorder
        self._authority = authority
        self._judge = judge
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._triage_judge = triage_judge
        self._final_interval = final_interval
        self._final_lane_ids = final_lane_ids
        state = Path(recorder.run_dir).parents[1]
        self._journal = OrderJournal(
            state,
            recorder.run_id,
            recorder.redactor,
            timestamp=lambda: self._now().isoformat(),
        )
        self._journal.resume_pending()
        self._held: dict[tuple[str, int | str], Pick] = {}

    def acquire(
        self, snapshot, *, leased: Collection[int | str] = (), solved: Collection[int | str] = ()
    ) -> Pick | None:
        if len(self._held) >= self.dials.concurrency:
            raise ValueError("[order] release an acquired Attempt before exceeding Lane capacity")
        if self._scoreable_left() <= 0:
            return None
        if pending := self._unconsumed_pick(snapshot):
            self._held[_typed_key(pending.challenge.challenge_id)] = pending
            return pending
        authority = self._authority()
        self._ensure_durable_tiers(snapshot)
        boundary_at, clock_digest = self._record_boundary_clock(authority)
        facts = self._run_facts(authority, boundary_at, clock_digest)
        facts = self._record_boundary_facts(facts)
        decision = decide_order(OrderInput(authority, facts, _policy_dials(self.dials)))
        published = self._journal.publish(decision)
        from solver.order_receipt import write_decision_receipt

        write_decision_receipt(Path(self._recorder.run_dir).parents[1], self._recorder.run_id, published.publication_id)
        if decision.grant is None:
            return None
        pick = _project_pick(snapshot, decision.document(), published.publication_id)
        self._held[_typed_key(pick.challenge.challenge_id)] = pick
        return pick

    def release(self, outcome: Ended) -> None:
        self._held.pop(_typed_key(outcome.challenge_id), None)

    def out_of_time(self) -> bool:
        return self._scoreable_left() < self.dials.floor_seconds

    def _scoreable_left(self) -> float:
        return self.window.left(self._now()) - self.dials.tail_seconds

    def _unconsumed_pick(self, snapshot) -> Pick | None:
        try:
            replayed = replay_order_publication(Path(self._recorder.run_dir).parents[1], self._recorder.run_id)
        except LookupError:
            return None
        grant = replayed.boundary.get("decision", {}).get("grant")
        if not isinstance(grant, dict):
            return None
        generation_id = str(grant.get("generation_id", ""))
        state = next(
            (
                item
                for item in self._recorder.generations.projection().generations
                if item.generation_id == generation_id
            ),
            None,
        )
        if state is not None and not state.active:
            return None
        from solver.order_receipt import write_decision_receipt

        write_decision_receipt(Path(self._recorder.run_dir).parents[1], self._recorder.run_id, replayed.publication_id)
        pick = _project_pick(
            snapshot, {**replayed.boundary["decision"], "rows": list(replayed.rows)}, replayed.publication_id
        )
        return None if _typed_key(pick.challenge.challenge_id) in self._held else pick

    def _run_facts(
        self,
        authority: OrderAuthority,
        boundary_at: dt.datetime,
        clock_digest: str,
    ) -> OrderRunFacts:
        events = self._recorder.event_store.events()
        boundaries = [
            event
            for event in events
            if event.event_type == ORDER_PUBLICATION_RECORDED and event.payload.get("record") == "boundary"
        ]
        projection = self._recorder.generations.projection()
        _, priors = _prior_rows(Path(self._recorder.run_dir).parents[1], self._recorder.run_id)
        categories = {
            (item.challenge_id.kind, item.challenge_id.value): item.category for item in authority.snapshot.challenges
        }
        attempts = _attempt_facts(events, categories)
        active = _active_grants(events, projection)
        durable_tiers, admission, _ = _order_input_facts(events)
        window_path = Path(self._recorder.run_dir) / WINDOW
        try:
            window_raw = window_path.read_bytes()
            window_document = json.loads(window_raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("[order] the canonical Run window is unavailable") from error
        if window_document != {
            "opened_at": self.window.opened_at.isoformat(),
            "ends_at": self.window.ends_at.isoformat(),
        }:
            raise ValueError("[order] the supplied Run window differs from its durable stamp")
        cutoff = self.window.ends_at - dt.timedelta(seconds=self.dials.tail_seconds)
        try:
            prior_publication = replay_order_publication(Path(self._recorder.run_dir).parents[1], self._recorder.run_id)
            prior_fence = OrderFence(
                prior_publication.publication_id,
                prior_publication.decision_digest,
                prior_publication.events[-1].event_digest,
            )
        except LookupError:
            prior_fence = OrderFence()
        return OrderRunFacts(
            boundary_id=f"order-boundary-{len(boundaries) + 1:06d}",
            boundary_at=boundary_at,
            final_submission_cutoff=cutoff,
            window_seconds=max(0, int((cutoff - self.window.opened_at).total_seconds())),
            next_generation=_next_generation(projection.generations),
            window_digest=digest_bytes(window_raw),
            generation_projection_digest=projection.digest,
            intake_event_digest=authority.fence.event_digest,
            submission_tail_seconds=int(self.dials.tail_seconds),
            boundary_clock_event_digest=clock_digest,
            # v1's in-memory Lease collection has no canonical writer. An active acquired grant is
            # reconstructed above and frozen; no unverified Lease hint is promoted into Order.
            leased=(),
            solved=_solved_facts(events, authority),
            active_grants=active,
            attempts=attempts,
            prior_crowd=priors,
            durable_tiers=durable_tiers,
            admission=admission,
            prior_order_fence=prior_fence,
            final_chance_available=(
                any(self._final_interval.chance_available(lane_id) for lane_id in self._final_lane_ids)
                if self._final_interval is not None
                else False
            ),
        )

    def _record_boundary_clock(self, authority: OrderAuthority) -> tuple[dt.datetime, str]:
        observed_at = self._now()
        if observed_at.tzinfo is None or observed_at.utcoffset() != dt.timedelta(0):
            raise ValueError("[order] boundary clock must be UTC and timezone-aware")
        events = self._recorder.event_store.events()
        prior = [
            dt.datetime.fromisoformat(str(event.payload["ts"]))
            for event in events
            if event.event_type == ORDER_INPUT_RECORDED
            and event.payload.get("record") == OrderInputRecord.BOUNDARY_CLOCK.value
        ]
        if observed_at < authority.snapshot.observed_at or observed_at < self.window.opened_at:
            raise ValueError("[order] boundary clock precedes its canonical sources")
        if prior and observed_at < prior[-1]:
            raise ValueError("[order] boundary clock moved backwards")
        document = {"schema_version": 1, "record": "boundary-clock", "observed_at": observed_at.isoformat()}
        body = canonical_bytes(document)
        serial = len(prior) + 1
        event = self._recorder.event_store.append(
            OrderInputRecorded(
                f"order-input:boundary-clock:{serial:06d}",
                OrderInputRecord.BOUNDARY_CLOCK,
                observed_at.isoformat(),
            ),
            body=body,
        )
        return observed_at, event.event_digest

    def _ensure_durable_tiers(self, snapshot) -> None:
        events = self._recorder.event_store.events()
        _, _, held = _order_input_facts(events)
        arrived = [item for item in snapshot.unsolved if _typed_key(item.challenge_id) not in held]
        if not arrived:
            return
        before = {event.event_digest for event in events}
        answers = []

        def observed_judge(prompt):
            answer = self._judge(prompt)
            answers.append((prompt, answer))
            return answer

        if self._triage_judge is not None:
            typed_evidence = tuple(
                TriageEvidence(
                    "challenge-arrival",
                    str(item.challenge_id),
                    digest_bytes(canonical_bytes({"challenge_id": str(item.challenge_id)})),
                    f"{item.name[:200]} | {item.category[:80]} | solves={item.solves}",
                )
                for item in arrived
            )
            digest = triage_evidence_digest(typed_evidence)
            batch_id = f"triage-arrivals:{digest}"
            outcome = self._triage_judge.evaluate(
                TriageJudgeRequest(
                    batch_id,
                    digest,
                    typed_evidence,
                    TriageProposal("triage", "", 1.0, "deterministic-v1"),
                    (self._now() + dt.timedelta(minutes=1)).isoformat(),
                )
            )

            def accepted_triage_judgement(prompt):
                answers.append((prompt, outcome.proposal.value))
                return outcome.proposal.value

            judgements = triage(arrived, recorder=self._recorder, judge=accepted_triage_judgement)
        else:
            judgements = triage(arrived, recorder=self._recorder, judge=observed_judge)
        evidence = [
            event.event_digest
            for event in self._recorder.event_store.events()
            if event.event_digest not in before and event.event_type == "observation.recorded"
        ]
        tiers = [
            {
                "challenge_id": _typed(item.challenge_id).document(),
                "tier": item.tier,
                "provenance": item.provenance,
            }
            for item in judgements
            if item.provenance == JUDGED
        ]
        judgement_event_digest = ""
        if answers:
            if len(answers) != 1:
                raise ValueError("one Triage batch produced multiple model answers")
            prompt, answer = answers[0]
            candidates = [item.challenge_id for item in judgements if item.provenance in {JUDGED, UNJUDGED}]
            asked = [_typed(item).document() for item in judgeable_ids(candidates)]
            judgement_body = canonical_bytes(
                {
                    "schema_version": 1,
                    "asked": asked,
                    "response": answer,
                    "observation_event_digests": evidence,
                }
            )
            judgement = self._recorder.event_store.append(
                TriageJudgementRecorded(
                    f"triage-judgement:{digest_bytes(judgement_body)}",
                    digest_bytes(prompt.encode()),
                    self._now().isoformat(),
                ),
                body=judgement_body,
            )
            judgement_event_digest = judgement.event_digest
        if tiers and not judgement_event_digest:
            raise ValueError("model-judged Order tiers have no typed canonical judgement")
        body = canonical_bytes(
            {
                "schema_version": 1,
                "record": "durable-tiers",
                "assessed": [_typed(item.challenge_id).document() for item in arrived],
                "evidence_event_digests": evidence,
                "judgement_event_digest": judgement_event_digest,
                "tiers": tiers,
            }
        )
        identity = "order-input:durable-tiers:" + digest_bytes(body)
        self._recorder.event_store.append(
            OrderInputRecorded(identity, OrderInputRecord.DURABLE_TIERS, self._now().isoformat()), body=body
        )

    def _record_boundary_facts(self, facts: OrderRunFacts) -> OrderRunFacts:
        body = canonical_bytes(facts.boundary_source_document())
        event = self._recorder.event_store.append(
            OrderInputRecorded(
                f"order-input:boundary:{facts.boundary_id}",
                OrderInputRecord.BOUNDARY_FACTS,
                facts.boundary_at.isoformat(),
            ),
            body=body,
        )
        return replace(facts, boundary_fact_event_digest=event.event_digest)


def _policy_dials(dials: Dials) -> PolicyDials:
    return PolicyDials(
        w_crowd=Fraction(str(dials.w_solves)),
        w_value=Fraction(str(dials.w_value)),
        w_lease=Fraction(str(dials.w_lease)),
        w_progress=Fraction(str(dials.w_progress)),
        w_spend=Fraction(str(dials.w_spend)),
        knee_seconds=int(dials.knee_seconds),
        floor_seconds=int(dials.floor_seconds),
        tier_cap=dials.tier_cap,
        tier_step=Fraction(str(dials.tier_step)),
        ceiling_fraction=Fraction(str(dials.ceiling_fraction)),
        attempts_full=dials.attempts_full,
        checkpoints_full=dials.checkpoints_full,
        explore_every=dials.explore_every,
        concurrency=dials.concurrency,
    )


def _typed(value: int | str):
    from solver.intake_qualification import TypedId

    return TypedId.parse(value)


def _typed_key(value: int | str) -> tuple[str, int | str]:
    item = _typed(value)
    return item.kind, item.value


def _solved_facts(events, authority: OrderAuthority):
    solved = {
        (item.challenge_id.kind, item.challenge_id.value): item.challenge_id
        for item in authority.snapshot.challenges
        if item.solved.outcome == "answered" and item.solved.value is True
    }
    for event in events:
        if (
            event.event_type != WORK_GENERATION_RECORDED
            or event.payload.get("record") != GenerationRecord.CLOSE.value
            or event.payload.get("disposition") != "complete"
        ):
            continue
        try:
            kind, raw = str(event.payload["work_id"]).split(":", 1)
            value: int | str = int(raw) if kind == "integer" else raw
            typed = _typed(value)
            if typed.kind != kind:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        solved[(typed.kind, typed.value)] = typed
    return tuple(solved.values())


def _project_pick(snapshot, decision: dict[str, object], publication_id: str) -> Pick:
    grant = decision.get("grant")
    if not isinstance(grant, dict):
        raise ValueError("Order decision carries no grant")
    challenge_document = grant.get("challenge_id")
    if not isinstance(challenge_document, dict):
        raise ValueError("Order grant challenge identity is invalid")
    kind, raw = challenge_document.get("type"), challenge_document.get("value")
    challenge_id: int | str = int(str(raw)) if kind == "integer" else str(raw)
    challenge = next(
        (item for item in snapshot.unsolved if _typed_key(item.challenge_id) == _typed_key(challenge_id)), None
    )
    if challenge is None:
        raise ValueError("Order grant is absent from the derived v1 Intake view")
    rows = decision.get("rows")
    if not isinstance(rows, list):
        raise ValueError("Order decision rows are invalid")
    next(item for item in rows if item.get("challenge_id") == challenge_document)
    deadline = dt.datetime.fromisoformat(str(grant["deadline"]))
    return Pick(
        challenge=challenge,
        budget_s=int(grant["budget_s"]),
        deadline=deadline,
        tier=int(grant["tier"]),
        attempt_sequence=int(str(grant["attempt_id"]).rsplit("-", 1)[-1]),
        order_ranks={str(item["challenge_id"]["value"]): int(item["rank"]) for item in rows},
        working_set=tuple(
            int(item["value"]) if item["type"] == "integer" else str(item["value"])
            for item in decision.get("working_set", [])
        ),
        exploring=bool(decision.get("grant_exploring", False)),
        order_attempt_id=str(grant["attempt_id"]),
        order_generation_id=str(grant["generation_id"]),
        order_publication_id=publication_id,
    )


def _prior_rows(state: Path, run_id: str, publication_id: str | None = None):
    try:
        replayed = replay_order_publication(state, run_id, publication_id)
    except LookupError:
        return {}, ()
    decision = replayed.boundary["decision"]
    boundary_event = replayed.events[-1]
    boundary_at = dt.datetime.fromisoformat(str(decision["boundary_at"]))
    categories = {
        (str(row["challenge_id"]["type"]), _id_value(row["challenge_id"])): str(row["category"])
        for row in replayed.rows
    }
    priors = []
    for row in replayed.rows:
        crowd = row["crowd"]
        velocity = crowd.get("velocity")
        from fractions import Fraction
        from solver.intake_qualification import TypedId

        typed = TypedId(str(row["challenge_id"]["type"]), _id_value(row["challenge_id"]))
        priors.append(
            PriorCrowd(
                typed,
                str(crowd["state"]),
                consecutive_passes=2 if crowd["state"] == "qualified" else 1 if crowd["state"] == "provisional" else 0,
                consecutive_failures=1 if crowd.get("reason") == "last-qualified-held" else 0,
                velocity=Fraction(int(velocity["numerator"]), int(velocity["denominator"])) if velocity else None,
                last_qualified_tier=int(row["base_tier"]) if crowd["state"] == "qualified" else None,
                source_event_digest=boundary_event.event_digest,
                qualified_at=boundary_at if crowd["state"] == "qualified" else None,
            )
        )
    return categories, tuple(priors)


def _id_value(document: dict[str, object]) -> int | str:
    return int(str(document["value"])) if document["type"] == "integer" else str(document["value"])


def _attempt_facts(events, categories):
    acquired = {}
    answer = []
    checkpoints = {}
    for event in events:
        if event.event_type == "observation.recorded" and event.payload.get("checkpoint"):
            checkpoints.setdefault(str(event.payload.get("attempt_id", "")), []).append(event.event_digest)
    for event in events:
        if event.event_type != WORK_GENERATION_RECORDED:
            continue
        record = event.payload.get("record")
        if record == GenerationRecord.ACQUIRE.value:
            acquired[event.payload["generation_id"]] = event
        elif record == GenerationRecord.CLOSE.value and event.payload["generation_id"] in acquired:
            opened = acquired[event.payload["generation_id"]]
            try:
                began = dt.datetime.fromisoformat(str(opened.payload["ts"]))
                ended = dt.datetime.fromisoformat(str(event.payload["ts"]))
                seconds_ms = max(0, round((ended - began).total_seconds() * 1000))
                kind, raw = str(event.payload["work_id"]).split(":", 1)
                value: int | str = int(raw) if kind == "integer" else raw
            except (ValueError, TypeError):
                continue
            from solver.intake_qualification import TypedId

            key = (kind, value)
            attempt_id = str(event.payload["attempt_id"])
            checkpoint_digests = tuple(checkpoints.get(attempt_id, ()))
            answer.append(
                AttemptFact(
                    TypedId(kind, value),
                    attempt_id,
                    categories.get(key, ""),
                    seconds_ms,
                    len(checkpoint_digests),
                    str(event.payload["disposition"]),
                    event.event_digest,
                    checkpoint_digests,
                )
            )
    return tuple(answer)


def _active_grants(events, projection):
    active_ids = {state.generation_id for state in projection.generations if state.active}
    grants = []
    for event in events:
        if event.event_type != ORDER_PUBLICATION_RECORDED or event.payload.get("record") != "boundary":
            continue
        body = json.loads(event.body)
        grant = body.get("decision", {}).get("grant")
        if isinstance(grant, dict) and grant.get("generation_id") in active_ids:
            document = grant["challenge_id"]
            from solver.intake_qualification import TypedId

            grants.append(
                ActiveGrant(
                    str(grant["attempt_id"]),
                    str(grant["generation_id"]),
                    TypedId(str(document["type"]), _id_value(document)),
                    int(grant["tier"]),
                    int(grant["budget_s"]),
                    dt.datetime.fromisoformat(str(grant["deadline"])),
                    event.event_digest,
                )
            )
    return tuple(grants)


def _order_input_facts(events):
    tiers = {}
    admission = {}
    assessed = set()
    event_by_digest = {event.event_digest: event for event in events}
    for event in events:
        if event.event_type != ORDER_INPUT_RECORDED:
            continue
        try:
            document = json.loads(event.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("canonical Order input fact is unreadable") from error
        if canonical_bytes(document) != event.body:
            raise ValueError("canonical Order input fact body is not canonical")
        record = event.payload.get("record")
        if record == OrderInputRecord.DURABLE_TIERS.value:
            if (
                set(document)
                != {
                    "schema_version",
                    "record",
                    "assessed",
                    "evidence_event_digests",
                    "judgement_event_digest",
                    "tiers",
                }
                or document.get("schema_version") != 1
            ):
                raise ValueError("durable Order tier fact schema is unsupported")
            assessed.update(
                (_typed_from_document(item).kind, _typed_from_document(item).value) for item in document["assessed"]
            )
            evidence = document["evidence_event_digests"]
            if any(
                digest not in event_by_digest or event_by_digest[digest].event_type != "observation.recorded"
                for digest in evidence
            ):
                raise ValueError("durable Order tier evidence is not canonical model observation")
            judgement_digest = str(document["judgement_event_digest"])
            judgement = event_by_digest.get(judgement_digest)
            parsed = {}
            if judgement_digest:
                if judgement is None or judgement.event_type != TRIAGE_JUDGEMENT_RECORDED:
                    raise ValueError("durable Order tier judgement is absent")
                judgement_body = json.loads(judgement.body)
                asked = [_typed_from_document(item) for item in judgement_body["asked"]]
                parsed = parse_judged_tiers(str(judgement_body["response"]), tuple(item.value for item in asked))
                if judgement_body.get("observation_event_digests") != evidence:
                    raise ValueError("Triage judgement observations differ from Order input")
            for item in document["tiers"]:
                if item.get("provenance") != JUDGED or not judgement_digest:
                    raise ValueError("durable Order tier is not a model judgement")
                typed = _typed_from_document(item["challenge_id"])
                if typed not in asked:
                    raise ValueError("durable Order tier was not among the exact typed IDs asked")
                if parsed.get(typed.value) != int(item["tier"]):
                    raise ValueError("durable Order tier differs from the canonical model answer")
                fact = DurableTier(typed, int(item["tier"]), judgement_digest)
                key = (typed.kind, typed.value)
                if key in tiers and tiers[key].tier != fact.tier:
                    raise ValueError("durable Order tier changed after publication")
                tiers[key] = fact
        elif record == OrderInputRecord.ADMISSION.value:
            if set(document) != {"schema_version", "record", "admission"} or document.get("schema_version") != 1:
                raise ValueError("Order admission fact schema is unsupported")
            for item in document["admission"]:
                typed = _typed_from_document(item["challenge_id"])
                fact = AdmissionFact(
                    typed,
                    bool(item["admissible"]),
                    str(item["reason"]),
                    str(item["evidence_digest"]),
                    dt.datetime.fromisoformat(str(item["safe_deadline"])) if item.get("safe_deadline") else None,
                )
                admission[(typed.kind, typed.value)] = fact
    return tuple(tiers.values()), tuple(admission.values()), assessed


def _typed_from_document(document):
    from solver.intake_qualification import TypedId

    return TypedId(str(document["type"]), _id_value(document))


def record_admission_facts(recorder: Recorder, facts: tuple[AdmissionFact, ...], *, at: dt.datetime) -> str:
    """Publish explicit collaborator-owned admission facts for later Order boundaries."""

    available = {event.event_digest for event in recorder.event_store.events()}
    if any(not fact.admissible and fact.evidence_digest not in available for fact in facts):
        raise ValueError("inadmissible Order fact has no canonical evidence")
    body = canonical_bytes(
        {"schema_version": 1, "record": "admission", "admission": [fact.document() for fact in facts]}
    )
    event = recorder.event_store.append(
        OrderInputRecorded("order-input:admission:" + digest_bytes(body), OrderInputRecord.ADMISSION, at.isoformat()),
        body=body,
    )
    return event.event_digest


def _next_generation(states) -> int:
    serials = [int(state.generation_id.rsplit("-", 1)[-1]) for state in states]
    return max(serials, default=0) + 1


__all__ = ["CanonicalScheduler", "record_admission_facts"]
