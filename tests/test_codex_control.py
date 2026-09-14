"""Codex Control's capability boundary, catalogue, measurement and limit truth."""

import json
import os
import socket
import threading
import tempfile
from pathlib import Path
import datetime as dt

from solver.codex_control import CodexControl, CodexControlClient, CodexControlServer, CodexControlService
from solver.codex_control_contracts import (
    CodexCatalogueEntry,
    CodexRequest,
    CodexTurn,
    LimitObservation,
    NativeResponse,
)
from solver.codex_control_receipt import receipt_document
from solver.codex_control_native import ControlledNativeProbe, V1CodexControlAdapter
from solver.codex_control_custody import establish_service_custody
from solver.codex import Child, Credential, Invocation
from solver.broker_contracts import Broker, BrokerReceipt
from solver.executor_secret_probe import classify_surfaces
from solver.event_store import EventStore
from solver.record import Recorder
from solver.redaction import Redactor
from solver.stall import Deadline
from solver.capability import CapabilityAuthority, CapabilityBinding, PeerIdentity
from solver.work_generation import GenerationFence


class Native:
    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        return NativeResponse(self.answer, getattr(self, "limits", ()))

    def cancel(self, request_id):
        self.cancelled = request_id
        return True


class Authority:
    def __init__(self, handle="controller-handle", scope="codex.engage"):
        self.handle = handle
        self.scope = scope

    def authorize(self, _channel, handle):
        if handle != self.handle:
            from solver.capability import CapabilityRefused

            raise CapabilityRefused()
        return type("Grant", (), {"scope": self.scope})()


class Canned(Child):
    def __init__(self, stream):
        self.stream = [stream]

    def read(self, _budget):
        return self.stream.pop(0) if self.stream else b""

    def stop(self):
        pass

    def close(self):
        return 0, b""


CATALOGUE = (CodexCatalogueEntry("gpt-5.6", ("low", "medium", "high")),)
REQUEST = CodexRequest("request-1", "turn-1", "gpt-5.6", "high", "solve this")


def test_executor_origin_is_rejected_before_native_transport():
    native = Native(CodexTurn("request-1", "turn-1", "gpt-5.6", "ok", 12, 3, 40))
    control = CodexControl(CATALOGUE, native)

    result = control.request(REQUEST, origin="attempt-executor")

    assert result.outcome == "capability-refused"
    assert native.requests == []


def test_model_and_effort_are_rejected_before_native_transport():
    native = Native(CodexTurn("request-1", "turn-1", "gpt-5.6", "ok", 12, 3, 40))
    control = CodexControl(CATALOGUE, native)

    wrong_model = control.request(
        CodexRequest("request-x", "turn-x", "not-offered", "high", "solve this"),
        origin="run-controller",
    )
    wrong_effort = control.request(
        CodexRequest("request-2", "turn-2", "gpt-5.6", "ultra", "solve this"),
        origin="run-controller",
    )

    assert wrong_model.outcome == "catalogue-refused"
    assert wrong_effort.outcome == "catalogue-refused"
    assert native.requests == []


def test_native_model_mismatch_is_distinct():
    native = Native(CodexTurn("request-1", "turn-1", "other-model", "ok", 12, 3, 40))

    result = CodexControl(CATALOGUE, native).request(REQUEST, origin="run-controller")

    assert result.outcome == "model-mismatch"


def test_controller_can_cancel_active_native_request():
    native = Native(CodexTurn("request-1", "turn-1", "gpt-5.6", "ok", 12, 3, 40))

    result = CodexControl(CATALOGUE, native).cancel("request-1", origin="run-controller")

    assert result.outcome == "cancelled"
    assert native.cancelled == "request-1"


def test_measured_turn_and_observed_limits_keep_unknown_missing():
    turn = CodexTurn("request-1", "turn-1", "gpt-5.6", "ok", 12, 3, 40)
    native = Native(turn)
    control = CodexControl(CATALOGUE, native)

    native.limits = (LimitObservation("codex", 98.0, None, "rate-limit-header", "2026-09-12T00:00:00Z"),)
    result = control.request(REQUEST, origin="run-controller")

    assert result.turn == turn
    assert result.limits[0].remaining_percent == 2.0
    assert result.limits[0].resets_at is None
    assert control.limit("unobserved") is None


def test_fake_native_turn_traverses_capability_ipc():
    native = Native(CodexTurn("request-1", "turn-1", "gpt-5.6", "ok", 12, 3, 40))
    server = CodexControlServer(CodexControl(CATALOGUE, native), authority=Authority())
    client_channel, server_channel = socket.socketpair()
    thread = threading.Thread(target=server.serve, args=(server_channel,))
    thread.start()
    try:
        result = CodexControlClient(client_channel, "controller-handle").request(REQUEST)
    finally:
        client_channel.close()
        thread.join(timeout=1)
        server_channel.close()

    assert result.turn == native.answer
    assert native.requests == [REQUEST]


