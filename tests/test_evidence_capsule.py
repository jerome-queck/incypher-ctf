"""Evidence capsules publish one verified, sanitized, authority-bound transaction."""

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from solver.evidence_capsule import CapsuleRefused, CapsuleRequest, promote_capsule
from solver.evidence_capsule_contracts import (
    PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN,
    BlobSelection,
    CapsuleInvalid,
    ReceiptContract,
    ReceiptRegistry,
    SanitizationPolicy,
)
from solver.evidence_capsule_reader import read_evidence, read_promoted_evidence, verify_evidence_content
from solver.event_store import EventStore
from solver.event_store_contracts import (
    LifecycleRecorded,
    ObservationRecorded,
    RunClosed,
    RunOpened,
    TerminalDisposition,
)
from solver.event_store_storage import canonical_bytes
from solver.manifest import canonical_manifest_bytes, generate_manifest, manifest_digest
from solver.write_reservation import Capacity, ReservationState, WriteAuthority, WriteProfile
from test_manifest import release_candidate_profile


AREA = Capacity(
    bytes=2_000_000,
    objects=40,
    operations=200,
    create=200,
    append=200,
    rename=200,
    unlink=200,
    durability=500,
)
PROFILE = WriteProfile(AREA, AREA, AREA)


def authority(tmp_path):
    return WriteAuthority(tmp_path / "authority", PROFILE)


def terminal_store(tmp_path, evidence=b'{"answer":"clean"}', run_id="run-proof", command="solve"):
    store = EventStore(tmp_path / run_id, run_id=run_id)
    store.append(LifecycleRecorded("run:open", RunOpened()), body=b"")
    observation = store.append(
        ObservationRecorded("attempt-1", 1, command, command, "bash"),
        body=evidence,
    )
    store.append(LifecycleRecorded("run:close", RunClosed(TerminalDisposition.NORMAL)), body=b"")
    return store, observation.blob_digest


def draft(image="a"):
    return generate_manifest(
        image_digest="sha256:" + image * 64,
        release_candidate_profile=release_candidate_profile(),
    )


def receipt(digest, *, producer="synthetic-proof", schema_version=1):
    return canonical_bytes(
        {
            "schema_version": schema_version,
            "kind": "promotion-transaction",
            "producer": producer,
            "evidence_digest": digest,
            "result": "passed",
        }
    )


def registry(*, producer="synthetic-proof", structured=True):
    def validate(document, source):
        assert source.events
        return [BlobSelection(document["evidence_digest"], "application/json", structured)]

    return ReceiptRegistry([ReceiptContract("promotion-transaction", 1, producer, validate)])


def request(tmp_path, **overrides):
    source = overrides.pop("source", None)
    store, digest = source if source is not None else terminal_store(tmp_path)
    values = {
        "store": store,
        "receipt": receipt(digest),
        "receipt_ref": "receipt:promotion-transaction",
        "candidate_manifest": draft(),
        "manifest_row_id": "core.receipts-capsules",
        "registry": registry(),
        "sanitization": SanitizationPolicy((), ("/Users/private",)),
        "authority": authority(tmp_path),
        "runs_directory": tmp_path / "runs",
    }
    values.update(overrides)
    return CapsuleRequest(**values)


def test_synthetic_receipt_passes_the_whole_transaction_and_versioned_read(tmp_path):
    capsule_request = request(tmp_path)
    promoted = promote_capsule(capsule_request)
    read = read_evidence(promoted.path, capsule_request.authority)

    assert read.capsule_id == promoted.capsule_id
    assert read_promoted_evidence(promoted.path, capsule_request.authority).capsule_id == promoted.capsule_id
    assert (promoted.path / "receipt.json").is_file()
    assert (promoted.path / "source" / "events.jsonl").is_file()
    row = next(row for row in promoted.candidate_manifest["requirements"] if row["row_id"] == "core.receipts-capsules")
    assert row["status"] == "implemented"
    assert row["receipt_ref"] == "receipt:promotion-transaction"
    assert row["evidence_refs"] == [promoted.content_ref]
    assert promoted.candidate_manifest["lifecycle"] == "provisional"


