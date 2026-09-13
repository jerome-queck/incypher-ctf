"""Evidence capsules publish one verified, sanitized, authority-bound transaction."""

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import solver.evidence_capsule_storage as capsule_storage
import solver.evidence_capsule_vault as capsule_vault

from solver.evidence_capsule import CapsuleRefused, CapsuleRequest, EvidenceCapsulePromoter
from solver.evidence_capsule_contracts import (
    PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN,
    BlobSelection,
    CapsuleInvalid,
    ReceiptContract,
    ReceiptRegistry,
)
from solver.evidence_capsule_reader import read_evidence, verify_evidence_content
from solver.evidence_capsule_scan import HostSanitizationAuthority
from solver.evidence_capsule_vault import VAULT_KIND, vault_receipt
from solver.credentials import SCANNED_SECRETS
from solver.board_broker_contracts import BoardBrokerRecorded, BoardOperation, BoardOutcome, BoardRecord
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


def terminal_intake_store(tmp_path, raw: bytes, sanitized: bytes, run_id="run-proof"):
    store = EventStore(tmp_path / run_id, run_id=run_id)
    store.append(LifecycleRecorded("run:open", RunOpened()), body=b"")
    event = store.append(
        BoardBrokerRecorded(
            event_id="board-broker:000002",
            request_id="board-broker:000001",
            record=BoardRecord.CLASSIFIED,
            operation=BoardOperation.INTAKE_READ,
            binding_digest="1" * 64,
            run_id=run_id,
            boot_id="boot-1",
            generation_id="generation-1",
            lane_id="lane-1",
            attempt_id="attempt-1",
            step_id="step-1",
            scope="board.intake",
            peer_identity_digest="2" * 64,
            request_digest="3" * 64,
            outcome=BoardOutcome.ANSWERED,
            endpoint="/api/v1/challenges",
            http_status=200,
            response_digest=hashlib.sha256(raw).hexdigest(),
            response_original_bytes=len(raw),
            response_sanitized_bytes=len(sanitized),
            raw_blob_digest=hashlib.sha256(raw).hexdigest(),
            raw_blob_bytes=len(raw),
            raw_blob_class="canonical-private-board-response",
            response_content_type="application/json",
            redaction_policy_digest="4" * 64,
        ),
        body=sanitized,
    )
    store.append(LifecycleRecorded("run:close", RunClosed(TerminalDisposition.NORMAL)), body=b"")
    return store, event.blob_digest


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
        causal = [
            event["payload"]["blob_digest"] for event in source.events if event["event_type"] == "observation.recorded"
        ]
        if len(causal) != 1 or document.get("evidence_digest") != causal[0]:
            raise CapsuleRefused("receipt evidence claim does not match its causal source")
        return [BlobSelection(causal[0], "application/json", structured)]

    return ReceiptRegistry([ReceiptContract("promotion-transaction", 1, producer, validate)])


def intake_registry():
    def validate(document, source):
        causal = [
            event["payload"]["blob_digest"] for event in source.events if event["event_type"] == "board-broker.recorded"
        ]
        if causal != [document.get("evidence_digest")]:
            raise CapsuleRefused("receipt evidence claim does not match its causal source")
        return [BlobSelection(causal[0], "application/json", True)]

    return ReceiptRegistry([ReceiptContract("promotion-transaction", 1, "synthetic-proof", validate)])


class VaultReader:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body


def vault_body(store, *, current=None, historical=(), source=None, version=1, attestation_id="a" * 64):
    if source is None:
        try:
            snapshot = store.terminal_snapshot()
            source = (snapshot.run_id, snapshot.chain_head)
        except RuntimeError:
            source = (store.run_id, "0" * 64)
    values = {name: {"current": [], "historical": []} for name in SCANNED_SECRETS}
    for name, value in (("TEAM_KEY", "fixture-current"),) if current is None else current:
        values[name]["current"].append(value)
    for name, value in historical:
        values[name]["historical"].append(value)
    through = {"run_id": source[0], "chain_head": source[1]}
    return canonical_bytes(
        {
            "schema_version": 1,
            "kind": VAULT_KIND,
            "version": version,
            "attestation_id": attestation_id,
            "completeness_through": through,
            "values": values,
        }
    )


