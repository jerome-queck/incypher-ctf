import copy
import json
from pathlib import Path

import pytest

from solver.event_store import (
    EventStore,
    GenerationAuthority,
    GenerationClassification,
    GenerationDisposition,
    GenerationRecord,
    InvalidReceiptError,
    LifecycleRecorded,
    WorkGenerationRecorded,
    WORK_GENERATION_RECORDED,
)
from solver.event_store_contracts import RunClosed, TerminalDisposition
from solver.manifest import generate_manifest
from solver.redaction import Redactor
from solver.work_generation import (
    AuthorityDecision,
    GenerationConflict,
    GenerationFence,
    GenerationIdentity,
    IllegalGenerationTransition,
    UnknownGeneration,
)
from solver.work_generation_receipt import manifest_receipt, verify_receipt
from test_manifest import release_candidate_profile


def make_fence(state: Path, *, timestamp: str = "2026-09-11T00:00:00Z") -> GenerationFence:
    return GenerationFence(state, "run-1", Redactor({}), lambda: timestamp)


def test_acquire_assigns_a_global_durable_generation_identity(tmp_path):
    fence = make_fence(tmp_path)

    identity = fence.acquire("challenge-1", "attempt-1")

    assert identity == GenerationIdentity("generation-000001", "challenge-1", "attempt-1")
    projection = fence.projection()
    assert [state.generation_id for state in projection.generations] == ["generation-000001"]
    assert projection.active_by_work["challenge-1"].generation_id == "generation-000001"
    events = EventStore(tmp_path, run_id="run-1").events()
    assert [event.event_type for event in events] == [WORK_GENERATION_RECORDED]
    assert events[0].payload["record"] == "acquire"


def test_acquire_rejects_reusing_an_attempt_identity(tmp_path):
    fence = make_fence(tmp_path)
    fence.acquire("challenge-1", "attempt-1")
    fence.close("generation-000001", GenerationDisposition.COMPLETE)

    with pytest.raises(GenerationConflict):
        fence.acquire("challenge-2", "attempt-1")


def test_projection_active_index_is_read_only(tmp_path):
    fence = make_fence(tmp_path)
    fence.acquire("challenge-1", "attempt-1")

    with pytest.raises(TypeError):
        fence.projection().active_by_work["challenge-2"] = fence.projection().generations[0]  # type: ignore[index]


def test_close_is_durable_and_idempotent_only_for_the_same_disposition(tmp_path):
    fence = make_fence(tmp_path)
    identity = fence.acquire("challenge-1", "attempt-1")

    fence.close(identity.generation_id, GenerationDisposition.COMPLETE)
    before = fence.projection()
    fence.close(identity.generation_id, GenerationDisposition.COMPLETE)

    assert before == fence.projection()
    state = fence.projection().generations[0]
    assert state.disposition is GenerationDisposition.COMPLETE
    assert state.acquired_sequence == 1
    assert state.closed_sequence == 2
    assert [event.payload["disposition"] for event in EventStore(tmp_path, run_id="run-1").events()] == ["", "complete"]
    with pytest.raises(GenerationConflict):
        fence.close(identity.generation_id, GenerationDisposition.ABANDON)


def test_only_one_generation_for_work_is_active_and_successors_get_global_ids(tmp_path):
    fence = make_fence(tmp_path)
    first = fence.acquire("challenge-1", "attempt-1")

    with pytest.raises(GenerationConflict):
        fence.acquire("challenge-1", "attempt-2")

    fence.close(first.generation_id, GenerationDisposition.SUPERSEDE)
    successor = fence.acquire("challenge-1", "attempt-2")

    assert successor.generation_id == "generation-000002"
    assert fence.projection().active_by_work["challenge-1"].attempt_id == "attempt-2"


def test_replace_supersedes_before_the_successor_is_acquired(tmp_path):
    fence = make_fence(tmp_path)
    first = fence.acquire("challenge-1", "attempt-1")

    successor = fence.replace("challenge-1", "attempt-2")

    events = EventStore(tmp_path, run_id="run-1").events()
    assert [(event.payload["record"], event.payload["disposition"]) for event in events] == [
        ("acquire", ""),
        ("close", "supersede"),
        ("acquire", ""),
    ]
    assert fence.projection().generations[0].generation_id == first.generation_id
    assert fence.projection().generations[0].disposition is GenerationDisposition.SUPERSEDE
    assert fence.projection().active_by_work["challenge-1"].generation_id == successor.generation_id