def test_identity_basis_binds_candidate_profile_source_blobs_schema_and_producer(tmp_path):
    capsule_request = request(tmp_path)
    promoted = promote_capsule(capsule_request)
    basis = read_evidence(promoted.path, capsule_request.authority).manifest["content_basis"]
    source_events = promoted.path.joinpath("source/events.jsonl").read_bytes().splitlines()

    assert basis["candidate"] == {
        "image_digest": "sha256:" + "a" * 64,
        "profile_digest": release_candidate_profile()["profile_digest"],
    }
    assert basis["source"]["first_sequence"] == 1
    assert basis["source"]["last_sequence"] == 3
    assert basis["source"]["chain_head"] == json.loads(source_events[-1])["event_digest"]
    assert basis["blobs"][0]["digest"] == json.loads(source_events[1])["payload"]["blob_digest"]
    assert basis["receipt"]["schema_version"] == 1
    assert basis["receipt"]["producer"] == "synthetic-proof"
    assert {item["digest"] for item in basis["blobs"]} | {
        item["digest"] for item in basis["excluded_source_blobs"]
    } == {json.loads(row)["payload"]["blob_digest"] for row in source_events}

    document = json.loads((promoted.path / "capsule.json").read_bytes())
    document["content_basis"]["receipt"]["producer"] = "different"
    (promoted.path / "capsule.json").write_bytes(canonical_bytes(document))
    with pytest.raises(CapsuleInvalid, match="content identity"):
        verify_evidence_content(promoted.path)


def test_reader_requires_exact_reciprocal_candidate_link_even_when_other_hashes_agree(tmp_path):
    promoted = promote_capsule(request(tmp_path))
    candidate = copy.deepcopy(promoted.candidate_manifest)
    row = next(row for row in candidate["requirements"] if row["row_id"] == "core.receipts-capsules")
    row["evidence_refs"] = []
    relinked = generate_manifest(
        image_digest=candidate["candidate"]["image_digest"],
        release_candidate_profile=candidate["selected_profile"],
        requirements=candidate["requirements"],
        receipts=candidate["receipts"],
    )
    candidate_body = canonical_manifest_bytes(relinked)
    (promoted.path / "candidate-manifest.json").write_bytes(candidate_body)
    document = json.loads((promoted.path / "capsule.json").read_bytes())
    document["candidate_manifest_digest"] = manifest_digest(relinked)
    document["candidate_identity"] = relinked["candidate"]["identity"]
    document["files"]["candidate-manifest.json"] = hashlib.sha256(candidate_body).hexdigest()
    document["publication_basis"]["candidate_manifest_digest"] = manifest_digest(relinked)
    document["publication_basis"]["candidate_identity"] = relinked["candidate"]["identity"]
    new_id = hashlib.sha256(canonical_bytes(document["publication_basis"])).hexdigest()
    document["capsule_id"] = new_id
    (promoted.path / "capsule.json").write_bytes(canonical_bytes(document))
    changed_path = promoted.path.with_name(new_id)
    promoted.path.rename(changed_path)

    with pytest.raises(CapsuleInvalid, match="exactly this capsule content"):
        verify_evidence_content(changed_path)


@pytest.mark.parametrize(
    ("evidence", "policy", "message"),
    [
        (b"token=old-secret", SanitizationPolicy((("TOKEN", "old-secret"),), ()), "credential TOKEN"),
        (b"copied /Users/private/file", SanitizationPolicy((), ("/Users/private",)), "host path"),
        (b'{"a":1,"a":2}', SanitizationPolicy((), ()), "duplicate JSON key"),
    ],
)
def test_sanitization_refuses_before_anything_appears_under_runs(tmp_path, evidence, policy, message):
    source = terminal_store(tmp_path, evidence=evidence)
    with pytest.raises(CapsuleRefused, match=message):
        promote_capsule(request(tmp_path, source=source, sanitization=policy))
    assert not (tmp_path / "runs").exists()