def test_controlled_native_probe_and_v1_adapter_traverse_ipc(tmp_path):
    now = dt.datetime(2026, 9, 12, tzinfo=dt.timezone.utc)
    workdir = tmp_path / "work"
    workdir.mkdir()
    home = tmp_path / "codex"
    home.mkdir()
    stream = (
        b'{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n'
        b'{"type":"turn.completed","usage":{"input_tokens":12,"cached_input_tokens":2,"output_tokens":3}}\n'
    )
    launched = {}

    def launch(_argv, _workdir, environment, _prompt):
        launched.update(environment)
        return Canned(stream)

    probe = ControlledNativeProbe(
        workdir=workdir,
        deadline=Deadline(budget=now + dt.timedelta(minutes=1)),
        recorder=Recorder(tmp_path, "run-1", Redactor({})),
        attempt_id="attempt-1",
        credential=Credential("codex-subscription", "gpt-5.6", home),
        invocation=Invocation(reasoning_effort="high"),
        launch=launch,
        now=lambda: now,
    )
    generation = GenerationFence(tmp_path, "run-1", Redactor({}), lambda: now.isoformat()).acquire(
        "work-1", "attempt-1"
    )
    peer = PeerIdentity(101, 1000, 1000, "1", "/run-controller")
    authority = CapabilityAuthority(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-1",
        redactor=Redactor({}),
        peer_identity=lambda _connection: peer,
        timestamp=lambda: now.isoformat(),
    )
    server = CodexControlServer(CodexControl(CATALOGUE, probe), authority=authority)
    runtime = Path(tempfile.mkdtemp(prefix="codex-control-", dir="/tmp"))
    service = CodexControlService(runtime / "codex.sock", server, authority)
    service.start()
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
    client = CodexControlClient.open(service.path, binding)
    try:
        turn = V1CodexControlAdapter(client=client, model="gpt-5.6", effort="high").turn(
            "solve this",
            request_id="request-1",
            turn_id="turn-1",
            tool_socket=tmp_path / "attempt-tool.sock",
            tool_handle="attempt-tool-handle",
        )
    finally:
        client.close()
        service.close()
        runtime.rmdir()

    assert (turn.tokens_in, turn.tokens_out, turn.text) == (10, 3, "done")
    assert launched["INCYPHER_TOOL_SOCKET"] == str(tmp_path / "attempt-tool.sock")
    assert launched["INCYPHER_TOOL_HANDLE"] == "attempt-tool-handle"


def test_receipt_is_versioned_sanitized_and_independently_verified(tmp_path):
    tool_handle = "ephemeral-attempt-tool-handle"
    control = CodexControl(
        CATALOGUE,
        Native(CodexTurn("request-1", "turn-1", "gpt-5.6", "secret prose", 12, 3, 40)),
        state=tmp_path,
        run_id="run-1",
    )
    control.request(
        CodexRequest("request-1", "turn-1", "gpt-5.6", "high", "solve this", tool_handle=tool_handle),
        origin="run-controller",
    )
    secret = b"native-subscription-secret"
    probe = classify_surfaces(
        {name: b"attempt-owned-data" for name in ("memory", "environment", "argv", "file", "event")},
        (secret,),
    )
    custody = BrokerReceipt(Broker.CODEX, ("CODEX_HOME",), "a" * 64, os.getpid(), 1000, "b" * 64)
    establish_service_custody(control, custody, probe)

    receipt = receipt_document(tmp_path, "run-1")
    control_events = [
        event
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.payload.get("tool") == "codex-control-state"
    ]
    assert [event.payload["step_index"] for event in control_events] == [1, 2, 3]
    assert receipt["schema_version"] == 1
    assert receipt["turn"] == {
        "request_id": "request-1",
        "turn_id": "turn-1",
        "model": "gpt-5.6",
        "tokens_in": 12,
        "tokens_out": 3,
        "duration_ms": 40,
    }
    assert "secret prose" not in json.dumps(receipt)
    assert all(
        tool_handle.encode() not in path.read_bytes()
        for path in (tmp_path / "runs" / "run-1").rglob("*")
        if path.is_file()
    )


def test_only_transferred_codex_service_accepts_clear_attempt_secret_probe(tmp_path):
    control = CodexControl(CATALOGUE, Native(CodexTurn("request-1", "turn-1", "gpt-5.6", "ok", 1, 1, 1)))
    probe = classify_surfaces(
        {name: b"attempt-only" for name in ("memory", "environment", "argv", "file", "event")},
        (b"subscription-secret",),
    )
    receipt = BrokerReceipt(Broker.CODEX, ("CODEX_HOME",), "a" * 64, os.getpid(), 1000, "b" * 64)

    establish_service_custody(control, receipt, probe)

    assert dict(probe.checks)["environment"]
    assert dict(probe.checks)["file"]
    assert dict(probe.checks)["event"]
