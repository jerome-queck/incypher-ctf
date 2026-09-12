"""Runtime Order receipts and the manifest-linked controlled qualification aggregate."""

from __future__ import annotations

import hashlib
import json
import datetime as dt
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.order_journal import replay_order_publication
from solver.order_policy import decide_order, order_input_from_document

SCHEMA_VERSION = 1
DECISION_RECEIPT_TYPE = "order-decision"
QUALIFICATION_RECEIPT_TYPE = "order-policy"
MANIFEST_RECEIPT_REF = "receipt:order-policy"
MANIFEST_ROW_ID = "core.triage-order"
RECEIPTS_DIRECTORY = "order-receipts"
MAX_RECEIPT_BYTES = 1024 * 1024


def write_decision_receipt(state: Path, run_id: str, publication_id: str) -> Path:
    document = _decision_receipt_document(Path(state), run_id, publication_id)
    path = (
        Path(state)
        / "runs"
        / run_id
        / "canonical"
        / RECEIPTS_DIRECTORY
        / (hashlib.sha256(publication_id.encode()).hexdigest() + ".json")
    )
    body = canonical_bytes(document) + b"\n"
    if len(body) > MAX_RECEIPT_BYTES:
        raise ValueError("Order decision receipt exceeds one MiB")
    if path.exists():
        if path.read_bytes() != body:
            raise ValueError("immutable Order decision receipt already exists with different content")
        return path
    atomic_write(path, body)
    return path


def _decision_receipt_document(state: Path, run_id: str, publication_id: str) -> dict[str, object]:
    replayed = replay_order_publication(state, run_id, publication_id)
    decision = replayed.boundary["decision"]
    _verify_decision_semantics(Path(state), run_id, decision, replayed.rows, replayed.decision_digest)
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": DECISION_RECEIPT_TYPE,
        "run_id": run_id,
        "publication_id": publication_id,
        "decision_digest": replayed.decision_digest,
        "snapshot_digest": decision["snapshot_digest"],
        "intake_fence": decision["intake_fence"],
        "policy_digest": decision["policy_digest"],
        "input_digest": decision["input_digest"],
        "order_value_basis": decision["order_value_basis"],
        "fallback_reason": decision["fallback_reason"],
        "working_set": decision["working_set"],
        "grant": decision["grant"],
        "row_count": len(replayed.rows),
        "row_chunk_digests": replayed.boundary["row_chunk_digests"],
        "events": [
            {"sequence": event.sequence, "event_digest": event.event_digest, "blob_digest": event.blob_digest}
            for event in replayed.events
        ],
        "manifest_link": None,
    }


def verify_decision_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Order decision receipt cannot be read") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise ValueError("Order decision receipt is not canonical JSON")
    if supplied.get("schema_version") != SCHEMA_VERSION or supplied.get("receipt_type") != DECISION_RECEIPT_TYPE:
        raise ValueError("Order decision receipt schema is unsupported")
    if supplied.get("manifest_link") is not None:
        raise ValueError("runtime Order decision receipts cannot satisfy the candidate manifest")
    if receipt_path.parent.name != RECEIPTS_DIRECTORY or receipt_path.parent.parent.name != "canonical":
        raise ValueError("Order decision receipt path is invalid")
    run_id = str(supplied.get("run_id", ""))
    if receipt_path.parents[2].name != run_id or receipt_path.parents[3].name != "runs":
        raise ValueError("Order decision receipt path differs from its Run")
    try:
        expected = _decision_receipt_document(receipt_path.parents[4], run_id, str(supplied.get("publication_id", "")))
    except (LookupError, ValueError) as error:
        raise ValueError("Order decision receipt differs from canonical publication") from error
    if supplied != expected:
        raise ValueError("Order decision receipt differs from canonical publication")
    expected_name = hashlib.sha256(str(supplied["publication_id"]).encode()).hexdigest() + ".json"
    if receipt_path.name != expected_name:
        raise ValueError("Order decision receipt filename differs from its publication")
    return receipt_path


