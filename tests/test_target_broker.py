"""Attempt-scoped Target exchanges cross one bounded pathname broker."""

from __future__ import annotations

import hashlib
import datetime as dt
import json
import socket
import tempfile
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from solver.capability import CapabilityBinding, CapabilityRefused
from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import NetworkProbeDeclaration, ResourceOutcome, RuntimeBinding
from solver.event_store import GenerationDisposition
from solver.event_store import EventStore
from solver.redaction import Redactor
from solver.recovery.incident import RECEIPT
from solver.recovery.runtime import DeterministicRecovery
from solver.target_broker import TargetBrokerRuntime
from solver.target_broker_contracts import (
    TARGET_EXCHANGE_RECORDED,
    TargetCandidateBinding,
    TargetEndpoint,
    TargetLimits,
    TargetOutcome,
    TargetProtocol,
)
from solver.target_broker_ipc import (
    CLIENT_TIMEOUT_SECONDS,
    MAX_CLIENTS,
    TargetBrokerClient,
    TargetBrokerService,
)
from solver.target_broker_receipt import capsule_contract, link_manifest, verify_receipt, write_receipt
from solver.target_broker_transport import TransportResult
from solver.work_generation import GenerationFence
from test_attempt_executor import (
    IMAGE_ID,
    ImmediateRuntime,
    OutcomeRuntime,
    isolation_receipt,
    request as attempt_request,
)
from test_manifest import draft

TEST_CANDIDATE = TargetCandidateBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64", "d" * 64)


def claim_client(runtime, service, binding):
    runtime.prepare_attempt(binding)
    return TargetBrokerClient.claim(service.path, binding.generation_id)


