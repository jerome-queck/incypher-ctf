"""Canonical capability-custody evidence and receipt proofs."""

import copy
import json
import os
from contextlib import contextmanager

import env_file
import pytest

from solver.bootstrap_custody import BootstrapCustody, Broker, SecretSource
from solver.capability import CapabilityAuthority, CapabilityBinding, CapabilityRefused, PeerIdentity
from solver.capability_receipt import (
    MANIFEST_RECEIPT_REF,
    MANIFEST_ROW_ID,
    RECEIPT_FILENAME,
    CapabilityEvidence,
    manifest_receipt,
    verify_receipt,
    write_receipt,
)
from solver.event_store import EventStore, InvalidReceiptError
from solver.executor_secret_probe import classify_surfaces
from solver.manifest import generate_manifest
from solver.redaction import Redactor
from solver.work_generation import GenerationFence
from test_manifest import release_candidate_profile


SECRETS = {
    "TEAM_KEY": "board-team-secret",
    "CODEX_AUTH": "codex-subscription-secret",
    "CPA_AUTH": "cpa-oauth-secret",
}
PEER = PeerIdentity(pid=101, uid=20_000, gid=20_000, started="123", cgroup="/attempt-1")
OTHER_PEER = PeerIdentity(pid=202, uid=20_001, gid=20_001, started="456", cgroup="/attempt-2")


class Peers:
    def __init__(self, *identities: PeerIdentity) -> None:
        self._identities = list(identities)

    def __call__(self, _connection) -> PeerIdentity:
        return self._identities.pop(0)


def _redactor() -> Redactor:
    return Redactor(SECRETS)


@contextmanager
def _bootstrap(tmp_path, redactor):
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "executor-home"),
        "LANG": "C.UTF-8",
        "TEAM_KEY": SECRETS["TEAM_KEY"],
    }
    codex_file = tmp_path / "codex-bootstrap"
    cpa_file = tmp_path / "cpa-bootstrap"
    codex_file.write_text(SECRETS["CODEX_AUTH"])
    cpa_file.write_text(SECRETS["CPA_AUTH"])
    codex_file.chmod(0o600)
    cpa_file.chmod(0o600)
    sources = [
        SecretSource.from_environment(environment, "TEAM_KEY", Broker.BOARD),
        SecretSource.from_temporary_file(codex_file, "CODEX_AUTH", Broker.CODEX),
        SecretSource.from_temporary_file(cpa_file, "CPA_AUTH", Broker.CPA),
    ]
    custody = BootstrapCustody(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-000001",
        redactor=redactor,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )
    result = custody.transfer(sources, environment)
    try:
        yield result, sources, environment
    finally:
        result.close()


def _probe(*, found: bytes | None = None, memory_complete: bool = True):
    surfaces = {kind: b"ordinary" for kind in ("memory", "environment", "argv", "file", "event")}
    if found is not None:
        surfaces["file"] = found
    return classify_surfaces(
        surfaces, tuple(value.encode() for value in SECRETS.values()), memory_complete=memory_complete
    )


def _receipt_setup(
    tmp_path,
    *,
    probe=True,
    probe_result=None,
    peer_failure=True,
    revocation=True,
):
    redactor = _redactor()
    fence = GenerationFence(tmp_path, "run-1", redactor, lambda: "2026-09-11T00:00:00Z")
    fence.acquire("work-1", "attempt-1")
    evidence = CapabilityEvidence(
        tmp_path,
        "run-1",
        "boot-000001",
        redactor,
        lambda: "2026-09-11T00:00:00Z",
    )
    cutover_path = tmp_path / ".env"
    cutover = env_file.atomic_replace(cutover_path, f"TEAM_KEY={SECRETS['TEAM_KEY']}\n")
    evidence.record_env_cutover(cutover)
    context = _bootstrap(tmp_path, redactor)
    result, sources, environment = context.__enter__()
    peer_sequence = [PEER]
    if peer_failure:
        peer_sequence.append(OTHER_PEER)
    if revocation:
        peer_sequence.append(PEER)
    peer_sequence.append(OTHER_PEER)
    peers = Peers(*peer_sequence)
    authority = CapabilityAuthority(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-000001",
        redactor=redactor,
        peer_identity=peers,
        token_bytes=lambda count: b"h" * count,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )
    handle = authority.issue(
        CapabilityBinding(
            "run-1",
            "boot-000001",
            "generation-000001",
            "lane-1",
            "attempt-1",
            "step-1",
        ),
        "board.read",
        PEER,
    )
    authority.authorize(object(), handle)
    if peer_failure:
        with pytest.raises(CapabilityRefused):
            authority.authorize(object(), handle)
    if revocation:
        authority.revoke(handle, "attempt-closed")
        with pytest.raises(CapabilityRefused):
            authority.authorize(object(), handle)
    with pytest.raises(CapabilityRefused):
        authority.authorize(object(), "unknown-handle")
    if probe:
        evidence.record_executor_probe(probe_result or _probe())
    return context, evidence, handle


