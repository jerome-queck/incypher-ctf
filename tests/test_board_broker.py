"""Public Board-broker authority and typed-result seam."""

from __future__ import annotations

import hashlib
import json
import socket
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import solver.board_broker as board_broker

from solver.board_broker import (
    BoardBrokerClient,
    BoardBrokerRuntime,
    BoardBrokerService,
    BoardCompatibilityClient,
    BoardProfileClient,
)
from solver.board import MAX_FETCH_BYTES
from solver.board_broker_contracts import (
    BoardBrokerResult,
    BoardOperation,
    BoardOutcome,
    BoardRecord,
    DownloadValue,
    ReadContractValue,
    encode_result,
)
from solver.bootstrap_custody import Broker, BrokerVaultProcess
from solver.capability import CapabilityAuthority, CapabilityBinding, CapabilityRefused, PeerIdentity
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.event_store_contracts import (
    BlobDigestMismatchError,
    LifecycleRecorded,
    MissingBlobError,
    RunClosed,
    TerminalDisposition,
)
from solver.intake_evidence import IntakeEvidenceReader
from solver.intake_journal import IntakeJournal
from solver.intake_qualification import IntakeContract, IntakeDocument, PriorFence
from solver.intake_receipt import write_interrupted_receipt
from solver.redaction import Redactor
from solver.local_ipc import receive_line
from solver.work_generation import GenerationFence
from solver.profile import Rules
from solver.instance_ledger import AuthenticatedIdentity, read_profiled_instance_ledger, write_receipt, verify_receipt


PEER = PeerIdentity(101, 1000, 1000, "11", "0::/controller\n")
OTHER = PeerIdentity(202, 20000, 20000, "22", "0::/executor\n")
PROFILE_HANDLE = "boot-profile-authority"


def test_board_owner_process_serves_a_typed_authenticated_read_over_pathname_ipc(tmp_path: Path) -> None:
    observed = {}

    class BoardEndpoint(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.setdefault("authorizations", []).append(self.headers.get("Authorization"))
            body = b""
            content_type = "application/json"
            if self.path == "/api/v1/users/me":
                body = b'{"success":true,"data":{"id":7,"team_id":null}}'
                status = 200
            elif self.path == "/":
                body = b'<script>window.init = {"userId":7,"teamId":null,"userMode":"users"};</script>'
                content_type = "text/html"
                status = 200
            elif "field=intake-is-not-a-field" in self.path:
                body = b'{"success":false,"message":"invalid field"}'
                status = 403
            elif self.path == "/api/v1/challenges":
                if self.headers.get("Authorization"):
                    body = b'{"success":true,"data":[]}'
                    status = 200
                else:
                    content_type = "text/html"
                    status = 302
            elif self.path == "/plugins/ctfd-chall-manager/instances":
                body = (
                    b'<script>window.init = {"userId":7,"teamId":null,"userMode":"users"};</script>'
                    b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>"
                )
                content_type = "text/html"
                status = 200
            elif self.path.endswith("/mana"):
                body = b'{"success":true,"data":{"used":0,"total":0}}'
                status = 200
            elif self.path == "/api/v1/configs":
                body = b'{"success":true,"data":[]}'
                status = 200
            else:
                status = 404
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if status == 302:
                self.send_header("Location", "/login")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), BoardEndpoint)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    owner, _receipt = BrokerVaultProcess.start(
        Broker.BOARD,
        {"CTFD_API_TOKEN": bytearray(b"board-token"), "TEAM_KEY": bytearray(b"team-key")},
    )
    try:
        socket_path = owner.configure_board(
            state=state,
            run_id="run-1",
            boot_id="boot-000001",
            url=f"http://127.0.0.1:{endpoint.server_port}",
        )
        profiler = BoardProfileClient(socket_path, owner.profile_handle)
        decision = profiler.qualify(
            Rules(
                event="fixture",
                url=f"http://127.0.0.1:{endpoint.server_port}",
                flag_wrappers=(r"flag\{[^}]+\}",),
                window_seconds=1,
            ),
            "fixture.board.json",
        )
        assert decision.authoritative is True
        profiler.open_operations()
        binding = CapabilityBinding(
            "run-1",
            "boot-000001",
            generation.generation_id,
            "lane-1",
            "attempt-1",
            "step-1",
        )

        client = BoardBrokerClient.open(socket_path, binding, scope="board.read")
        try:
            result = client.read_contract()
        finally:
            client.close()

        assert result.outcome is BoardOutcome.ANSWERED
        assert result.value == ReadContractValue(reaches_ctfd=True)
        assert result.provenance.endpoint == "/api/v1/challenges?field=intake-is-not-a-field&q=a"
        assert result.provenance.response_digest
        assert "Token board-token" in observed["authorizations"]
    finally:
        owner.close()
        endpoint.shutdown()
        thread.join()


