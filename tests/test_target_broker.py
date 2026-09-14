"""Attempt-scoped Target exchanges cross one bounded pathname broker."""

from __future__ import annotations

import base64
import hashlib
import io
import datetime as dt
import gzip
import json
import socket
import subprocess
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
from solver.lease_target_broker import LeaseTargetBroker, target_endpoint
from solver.target_broker import TargetBrokerRuntime
from solver.target_broker_contracts import (
    TARGET_EXCHANGE_RECORDED,
    BrowserObservations,
    BrowserSessionRequest,
    BrowserTransportResult,
    HttpBody,
    HttpFuzzRequest,
    HttpSessionRequest,
    TcpReceive,
    TcpSessionRequest,
    TargetCandidateBinding,
    TargetEndpoint,
    TargetLimits,
    TargetOutcome,
    TargetProtocol,
    TargetResult,
)
from solver.target_broker_ipc import (
    CLIENT_TIMEOUT_SECONDS,
    MAX_CLIENTS,
    TargetBrokerClient,
    TargetBrokerService,
)
from solver.target_broker_ffuf import FfufResult
from solver.target_broker_receipt import capsule_contract, link_manifest, verify_receipt, write_receipt
from solver.target_broker_transport import (
    HttpTransportRequest,
    HttpTransportResult,
    TransportResult,
    exchange as target_transport_exchange,
    http_session_exchange,
)
from solver import target_broker_worker_client
from solver import target_broker_browser
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


def test_browser_launcher_has_a_dedicated_uid_and_private_mount_process_boundary() -> None:
    command = target_broker_browser._browser_worker_command(9, 10)

    assert command[0] == "/usr/bin/bwrap"
    assert "--unshare-pid" in command
    assert "--unshare-uts" in command
    assert "--unshare-ipc" in command
    assert "--unshare-net" in command
    assert "/state" not in command
    assert ["--ro-bind", "/opt/solver", "/opt/solver"] not in [
        command[index : index + 3] for index in range(len(command) - 2)
    ]
    assert command[-15:] == [
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "/usr/bin/setpriv",
        "--reuid",
        "30001",
        "--regid",
        "30001",
        "--clear-groups",
        "--bounding-set=-all",
        "--no-new-privs",
        "/usr/bin/python3",
        "-I",
        "/browser-worker.py",
        "9",
        "10",
    ]


def test_controller_attaches_and_resets_the_inherited_browser_launcher(monkeypatch) -> None:
    controller, worker = socket.socketpair()
    transport_controller, transport_worker = socket.socketpair()
    descriptor = controller.detach()
    transport_descriptor = transport_controller.detach()
    reset = {}

    def acknowledge() -> None:
        request = target_broker_browser._receive(worker)
        reset.update(request)
        target_broker_browser._send(
            worker,
            {"status": "ready", "reset_id": request["reset_id"]},
            threading.Lock(),
        )

    thread = threading.Thread(target=acknowledge)
    thread.start()
    monkeypatch.setattr(target_broker_browser, "_prepared_launcher", None)
    launcher = target_broker_browser.attach_browser_launcher(
        {target_broker_browser.BROWSER_LAUNCHER_FD_ENV: f"{descriptor}:{transport_descriptor}"}
    )
    thread.join()

    assert reset["command"] == "reset"
    assert isinstance(reset["reset_id"], str)
    assert target_broker_browser._prepared_launcher is launcher
    target_broker_browser.close_browser_launcher()
    worker.close()
    transport_worker.close()


def test_owner_kills_and_reaps_a_browser_launcher_that_ignores_shutdown() -> None:
    controller, worker = socket.socketpair()
    target_broker_browser._send(worker, {"status": "ready"}, threading.Lock())

    class Process:
        def __init__(self) -> None:
            self.killed = False
            self.waits = 0

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("browser", timeout)
            return 0

        def kill(self):
            self.killed = True

    process = Process()
    launcher = target_broker_browser.BrowserLauncher(controller, process)  # type: ignore[arg-type]

    launcher.close()

    assert process.killed is True
    assert process.waits == 2
    worker.close()