def test_restart_interrupts_every_active_generation_before_replacement(tmp_path):
    fence = make_fence(tmp_path)
    first = fence.acquire("challenge-1", "attempt-1")
    second = fence.acquire("challenge-2", "attempt-2")

    interrupted = fence.reconcile_restart()

    assert interrupted == (first.generation_id, second.generation_id)
    assert [state.disposition for state in fence.projection().generations] == [
        GenerationDisposition.INTERRUPT,
        GenerationDisposition.INTERRUPT,
    ]
    replacement = fence.acquire("challenge-1", "attempt-3")
    assert replacement.generation_id == "generation-000003"
    assert fence.reconcile_restart() == (replacement.generation_id,)


def test_active_authority_is_durably_reserved_before_the_caller_mutates_state(tmp_path):
    fence = make_fence(tmp_path)
    identity = fence.acquire("challenge-1", "attempt-1")
    decision = fence.authorize(identity.generation_id, GenerationAuthority.CANDIDATE, b"candidate")

    assert decision == AuthorityDecision(True, GenerationClassification.CURRENT)
    reservation = EventStore(tmp_path, run_id="run-1").events()[-1]
    assert reservation.payload["record"] == "authority"
    assert reservation.payload["authority"] == "candidate"
    assert reservation.payload["classification"] == "current-generation"
    assert reservation.body == b""


def test_closed_authority_is_recorded_as_redacted_late_evidence(tmp_path):
    fence = GenerationFence(tmp_path, "run-1", Redactor({"TOKEN": "secret-value"}), lambda: "2026-09-11T00:00:00Z")
    identity = fence.acquire("challenge-1", "attempt-1")
    fence.close(identity.generation_id, GenerationDisposition.COMPLETE)
    before = fence.projection()

    decision = fence.authorize(identity.generation_id, GenerationAuthority.TOOL, b"secret-value-output")

    assert decision == AuthorityDecision(False, GenerationClassification.CLOSED)
    after = fence.projection()
    assert after.generations == before.generations
    late = EventStore(tmp_path, run_id="run-1").events()[-1]
    assert late.payload["record"] == "late-event"
    assert late.payload["classification"] == "closed-generation"
    assert late.payload["authority"] == "tool"
    assert late.body == b"[redacted:TOKEN]-output"
    assert late.body == EventStore(tmp_path, run_id="run-1").blob(late.blob_digest)


def test_superseded_authority_has_a_distinct_rejection_classification(tmp_path):
    fence = make_fence(tmp_path)
    identity = fence.acquire("challenge-1", "attempt-1")
    fence.close(identity.generation_id, GenerationDisposition.SUPERSEDE)

    decision = fence.authorize(identity.generation_id, GenerationAuthority.CARRY, b"stale")

    assert decision == AuthorityDecision(False, GenerationClassification.SUPERSEDED)
    assert fence.projection().generations[0].disposition is GenerationDisposition.SUPERSEDE


@pytest.mark.parametrize("authority", tuple(GenerationAuthority))
def test_every_closed_authority_domain_is_rejected_without_changing_ownership(tmp_path, authority):
    fence = make_fence(tmp_path)
    identity = fence.acquire("challenge-1", "attempt-1")
    fence.close(identity.generation_id, GenerationDisposition.ABANDON)
    before = fence.projection().generations

    decision = fence.authorize(identity.generation_id, authority, b"late")

    assert decision == AuthorityDecision(False, GenerationClassification.CLOSED)
    assert fence.projection().generations == before


def test_unknown_generation_cannot_authorize_or_close(tmp_path):
    fence = make_fence(tmp_path)

    with pytest.raises(UnknownGeneration):
        fence.authorize("generation-000999", GenerationAuthority.AUTHORITY)
    with pytest.raises(UnknownGeneration):
        fence.close("generation-000999", GenerationDisposition.INTERRUPT)


def test_projection_rejects_an_illegal_late_transition(tmp_path):
    fence = make_fence(tmp_path)
    store = EventStore(tmp_path, run_id="run-1")
    store.append(
        WorkGenerationRecorded(
            event_id="manual-acquire",
            generation_id="generation-000001",
            work_id="challenge-1",
            attempt_id="attempt-1",
            record=GenerationRecord.ACQUIRE,
            ts="2026-09-11T00:00:00Z",
        ),
        body=b"",
    )
    store.append(
        WorkGenerationRecorded(
            event_id="manual-late",
            generation_id="generation-000001",
            work_id="challenge-1",
            attempt_id="attempt-1",
            record=GenerationRecord.LATE_EVENT,
            authority=GenerationAuthority.TOOL,
            classification=GenerationClassification.CLOSED,
            ts="2026-09-11T00:00:00Z",
        ),
        body=b"late",
    )

    with pytest.raises(IllegalGenerationTransition):
        fence.projection()