def _broker(
    tmp_path: Path,
    transport,
    *,
    sealed_response_bytes: int = 4096,
    scope: str = "board.read",
    fetch_bytes: int = MAX_FETCH_BYTES,
):
    state = tmp_path / "state"

    def timestamp():
        return "2026-09-12T00:00:00+00:00"

    generations = GenerationFence(state, "run-1", Redactor({}), timestamp)
    generation = generations.acquire("challenge-7", "attempt-1")
    authority = CapabilityAuthority(
        state=state,
        run_id="run-1",
        boot_id="boot-000001",
        redactor=Redactor({"CTFD_API_TOKEN": "board-token", "TEAM_KEY": "team-key"}),
        peer_identity=lambda _connection: PEER,
        token_bytes=lambda count: b"h" * count,
        timestamp=timestamp,
    )
    binding = CapabilityBinding(
        "run-1",
        "boot-000001",
        generation.generation_id,
        "lane-1",
        "attempt-1",
        "step-1",
    )
    handle = authority.issue(binding, scope, PEER)
    runtime = BoardBrokerRuntime(
        state=state,
        run_id="run-1",
        authority=authority,
        url="https://board.example",
        token="board-token",
        team_key="team-key",
        boot_id="boot-000001",
        transport=transport,
        timestamp=timestamp,
        sealed_response_bytes=sealed_response_bytes,
        fetch_bytes=fetch_bytes,
        profile_handle=PROFILE_HANDLE,
    )
    return state, authority, binding, handle, runtime


def test_profiled_instance_ledger_uses_the_privileged_broker_and_real_board_response(tmp_path: Path) -> None:
    secret = "board-token"
    payload = json.dumps(
        {
            "success": True,
            "data": {
                "page": 1,
                "totalPages": 1,
                "totalRows": 2,
                "rows": [
                    {"instanceId": "ours", "challengeId": 7, "userId": 3, "teamId": 11},
                    {"instanceId": "foreign", "challengeId": 8, "userId": 4, "teamId": 12},
                ],
            },
        }
    ).encode()
    state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda request: (200, payload, "", "application/json"),
        scope="board.instance.read",
    )

    class Broker:
        def instance_ledger_page(self, page):
            return runtime.execute(object(), handle, BoardOperation.INSTANCE_LEDGER_PAGE, page=page)

    result = read_profiled_instance_ledger(AuthenticatedIdentity("teams", 3, 11), Broker())
    receipt_path = write_receipt(state, "run-1", result)

    assert result.outcome == "populated"
    assert [row.row_id for row in result.owned] == ["ours"]
    assert [row.row_id for row in result.foreign] == ["foreign"]
    assert verify_receipt(receipt_path) == receipt_path
    assert secret not in receipt_path.read_text()


def test_authorized_current_generation_completes_typed_authenticated_read(tmp_path: Path) -> None:
    requests = []

    def transport(request):
        requests.append(request)
        return 403, b'{"success":false,"message":"invalid field"}', ""

    state, _authority, binding, handle, runtime = _broker(tmp_path, transport)

    result = runtime.execute(object(), handle, BoardOperation.READ_CONTRACT)

    assert result.outcome is BoardOutcome.ANSWERED
    assert result.value == ReadContractValue(reaches_ctfd=True)
    assert result.operation is BoardOperation.READ_CONTRACT
    assert requests[0].get_header("Authorization") == "Token board-token"
    records = runtime.records()
    assert [record.record for record in records] == ["reserved", "classified"]
    assert records[0].request_id == records[1].request_id
    assert records[0].binding_digest == binding.digest
    assert records[1].response_original_bytes == 43
    assert records[1].response_truncated is False
    assert runtime.response_body(records[1]) == b'{"success":false,"message":"invalid field"}'
    assert b"board-token" not in (state / "runs" / "run-1" / "canonical" / "events.jsonl").read_bytes()