def test_browser_launcher_multiplexes_every_request_through_controller_transport() -> None:
    controller, worker = socket.socketpair()
    transport_controller, transport_worker = socket.socketpair()
    launcher = target_broker_browser.BrowserLauncher(
        controller,
        None,
        transport_controller,
        await_ready=False,
    )
    observed = {}

    def browse() -> None:
        request = target_broker_browser._receive(worker)
        request_id = request["request_id"]
        transport_request_id = "subrequest-1"
        target_broker_browser._send(
            transport_worker,
            {"request_id": transport_request_id, "exchange": {"method": "GET", "url": "http://target.test/"}},
            threading.Lock(),
        )
        transport_response = target_broker_browser._receive(transport_worker)
        observed.update(transport_response["result"])
        observed["request_id"] = transport_response["request_id"]
        target_broker_browser._send(
            worker,
            {
                "outcome": "completed",
                "request_id": request_id,
                "returncode": 0,
                "stdout": base64.b64encode(
                    b'{"dom":"","network":[],"local_storage":[],"session_storage":[],"downloads":[]}'
                ).decode(),
            },
            threading.Lock(),
        )

    thread = threading.Thread(target=browse)
    thread.start()
    result = launcher.execute(
        "request-1",
        {},
        timeout_seconds=1,
        transport=lambda request: {"outcome": "answered", "status": 200, "url": request["url"]},
    )
    thread.join()
    launcher.close()
    worker.close()
    transport_worker.close()

    assert result is not None and result[0] == "completed"
    assert observed == {
        "outcome": "answered",
        "request_id": "subrequest-1",
        "status": 200,
        "url": "http://target.test/",
    }


def test_browser_session_uses_the_prepared_fixed_uid_launcher() -> None:
    observed = {}

    class Launcher:
        def execute(self, request_id, document, *, timeout_seconds, transport):
            observed.update(request_id=request_id, document=document, timeout_seconds=timeout_seconds)
            observed["transport"] = transport
            return (
                "completed",
                0,
                json.dumps(
                    {
                        "dom": "<html></html>",
                        "network": [],
                        "local_storage": [],
                        "session_storage": [],
                        "downloads": [],
                    }
                ).encode(),
            )

        def cancel(self, _request_id):
            raise AssertionError("completed launch must not be cancelled")

    transport = target_broker_browser.BrowserSessionTransport(
        TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 8080),
        "127.0.0.1",
        TargetLimits(1, 1024, 1024, 1),
        {},
        Launcher(),
    )

    result = transport.browse(BrowserSessionRequest("/"))

    assert result.outcome is TargetOutcome.ANSWERED
    assert observed["document"]["origin"]["address"] == "127.0.0.1"
    assert observed["timeout_seconds"] == 1
    assert callable(observed["transport"])


def test_browser_session_brokers_only_admitted_origins_and_uses_measured_wire_bytes(monkeypatch) -> None:
    exchanges = []

    class Transport:
        def __init__(self, endpoint, address, _limits):
            exchanges.append((endpoint, address))

        def exchange(self, request, *, max_response_bytes):
            assert request.target == "/state?view=full"
            assert max_response_bytes == 1024
            return HttpTransportResult(
                TargetOutcome.ANSWERED,
                b"held",
                200,
                (("content-length", "999999"), ("content-type", "text/plain")),
                request_bytes=31,
                response_bytes=47,
                certificate_sha256="b" * 64,
            )

    monkeypatch.setattr(target_broker_browser, "HttpSessionTransport", Transport)

    class Launcher:
        def execute(self, request_id, _document, *, timeout_seconds, transport):
            assert timeout_seconds == 1
            denied = transport({"method": "GET", "url": "http://off-origin.test/", "headers": {}, "body": ""})
            answered = transport(
                {
                    "method": "GET",
                    "url": "https://target.test/state?view=full",
                    "headers": {"Accept": "text/plain"},
                    "body": "",
                }
            )
            assert denied == {"outcome": "denied"}
            assert answered["response_bytes"] == 47
            assert ["content-length", "999999"] not in answered["headers"]
            return (
                "completed",
                0,
                json.dumps(
                    {
                        "dom": "held",
                        "network": [["GET", "/state", 200, 47]],
                        "local_storage": [],
                        "session_storage": [],
                        "downloads": [],
                    }
                ).encode(),
            )

        def cancel(self, _request_id):
            return None

    transport = target_broker_browser.BrowserSessionTransport(
        TargetEndpoint(TargetProtocol.HTTPS, "target.test", 443),
        "192.0.2.10",
        TargetLimits(1, 1024, 1024, 1),
        {},
        Launcher(),
    )

    result = transport.browse(BrowserSessionRequest("/state"))

    assert result.outcome is TargetOutcome.ANSWERED
    assert result.request_bytes == 31
    assert result.response_bytes == 47
    assert result.connections == 1
    assert result.certificate_sha256 == "b" * 64
    assert exchanges == [(TargetEndpoint(TargetProtocol.HTTPS, "target.test", 443), "192.0.2.10")]


