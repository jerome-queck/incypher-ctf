"""Independent Board-broker receipt projection and verification."""

from __future__ import annotations

import json
import tempfile
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from solver.board_broker import BoardBrokerClient, BoardBrokerService, denied_probe
from solver.board_broker_contracts import BoardOperation
from solver.board_broker_receipt import MANIFEST_ROW_ID, link_manifest, verify_receipt, write_receipt
from solver.board_broker_receipt_projection import receipt_document
from solver.capability_evidence import CapabilityEvidence
from solver.event_store import EventStore, InvalidReceiptError
from solver.executor_secret_probe import classify_surfaces
from solver.manifest import generate_manifest
from solver.redaction import Redactor

from test_board_broker import _broker
from test_manifest import release_candidate_profile


def _record_secret_proofs(state, authority, runtime, handle):
    socket_root = Path(tempfile.mkdtemp(prefix="bb-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    evidence = CapabilityEvidence(
        state,
        "run-1",
        "boot-000001",
        Redactor({"TEAM_KEY": "team-key"}),
        lambda: "2026-09-12T00:00:00+00:00",
    )
    try:
        response = denied_probe(service.path, b"team-key")
    finally:
        service.close()
        socket_root.rmdir()
    evidence.record_socket_probe(response, (b"team-key",))
    evidence.record_executor_probe(
        classify_surfaces(
            {name: b"clear" for name in ("memory", "environment", "argv", "file", "event")},
            (b"team-key",),
        )
    )


def test_receipt_binds_scopes_callers_classifications_and_sealed_responses(tmp_path: Path) -> None:
    state, authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (403, b'{"success":false,"message":"team-key"}', ""),
    )
    _record_secret_proofs(state, authority, runtime, handle)
    runtime.execute(object(), handle, BoardOperation.READ_CONTRACT)

    path = write_receipt(state, "run-1")
    verified = verify_receipt(path)
    document = json.loads(verified.read_text())

    assert document["receipt_type"] == "board-broker"
    assert document["requests"][0]["scope"] == "board.read"
    assert document["requests"][0]["binding"] == {
        "run_id": "run-1",
        "boot_id": "boot-000001",
        "generation_id": "generation-000001",
        "lane_id": "lane-1",
        "attempt_id": "attempt-1",
        "step_id": "step-1",
    }
    assert document["requests"][0]["classification"] == "answered"
    assert document["requests"][0]["caller_identity_digest"]
    assert document["requests"][0]["response"]["original_bytes"] == 38
    assert document["requests"][0]["response"]["sealed_digest"]
    assert document["manifest_link"] == {
        "row_id": MANIFEST_ROW_ID,
        "receipt_ref": "receipt:board-broker",
    }
    assert document["secret_leak_probes"] == {
        "argv": "clear",
        "environment": "clear",
        "event": "clear",
        "file": "clear",
        "memory": "clear",
        "socket": "refused",
    }
    assert "team-key" not in verified.read_text()


def test_receipt_pairs_concurrent_requests_by_identity_when_they_complete_in_reverse(tmp_path: Path) -> None:
    both_in_transport = threading.Barrier(2)
    slow_entered = threading.Event()
    release_slow = threading.Event()
    transport_lock = threading.Lock()
    transport_count = 0

    def transport(_request):
        nonlocal transport_count
        with transport_lock:
            transport_count += 1
            is_slow = transport_count == 1
        if is_slow:
            slow_entered.set()
            both_in_transport.wait()
            assert release_slow.wait(5)
        else:
            both_in_transport.wait()
        return 403, b'{"success":false,"message":"invalid field"}', ""

    state, authority, _binding, handle, runtime = _broker(tmp_path, transport)
    _record_secret_proofs(state, authority, runtime, handle)
    socket_root = Path(tempfile.mkdtemp(prefix="bb-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    client = BoardBrokerClient(service.path, handle)
    results = []

    def read():
        results.append(client.read_contract())

    slow = threading.Thread(target=read, name="slow-request")
    fast = threading.Thread(target=read, name="fast-request")
    try:
        slow.start()
        assert slow_entered.wait(5)
        fast.start()
        fast.join(5)
        assert not fast.is_alive()
        release_slow.set()
        slow.join(5)
        assert not slow.is_alive()
    finally:
        release_slow.set()
        service.close()
        socket_root.rmdir()

    verified = verify_receipt(write_receipt(state, "run-1"))
    requests = json.loads(verified.read_text())["requests"]

    assert len(results) == 2
    assert [request["request_id"] for request in requests] == ["board-broker:000001", "board-broker:000002"]
    assert requests[0]["classification_sequence"] > requests[1]["classification_sequence"]


def test_receipt_rejects_duplicate_records_for_one_request_identity(tmp_path: Path) -> None:
    state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (403, b"{}", ""),
    )
    runtime.execute(object(), handle, BoardOperation.READ_CONTRACT)
    events = EventStore(state, run_id="run-1").events()
    classified = next(event for event in events if event.payload.get("record") == "classified")

    with pytest.raises(InvalidReceiptError, match="incomplete or duplicate"):
        receipt_document("run-1", [*events, replace(classified, sequence=classified.sequence + 1)])


def test_candidate_manifest_row_accepts_the_independently_verified_receipt(tmp_path: Path) -> None:
    state, authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (403, b"{}", ""),
    )
    _record_secret_proofs(state, authority, runtime, handle)
    runtime.execute(object(), handle, BoardOperation.READ_CONTRACT)
    path = write_receipt(state, "run-1")
    draft = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    linked = link_manifest(draft, path)

    linked_row = next(item for item in linked["requirements"] if item["row_id"] == MANIFEST_ROW_ID)
    assert linked_row["receipt_ref"] == "receipt:board-broker"


def test_receipt_verifier_rejects_tampering(tmp_path: Path) -> None:
    state, authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (403, b"{}", ""),
    )
    _record_secret_proofs(state, authority, runtime, handle)
    runtime.execute(object(), handle, BoardOperation.READ_CONTRACT)
    path = write_receipt(state, "run-1")
    document = json.loads(path.read_text())
    document["requests"][0]["classification"] = "malformed"
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(InvalidReceiptError, match="canonical state"):
        verify_receipt(path)
