"""The verified replay seam rebuilds a stable projection before authority opens."""

import copy
import json

import pytest

from solver.event_store import (
    DamageKind,
    EventStore,
    InvalidEventError,
    InvalidReceiptError,
    ObservationRecorded,
    ProjectionMismatchError,
)
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.manifest import generate_manifest
from solver.replay import (
    verify_and_materialize_run_state,
    verify_replay_receipt,
    verified_replay_manifest_receipt,
)
from solver.record import Recorder, Usage
from solver.redaction import Redactor

from test_manifest import release_candidate_profile


def test_empty_run_is_a_verified_replay_with_a_versioned_receipt(tmp_path):
    result = verify_and_materialize_run_state(tmp_path, "run-empty")

    assert result.run_id == "run-empty"
    assert result.event_count == 0
    assert result.chain_head == ""
    assert result.projection.digest

    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["receipt_type"] == "verified-replay"
    assert receipt["run_id"] == "run-empty"
    assert receipt["schema_versions"]["canonical"] == 1
    assert receipt["projection_digests"] == {"v1": result.projection.digest}


def test_replaying_a_canonical_chain_twice_is_byte_identical_and_read_only(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    store.append(_event(step_index=1), body=b"one")
    store.append(_event(step_index=2), body=b"two")

    first = verify_and_materialize_run_state(tmp_path, "run-1")
    second = verify_and_materialize_run_state(tmp_path, "run-1")

    assert first.projection.serialized == second.projection.serialized
    assert first.projection.digest == second.projection.digest
    assert first.projection.serialized == first.projection_path.read_bytes()
    assert first.projection.digest == digest_bytes(first.projection.serialized)
    row = first.projection.rows[0]
    assert row["record"] == "step-end"
    assert row["seq"] == 1
    assert row["observation_ref"].startswith("sealed/sha256/")
    with pytest.raises(TypeError):
        first.projection.rows[0]["tool"] = "mutated"


def test_the_compatibility_view_is_derived_and_projection_edits_fail_closed(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    store.append(_event(step_index=1), body=b"one")
    result = verify_and_materialize_run_state(tmp_path, "run-1")

    projection_path = result.projection_path
    projection_path.write_bytes(projection_path.read_bytes().replace(b'"tool":"bash"', b'"tool":"fake"'))

    with pytest.raises(ProjectionMismatchError) as failure:
        verify_and_materialize_run_state(tmp_path, "run-1")
    assert failure.value.classification == "projection-mismatch"


def test_a_verified_replay_receipt_can_be_checked_without_rewriting_state(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    store.append(_event(step_index=1), body=b"one")
    result = verify_and_materialize_run_state(tmp_path, "run-1")
    before_projection = result.projection_path.stat().st_mtime_ns
    before_receipt = result.receipt_path.stat().st_mtime_ns

    checked = verify_replay_receipt(result.receipt_path)

    assert checked.projection.digest == result.projection.digest
    assert checked.projection_path.stat().st_mtime_ns == before_projection
    assert checked.receipt_path.stat().st_mtime_ns == before_receipt


def test_corruption_results_are_observed_by_the_replay_proof(tmp_path):
    result = verify_and_materialize_run_state(tmp_path, "run-1")

    results = result.receipt["corruption_fixture_results"]
    assert set(results) == {
        DamageKind.EVENT_DIGEST_MISMATCH.value,
        DamageKind.PREVIOUS_DIGEST_MISMATCH.value,
        DamageKind.MISSING_BLOB.value,
        DamageKind.UNKNOWN_SCHEMA.value,
    }
    assert all(
        entry["status"] == "refused" and entry["observed"] == classification
        for classification, entry in results.items()
    )


def test_receipt_embeds_the_controlled_pre_authority_proof_and_tampering_fails(tmp_path):
    result = verify_and_materialize_run_state(tmp_path, "run-1")
    proof = result.receipt["pre_authority_refusal"]

    assert proof == {
        "entry_point": "solver.__main__.main",
        "external_clients_constructed": 0,
        "refusal_exit_code": 2,
    }

    receipt = json.loads(result.receipt_path.read_text())
    receipt["pre_authority_refusal"]["external_clients_constructed"] = 1
    result.receipt_path.write_bytes(canonical_bytes(receipt) + b"\n")
    with pytest.raises(InvalidReceiptError):
        verify_replay_receipt(result.receipt_path)


def test_observation_payload_schema_rejects_missing_fields_as_typed_damage(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    store.append(_event(step_index=1), body=b"one")
    envelope = json.loads(store.events_path.read_text())
    envelope["payload"].pop("tool")
    unsigned = {key: value for key, value in envelope.items() if key != "event_digest"}
    envelope["event_digest"] = digest_bytes(canonical_bytes(unsigned))
    store.events_path.write_bytes(canonical_bytes(envelope) + b"\n")

    with pytest.raises(InvalidEventError) as failure:
        verify_and_materialize_run_state(tmp_path, "run-1")
    assert failure.value.kind is DamageKind.INVALID_EVENT


def test_unsupported_event_type_is_typed_damage(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    store.append(_event(step_index=1), body=b"one")
    envelope = json.loads(store.events_path.read_text())
    envelope["event_type"] = "future.event"
    unsigned = {key: value for key, value in envelope.items() if key != "event_digest"}
    envelope["event_digest"] = digest_bytes(canonical_bytes(unsigned))
    store.events_path.write_bytes(canonical_bytes(envelope) + b"\n")

    with pytest.raises(InvalidEventError) as failure:
        verify_and_materialize_run_state(tmp_path, "run-1")
    assert failure.value.kind is DamageKind.INVALID_EVENT


def test_edited_legacy_identity_is_divergence_not_a_missing_projection(tmp_path):
    recorder = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}))
    recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="printf output",
        command_normalised="printf output",
        tool="bash",
    ).end(exit_code=0, output=b"one", usage=Usage(model="gpt-5"))
    verify_and_materialize_run_state(tmp_path, "run-1")
    rows = [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]
    rows[-1]["attempt_id"] = "edited-attempt"
    recorder.stream_path.write_bytes(b"".join(canonical_bytes(row) + b"\n" for row in rows))

    with pytest.raises(ProjectionMismatchError):
        verify_and_materialize_run_state(tmp_path, "run-1")


def test_coordinated_legacy_identity_and_blob_edits_are_still_divergence(tmp_path):
    recorder = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}))
    recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="printf output",
        command_normalised="printf output",
        tool="bash",
    ).end(exit_code=0, output=b"one", usage=Usage(model="gpt-5"))
    rows = [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]
    rows[-1].update(
        attempt_id="edited-attempt",
        observation_digest="0" * 64,
        observation_bytes=999,
    )
    recorder.stream_path.write_bytes(b"".join(canonical_bytes(row) + b"\n" for row in rows))

    with pytest.raises(ProjectionMismatchError):
        verify_and_materialize_run_state(tmp_path, "run-1")