def test_browser_session_close_interrupts_an_active_controller_exchange(monkeypatch) -> None:
    observed = {"transport_closed": False, "cancelled": ""}

    class Transport:
        def __init__(self, _endpoint, _address, _limits):
            return None

        def exchange(self, _request, *, max_response_bytes):
            assert max_response_bytes == 1024
            session.close()
            assert observed["transport_closed"] is True
            return HttpTransportResult(TargetOutcome.REVOKED)

        def close(self):
            observed["transport_closed"] = True

    monkeypatch.setattr(target_broker_browser, "HttpSessionTransport", Transport)

    class Launcher:
        def execute(self, request_id, _document, *, timeout_seconds, transport):
            assert timeout_seconds == 1
            transport({"method": "GET", "url": "http://target.test/", "headers": {}, "body": ""})
            return None

        def cancel(self, request_id):
            observed["cancelled"] = request_id

    session = target_broker_browser.BrowserSessionTransport(
        TargetEndpoint(TargetProtocol.HTTP, "target.test", 80),
        "192.0.2.10",
        TargetLimits(1, 1024, 1024, 1),
        {},
        Launcher(),
    )

    assert session.browse(BrowserSessionRequest("/")).outcome is TargetOutcome.REVOKED
    assert observed["cancelled"]


def test_browser_session_does_not_discover_a_launcher_from_module_state(monkeypatch) -> None:
    class Launcher:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("an uninjected launcher must remain unreachable")

    monkeypatch.setattr(target_broker_browser, "_prepared_launcher", Launcher())
    transport = target_broker_browser.BrowserSessionTransport(
        TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 8080),
        "127.0.0.1",
        TargetLimits(1, 1024, 1024, 1),
        {},
        None,
    )

    assert transport.browse(BrowserSessionRequest("/")).outcome is TargetOutcome.DENIED


def test_hostile_target_client_exposes_typed_session_documents(tmp_path: Path, monkeypatch, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(TcpSessionRequest(b"a\0b", TcpReceive.fixed(4)).document()))
    sent = []

    def request(document):
        sent.append(document)
        if document["command"] == "claim":
            return {"status": "issued", "handle": "opaque"}
        return {
            "status": "answered",
            "result": {"outcome": "answered", "body": base64.b64encode(b"held").decode()},
        }

    monkeypatch.setenv("INCYPHER_TARGET_GENERATION", "generation-000001")
    monkeypatch.setattr(target_broker_worker_client, "request", request)

    assert target_broker_worker_client.main(["tcp-session", str(request_path)]) == 0

    assert json.loads(capsys.readouterr().out)["body"] == base64.b64encode(b"held").decode()
    assert sent[1] == {
        "command": "tcp-session",
        "handle": "opaque",
        "request": TcpSessionRequest(b"a\0b", TcpReceive.fixed(4)).document(),
    }


def test_hostile_target_client_exposes_only_bounded_http_fuzz_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(HttpFuzzRequest(("/missing", "/hidden")).document()))
    sent = []

    def request(document):
        sent.append(document)
        if document["command"] == "claim":
            return {"status": "issued", "handle": "opaque"}
        return {"status": "answered", "result": {"outcome": "answered", "body": ""}}

    monkeypatch.setenv("INCYPHER_TARGET_GENERATION", "generation-000001")
    monkeypatch.setattr(target_broker_worker_client, "request", request)

    assert target_broker_worker_client.main(["http-fuzz", str(request_path)]) == 0

    assert json.loads(capsys.readouterr().out)["outcome"] == "answered"
    assert sent[1] == {
        "command": "http-fuzz",
        "handle": "opaque",
        "request": {"paths": ["/missing", "/hidden"]},
    }