def _verify_decision_semantics(
    state: Path,
    run_id: str,
    decision_document: Mapping[str, object],
    rows: tuple[dict[str, object], ...],
    decision_digest: str,
) -> None:
    sealed = decision_document.get("input_document")
    from solver.event_store import EventStore

    all_events = EventStore(state, run_id=run_id).events()
    terminal = next(
        (
            event
            for event in all_events
            if event.event_type == "order-publication.recorded"
            and event.payload.get("record") == "boundary"
            and event.payload.get("decision_digest") == decision_digest
        ),
        None,
    )
    if terminal is None:
        raise ValueError("Order terminal boundary is absent")
    events = [event for event in all_events if event.sequence <= terminal.sequence]
    snapshots = _canonical_snapshots(events, sealed)
    request = order_input_from_document(sealed, snapshots)
    if request.digest != decision_document.get("input_digest"):
        raise ValueError("Order input digest differs from its sealed input")
    recomputed = decide_order(request).document()
    published = {**dict(decision_document), "rows": list(rows)}
    if recomputed != published or digest_bytes(canonical_bytes(published)) != decision_digest:
        raise ValueError("Order decision does not recompute from its sealed input")

    by_digest = {event.event_digest: event for event in events}
    intake = by_digest.get(request.authority.fence.event_digest)
    if (
        intake is None
        or intake.event_type != "intake-decision.recorded"
        or intake.payload.get("snapshot_digest") != request.authority.snapshot.digest
    ):
        raise ValueError("Order input does not name a verified canonical Intake boundary")
    try:
        intake_body = json.loads(intake.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Order Intake boundary body is unreadable") from error
    if intake_body != request.authority.snapshot.document():
        raise ValueError("Order snapshot differs from its canonical Intake boundary")
    starts = [
        event
        for event in events
        if event.event_type == "intake-decision.recorded"
        and event.payload.get("record") == "started"
        and event.payload.get("attempt_id") == intake.payload.get("attempt_id")
    ]
    if len(starts) != 1:
        raise ValueError("Order Intake boundary has no unique canonical contract")
    from solver.intake_qualification import contract_from_document

    try:
        contract = contract_from_document(json.loads(starts[0].body)["contract"])
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("Order Intake contract is unreadable") from error
    if (
        contract.digest != starts[0].payload.get("contract_digest")
        or contract.digest != intake.payload.get("contract_digest")
        or contract.profile_digest != request.authority.snapshot.profile_digest
    ):
        raise ValueError("Order Intake contract differs from its canonical boundary")
    from solver.coherent_intake import crowd_source_from_contract

    expected_crowd = crowd_source_from_contract(state, run_id, contract)
    if (
        request.authority.effective_max_challenges != contract.max_challenges
        or request.authority.crowd_source.document() != expected_crowd.document()
    ):
        raise ValueError("Order authority differs from its verified Intake contract")

    source_digests = (
        {item.source_event_digest for item in request.run.attempts if item.source_event_digest}
        | {digest for item in request.run.attempts for digest in item.checkpoint_event_digests}
        | {item.source_event_digest for item in request.run.durable_tiers if item.source_event_digest}
        | {item.evidence_digest for item in request.run.admission if item.evidence_digest}
        | {item.order_event_digest for item in request.run.active_grants if item.order_event_digest}
        | {item.source_event_digest for item in request.run.prior_crowd if item.source_event_digest}
        | ({request.run.boundary_fact_event_digest} if request.run.boundary_fact_event_digest else set())
        | ({request.run.boundary_clock_event_digest} if request.run.boundary_clock_event_digest else set())
    )
    if source_digests - by_digest.keys():
        raise ValueError("Order input fact source is absent from the canonical Run")
    _verify_run_facts(state, run_id, request, events)


def _verify_run_facts(state: Path, run_id: str, request, events) -> None:
    from solver.order_runtime import (
        _active_grants,
        _attempt_facts,
        _next_generation,
        _order_input_facts,
        _prior_rows,
        _solved_facts,
    )
    from solver.work_generation import _project_events

    categories = {
        (item.challenge_id.kind, item.challenge_id.value): item.category
        for item in request.authority.snapshot.challenges
    }
    attempts = _attempt_facts(events, categories)
    durable, admission, _ = _order_input_facts(events)
    by_digest = {event.event_digest: event for event in events}
    boundary_source = by_digest.get(request.run.boundary_fact_event_digest)
    if boundary_source is None:
        raise ValueError("Order boundary facts have no typed canonical source")
    source_events = [event for event in events if event.sequence < boundary_source.sequence]
    projection = _project_events(source_events, run_id)
    active = _active_grants(source_events, projection)
    if request.run.prior_order_fence.publication_id:
        _, prior = _prior_rows(state, run_id, request.run.prior_order_fence.publication_id)
    else:
        prior = ()
    if (
        boundary_source.event_type != "order-input.recorded"
        or boundary_source.payload.get("record") != "boundary-facts"
        or boundary_source.payload.get("ts") != request.run.boundary_at.isoformat()
    ):
        raise ValueError("Order boundary facts have no typed canonical source")
    try:
        source_document = json.loads(boundary_source.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Order boundary fact source is unreadable") from error
    if source_document != request.run.boundary_source_document():
        raise ValueError("Order boundary facts differ from their canonical source")
    window_path = state / "runs" / run_id / "window.json"
    try:
        window_raw = window_path.read_bytes()
        window = json.loads(window_raw)
        opened_at = dt.datetime.fromisoformat(str(window["opened_at"]))
        ends_at = dt.datetime.fromisoformat(str(window["ends_at"]))
    except (OSError, KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Order Run window source is unreadable") from error
    solved = _solved_facts(source_events, request.authority)
    clock_source = by_digest.get(request.run.boundary_clock_event_digest)
    expected_clock_body = {
        "schema_version": 1,
        "record": "boundary-clock",
        "observed_at": request.run.boundary_at.isoformat(),
    }
    if (
        clock_source is None
        or clock_source.sequence >= boundary_source.sequence
        or clock_source.event_type != "order-input.recorded"
        or clock_source.payload.get("record") != "boundary-clock"
        or clock_source.payload.get("ts") != request.run.boundary_at.isoformat()
        or clock_source.body != canonical_bytes(expected_clock_body)
    ):
        raise ValueError("Order boundary clock has no exact canonical observation")
    earlier_clocks = [
        dt.datetime.fromisoformat(str(event.payload["ts"]))
        for event in source_events
        if event.event_type == "order-input.recorded" and event.payload.get("record") == "boundary-clock"
    ]
    cutoff = ends_at - dt.timedelta(seconds=request.run.submission_tail_seconds)
    if (
        request.run.window_digest != digest_bytes(window_raw)
        or request.run.intake_event_digest != request.authority.fence.event_digest
        or request.run.generation_projection_digest != projection.digest
        or request.run.boundary_at < request.authority.snapshot.observed_at
        or request.run.boundary_at < opened_at
        or any(later < earlier for earlier, later in zip(earlier_clocks, earlier_clocks[1:], strict=False))
        or request.run.final_submission_cutoff != cutoff
        or request.run.window_seconds != max(0, int((cutoff - opened_at).total_seconds()))
        or request.run.leased
        or request.run.solved != solved
    ):
        raise ValueError("Order boundary facts differ from canonical Intake, Window, or generation sources")
    if request.run.next_generation != _next_generation(projection.generations):
        raise ValueError("Order next generation differs from its canonical projection")
    earlier_boundaries = [
        event
        for event in events
        if event.event_type == "order-publication.recorded"
        and event.payload.get("record") == "boundary"
        and event.sequence < boundary_source.sequence
    ]
    if request.run.prior_order_fence.publication_id:
        prior_event = next(
            (
                event
                for event in earlier_boundaries
                if event.payload.get("publication_id") == request.run.prior_order_fence.publication_id
            ),
            None,
        )
        if (
            prior_event is None
            or prior_event.event_digest != request.run.prior_order_fence.boundary_event_digest
            or prior_event.payload.get("decision_digest") != request.run.prior_order_fence.decision_digest
            or prior_event is not earlier_boundaries[-1]
        ):
            raise ValueError("Order prior fence differs from the canonical predecessor")
    elif earlier_boundaries:
        raise ValueError("Order genesis fence hides a canonical predecessor")
    comparisons = (
        (request.run.attempts, attempts, "Attempt"),
        (request.run.durable_tiers, durable, "durable tier"),
        (request.run.admission, admission, "admission"),
        (request.run.active_grants, active, "active grant"),
        (request.run.prior_crowd, prior, "prior crowd"),
    )
    for sealed, canonical, label in comparisons:
        if [item.document() for item in sealed] != [item.document() for item in canonical]:
            raise ValueError(f"Order {label} facts differ from canonical sources")


def _canonical_snapshots(events, sealed: object):
    if not isinstance(sealed, Mapping) or not isinstance(sealed.get("authority"), Mapping):
        raise ValueError("Order input authority descriptor is invalid")
    authority = sealed["authority"]
    wanted = {str(authority.get("snapshot_digest", ""))}
    history = authority.get("history_digests")
    if not isinstance(history, list) or len(history) > 3:
        raise ValueError("Order input history descriptor is invalid")
    wanted.update(str(item) for item in history)
    snapshots = {}
    from solver.intake_qualification import snapshot_from_document

    for event in events:
        if event.event_type != "intake-decision.recorded" or event.payload.get("record") != "settled":
            continue
        digest = str(event.payload.get("snapshot_digest", ""))
        if digest not in wanted:
            continue
        try:
            snapshot = snapshot_from_document(json.loads(event.body))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("canonical Intake snapshot is unreadable") from error
        if snapshot.digest != digest:
            raise ValueError("canonical Intake snapshot digest differs from its body")
        snapshots[digest] = snapshot
    if snapshots.keys() != wanted:
        raise ValueError("Order input history is absent from canonical Intake")
    return snapshots


def manifest_receipt() -> dict[str, str]:
    from solver.order_policy_proof import proof_path, load_controlled_proof

    load_controlled_proof()
    path = proof_path()
    return {"ref": MANIFEST_RECEIPT_REF, "kind": QUALIFICATION_RECEIPT_TYPE, "digest": digest_bytes(path.read_bytes())}


def link_manifest(manifest: Mapping[str, object]):
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt())


def capsule_contract():
    """Register the aggregate itself; scenario references travel inside it, not as runtime receipts."""

    from solver.evidence_capsule_contracts import ReceiptContract
    from solver.order_policy_proof import PRODUCER, load_controlled_proof

    def validate(receipt, _source):
        if dict(receipt) != load_controlled_proof():
            raise ValueError("Order-policy capsule receipt differs from the controlled aggregate")
        return ()

    return ReceiptContract(QUALIFICATION_RECEIPT_TYPE, SCHEMA_VERSION, PRODUCER, validate)


__all__ = [
    "MANIFEST_RECEIPT_REF",
    "capsule_contract",
    "link_manifest",
    "manifest_receipt",
    "verify_decision_receipt",
    "write_decision_receipt",
]