def test_controller_intake_read_seals_exact_raw_privately_and_a_sanitized_canonical_view(tmp_path: Path) -> None:
    raw = b'{"success":true,"data":{"description":"board-token"}}'
    state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (200, raw, "", "application/json"),
        scope="board.intake",
    )

    result = runtime.execute(object(), handle, BoardOperation.INTAKE_READ, path="/api/v1/challenges/7")

    assert result.outcome is BoardOutcome.ANSWERED
    assert result.value.body == raw
    classified = runtime.records()[-1]
    assert classified.raw_blob_class == "canonical-private-board-response"
    assert classified.response_truncated is False
    assert classified.response_lost_bytes == 0
    assert classified.response_sanitized_bytes == len(runtime.response_body(classified))
    assert (
        IntakeEvidenceReader(state, "run-1").read(
            classified.raw_blob_digest,
            classified_event_id=classified.event_id,
            sanitized=runtime.response_body(classified),
        )
        == raw
    )
    registration = next((state / "runs" / "run-1" / "sealed" / "private-board-response" / "references").iterdir())
    registered = json.loads(registration.read_text())
    assert registered["classified_event_id"] == classified.event_id
    assert registered["storage_class"] == "restart-private-broker"
    assert registered["retention"] == "retain-for-replay-unless-interrupted-attempt-retires"
    assert registered["export"] is False
    assert registered["model_readable"] is False
    assert b"board-token" not in runtime.response_body(classified)
    assert runtime.response_body(classified) != raw
    assert b"board-token" not in (state / "runs" / "run-1" / "canonical" / "events.jsonl").read_bytes()


def test_private_raw_reader_replays_the_sealed_redaction_transform(tmp_path: Path) -> None:
    raw = b'{"secret":"board-token"}'
    state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (200, raw, "", "application/json"),
        scope="board.intake",
    )
    runtime.execute(object(), handle, BoardOperation.INTAKE_READ, path="/api/v1/challenges")
    classified = runtime.records()[-1]
    reference = next((state / "runs" / "run-1" / "sealed" / "private-board-response" / "references").iterdir())
    registration = json.loads(reference.read_text())
    registration["redaction"]["steps"][0]["positions"] = [0]
    reference.write_text(json.dumps(registration, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(BlobDigestMismatchError, match="redaction"):
        IntakeEvidenceReader(state, "run-1").read(
            classified.raw_blob_digest,
            classified_event_id=classified.event_id,
            sanitized=runtime.response_body(classified),
        )


def test_canonical_broker_event_rejects_an_internally_consistent_substitute_redaction_policy(tmp_path: Path) -> None:
    raw = b'{"secret":"board-token"}'
    state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (200, raw, "", "application/json"),
        scope="board.intake",
    )
    runtime.execute(object(), handle, BoardOperation.INTAKE_READ, path="/api/v1/challenges")
    classified = runtime.records()[-1]
    reference = next((state / "runs" / "run-1" / "sealed" / "private-board-response" / "references").iterdir())
    registration = json.loads(reference.read_text())
    registration["redaction"]["policy"]["forms"].append({"name": "SUBSTITUTE", "bytes": 8, "digest": "0" * 64})
    registration["redaction"]["policy_digest"] = hashlib.sha256(
        canonical_bytes(registration["redaction"]["policy"])
    ).hexdigest()
    reference.write_bytes(canonical_bytes(registration) + b"\n")

    with pytest.raises(BlobDigestMismatchError, match="canonically reachable"):
        IntakeEvidenceReader(state, "run-1").read(
            classified.raw_blob_digest,
            classified_event_id=classified.event_id,
            sanitized=runtime.response_body(classified),
        )


def test_intake_over_limit_preserves_only_the_bounded_lower_bound_evidence(tmp_path: Path) -> None:
    raw = b"12345"
    state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (200, raw, "", "application/octet-stream"),
        scope="board.intake",
        fetch_bytes=4,
    )

    result = runtime.execute(object(), handle, BoardOperation.INTAKE_READ, path="/files/archive")

    assert result.outcome is BoardOutcome.TOO_LARGE
    assert result.value.body == raw
    classified = runtime.records()[-1]
    assert classified.response_original_bytes == 5
    assert classified.raw_blob_bytes == 5
    assert (
        IntakeEvidenceReader(state, "run-1").read(
            classified.raw_blob_digest,
            classified_event_id=classified.event_id,
        )
        == raw
    )