def test_browser_session_uses_fresh_generation_owned_target_context(tmp_path: Path) -> None:
    made = []

    class Browser:
        def __init__(self, *_args):
            self.closed = False
            made.append(self)

        def browse(self, request):
            assert request == BrowserSessionRequest("/state", wait_selector="#held")
            return BrowserTransportResult(
                TargetOutcome.ANSWERED,
                BrowserObservations(
                    dom='<div id="held">browser-only</div>',
                    network=(("GET", "/state", 200, 41),),
                    local_storage=(("held", "browser-only"),),
                ),
                elapsed_ms=7,
                response_bytes=41,
                request_bytes=29,
                connections=1,
                certificate_sha256="a" * 64,
            )

        def close(self):
            self.closed = True

    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 9),
        limits=TargetLimits(1, 1024, 1024, 1, max_exchanges=2),
        timestamp=lambda: "now",
        browser_factory=Browser,
    )
    with tempfile.TemporaryDirectory(prefix="target-broker-", dir="/tmp") as directory:
        service = TargetBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        client = claim_client(
            runtime,
            service,
            CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1"),
        )
        result = client.browser(BrowserSessionRequest("/state", wait_selector="#held"))
        runtime.revoke_generation(generation.generation_id)
        service.close()

    assert result.outcome is TargetOutcome.ANSWERED
    assert result.browser.dom == '<div id="held">browser-only</div>'
    assert result.browser.network == (("GET", "/state", 200, 41),)
    assert result.browser.local_storage == (("held", "browser-only"),)
    assert result.provenance.resolved_address == "127.0.0.1"
    assert result.provenance.certificate_sha256 == "a" * 64
    assert made[0].closed


@pytest.mark.parametrize(
    ("connection", "expected"),
    (
        ("nc target.example 31337", TargetEndpoint(TargetProtocol.TCP, "target.example", 31337)),
        ("target.example:31337", TargetEndpoint(TargetProtocol.TCP, "target.example", 31337)),
        ("http://target.example", TargetEndpoint(TargetProtocol.HTTP, "target.example", 80)),
        ("https://target.example:8443", TargetEndpoint(TargetProtocol.HTTPS, "target.example", 8443)),
    ),
)
def test_lease_connection_is_parsed_into_one_exact_target_endpoint(connection, expected):
    assert target_endpoint(connection) == expected


@pytest.mark.parametrize("connection", ("", "ssh target", "http://target.example/path", "nc target 0"))
def test_unsupported_or_ambiguous_lease_connection_is_refused(connection):
    with pytest.raises(ValueError, match="Target connection"):
        target_endpoint(connection)


def test_lease_target_router_publishes_only_current_generation_authority(tmp_path):
    class Authority:
        current = True

        def reauthorize(self, _grant):
            return self.current

    class Runtime:
        def __init__(self, **fields):
            self.fields = fields
            self.revoked = []

        def prepare_attempt(self, binding):
            self.binding = binding

        def claim(self, _connection, generation_id):
            assert generation_id == self.binding.generation_id
            return "opaque-handle"

        def exchange(self, _connection, handle, _request):
            assert handle == "opaque-handle"
            return TargetResult(TargetOutcome.ANSWERED, b"answer")

        http_session = exchange
        http_fuzz = exchange
        tcp_session = exchange
        browser = exchange

        def revoke(self, handle):
            self.revoked.append(handle)

        def revoke_generation(self, generation_id):
            self.revoked.append(generation_id)

    authority = Authority()
    made = []

    def factory(**fields):
        made.append(Runtime(**fields))
        return made[-1]

    router = LeaseTargetBroker(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-1",
        candidate=TEST_CANDIDATE,
        target_authority=authority,
        timestamp=lambda: "now",
        runtime_factory=factory,
    )
    grant = type(
        "Grant",
        (),
        {
            "generation_id": "generation-000001",
            "work_id": "integer:42",
            "exact_connection": "nc target.example 31337",
            "connection_digest": "a" * 64,
        },
    )()
    binding = CapabilityBinding("run-1", "boot-1", grant.generation_id, "lane-1", "attempt-1", "step-1")
    left, right = socket.socketpair()
    try:
        router.register(grant)
        assert router.available(grant.generation_id)
        router.prepare_attempt(binding)
        handle = router.claim(left, grant.generation_id)
        assert router.exchange(left, handle, {"body": ""}).outcome is TargetOutcome.ANSWERED
        assert router.http_session(left, handle, {}).outcome is TargetOutcome.ANSWERED
        assert router.http_fuzz(left, handle, {}).outcome is TargetOutcome.ANSWERED
        assert router.tcp_session(left, handle, {}).outcome is TargetOutcome.ANSWERED
        assert router.browser(left, handle, {}).outcome is TargetOutcome.ANSWERED
        authority.current = False
        assert router.exchange(left, handle, {"body": ""}).outcome is TargetOutcome.REVOKED
    finally:
        left.close()
        right.close()

    assert made[0].fields["challenge_id"] == "integer:42"
    assert made[0].fields["endpoint"] == TargetEndpoint(TargetProtocol.TCP, "target.example", 31337)
    assert made[0].revoked == [grant.generation_id]