def scan_authority(
    tmp_path,
    store,
    *,
    current=None,
    historical=(),
    active=(),
    forbidden_paths=("/Users/private",),
    vault=None,
    vault_reader=None,
):
    root = tmp_path / "host-scan-authority"
    root.mkdir(parents=True, exist_ok=True)
    lines = [f"{name}={value}" for name, value in active]
    (root / ".env").write_text("\n".join(lines) + ("\n" if lines else ""))
    reader = vault_reader or VaultReader(
        vault if vault is not None else vault_body(store, current=current, historical=historical)
    )
    return HostSanitizationAuthority(
        root,
        host_paths=tuple(Path(path) for path in forbidden_paths),
        vault_reader=reader,
    )


def transaction(tmp_path, **overrides):
    source = overrides.pop("source", None)
    store, digest = source if source is not None else terminal_store(tmp_path)
    write_authority = overrides.pop("write_authority", None)
    if write_authority is None:
        write_authority = authority(tmp_path)
    trusted_scan = overrides.pop("scan_authority", None)
    if trusted_scan is None:
        trusted_scan = scan_authority(tmp_path, store)
    promoter = EvidenceCapsulePromoter(
        store=store,
        candidate_manifest=overrides.pop("candidate_manifest", draft()),
        manifest_row_id=overrides.pop("manifest_row_id", "core.receipts-capsules"),
        registry=overrides.pop("registry", registry()),
        scan_authority=trusted_scan,
        write_authority=write_authority,
        runs_directory=overrides.pop("runs_directory", tmp_path / "runs"),
        hook=overrides.pop("hook", None),
    )
    request = CapsuleRequest(
        receipt=overrides.pop("receipt", receipt(digest)),
        receipt_ref=overrides.pop("receipt_ref", "receipt:promotion-transaction"),
    )
    assert not overrides
    return promoter, request, write_authority


def publish(configured):
    promoter, request, _write_authority = configured
    return promoter.promote(request)


def test_producer_request_cannot_inject_scan_or_receipt_authority(tmp_path):
    assert set(CapsuleRequest.__dataclass_fields__) == {"receipt", "receipt_ref"}
    with pytest.raises(TypeError):
        CapsuleRequest(receipt=b"{}", receipt_ref="receipt:x", registry=ReceiptRegistry())
    empty = transaction(tmp_path / "empty-request")
    with pytest.raises(CapsuleRefused, match="lacks typed schema"):
        empty[0].promote(CapsuleRequest(b"{}", "receipt:empty"))

    store, digest = terminal_store(tmp_path)
    forged = receipt(digest, producer="self-authorized")
    with pytest.raises(CapsuleRefused, match="not registered"):
        publish(transaction(tmp_path / "forged", source=(store, digest), receipt=forged))
    with pytest.raises(CapsuleRefused, match="does not match its causal source"):
        publish(transaction(tmp_path / "empty", source=(store, digest), receipt=receipt("0" * 64)))


@pytest.mark.parametrize(
    ("current", "active"),
    [
        ((("CTFD_API_TOKEN", "current-secret"),), (("CTFD_API_TOKEN", "current-secret"),)),
        ((), ()),
    ],
)
def test_scanner_vault_retains_a_deleted_or_rotated_secret(tmp_path, current, active):
    source = terminal_store(tmp_path, evidence=b'{"secret":"old-secret"}')
    trusted = scan_authority(
        tmp_path,
        source[0],
        current=current,
        historical=(("CTFD_API_TOKEN", "old-secret"),),
        active=active,
    )
    with pytest.raises(CapsuleRefused, match="credential CTFD_API_TOKEN"):
        publish(transaction(tmp_path, source=source, scan_authority=trusted))


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"", "missing or empty"),
        (b"{}", "schema is invalid or incomplete"),
    ],
)
def test_scanner_vault_missing_empty_or_incomplete_refuses(tmp_path, body, message):
    store, _digest = terminal_store(tmp_path)
    root = tmp_path / "host-scan-authority"
    root.mkdir()
    (root / ".env").write_bytes(b"")
    with pytest.raises(CapsuleRefused, match=message):
        HostSanitizationAuthority(root, host_paths=(), vault_reader=VaultReader(body))


