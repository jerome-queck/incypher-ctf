"""Load and rerun the controlled Order-policy qualification aggregate."""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.intake_qualification import ChallengeSnapshot, IntakeSnapshot, PresentValue, PriorFence, TypedId, ValueFact
from solver.order_policy import (
    ActiveGrant,
    AdmissionFact,
    CrowdSource,
    OrderAuthority,
    OrderInput,
    OrderRunFacts,
    PolicyDials,
    decide_order,
)
from solver.order_policy_proof_lock import CONTROLLED_PROOF_DIGEST

PROOF_FILENAME = "order-policy.proof.json"
PROOF_TYPE = "order-policy-qualification"
PRODUCER = "controlled-order-qualification"
UTC = dt.timezone.utc
START = dt.datetime(2026, 9, 22, 1, 0, tzinfo=UTC)


def proof_path() -> Path:
    return Path(__file__).with_name(PROOF_FILENAME)


def subject_digest() -> str:
    package = Path(__file__).parent
    names = (
        "order_policy.py",
        "order_contracts.py",
        "order_input_contracts.py",
        "order_journal.py",
        "order_runtime.py",
        "order_receipt.py",
    )
    return digest_bytes(canonical_bytes({name: digest_bytes((package / name).read_bytes()) for name in names}))


def scenario_decisions() -> dict[str, str]:
    unit = _input((100, 1), unsettled=2)
    crowd = _crowd_input()
    final = _input((100,), minute=116, cutoff_minute=120)
    typed = _typed_tie_input()
    active = _active_freeze_input()
    working = _working_set_input()
    return {
        "unit-fallback": decide_order(unit).digest,
        "qualified-crowd": decide_order(crowd).digest,
        "floor-and-final-interval": decide_order(final).digest,
        "typed-id-ties": decide_order(typed).digest,
        "active-grant-freeze": decide_order(active).digest,
        "working-set-no-park": decide_order(working).digest,
    }


def load_controlled_proof() -> dict[str, object]:
    path = proof_path()
    try:
        raw = path.read_bytes()
        proof = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("controlled Order-policy proof is unavailable") from error
    if digest_bytes(raw) != CONTROLLED_PROOF_DIGEST:
        raise ValueError("controlled Order-policy proof differs from its reviewed artifact")
    if not isinstance(proof, dict) or raw != canonical_bytes(proof) + b"\n":
        raise ValueError("controlled Order-policy proof is not canonical JSON")
    if (
        proof.get("schema_version") != 1
        or proof.get("proof_type") != PROOF_TYPE
        or proof.get("kind") != "order-policy"
        or proof.get("producer") != PRODUCER
        or proof.get("verdict") != "pass"
    ):
        raise ValueError("controlled Order-policy proof has an unsupported contract")
    if proof.get("subject_digest") != subject_digest() or proof.get("policy_digest") != PolicyDials().digest:
        raise ValueError("controlled Order-policy proof does not cover this implementation")
    observed = {item["scenario_id"]: item["decision_digest"] for item in proof.get("scenarios", [])}
    if observed != scenario_decisions():
        raise ValueError("controlled Order-policy scenarios no longer reproduce")
    return proof


def _challenge(index: int, value: int, solves: int = 0, *, unsettled: bool = False) -> ChallengeSnapshot:
    return ChallengeSnapshot(
        TypedId.parse(index),
        f"challenge-{index}",
        "web",
        "standard",
        "",
        ValueFact(value, "list", "unsettled" if unsettled else "answered", START),
        ValueFact(solves, "list", "answered", START),
        ValueFact(False, "list", "answered", START),
        PresentValue("value", index),
        0,
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        PresentValue("missing"),
        (),
        f"{index:064x}",
    )


def _snapshot(minute: int, values: tuple[int, ...], solves: tuple[int, ...], *, unsettled: int = 0) -> IntakeSnapshot:
    at = START + dt.timedelta(minutes=minute)
    challenges = tuple(
        _challenge(index, value, solves[index - 1], unsettled=index == unsettled)
        for index, value in enumerate(values, 1)
    )
    scoreboard = (
        {"source": "scoreboard", "observed_at": at.isoformat(), "rank": 1, "name": "team", "score": sum(solves)},
    )
    return IntakeSnapshot(f"snapshot-{minute}", "a" * 64, at, challenges, "not-empty", scoreboard=scoreboard)


def _request(
    current: IntakeSnapshot, history: tuple[IntakeSnapshot, ...], minute: int, cutoff_minute: int
) -> OrderInput:
    fence = PriorFence("a" * 64, f"intake:{current.snapshot_id}", "b" * 64, current.digest)
    authority = OrderAuthority(current, fence, (current, *history), 100_000, CrowdSource(True, False, "c" * 64))
    run = OrderRunFacts(
        "boundary", START + dt.timedelta(minutes=minute), START + dt.timedelta(minutes=cutoff_minute), 7200, 1
    )
    return OrderInput(authority, run)


def _input(values: tuple[int, ...], *, unsettled: int = 0, minute: int = 10, cutoff_minute: int = 120) -> OrderInput:
    current = _snapshot(10, values, tuple(0 for _ in values), unsettled=unsettled)
    return _request(current, (), minute, cutoff_minute)


def _crowd_input() -> OrderInput:
    values = (100, 100, 100, 100)
    first = _snapshot(0, values, (0, 0, 0, 0))
    second = _snapshot(5, values, (1, 2, 3, 0))
    current = _snapshot(10, values, (2, 4, 7, 0))
    return _request(current, (second, first), 10, 120)


def _typed_tie_input() -> OrderInput:
    integer = _challenge(2, 100)
    string = replace(integer, challenge_id=TypedId("string", "2"), name="challenge-string-2")
    current = IntakeSnapshot("snapshot-typed", "a" * 64, START, (string, integer), "not-empty")
    return _request(current, (), 10, 120)


def _active_freeze_input() -> OrderInput:
    request = _input((1000, 1))
    active = ActiveGrant(
        "i:1-1",
        "generation-000001",
        TypedId.parse(1),
        4,
        900,
        START + dt.timedelta(minutes=30),
        "e" * 64,
    )
    return replace(request, run=replace(request.run, active_grants=(active,), next_generation=2))


def _working_set_input() -> OrderInput:
    current = _snapshot(0, (1000, 1), (0, 0))
    request = _request(current, (), 1, 11)
    admission = AdmissionFact(TypedId.parse(1), False, "unsafe", "d" * 64)
    return replace(request, run=replace(request.run, admission=(admission,)))


__all__ = ["load_controlled_proof", "proof_path", "scenario_decisions", "subject_digest"]