def test_opaque_binary_is_scanned_raw_without_forcing_semantic_decode(tmp_path):
    source = terminal_store(tmp_path, evidence=b"\x00\xffclean")
    capsule_request = request(tmp_path, source=source, registry=registry(structured=False))
    promoted = promote_capsule(capsule_request)
    assert read_evidence(promoted.path, capsule_request.authority).capsule_id == promoted.capsule_id


def test_canonical_stream_is_scanned_in_addition_to_selected_blobs(tmp_path):
    source = terminal_store(tmp_path, command="old-secret")
    policy = SanitizationPolicy((("HISTORICAL_TOKEN", "old-secret"),), ())
    with pytest.raises(CapsuleRefused, match="source event 2 carries declared credential"):
        promote_capsule(request(tmp_path, source=source, sanitization=policy))
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("crash_point", ["before_rename", "after_rename"])
def test_power_loss_yields_no_partial_capsule_and_preserves_v1(tmp_path, crash_point):
    runs = tmp_path / "runs"
    runs.mkdir()
    v1 = runs / "gate-v1.jsonl"
    v1.write_bytes(b'{"record":"run-open"}\n')

    def crash(point):
        if point == crash_point:
            raise RuntimeError("power lost")

    capsule_request = request(tmp_path, runs_directory=runs, hook=crash)
    with pytest.raises(RuntimeError, match="power lost"):
        promote_capsule(capsule_request)

    capsules = list((runs / "capsules").iterdir()) if (runs / "capsules").exists() else []
    assert v1.read_bytes() == b'{"record":"run-open"}\n'
    assert len(capsules) == (1 if crash_point == "after_rename" else 0)
    if capsules:
        assert verify_evidence_content(capsules[0]).capsule_id == capsules[0].name
        original_manifest = (capsules[0] / "capsule.json").read_bytes()
        original_inode = capsules[0].stat().st_ino
        capsule_request.authority.close()
        recovered_authority = WriteAuthority(tmp_path / "authority", PROFILE)
        for _ in range(2):
            with pytest.raises(CapsuleRefused) as refused:
                read_promoted_evidence(capsules[0], recovered_authority)
            assert refused.value.classification == PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN
        with pytest.raises(CapsuleRefused) as duplicate:
            promote_capsule(replace(capsule_request, authority=recovered_authority, hook=None))
        assert duplicate.value.classification == PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN
        assert (capsules[0] / "capsule.json").read_bytes() == original_manifest
        assert capsules[0].stat().st_ino == original_inode
        reservation_key = json.loads(original_manifest)["reservation"]["key"]
        assert recovered_authority.current(reservation_key).state is ReservationState.POSSIBLY_SENT


def test_versioned_reader_keeps_v1_run_bytes_intact(tmp_path):
    legacy = tmp_path / "gate-v1.jsonl"
    legacy.write_bytes(b'{"record":"run-open"}\n')
    read = read_evidence(legacy)
    assert (read.schema_version, read.kind, read.raw) == (1, "v1-run-jsonl", legacy.read_bytes())


def test_nonterminal_source_and_unregistered_receipt_refuse(tmp_path):
    store = EventStore(tmp_path / "live", run_id="live")
    store.append(LifecycleRecorded("run:open", RunOpened()), body=b"")
    with pytest.raises(CapsuleRefused, match="not a terminal Run"):
        promote_capsule(request(tmp_path, source=(store, hashlib.sha256(b"").hexdigest())))

    source = terminal_store(tmp_path, run_id="closed")
    with pytest.raises(CapsuleRefused, match="not registered"):
        promote_capsule(request(tmp_path, source=source, registry=ReceiptRegistry()))