def test_valid_but_empty_scanner_vault_refuses(tmp_path):
    store, _digest = terminal_store(tmp_path)
    empty = vault_body(store, current=())
    with pytest.raises(CapsuleRefused, match="contains no exact credential values"):
        scan_authority(tmp_path, store, vault=empty)


def test_scanner_vault_missing_one_declared_name_refuses(tmp_path):
    store, _digest = terminal_store(tmp_path)
    incomplete = json.loads(vault_body(store))
    del incomplete["values"]["TEAM_KEY"]
    with pytest.raises(CapsuleRefused, match="does not attest every declared credential"):
        scan_authority(tmp_path, store, vault=canonical_bytes(incomplete))


def test_production_keychain_reader_refuses_unavailable_or_missing_item(tmp_path, monkeypatch, capsys):
    store, _digest = terminal_store(tmp_path)
    root = tmp_path / "host-scan-authority"
    root.mkdir()
    (root / ".env").write_bytes(b"")

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(capsule_vault.subprocess, "run", unavailable)
    with pytest.raises(CapsuleRefused, match="unavailable from macOS Keychain"):
        HostSanitizationAuthority.from_macos_keychain(root, host_paths=())

    missing = subprocess.CompletedProcess([], 44, stdout=b"do-not-print-secret", stderr=b"keychain detail")
    monkeypatch.setattr(capsule_vault.subprocess, "run", lambda *_args, **_kwargs: missing)
    with pytest.raises(CapsuleRefused, match="item is missing or empty"):
        HostSanitizationAuthority.from_macos_keychain(root, host_paths=())
    captured = capsys.readouterr()
    assert "do-not-print-secret" not in captured.out + captured.err


def test_scanner_vault_stale_or_missing_active_current_refuses(tmp_path):
    source = terminal_store(tmp_path)
    stale = vault_body(source[0], source=("another-run", "f" * 64))
    authority = scan_authority(tmp_path / "stale", source[0], vault=stale)
    with pytest.raises(CapsuleRefused, match="stale for the terminal source Run"):
        publish(transaction(tmp_path / "stale", source=source, scan_authority=authority))

    with pytest.raises(CapsuleRefused, match="absent from the scanner vault current set"):
        scan_authority(
            tmp_path / "missing-current",
            source[0],
            current=(("TEAM_KEY", "another-current"),),
            active=(("CTFD_API_TOKEN", "not-in-vault"),),
        )


def test_legacy_env_overlay_cannot_become_scanner_authority(tmp_path):
    source = terminal_store(tmp_path)
    root = tmp_path / "legacy"
    trusted = scan_authority(root, source[0])
    (root / "host-scan-authority" / ".env.older").write_text("CTFD_API_TOKEN=old-secret\n")
    with pytest.raises(CapsuleRefused, match="legacy .env overlays are ambiguous"):
        publish(transaction(root, source=source, scan_authority=trusted))


def test_scanner_vault_rechecks_before_rename(tmp_path):
    source = terminal_store(tmp_path)

    staged = tmp_path / "staged"
    reader = VaultReader(vault_body(source[0]))
    trusted = scan_authority(staged, source[0], vault_reader=reader)

    def rotate_vault(phase):
        if phase == "before_rename":
            reader.body = vault_body(source[0], current=(("TEAM_KEY", "changed-secret"),))

    with pytest.raises(CapsuleRefused, match="authority changed"):
        publish(transaction(staged, source=source, scan_authority=trusted, hook=rotate_vault))
    assert not (staged / "runs" / "capsules").exists()


def test_scanner_vault_attestation_revision_changes_published_identity(tmp_path):
    store, _digest = terminal_store(tmp_path)
    first = scan_authority(
        tmp_path / "first",
        store,
        vault=vault_body(store, attestation_id="a" * 64),
    )
    second = scan_authority(
        tmp_path / "second",
        store,
        vault=vault_body(store, attestation_id="b" * 64),
    )
    snapshot = store.terminal_snapshot()

    first.bind_source(snapshot.run_id, snapshot.chain_head)
    second.bind_source(snapshot.run_id, snapshot.chain_head)

    assert first.identity != second.identity
    assert first.identity["receipt"] != second.identity["receipt"]