def test_receipt_proves_real_bootstrap_handles_denials_revocation_and_clear_probes(tmp_path):
    context, _evidence, handle = _receipt_setup(tmp_path)
    try:
        path = write_receipt(tmp_path, "run-1")
        document = json.loads(path.read_text())

        assert path == tmp_path / "runs" / "run-1" / "canonical" / RECEIPT_FILENAME
        assert set(document) == {
            "schema_version",
            "receipt_type",
            "run_id",
            "boot_id",
            "env_cutover",
            "broker_transfers",
            "handle_scopes",
            "peer_auth_failures",
            "revocation_trace",
            "denial_trace",
            "executor_secret_probes",
            "chain_head",
            "manifest_link",
        }
        assert document["env_cutover"]["decision"] == "passed"
        assert document["env_cutover"]["scope"] == "single-env-atomic-replace"
        assert {row["broker"] for row in document["broker_transfers"]} == {"board", "codex", "cpa"}
        assert all(row["secret_names"] == sorted(row["secret_names"]) for row in document["broker_transfers"])
        assert document["handle_scopes"][0]["handle_digest"] != handle
        assert document["handle_scopes"][0]["generation_id"] == "generation-000001"
        assert [row["reason"] for row in document["peer_auth_failures"]] == ["peer-mismatch"]
        assert {row["reason"] for row in document["denial_trace"]} == {
            "peer-mismatch",
            "revoked",
            "unknown-handle",
        }
        assert document["revocation_trace"][0]["reason"] == "attempt-closed"
        assert document["executor_secret_probes"]["checks"] == {
            "memory": "clear",
            "environment": "clear",
            "argv": "clear",
            "file": "clear",
            "event": "clear",
        }
        assert document["executor_secret_probes"]["evidence_digest"]
        assert document["manifest_link"] == {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF}
        assert verify_receipt(path) == path
        assert manifest_receipt(path)["ref"] == MANIFEST_RECEIPT_REF
        serialized = path.read_text()
        assert handle not in serialized
        assert all(secret not in serialized for secret in SECRETS.values())
        capability_events = [
            event
            for event in EventStore(tmp_path, run_id="run-1").events()
            if event.event_type == "capability-custody.recorded"
        ]
        assert len({event.payload["event_id"] for event in capability_events}) == len(capability_events)
    finally:
        context.__exit__(None, None, None)


def test_manifest_row_links_the_verified_receipt(tmp_path):
    context, _evidence, _handle = _receipt_setup(tmp_path)
    try:
        descriptor = manifest_receipt(write_receipt(tmp_path, "run-1"))
        draft = generate_manifest(
            image_digest="sha256:" + "a" * 64,
            release_candidate_profile=release_candidate_profile(),
        )
        requirements = copy.deepcopy(draft["requirements"])
        row = next(item for item in requirements if item["row_id"] == MANIFEST_ROW_ID)
        row.update(status="implemented", receipt_ref=descriptor["ref"])
        linked = generate_manifest(
            image_digest=draft["candidate"]["image_digest"],
            release_candidate_profile=draft["selected_profile"],
            requirements=requirements,
            receipts=[descriptor],
        )
        linked_row = next(item for item in linked["requirements"] if item["row_id"] == MANIFEST_ROW_ID)
        assert linked_row["receipt_ref"] == MANIFEST_RECEIPT_REF
        assert linked_row["status"] == "implemented"
    finally:
        context.__exit__(None, None, None)


def test_tampered_receipt_is_rejected_against_canonical_state(tmp_path):
    context, _evidence, _handle = _receipt_setup(tmp_path)
    try:
        path = write_receipt(tmp_path, "run-1")
        document = json.loads(path.read_text())
        document["chain_head"] = "0" * 64
        path.write_bytes(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        with pytest.raises(InvalidReceiptError):
            verify_receipt(path)
    finally:
        context.__exit__(None, None, None)


def test_missing_or_nonclear_latest_probe_cannot_verify(tmp_path):
    context, _evidence, _handle = _receipt_setup(tmp_path, probe=False)
    try:
        with pytest.raises(InvalidReceiptError):
            verify_receipt(write_receipt(tmp_path, "run-1"))
    finally:
        context.__exit__(None, None, None)


@pytest.mark.parametrize(
    "missing_trace",
    ["peer_failure", "revocation"],
)
def test_receipt_without_a_required_control_trace_cannot_verify(tmp_path, missing_trace):
    options = {missing_trace: False}
    context, _evidence, _handle = _receipt_setup(tmp_path, **options)
    try:
        with pytest.raises(InvalidReceiptError):
            verify_receipt(write_receipt(tmp_path, "run-1"))
    finally:
        context.__exit__(None, None, None)

    nonclear_state = tmp_path / "nonclear"
    nonclear_state.mkdir()
    context, _evidence, _handle = _receipt_setup(
        nonclear_state,
        probe_result=_probe(found=SECRETS["CPA_AUTH"].encode()),
    )
    try:
        with pytest.raises(InvalidReceiptError):
            verify_receipt(write_receipt(nonclear_state, "run-1"))
    finally:
        context.__exit__(None, None, None)


def test_incomplete_memory_probe_is_retained_but_not_verified(tmp_path):
    context, _evidence, _handle = _receipt_setup(tmp_path, probe_result=_probe(memory_complete=False))
    try:
        path = write_receipt(tmp_path, "run-1")
        document = json.loads(path.read_text())
        assert document["executor_secret_probes"]["checks"]["memory"] == "incomplete"
        with pytest.raises(InvalidReceiptError):
            verify_receipt(path)
    finally:
        context.__exit__(None, None, None)