def test_projection_rejects_reusing_an_attempt_identity(tmp_path):
    fence = make_fence(tmp_path)
    store = EventStore(tmp_path, run_id="run-1")
    for event_id, generation_id, work_id in (
        ("manual-acquire-1", "generation-000001", "challenge-1"),
        ("manual-acquire-2", "generation-000002", "challenge-2"),
    ):
        store.append(
            WorkGenerationRecorded(
                event_id=event_id,
                generation_id=generation_id,
                work_id=work_id,
                attempt_id="attempt-1",
                record=GenerationRecord.ACQUIRE,
                ts="2026-09-11T00:00:00Z",
            ),
            body=b"",
        )

    with pytest.raises(IllegalGenerationTransition):
        fence.projection()


def test_receipt_replays_the_fence_and_links_its_manifest_row(tmp_path):
    fence = make_fence(tmp_path)
    first = fence.acquire("challenge-1", "attempt-1")
    fence.authorize(first.generation_id, GenerationAuthority.AUTHORITY)
    fence.close(first.generation_id, GenerationDisposition.COMPLETE)
    fence.authorize(first.generation_id, GenerationAuthority.CANDIDATE, b"late-output")
    second = fence.acquire("challenge-2", "attempt-2")
    fence.close(second.generation_id, GenerationDisposition.SUPERSEDE)
    fence.authorize(second.generation_id, GenerationAuthority.CARRY, b"superseded-output")
    third = fence.acquire("challenge-3", "attempt-3")
    fence.close(third.generation_id, GenerationDisposition.ABANDON)
    fourth = fence.acquire("challenge-4", "attempt-4")
    fence.reconcile_restart()

    path = fence.write_receipt()
    document = json.loads(path.read_text())

    assert path == tmp_path / "runs" / "run-1" / "canonical" / "work-generation-fence.receipt.json"
    assert document["receipt_type"] == "work-generation-fence"
    assert document["transitions"][0]["generation_id"] == first.generation_id
    assert document["late_output_attempts"][0]["classification"] == "closed-generation"
    assert document["authority_rejection_trace"][0]["authority"] == "candidate"
    assert document["authority_reservation_trace"][0]["authority"] == "authority"
    assert document["authority_reservation_trace"][0]["accepted"] is True
    assert [transition["disposition"] for transition in document["transitions"]] == [
        None,
        "complete",
        None,
        "supersede",
        None,
        "abandon",
        None,
        "interrupt",
    ]
    assert document["restart_projection"]["active_by_work"] == {}
    assert document["projection_digest"] == fence.projection().digest
    assert document["chain_head"] == fence.projection().chain_head
    assert document["manifest_link"] == {
        "row_id": "core.canonical-state-replay-restart",
        "receipt_ref": "receipt:work-generation-fence",
    }
    assert verify_receipt(path) == path
    descriptor = manifest_receipt(path)
    assert descriptor["ref"] == "receipt:work-generation-fence"
    assert descriptor["kind"] == "work-generation-fence"
    draft = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    requirements = copy.deepcopy(draft["requirements"])
    row = next(item for item in requirements if item["row_id"] == "core.canonical-state-replay-restart")
    row.update(status="implemented", receipt_ref=descriptor["ref"])
    linked = generate_manifest(
        image_digest=draft["candidate"]["image_digest"],
        release_candidate_profile=draft["selected_profile"],
        requirements=requirements,
        receipts=[descriptor],
    )
    linked_row = next(
        item for item in linked["requirements"] if item["row_id"] == "core.canonical-state-replay-restart"
    )
    assert linked_row["status"] == "implemented"
    assert linked_row["receipt_ref"] == "receipt:work-generation-fence"
    assert fourth.generation_id == "generation-000004"


def test_receipt_rejects_noncanonical_or_changed_documents(tmp_path):
    fence = make_fence(tmp_path)
    fence.acquire("challenge-1", "attempt-1")
    path = fence.write_receipt()
    document = json.loads(path.read_text())
    document["chain_head"] = "tampered"
    path.write_text(json.dumps(document))

    with pytest.raises(InvalidReceiptError):
        verify_receipt(path)


def test_receipt_tracks_the_generation_substream_not_unrelated_canonical_events(tmp_path):
    fence = make_fence(tmp_path)
    fence.acquire("challenge-1", "attempt-1")
    path = fence.write_receipt()
    EventStore(tmp_path, run_id="run-1").append(
        LifecycleRecorded("run:close", RunClosed(TerminalDisposition.NORMAL)),
        body=b"",
    )

    assert verify_receipt(path) == path