def test_intake_cap_plus_one_crosses_the_real_service_client_seam(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(board_broker, "MAX_FETCH_BYTES", 4)
    raw = b"12345"
    _state, authority, binding, _handle, runtime = _broker(
        tmp_path,
        lambda _request: (200, raw, "", "application/octet-stream"),
        fetch_bytes=4,
    )
    runtime._profile_session_opened = True
    runtime._profile_receipt_digest = "a" * 64
    runtime._profile_peer_digest = PEER.digest
    authority._token_bytes = lambda count: b"i" * count
    socket_root = Path(tempfile.mkdtemp(prefix="bb-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    try:
        client, _peer, _profile = BoardBrokerClient.open_intake(service.path, binding, PROFILE_HANDLE)

        result = client.intake_read("/files/archive")

        assert result.outcome is BoardOutcome.TOO_LARGE
        assert result.value.body == raw
        client.close()
    finally:
        service.close()


def test_interrupted_attempt_retires_its_reference_but_preserves_a_shared_live_blob(tmp_path: Path) -> None:
    raw = b'{"success":true,"data":[]}'
    state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (200, raw, "", "application/json"),
        scope="board.intake",
    )
    first = runtime.execute(object(), handle, BoardOperation.INTAKE_READ, path="/api/v1/challenges")
    second = runtime.execute(object(), handle, BoardOperation.INTAKE_READ, path="/api/v1/challenges")
    profile = "1" * 64
    subject = "2" * 64
    journal = IntakeJournal(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-12T00:00:00+00:00")
    journal.start("attempt-interrupted", IntakeContract(profile, subject), PriorFence.genesis(profile))
    provenance = first.provenance
    journal.observe(
        "attempt-interrupted",
        IntakeDocument(
            first.request_id,
            provenance.classified_event_id,
            1,
            "list",
            "/api/v1/challenges?page=1",
            provenance.http_status,
            provenance.content_type,
            raw,
            provenance.original_bytes,
            True,
            profile,
            subject,
            provenance.binding_digest,
            provenance.peer_identity_digest,
            page=1,
            raw_blob_digest=provenance.raw_blob_digest,
            sanitized_blob_digest=provenance.sanitized_blob_digest,
            request_digest=provenance.request_digest,
        ),
    )
    journal.close_orphans()

    write_interrupted_receipt(state, "run-1", "attempt-interrupted")

    with pytest.raises(MissingBlobError, match="registration is unavailable"):
        IntakeEvidenceReader(state, "run-1").read(
            first.provenance.raw_blob_digest,
            classified_event_id=first.provenance.classified_event_id,
        )
    assert (
        IntakeEvidenceReader(state, "run-1").read(
            second.provenance.raw_blob_digest,
            classified_event_id=second.provenance.classified_event_id,
        )
        == raw
    )


def test_only_profile_bound_controller_command_can_issue_intake_scope(tmp_path: Path) -> None:
    _state, authority, binding, _handle, runtime = _broker(
        tmp_path,
        lambda _request: (200, b'{"success":true,"data":[]}', "", "application/json"),
    )
    runtime._profile_session_opened = True
    runtime._profile_receipt_digest = "a" * 64
    runtime._profile_peer_digest = PEER.digest
    authority._token_bytes = lambda count: b"i" * count
    socket_root = Path(tempfile.mkdtemp(prefix="bb-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    try:
        with pytest.raises(CapabilityRefused):
            BoardBrokerClient.open(service.path, binding, scope="board.intake")
        client, peer_digest, profile_digest = BoardBrokerClient.open_intake(
            service.path,
            binding,
            PROFILE_HANDLE,
        )
        try:
            result = client.intake_read("/api/v1/challenges?page=1")
        finally:
            client.close()
    finally:
        service.close()
        socket_root.rmdir()

    assert result.outcome is BoardOutcome.ANSWERED
    assert peer_digest == PEER.digest
    assert profile_digest == "a" * 64


def test_compatibility_download_crosses_the_bounded_ipc_as_binary_not_a_json_frame(tmp_path: Path) -> None:
    payload = bytes(range(256)) * 320
    _state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (200, payload, ""),
    )
    socket_root = Path(tempfile.mkdtemp(prefix="bb-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    try:
        downloaded, hops = BoardCompatibilityClient(service.path).download("files/large.bin")
        typed = BoardBrokerClient(service.path, handle).download("files/large.bin")
    finally:
        service.close()
        socket_root.rmdir()

    assert downloaded == payload
    assert hops == []
    assert typed.value == DownloadValue(payload, ())
    assert typed.provenance.response_digest == hashlib.sha256(payload).hexdigest()
    assert typed.request_id == "board-broker:000001"


@pytest.mark.parametrize(
    ("declared_bytes", "message", "acknowledged"),
    [
        (MAX_FETCH_BYTES, "incomplete Board broker binary response", True),
        (MAX_FETCH_BYTES + 1, "exceeds the download bound", False),
    ],
)
def test_binary_download_protocol_enforces_the_board_limit_without_allocating_the_body(
    declared_bytes: int,
    message: str,
    acknowledged: bool,
) -> None:
    socket_root = Path(tempfile.mkdtemp(prefix="bb-", dir="/tmp"))
    socket_path = socket_root / "board.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    acknowledgements = []
    header = {
        "status": "answered",
        "result": encode_result(
            BoardBrokerResult(BoardOperation.DOWNLOAD, BoardOutcome.ANSWERED, DownloadValue(b"", ()))
        ),
        "binary": {"bytes": declared_bytes, "digest": "0" * 64},
    }

    def serve_header():
        connection, _address = listener.accept()
        try:
            receive_line(connection, failure="request absent")
            connection.sendall(canonical_bytes(header) + b"\n")
            if declared_bytes <= MAX_FETCH_BYTES:
                acknowledgements.append(receive_line(connection, failure="acknowledgement absent"))
        finally:
            connection.close()

    thread = threading.Thread(target=serve_header)
    thread.start()
    try:
        with pytest.raises(ValueError, match=message):
            BoardCompatibilityClient(socket_path).download("files/boundary.bin")
        thread.join(5)
        assert not thread.is_alive()
    finally:
        listener.close()
        socket_path.unlink(missing_ok=True)
        socket_root.rmdir()

    assert acknowledgements == (["ready"] if acknowledged else [])


@pytest.mark.parametrize(
    ("transport", "outcome"),
    [
        (lambda _request: (_ for _ in ()).throw(TimeoutError("late")), BoardOutcome.TIMEOUT),
        (lambda _request: (_ for _ in ()).throw(OSError("lost")), BoardOutcome.UNREACHABLE),
        (lambda _request: (200, b"<html>not json</html>", ""), BoardOutcome.MALFORMED),
        (lambda _request: (401, b'{"success":false}', ""), BoardOutcome.AUTH_FAILURE),
    ],
)
def test_read_failures_remain_distinct_and_never_become_empty(tmp_path: Path, transport, outcome) -> None:
    _state, _authority, _binding, handle, runtime = _broker(tmp_path, transport)

    result = runtime.execute(object(), handle, BoardOperation.CHALLENGES)

    assert result.outcome is outcome
    assert result.value is None


@pytest.mark.parametrize(
    ("operation", "scope", "arguments", "failure"),
    [
        (BoardOperation.SUBMIT, "board.submit", {"challenge_id": 7, "flag": "flag"}, TimeoutError("late")),
        (BoardOperation.INSTANCE_DEPLOY, "board.instance.effect", {"challenge_id": 7}, OSError("lost")),
        (BoardOperation.INSTANCE_RENEW, "board.instance.effect", {"challenge_id": 7}, TimeoutError("late")),
        (BoardOperation.INSTANCE_TERMINATE, "board.instance.effect", {"challenge_id": 7}, OSError("lost")),
    ],
)
def test_effect_transport_loss_is_one_reservation_bound_indeterminate_result(
    tmp_path: Path,
    operation: BoardOperation,
    scope: str,
    arguments: dict[str, object],
    failure: OSError,
) -> None:
    calls = []

    def transport(request):
        calls.append(request)
        raise failure

    _state, _authority, _binding, handle, runtime = _broker(tmp_path, transport, scope=scope)

    result = runtime.execute(object(), handle, operation, **arguments)

    assert result.outcome is BoardOutcome.EFFECT_INDETERMINATE
    assert result.value is None
    assert result.request_id == "board-broker:000001"
    assert len(calls) == 1
    records = runtime.records()
    assert [record.request_id for record in records] == [result.request_id, result.request_id]
    assert records[-1].outcome == BoardOutcome.EFFECT_INDETERMINATE.value


def test_effect_classification_append_failure_keeps_the_durable_request_indeterminate(tmp_path: Path) -> None:
    calls = []
    _state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda request: (
            calls.append(request) or (200, b'{"success":true,"data":{"status":"correct","message":""}}', "")
        ),
        scope="board.submit",
    )
    append = runtime._store.append

    def fail_classification(event, *, body):
        if event.record is BoardRecord.CLASSIFIED:
            raise OSError("classification ledger unavailable")
        return append(event, body=body)

    runtime._store.append = fail_classification

    result = runtime.execute(object(), handle, BoardOperation.SUBMIT, challenge_id=7, flag="flag")

    assert result.outcome is BoardOutcome.EFFECT_INDETERMINATE
    assert result.value is None
    assert result.request_id == "board-broker:000001"
    assert len(calls) == 1
    records = runtime.records()
    assert [record.record for record in records] == [BoardRecord.RESERVED.value]
    assert records[0].request_id == result.request_id


def test_wrong_peer_closed_generation_and_revocation_are_denied_before_transport(tmp_path: Path) -> None:
    calls = []
    state, authority, binding, handle, runtime = _broker(
        tmp_path,
        lambda request: calls.append(request) or (403, b"{}", ""),
    )
    authority._peer_identity = lambda _connection: OTHER
    assert runtime.execute(object(), handle, BoardOperation.READ_CONTRACT).outcome is BoardOutcome.CAPABILITY_REFUSED
    authority._peer_identity = lambda _connection: PEER
    GenerationFence(state, "run-1", Redactor({}), lambda: "later").close(binding.generation_id, "supersede")
    assert runtime.execute(object(), handle, BoardOperation.READ_CONTRACT).outcome is BoardOutcome.CAPABILITY_REFUSED
    assert calls == []

    _state2, authority2, _binding2, handle2, runtime2 = _broker(
        tmp_path / "revoked",
        lambda request: calls.append(request) or (403, b"{}", ""),
    )
    authority2.revoke(handle2, "attempt-closed")
    assert runtime2.execute(object(), handle2, BoardOperation.READ_CONTRACT).outcome is BoardOutcome.REVOKED
    assert calls == []


def test_wrong_scope_is_capability_refused_before_transport(tmp_path: Path) -> None:
    calls = []
    _state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda request: calls.append(request) or (200, b"{}", ""),
    )

    result = runtime.execute(object(), handle, BoardOperation.SUBMIT, challenge_id=7, flag="flag")

    assert result.outcome is BoardOutcome.CAPABILITY_REFUSED
    assert calls == []


@pytest.mark.parametrize(
    ("close_authority", "outcome"),
    [
        (lambda state, authority, binding, handle: authority.revoke(handle, "attempt-closed"), BoardOutcome.REVOKED),
        (
            lambda state, authority, binding, _handle: GenerationFence(
                state, "run-1", Redactor({}), lambda: "later"
            ).close(binding.generation_id, "supersede"),
            BoardOutcome.CAPABILITY_REFUSED,
        ),
    ],
)
def test_authority_lost_during_transport_suppresses_the_late_value(tmp_path: Path, close_authority, outcome) -> None:
    late = []

    def transport(_request):
        close_authority(state, authority, binding, handle)
        late.append(True)
        return 403, b'{"success":false}', ""

    state, authority, binding, handle, runtime = _broker(tmp_path, transport)

    result = runtime.execute(object(), handle, BoardOperation.READ_CONTRACT)

    assert late == [True]
    assert result.outcome is outcome
    assert result.value is None


def test_response_evidence_is_bounded_with_exact_loss_metadata(tmp_path: Path) -> None:
    body = json.dumps({"success": False, "message": "team-key" + "x" * 100}).encode()
    _state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda _request: (403, body, ""),
        sealed_response_bytes=32,
    )

    runtime.execute(object(), handle, BoardOperation.READ_CONTRACT)

    record = runtime.records()[-1]
    assert record.response_original_bytes == len(body)
    assert record.response_truncated is True
    assert record.response_lost_bytes == record.response_sanitized_bytes - 32
    assert len(runtime.response_body(record)) <= 32
    assert b"team-key" not in runtime.response_body(record)


def test_canonical_reservation_refusal_is_typed_and_prevents_transport(tmp_path: Path) -> None:
    calls = []
    state, _authority, _binding, handle, runtime = _broker(
        tmp_path,
        lambda request: calls.append(request) or (403, b"{}", ""),
    )
    EventStore(state, run_id="run-1").append(
        LifecycleRecorded("run:close", RunClosed(TerminalDisposition.REFUSED), ts="later"),
        body=b"",
    )

    result = runtime.execute(object(), handle, BoardOperation.READ_CONTRACT)

    assert result.outcome is BoardOutcome.RESERVATION_REFUSED
    assert calls == []