def test_safe_read_timeout_is_contained_without_an_identical_target_retry(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    attempts = []

    def exchange(*_args):
        attempts.append(True)
        if len(attempts) == 1:
            return TransportResult(TargetOutcome.TIMEOUT)
        return TransportResult(TargetOutcome.ANSWERED, b"answer", 200, request_bytes=32, response_bytes=38)

    monkeypatch.setattr("solver.target_broker.transport_exchange", exchange)
    recovery = DeterministicRecovery(
        state,
        "run-1",
        Redactor({}),
        now=lambda: dt.datetime(2026, 9, 12, tzinfo=dt.timezone.utc),
    )
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 9),
        limits=TargetLimits(3, 256, 256, 0.1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
        recovery=recovery,
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        result = runtime.exchange(
            left,
            handle,
            {"method": "GET", "path": "/", "body": ""},
        )
        refused = runtime.exchange(left, handle, {"method": "GET", "path": "/", "body": ""})
        unrelated = runtime.exchange(left, handle, {"method": "GET", "path": "/other", "body": ""})
    finally:
        left.close()
        right.close()

    assert result.outcome is TargetOutcome.TIMEOUT
    assert refused.outcome is TargetOutcome.DENIED
    assert unrelated.outcome is TargetOutcome.ANSWERED
    assert len(attempts) == 2
    receipt = json.loads((state / "runs" / "run-1" / "canonical" / RECEIPT).read_text())
    assert receipt["changed_action"] == {}
    assert receipt["probe"]["outcome"] == "unsettled"


def test_unsafe_target_timeout_is_fenced_without_inference_or_retry(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    attempts = []
    monkeypatch.setattr(
        "solver.target_broker.transport_exchange",
        lambda *_args: (attempts.append(True), TransportResult(TargetOutcome.TIMEOUT))[1],
    )
    recovery = DeterministicRecovery(
        state,
        "run-1",
        Redactor({}),
        now=lambda: dt.datetime(2026, 9, 12, tzinfo=dt.timezone.utc),
    )
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 9),
        limits=TargetLimits(3, 256, 256, 0.1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
        recovery=recovery,
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        result = runtime.exchange(left, handle, {"method": "POST", "path": "/", "body": "cGF5bG9hZA=="})
        refused = runtime.exchange(left, handle, {"method": "GET", "path": "/", "body": ""})
    finally:
        left.close()
        right.close()

    assert result.outcome is TargetOutcome.TIMEOUT
    assert refused.outcome is TargetOutcome.REVOKED
    assert len(attempts) == 1
    receipt = json.loads((state / "runs" / "run-1" / "canonical" / RECEIPT).read_text())
    assert receipt["disposition"] == "probation"
    assert receipt["changed_action"] == {}


def test_hostile_exchange_schema_and_non_ascii_request_are_canonically_classified(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 9),
        limits=TargetLimits(3, 256, 256, 0.1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        results = (
            runtime.exchange(left, handle, {"method": "GET", "path": "/", "body": "", "extra": "x"}),
            runtime.exchange(left, handle, {"method": "GET", "path": "/", "body": 1}),
            runtime.exchange(left, handle, {"method": "GET", "path": "/snowman-☃", "body": ""}),
            runtime.exchange(left, handle, {"method": "GET", "path": "/", "body": "%%%"}),
        )
    finally:
        left.close()
        right.close()

    assert {result.outcome for result in results} == {TargetOutcome.DENIED}
    events = [
        event for event in EventStore(state, run_id="run-1").events() if event.event_type == TARGET_EXCHANGE_RECORDED
    ]
    assert len(events) == 8
    assert [event.payload["record"] for event in events] == ["reserved", "classified"] * 4


def test_hostile_socket_is_claim_only_and_one_shot(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.TCP, "127.0.0.1", 9),
        limits=TargetLimits(1, 4, 4, 0.1),
        timestamp=lambda: "now",
    )
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
    with tempfile.TemporaryDirectory(prefix="target-broker-", dir="/tmp") as directory:
        service = TargetBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        slow = []
        for _ in range(MAX_CLIENTS):
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            connection.connect(str(service.path))
            connection.sendall(b"{")
            slow.append(connection)
        time.sleep(0.1)
        overflow = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        overflow.settimeout(0.5)
        overflow.connect(str(service.path))
        assert overflow.recv(1) == b""
        overflow.close()
        time.sleep(CLIENT_TIMEOUT_SECONDS + 0.1)
        for connection in slow:
            connection.close()
        runtime.prepare_attempt(binding)
        first = TargetBrokerClient.claim(service.path, generation.generation_id)
        with pytest.raises(CapabilityRefused):
            TargetBrokerClient.claim(service.path, generation.generation_id)
        assert not hasattr(TargetBrokerClient, "open")
        assert not hasattr(TargetBrokerClient, "probe")
        first.close()
        service.close()


def test_worker_completes_http_exchange_with_its_declared_target_through_broker(tmp_path: Path) -> None:
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"target-response"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", target.server_port),
        limits=TargetLimits(max_connections=1, max_request_bytes=256, max_response_bytes=256, timeout_seconds=1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    with tempfile.TemporaryDirectory(prefix="target-broker-", dir="/tmp") as directory:
        service = TargetBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        try:
            client = claim_client(
                runtime,
                service,
                CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1"),
            )
            result = client.http("GET", "/hello")
            client.close()
        finally:
            service.close()
            target.shutdown()
            target_thread.join()

    assert result.outcome is TargetOutcome.ANSWERED
    assert result.status == 200
    assert result.body == b"target-response"
    assert result.provenance.challenge_id == "challenge-7"
    assert result.provenance.generation_id == generation.generation_id
    assert result.provenance.endpoint == "declared-target"
    assert result.provenance.protocol is TargetProtocol.HTTP
    assert result.provenance.request_bytes > len(b"")
    assert result.provenance.response_bytes > len(result.body)


def test_http_response_headers_are_capped_below_library_defaults(tmp_path: Path) -> None:
    class HeaderTarget(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("X-Oversized", "x" * 1024)
            self.end_headers()

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), HeaderTarget)
    thread = threading.Thread(target=target.serve_forever, daemon=True)
    thread.start()
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", target.server_port),
        limits=TargetLimits(1, 256, 128, 1),
        timestamp=lambda: "now",
    )
    with tempfile.TemporaryDirectory(prefix="target-broker-", dir="/tmp") as directory:
        service = TargetBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        client = claim_client(
            runtime,
            service,
            CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1"),
        )
        result = client.http("GET", "/")
        client.close()
        service.close()
    target.shutdown()
    thread.join()

    assert result.outcome is TargetOutcome.TOO_LARGE
    assert result.provenance.response_bytes == 0


def test_tcp_exchange_is_bounded_and_old_generation_is_revoked_before_replacement(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    calls = []

    def serve():
        connection, _ = listener.accept()
        with connection:
            calls.append(connection.recv(32))
            connection.sendall(b"pong")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), lambda: "now")
    old = fence.acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.TCP, "127.0.0.1", listener.getsockname()[1]),
        limits=TargetLimits(1, 4, 4, 1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    with tempfile.TemporaryDirectory(prefix="target-broker-", dir="/tmp") as directory:
        service = TargetBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        client = claim_client(
            runtime,
            service,
            CapabilityBinding("run-1", "boot-1", old.generation_id, "lane-1", "attempt-1", "step-1"),
        )
        answered = client.tcp(b"ping")
        fence.close(old.generation_id, "supersede")
        refused = client.tcp(b"late")
        service.close()
    listener.close()
    thread.join()

    assert answered.outcome is TargetOutcome.ANSWERED
    assert answered.body == b"pong"
    assert answered.provenance.request_bytes == 4
    assert answered.provenance.response_bytes == 4
    assert answered.provenance.transcript_digest
    assert refused.outcome is TargetOutcome.CAPABILITY_REFUSED
    assert calls == [b"ping"]


def test_undeclared_destinations_are_denied_and_receipt_is_independently_verifiable(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    kinds = ("sibling-target", "board", "research", "control", "private", "undeclared-port")
    denied_endpoints = {
        kind: TargetEndpoint(TargetProtocol.TCP, "127.0.0.1", 10000 + index) for index, kind in enumerate(kinds)
    }
    denied_endpoints["control"] = denied_endpoints["board"]
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 9),
        limits=TargetLimits(1, 256, 256, 0.1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
        denied_endpoints=denied_endpoints,
    )
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(
            TEST_CANDIDATE.image_id,
            TEST_CANDIDATE.manifest_digest,
            TEST_CANDIDATE.config_digest,
            TEST_CANDIDATE.platform,
        ),
        generation_fence=GenerationFence(state, "run-1", Redactor({}), lambda: "now"),
        runtime=OutcomeRuntime(ResourceOutcome.NETWORK),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
    left, right = socket.socketpair()
    try:
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        malformed = (
            runtime.exchange(left, handle, {"method": "GET", "path": "/", "body": "", "extra": "x"}),
            runtime.exchange(left, handle, {"method": "GET", "path": "/", "body": 1}),
            runtime.exchange(left, handle, {"method": "GET", "path": "/snowman-☃", "body": ""}),
            runtime.exchange(left, handle, {"method": "GET", "path": "/", "body": "%%%"}),
        )
    finally:
        left.close()
        right.close()
    with tempfile.TemporaryDirectory(prefix="target-broker-", dir="/tmp") as directory:
        service = TargetBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        client = claim_client(
            runtime,
            service,
            binding,
        )
        outcomes = {}
        for kind in kinds:
            endpoint = denied_endpoints[kind]
            declaration = NetworkProbeDeclaration(
                kind, hashlib.sha256(f"{endpoint.host}:{endpoint.port}".encode()).hexdigest()
            )
            request = replace(attempt_request(tmp_path, generation.generation_id), network_probe=declaration)
            assert executor.start(request).result().outcome is ResourceOutcome.NETWORK
            outcomes[kind] = runtime.record_denial(binding, kind).outcome
        with pytest.raises(ValueError, match="not observed"):
            runtime.record_denial(binding, "board")

        wrong = replace(
            attempt_request(tmp_path, generation.generation_id),
            network_probe=NetworkProbeDeclaration("board", "f" * 64),
        )
        assert executor.start(wrong).result().outcome is ResourceOutcome.NETWORK
        with pytest.raises(ValueError, match="not observed"):
            runtime.record_denial(binding, "board")

        same_address = denied_endpoints["board"]
        relabelled = replace(
            attempt_request(tmp_path, generation.generation_id),
            network_probe=NetworkProbeDeclaration(
                "board", hashlib.sha256(f"{same_address.host}:{same_address.port}".encode()).hexdigest()
            ),
        )
        assert executor.start(relabelled).result().outcome is ResourceOutcome.NETWORK
        with pytest.raises(ValueError, match="not observed"):
            runtime.record_denial(binding, "control")
        oversized = client.http("GET", "/" + "x" * 300)
        unreachable = client.http("GET", "/")
        exhausted = client.http("GET", "/again")
        client.close()
        service.close()
        executor.close()

    assert set(outcomes.values()) == {TargetOutcome.DENIED}
    assert {result.outcome for result in malformed} == {TargetOutcome.DENIED}
    assert oversized.outcome is TargetOutcome.TOO_LARGE
    assert oversized.provenance.request_bytes == 0
    assert unreachable.outcome is TargetOutcome.UNREACHABLE
    assert exhausted.outcome is TargetOutcome.BUDGET_EXHAUSTED
    assert exhausted.provenance.request_bytes == 0
    receipt = write_receipt(state, "run-1")
    assert verify_receipt(receipt) == receipt
    document = __import__("json").loads(receipt.read_text())
    assert document["receipt_type"] == "target-broker"
    assert document["kind"] == "target-broker"
    assert document["producer"] == "target-broker"
    assert document["generation_accounting"][generation.generation_id]["connections"] == 1
    assert sum(request["classification"] == "denied" for request in document["requests"]) >= len(malformed)
    limit_attempts = [
        request for request in document["requests"] if request["classification"] in {"too-large", "budget-exhausted"}
    ]
    assert {request["classification"] for request in limit_attempts} == {
        "too-large",
        "budget-exhausted",
    }
    assert all(request["request_bytes"] == 0 for request in limit_attempts)
    assert document["revocation_trace"][-1]["record"] == "revoked"
    assert all(
        request["transcript_digest"] == request["sealed_transcript_digest"]
        for request in document["requests"]
        if request["classification"] != "denied"
    )
    events = EventStore(state, run_id="run-1").events()
    source = type("Source", (), {"run_id": "run-1", "events": tuple(event.envelope for event in events)})()
    assert capsule_contract().validate(document, source) == ()
    exact_binding = RuntimeBinding(
        TEST_CANDIDATE.image_id,
        TEST_CANDIDATE.manifest_digest,
        TEST_CANDIDATE.config_digest,
        TEST_CANDIDATE.platform,
    )
    linked = link_manifest(draft(image_digest=IMAGE_ID), receipt, binding=exact_binding)
    assert any(item.get("ref") == "receipt:target-broker" for item in linked["receipts"])
    with pytest.raises(ValueError, match="another candidate"):
        link_manifest(draft(image_digest="sha256:" + "f" * 64), receipt, binding=exact_binding)
    with pytest.raises(ValueError, match="another candidate"):
        link_manifest(
            draft(image_digest=IMAGE_ID),
            receipt,
            binding=RuntimeBinding(
                IMAGE_ID,
                "sha256:" + "e" * 64,
                TEST_CANDIDATE.config_digest,
                TEST_CANDIDATE.platform,
            ),
        )


def test_redirect_and_connection_budget_are_classified_without_following_another_destination(
    tmp_path: Path,
) -> None:
    reached = []

    class RedirectingTarget(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(self.path)
            self.send_response(302)
            self.send_header("Location", "http://192.0.2.1/control")
            self.end_headers()

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), RedirectingTarget)
    thread = threading.Thread(target=target.serve_forever, daemon=True)
    thread.start()
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", target.server_port),
        limits=TargetLimits(1, 256, 256, 1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    with tempfile.TemporaryDirectory(prefix="target-broker-", dir="/tmp") as directory:
        service = TargetBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        client = claim_client(
            runtime,
            service,
            CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1"),
        )
        redirected = client.http("GET", "/redirect")
        exhausted = client.http("GET", "/again")
        service.close()
    target.shutdown()
    thread.join()

    assert redirected.outcome is TargetOutcome.DENIED
    assert exhausted.outcome is TargetOutcome.BUDGET_EXHAUSTED
    assert reached == ["/redirect"]
    records = [
        event.payload
        for event in __import__("solver.event_store", fromlist=["EventStore"])
        .EventStore(state, run_id="run-1")
        .events()
        if event.event_type == TARGET_EXCHANGE_RECORDED
    ]
    assert [record["outcome"] for record in records if record["record"] == "classified"] == [
        "denied",
        "budget-exhausted",
    ]


def test_slow_drip_cannot_extend_the_absolute_exchange_deadline(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def drip() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(16)
            for _ in range(20):
                try:
                    connection.sendall(b"x")
                except OSError:
                    return
                time.sleep(0.02)

    thread = threading.Thread(target=drip, daemon=True)
    thread.start()
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.TCP, "127.0.0.1", listener.getsockname()[1]),
        limits=TargetLimits(1, 4, 64, 0.1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    with tempfile.TemporaryDirectory(prefix="target-broker-", dir="/tmp") as directory:
        service = TargetBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        client = claim_client(
            runtime,
            service,
            CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1"),
        )
        started = time.monotonic()
        result = client.tcp(b"ping")
        elapsed = time.monotonic() - started
        service.close()
    listener.close()
    thread.join(1)

    assert result.outcome is TargetOutcome.TIMEOUT
    assert elapsed < 0.25


def test_restart_revokes_old_target_capability_before_replacement_issue(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), lambda: "now")
    generation = fence.acquire("challenge-7", "attempt-1")
    endpoint = TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 9)
    limits = TargetLimits(1, 4, 4, 0.1)
    old = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=endpoint,
        limits=limits,
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        old.prepare_attempt(binding)
        old.claim(left, generation.generation_id)
        TargetBrokerRuntime(
            state=state,
            run_id="run-1",
            boot_id="boot-2",
            challenge_id="challenge-7",
            candidate=TEST_CANDIDATE,
            endpoint=endpoint,
            limits=limits,
            timestamp=lambda: "2026-09-12T00:00:01+00:00",
        )
    finally:
        left.close()
        right.close()
    custody = [
        event.payload
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "capability-custody.recorded"
    ]
    assert custody[-1]["record"] == "revoked"
    assert custody[-1]["reason"] == "restart-reconciled"