def test_https_target_transport_wraps_the_pinned_address_with_the_declared_hostname(monkeypatch):
    class Socket:
        def __init__(self):
            self.sent = b""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def sendall(self, value):
            self.sent += value

        def makefile(self, _mode):
            return io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")

        def close(self):
            pass

    target = Socket()
    wrapped = []

    class Context:
        def wrap_socket(self, opened, *, server_hostname):
            wrapped.append((opened, server_hostname))
            return opened

    monkeypatch.setattr("solver.target_broker_transport.socket.create_connection", lambda *_args: target)
    monkeypatch.setattr("solver.target_broker_transport.ssl.create_default_context", Context)

    result = target_transport_exchange(
        TargetEndpoint(TargetProtocol.HTTPS, "target.example", 443),
        "192.0.2.1",
        TargetLimits(1, 1024, 1024, 1),
        {"method": "GET", "path": "/", "body": ""},
        b"",
    )

    assert result.outcome is TargetOutcome.ANSWERED
    assert result.body == b"ok"
    assert wrapped == [(target, "target.example")]
    assert b"Host: target.example" in target.sent


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


def test_http_session_retains_cookie_and_returns_bounded_selected_observations(tmp_path: Path) -> None:
    observed = []

    class SessionTarget(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append((self.command, self.path, dict(self.headers), b""))
            if self.path == "/ready":
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(302)
            self.send_header("Location", "/ready")
            self.send_header("Set-Cookie", "challenge=admitted; Path=/; HttpOnly")
            self.end_headers()

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            observed.append((self.command, self.path, dict(self.headers), body))
            compressed = gzip.compress(b'{"held":"session-value"}')
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self.send_header("X-Proof", "selected")
            self.send_header("X-Private", "omitted")
            self.end_headers()
            self.wfile.write(compressed)

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), SessionTarget)
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
        limits=TargetLimits(
            max_connections=3,
            max_request_bytes=1024,
            max_response_bytes=1024,
            timeout_seconds=1,
            max_exchanges=3,
            max_total_request_bytes=2048,
            max_total_response_bytes=2048,
            max_redirects=1,
        ),
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
            redirected = client.http_session(HttpSessionRequest("GET", "/start"))
            answered = client.http_session(
                HttpSessionRequest(
                    "POST",
                    "/graphql",
                    query=(("operation", "Held Value"),),
                    body=HttpBody.json(b'{"query":"{ held }"}'),
                    headers=(("X-Challenge", "session"),),
                    response_headers=("X-Proof",),
                )
            )
            client.close()
        finally:
            service.close()
            target.shutdown()
            target_thread.join()

    assert redirected.outcome is TargetOutcome.ANSWERED
    assert redirected.redirect_chain == ("/start", "/ready")
    assert redirected.cookies == (("challenge", "admitted"),)
    assert answered.outcome is TargetOutcome.ANSWERED
    assert answered.body == b'{"held":"session-value"}'
    assert answered.headers == (("x-proof", "selected"),)
    assert answered.redirect_chain == ("/graphql?operation=Held+Value",)
    assert answered.cookies == (("challenge", "admitted"),)
    assert answered.provenance.resolved_address == "127.0.0.1"
    assert answered.provenance.response_bytes > len(answered.body)
    assert observed[0][1] == "/start"
    assert observed[1][1] == "/ready"
    method, path, headers, body = observed[2]
    assert method == "POST"
    assert path == "/graphql?operation=Held+Value"
    assert headers["Cookie"] == "challenge=admitted"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-Challenge"] == "session"
    assert body == b'{"query":"{ held }"}'