def test_published_scan_identity_cannot_verify_low_entropy_secret_guesses(tmp_path):
    secret = "password"
    source = terminal_store(tmp_path)
    trusted = scan_authority(
        tmp_path,
        source[0],
        historical=(("CTFD_API_TOKEN", secret),),
    )

    promoted = publish(transaction(tmp_path, source=source, scan_authority=trusted))

    published = b"".join(path.read_bytes() for path in promoted.path.rglob("*") if path.is_file())
    guesses = ("password", "hunter2", "letmein")
    assert b"source_digest" not in published
    assert b'"secrets"' not in published
    for guess in guesses:
        assert guess.encode() not in published
        assert hashlib.sha256(guess.encode()).hexdigest().encode() not in published


def test_directory_durability_precedes_authority_commit_in_full_order(tmp_path, monkeypatch):
    ordered = []
    monkeypatch.setattr(capsule_storage, "fsync_directory", lambda path: ordered.append(("fsync", path)))
    configured = transaction(tmp_path, hook=lambda phase: ordered.append(("hook", phase)))

    promoted = publish(configured)

    stage_parent = tmp_path / ".evidence-capsule-staging"
    runs = tmp_path / "runs"
    capsules = runs / "capsules"
    labels = [item[1] for item in ordered]
    assert labels.index(stage_parent) < labels.index("after_stage")
    assert labels.index("before_rename") < labels.index(runs) < labels.index(capsules)
    assert labels.index(capsules) < labels.index("after_rename") < labels.index("before_authority_commit")
    assert labels.index("before_authority_commit") < labels.index("after_authority_commit")
    assert ordered[-1] == ("fsync", stage_parent)
    assert promoted.path.parent == capsules


def test_failed_stage_is_removed_before_refusal_returns(tmp_path, monkeypatch):
    def fail_verification(_path):
        raise CapsuleInvalid("staged capsule failed verification")

    monkeypatch.setattr(capsule_storage, "verify_evidence_content", fail_verification)
    configured = transaction(tmp_path)

    with pytest.raises(CapsuleInvalid, match="failed verification"):
        publish(configured)

    stage_parent = tmp_path / ".evidence-capsule-staging"
    assert stage_parent.is_dir()
    assert not list(stage_parent.iterdir())
    assert configured[2].reservations()[0].state is ReservationState.ABORTED


def test_synthetic_receipt_passes_the_whole_transaction_and_versioned_read(tmp_path):
    configured = transaction(tmp_path)
    promoted = publish(configured)
    read = read_evidence(promoted.path, configured[2])

    assert read.capsule_id == promoted.capsule_id
    assert (promoted.path / "receipt.json").is_file()
    assert (promoted.path / "source" / "events.jsonl").is_file()
    row = next(row for row in promoted.candidate_manifest["requirements"] if row["row_id"] == "core.receipts-capsules")
    assert row["status"] == "implemented"
    assert row["receipt_ref"] == "receipt:promotion-transaction"
    assert row["evidence_refs"] == [promoted.content_ref]
    assert promoted.candidate_manifest["lifecycle"] == "provisional"


@pytest.mark.parametrize(
    ("raw", "sanitized", "private_excluded"),
    [
        (b'{"answer":"clean"}', b'{"answer":"clean"}', False),
        (b'{"token":"secret"}', b'{"token":"[redacted:CTFD_API_TOKEN]"}', True),
    ],
)
def test_capsule_verifies_with_equal_or_distinct_private_intake_raw_digest(tmp_path, raw, sanitized, private_excluded):
    source = terminal_intake_store(tmp_path, raw, sanitized)
    configured = transaction(tmp_path, source=source, registry=intake_registry())

    promoted = publish(configured)
    verified = verify_evidence_content(promoted.path)

    exclusions = verified.manifest["content_basis"]["excluded_source_blobs"]
    assert any(item["classification"] == "restart-private-broker" for item in exclusions) is private_excluded