def test_inflight_revocation_seals_the_reclassified_empty_transcript(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    release = threading.Event()

    def target() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(16)
            release.wait(1)
            connection.sendall(b"late-body")

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.TCP, "127.0.0.1", listener.getsockname()[1]),
        limits=TargetLimits(1, 4, 64, 1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        held: list = []
        exchange = threading.Thread(
            target=lambda: held.append(runtime.exchange(left, handle, {"body": "cGluZw=="})), daemon=True
        )
        exchange.start()
        time.sleep(0.05)
        runtime.revoke_generation(generation.generation_id)
        release.set()
        exchange.join(1)
    finally:
        left.close()
        right.close()
        listener.close()
        thread.join(1)
    assert held[0].outcome is TargetOutcome.REVOKED
    classified = next(
        event
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == TARGET_EXCHANGE_RECORDED and event.payload["record"] == "classified"
    )
    assert classified.payload["transcript_digest"] == classified.blob_digest


def test_attempt_executor_fences_target_authority_before_closing_generation(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), lambda: "now")
    generation = fence.acquire("challenge-7", "attempt-1")
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(
            IMAGE_ID,
            "sha256:" + "b" * 64,
            "sha256:" + "c" * 64,
            "linux/arm64",
        ),
        generation_fence=fence,
        runtime=ImmediateRuntime(),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    broker = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 9),
        limits=TargetLimits(1, 4, 4, 0.1),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
    )
    broker.bind_attempt_executor(executor)
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        broker.prepare_attempt(binding)
        handle = broker.claim(left, generation.generation_id)
        executor.close_generation(generation.generation_id, GenerationDisposition.SUPERSEDE)
        assert broker.exchange(left, handle, {"body": ""}).outcome is TargetOutcome.REVOKED
    finally:
        left.close()
        right.close()
        executor.close()