def test_http_fuzz_runs_controller_side_and_records_each_target_request(tmp_path: Path) -> None:
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/hidden" else 404)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    target_thread = threading.Thread(target=target.serve_forever, daemon=True)
    target_thread.start()
    made = []

    class Fuzz:
        def __init__(self, paths, exchange, timeout_seconds):
            self.paths = paths
            self.exchange = exchange
            self.timeout_seconds = timeout_seconds
            self.closed = False
            made.append(self)

        def run(self):
            results = {path: self.exchange(path) for path in self.paths}
            return FfufResult(
                TargetOutcome.ANSWERED,
                tuple(path for path, result in results.items() if result.status != 404),
            )

        def close(self):
            self.closed = True

    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", target.server_port),
        limits=TargetLimits(3, 1024, 1024, 1, max_exchanges=3),
        timestamp=lambda: "now",
        ffuf_factory=Fuzz,
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        result = runtime.http_fuzz(left, handle, HttpFuzzRequest(("/missing", "/hidden")).document())
        denied = runtime.http_fuzz(left, handle, {"paths": ["https://off-origin.invalid/"]})
    finally:
        left.close()
        right.close()
        target.shutdown()
        target_thread.join()

    assert result.outcome is TargetOutcome.ANSWERED
    assert json.loads(result.body) == {"found": ["/hidden"]}
    assert denied.outcome is TargetOutcome.DENIED
    assert made[0].timeout_seconds == 2
    assert made[0].closed
    classified = [
        event.payload["operation"]
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == TARGET_EXCHANGE_RECORDED and event.payload["record"] == "classified"
    ]
    assert classified == ["http-session", "http-session", "http-fuzz", "http-fuzz"]


def test_http_session_rejects_a_truncated_compressed_response() -> None:
    compressed = gzip.compress(b"held")[:-4]

    class CompressedTarget(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(compressed)))
            self.end_headers()
            self.wfile.write(compressed)

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), CompressedTarget)
    thread = threading.Thread(target=target.serve_forever, daemon=True)
    thread.start()
    try:
        result = http_session_exchange(
            TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", target.server_port),
            "127.0.0.1",
            TargetLimits(1, 1024, 1024, 1),
            HttpTransportRequest("GET", "/", b"", ()),
        )
    finally:
        target.shutdown()
        thread.join()

    assert result.outcome is TargetOutcome.TOO_LARGE
    assert result.body == b""