def test_identity_basis_binds_candidate_profile_source_blobs_schema_and_producer(tmp_path):
    configured = transaction(tmp_path)
    promoted = publish(configured)
    basis = read_evidence(promoted.path, configured[2]).manifest["content_basis"]
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
    assert basis["scan_policy"]["vault"] == {
        "receipt": vault_receipt(
            version=1,
            attestation_id="a" * 64,
            completeness_through={"run_id": "run-proof", "chain_head": basis["source"]["chain_head"]},
        ),
        "schema_version": 1,
        "version": 1,
        "attestation_id": "a" * 64,
        "completeness_through": {"run_id": "run-proof", "chain_head": basis["source"]["chain_head"]},
    }
    assert b"fixture-current" not in b"".join(path.read_bytes() for path in promoted.path.rglob("*") if path.is_file())
    assert {item["digest"] for item in basis["blobs"]} | {
        item["digest"] for item in basis["excluded_source_blobs"]
    } == {json.loads(row)["payload"]["blob_digest"] for row in source_events}

    document = json.loads((promoted.path / "capsule.json").read_bytes())
    document["content_basis"]["receipt"]["producer"] = "different"
    (promoted.path / "capsule.json").write_bytes(canonical_bytes(document))
    with pytest.raises(CapsuleInvalid, match="content identity"):
        verify_evidence_content(promoted.path)


def test_reader_requires_exact_reciprocal_candidate_link_even_when_other_hashes_agree(tmp_path):
    promoted = publish(transaction(tmp_path))
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
    ("evidence", "secrets", "forbidden_paths", "message"),
    [
        (b"token=old-secret", (("CTFD_API_TOKEN", "old-secret"),), (), "credential CTFD_API_TOKEN"),
        (b"copied /Users/private/file", (), ("/Users/private",), "host path"),
        (b'{"a":1,"a":2}', (), (), "duplicate JSON key"),
    ],
)
def test_sanitization_refuses_before_anything_appears_under_runs(tmp_path, evidence, secrets, forbidden_paths, message):
    source = terminal_store(tmp_path, evidence=evidence)
    with pytest.raises(CapsuleRefused, match=message):
        publish(
            transaction(
                tmp_path,
                source=source,
                scan_authority=scan_authority(
                    tmp_path,
                    source[0],
                    historical=secrets,
                    forbidden_paths=forbidden_paths,
                ),
            )
        )
    assert not (tmp_path / "runs").exists()


def test_opaque_binary_is_scanned_raw_without_forcing_semantic_decode(tmp_path):
    source = terminal_store(tmp_path, evidence=b"\x00\xffclean")
    configured = transaction(tmp_path, source=source, registry=registry(structured=False))
    promoted = publish(configured)
    assert read_evidence(promoted.path, configured[2]).capsule_id == promoted.capsule_id


def test_structured_blob_may_be_any_valid_json_value(tmp_path):
    promoted = publish(transaction(tmp_path, source=terminal_store(tmp_path, evidence=b'["clean"]')))
    assert verify_evidence_content(promoted.path).capsule_id == promoted.capsule_id


def test_canonical_stream_is_scanned_in_addition_to_selected_blobs(tmp_path):
    source = terminal_store(tmp_path, command="old-secret")
    with pytest.raises(CapsuleRefused, match="source event 2 carries declared credential"):
        publish(
            transaction(
                tmp_path,
                source=source,
                scan_authority=scan_authority(
                    tmp_path,
                    source[0],
                    historical=(("CTFD_API_TOKEN", "old-secret"),),
                ),
            )
        )
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

    source = terminal_store(tmp_path)
    configured = transaction(tmp_path, source=source, runs_directory=runs, hook=crash)
    with pytest.raises(RuntimeError, match="power lost"):
        publish(configured)

    capsules = list((runs / "capsules").iterdir()) if (runs / "capsules").exists() else []
    assert v1.read_bytes() == b'{"record":"run-open"}\n'
    assert len(capsules) == (1 if crash_point == "after_rename" else 0)
    if capsules:
        assert verify_evidence_content(capsules[0]).capsule_id == capsules[0].name
        original_manifest = (capsules[0] / "capsule.json").read_bytes()
        original_inode = capsules[0].stat().st_ino
        configured[2].close()
        recovered_authority = WriteAuthority(tmp_path / "authority", PROFILE)
        for _ in range(2):
            with pytest.raises(CapsuleRefused) as refused:
                read_evidence(capsules[0], recovered_authority)
            assert refused.value.classification == PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN
        with pytest.raises(CapsuleRefused) as duplicate:
            publish(
                transaction(
                    tmp_path,
                    source=source,
                    runs_directory=runs,
                    write_authority=recovered_authority,
                )
            )
        assert duplicate.value.classification == PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN
        assert (capsules[0] / "capsule.json").read_bytes() == original_manifest
        assert capsules[0].stat().st_ino == original_inode
        reservation_key = json.loads(original_manifest)["reservation"]["key"]
        assert recovered_authority.current(reservation_key).state is ReservationState.POSSIBLY_SENT