def test_missing_legacy_row_does_not_remove_the_canonical_projection(tmp_path):
    recorder = Recorder(tmp_path, run_id="run-1", redactor=Redactor({}))
    recorder.step_begin(
        attempt_id="attempt-1",
        step_index=1,
        command_raw="printf output",
        command_normalised="printf output",
        tool="bash",
    ).end(exit_code=0, output=b"one", usage=Usage(model="gpt-5"))
    rows = [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]
    rows = [row for row in rows if row.get("record") != "step-end"]
    recorder.stream_path.write_bytes(b"".join(canonical_bytes(row) + b"\n" for row in rows))

    result = verify_and_materialize_run_state(tmp_path, "run-1")
    assert [row["attempt_id"] for row in result.projection.rows] == ["attempt-1"]
    assert json.loads(result.projection_path.read_text())["attempt_id"] == "attempt-1"


def test_manifest_link_is_explicit_and_default_row_stays_planned(tmp_path):
    result = verify_and_materialize_run_state(tmp_path, "run-1")
    descriptor = verified_replay_manifest_receipt(result)
    manifest = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    default_row = next(
        row for row in manifest["requirements"] if row["row_id"] == "core.canonical-state-replay-restart"
    )
    assert default_row["status"] == "planned"
    assert result.receipt["manifest_link"] == {
        "row_id": "core.canonical-state-replay-restart",
        "receipt_ref": descriptor["ref"],
    }

    requirements = copy.deepcopy(manifest["requirements"])
    replay_row = next(row for row in requirements if row["row_id"] == result.receipt["manifest_link"]["row_id"])
    replay_row.update(status="implemented", receipt_ref=descriptor["ref"])
    linked = generate_manifest(
        image_digest=manifest["candidate"]["image_digest"],
        release_candidate_profile=manifest["selected_profile"],
        requirements=requirements,
        receipts=[descriptor],
    )
    assert (
        next(row for row in linked["requirements"] if row["row_id"] == result.receipt["manifest_link"]["row_id"])[
            "receipt_ref"
        ]
        == descriptor["ref"]
    )


def _event(*, step_index: int) -> ObservationRecorded:
    return ObservationRecorded(
        attempt_id="attempt-1",
        step_index=step_index,
        command_raw="printf output",
        command_normalised="printf output",
        tool="bash",
    )