def test_http_session_generation_revocation_interrupts_an_active_request(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowTarget(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            release.wait(3)
            try:
                self.send_response(200)
                self.end_headers()
            except OSError:
                pass

        def log_message(self, *_args):
            pass

    target = ThreadingHTTPServer(("127.0.0.1", 0), SlowTarget)
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
        limits=TargetLimits(1, 256, 256, 10, max_exchanges=2),
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
        held = []
        request_thread = threading.Thread(
            target=lambda: held.append(client.http_session(HttpSessionRequest("GET", "/")))
        )
        request_thread.start()
        assert entered.wait(1)
        runtime.revoke_generation(generation.generation_id)
        request_thread.join(1)
        interrupted = not request_thread.is_alive()
        release.set()
        service.close()
    target.shutdown()
    target_thread.join()

    assert interrupted
    assert held[0].outcome is TargetOutcome.REVOKED
    classified = next(
        event
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == TARGET_EXCHANGE_RECORDED and event.payload["record"] == "classified"
    )
    assert hashlib.sha256(classified.body).hexdigest() == classified.payload["transcript_digest"]


def test_http_session_revocation_after_transport_seals_the_scrubbed_result(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.HTTP, "127.0.0.1", 8080),
        limits=TargetLimits(1, 256, 256, 1),
        timestamp=lambda: "now",
    )
    left, right = socket.socketpair()
    try:
        runtime.prepare_attempt(
            CapabilityBinding(
                "run-1",
                "boot-1",
                generation.generation_id,
                "lane-1",
                "attempt-1",
                "step-1",
            )
        )
        handle = runtime.claim(left, generation.generation_id)

        class RevokingTransport:
            def exchange(self, *_args, **_kwargs):
                runtime.revoke_generation(generation.generation_id)
                return HttpTransportResult(
                    TargetOutcome.ANSWERED,
                    b"must-not-escape",
                    200,
                    request_bytes=64,
                    response_bytes=80,
                )

            def close(self):
                pass

        runtime._http_sessions[handle] = RevokingTransport()
        result = runtime.http_session(left, handle, HttpSessionRequest("GET", "/").document())
    finally:
        left.close()
        right.close()

    assert result.outcome is TargetOutcome.REVOKED
    assert result.body == b""
    classified = next(
        event
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == TARGET_EXCHANGE_RECORDED and event.payload["record"] == "classified"
    )
    assert hashlib.sha256(classified.body).hexdigest() == classified.payload["transcript_digest"]


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


def test_tcp_session_keeps_one_binary_connection_and_supports_framing_and_half_close(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    observed = []

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            first = connection.recv(32)
            observed.append(first)
            connection.sendall(b"first\0\n")
            second = connection.recv(32)
            observed.append(second)
            connection.sendall(len(b"second").to_bytes(4, "big") + b"second")
            third = connection.recv(32)
            observed.append(third)
            observed.append(connection.recv(1))
            connection.sendall(b"bye")

    thread = threading.Thread(target=serve, daemon=True)
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
        limits=TargetLimits(
            max_connections=1,
            max_request_bytes=32,
            max_response_bytes=32,
            timeout_seconds=1,
            max_exchanges=3,
            max_total_request_bytes=32,
            max_total_response_bytes=32,
        ),
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
            first = client.tcp_session(TcpSessionRequest(b"hello\0", TcpReceive.line(16)))
            second = client.tcp_session(TcpSessionRequest(b"again", TcpReceive.length_prefixed(4, 16)))
            third = client.tcp_session(TcpSessionRequest(b"done", TcpReceive.raw(16), half_close=True))
            client.close()
        finally:
            service.close()
            listener.close()
            thread.join(1)

    assert first.outcome is TargetOutcome.ANSWERED
    assert first.body == b"first\0\n"
    assert second.outcome is TargetOutcome.ANSWERED
    assert second.body == b"second"
    assert third.outcome is TargetOutcome.ANSWERED
    assert third.body == b"bye"
    assert observed == [b"hello\0", b"again", b"done", b""]
    assert sum(result.provenance.request_bytes for result in (first, second, third)) == 15
    assert sum(result.provenance.response_bytes for result in (first, second, third)) == 20


def test_tcp_session_reports_partial_fixed_frame_on_ambiguous_close(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            assert connection.recv(16) == b"frame"
            connection.sendall(b"part")

    thread = threading.Thread(target=serve, daemon=True)
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
        limits=TargetLimits(1, 16, 16, 1, max_exchanges=2),
        timestamp=lambda: "now",
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
            result = client.tcp_session(TcpSessionRequest(b"frame", TcpReceive.fixed(8)))
            client.close()
        finally:
            service.close()
            listener.close()
            thread.join(1)

    assert result.outcome is TargetOutcome.AMBIGUOUS_CLOSE
    assert result.body == b"part"
    assert result.provenance.response_bytes == 4


def test_tcp_session_timeout_retains_partial_delimited_response(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            assert connection.recv(16) == b"wait"
            connection.sendall(b"partial")
            time.sleep(0.3)

    thread = threading.Thread(target=serve, daemon=True)
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
        limits=TargetLimits(1, 16, 16, 0.1, max_exchanges=2),
        timestamp=lambda: "now",
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
            result = client.tcp_session(TcpSessionRequest(b"wait", TcpReceive.delimiter_terminated(b"END", 16)))
            client.close()
        finally:
            service.close()
            listener.close()
            thread.join(1)

    assert result.outcome is TargetOutcome.TIMEOUT
    assert result.body == b"partial"
    assert result.provenance.response_bytes == 7


def test_tcp_session_generation_revocation_interrupts_an_active_receive(tmp_path: Path) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    entered = threading.Event()

    def serve() -> None:
        connection, _ = listener.accept()
        with connection:
            connection.recv(16)
            entered.set()
            time.sleep(2)

    server = threading.Thread(target=serve, daemon=True)
    server.start()
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    runtime = TargetBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        challenge_id="challenge-7",
        candidate=TEST_CANDIDATE,
        endpoint=TargetEndpoint(TargetProtocol.TCP, "127.0.0.1", listener.getsockname()[1]),
        limits=TargetLimits(1, 64, 64, 10, max_exchanges=2),
        timestamp=lambda: "now",
    )
    left, right = socket.socketpair()
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
    runtime.prepare_attempt(binding)
    handle = runtime.claim(left, generation.generation_id)
    held = []
    request_thread = threading.Thread(
        target=lambda: held.append(
            runtime.tcp_session(left, handle, TcpSessionRequest(b"held", TcpReceive.fixed(8)).document())
        )
    )
    request_thread.start()
    assert entered.wait(1)
    runtime.revoke_generation(generation.generation_id)
    request_thread.join(1)
    listener.close()
    left.close()
    right.close()

    assert not request_thread.is_alive()
    assert held[0].outcome is TargetOutcome.REVOKED


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