@pytest.mark.parametrize("crash_point", ["before_rename", "after_rename"])
def test_abrupt_process_loss_recovers_absent_or_forensically_complete_publication(tmp_path, crash_point):
    store = EventStore(tmp_path / "source", run_id="process-loss")
    store.append(LifecycleRecorded("run:open", RunOpened()), body=b"")
    store.append(ObservationRecorded("attempt-1", 1, "solve", "solve", "bash"), body=b'{"proof":true}')
    closed = store.append(
        LifecycleRecorded("run:close", RunClosed(TerminalDisposition.NORMAL)),
        body=b"",
    )
    (tmp_path / "candidate.json").write_bytes(canonical_manifest_bytes(draft()))
    (tmp_path / "scanner-vault.json").write_bytes(vault_body(store))
    host_scan = tmp_path / "host-scan-authority"
    host_scan.mkdir()
    (host_scan / ".env").write_bytes(b"")
    runs = tmp_path / "runs"
    runs.mkdir()
    v1 = runs / "gate-v1.jsonl"
    original_v1 = b'{"record":"run-open"}\n'
    v1.write_bytes(original_v1)
    driver = Path(__file__).parent / "fixtures" / "evidence_capsule_crash.py"

    finished = subprocess.run(
        [sys.executable, str(driver), str(tmp_path), crash_point],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1])},
        check=False,
    )

    assert finished.returncode == 91, finished.stderr
    assert EventStore(tmp_path / "source", run_id="process-loss").terminal_snapshot().chain_head == closed.event_digest
    assert v1.read_bytes() == original_v1
    recovered_authority = WriteAuthority(tmp_path / "authority", PROFILE)
    capsules = list((runs / "capsules").iterdir()) if (runs / "capsules").exists() else []
    assert len(capsules) == (1 if crash_point == "after_rename" else 0)
    expected_state = ReservationState.POSSIBLY_SENT if capsules else ReservationState.ABORTED
    assert [reservation.state for reservation in recovered_authority.reservations()] == [expected_state]
    if capsules:
        assert verify_evidence_content(capsules[0]).capsule_id == capsules[0].name
        with pytest.raises(CapsuleRefused) as refused:
            read_evidence(capsules[0], recovered_authority)
        assert refused.value.classification == PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN


def test_versioned_reader_keeps_v1_run_bytes_intact(tmp_path):
    legacy = tmp_path / "gate-v1.jsonl"
    legacy.write_bytes(b'{"record":"run-open"}\n')
    read = read_evidence(legacy)
    assert (read.schema_version, read.kind, read.raw) == (1, "v1-run-jsonl", legacy.read_bytes())


def test_nonterminal_source_and_unregistered_receipt_refuse(tmp_path):
    store = EventStore(tmp_path / "live", run_id="live")
    store.append(LifecycleRecorded("run:open", RunOpened()), body=b"")
    with pytest.raises(CapsuleRefused, match="terminal snapshot"):
        publish(transaction(tmp_path, source=(store, hashlib.sha256(b"").hexdigest())))

    source = terminal_store(tmp_path, run_id="closed")
    with pytest.raises(CapsuleRefused, match="not registered"):
        publish(transaction(tmp_path, source=source, registry=ReceiptRegistry()))
