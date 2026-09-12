import multiprocessing
import os
import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
import datetime as dt
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from solver.cpa_contracts import CPAConfig, CPAModelReply, CPAServiceRefused, CPAStatus, CPAToolCall
from solver.cpa_harness import CPAHarness
from solver.cpa_receipt import build_cpa_receipt, validate_cpa_receipt
from solver.cpa_service import CPALeadPort, CPAService
from solver.cpa_service import CPA_CREDENTIAL_FD_ENV, CPA_RESPONSES_URL_ENV
from solver.__main__ import CPA_ROUTE_ENV, _compose_cpa_lead
from solver.carry import Boundary
from solver.codex import Credential, Invocation
from solver.capability_evidence import CapabilityEvidence
from solver.capability_event_contracts import BrokerCustody
from solver.event_store import EventStore
from solver.event_store_contracts import CapabilityCustodyRecorded, CapabilityRecord
from solver.event_store_storage import canonical_bytes
from solver.executor_secret_probe import SURFACE_NAMES, classify_surfaces
from solver.lead_contracts import LeadBinding, LeadInitialContext, LeadRequest, ProgressProposal
from solver.lead_controller import LeadController
from solver.lead_v1_adapter import V1LeadTurn
from solver.redaction import Redactor
from solver.work_generation import GenerationFence
from solver.local_ipc import receive_line


def clock():
    return "2026-09-12T00:00:00Z"


def execute_inspect(_name, _arguments):
    return "application/zip"


def clear_attempt_probe(secrets):
    return classify_surfaces({name: b"clear" for name in SURFACE_NAMES}, secrets)


def seed_cpa_custody(state):
    store = EventStore(state, run_id="run-1", redactor=Redactor({}))
    store.append(
        CapabilityCustodyRecorded(
            "capability:boot-1:000001",
            BrokerCustody(
                CapabilityRecord.TRANSFER_ACCEPTED,
                "run-1",
                "boot-1",
                "cpa",
                ("CPA_TOKEN",),
                "a" * 64,
                501,
                "b" * 64,
            ),
            clock(),
        ),
        body=b"",
    )
    CapabilityEvidence(state, "run-1", "boot-1", Redactor({}), clock).record_executor_probe(
        clear_attempt_probe((b"cpa-fixture",))
    )


def executor_origin_probe(endpoint, handle, output):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(endpoint)
    connection.sendall(
        canonical_bytes(
            {
                "version": "cpa-harness/v1",
                "handle": hashlib.sha256(handle.encode()).hexdigest(),
                "request_id": "executor-origin-probe",
                "prompt": "probe",
            }
        )
        + b"\n"
    )
    output.put(json.loads(receive_line(connection, failure="missing CPA probe response")))
    connection.close()


class ControlledModel:
    def __init__(self, *replies, calls=None, delay=0):
        self.replies, self.calls, self.delay = list(replies), calls, delay

    def respond(self, request, *, credential, parallel_tool_calls):
        if self.calls is not None:
            with self.calls.get_lock():
                self.calls.value += 1
        assert credential == "secret-cpa-token"
        assert parallel_tool_calls is False
        if self.delay:
            time.sleep(self.delay)
        return self.replies.pop(0)


class CrashingModel:
    def respond(self, request, *, credential, parallel_tool_calls):
        os._exit(70)


class ResponsesHandler(BaseHTTPRequestHandler):
    authorization = ""

    def do_POST(self):
        type(self).authorization = self.headers.get("Authorization", "")
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        assert request["parallel_tool_calls"] is False
        response = json.dumps(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "propose_progress",
                        "arguments": json.dumps({"detail": "production CPA route"}),
                    }
                ],
                "usage": {"input_tokens": 9, "output_tokens": 3},
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *_args):
        pass


def binding(generation_id):
    return LeadBinding(
        "run-1",
        "boot-1",
        generation_id,
        "lane-1",
        "attempt-1",
        "challenge-1",
        "engagement-1",
        "run-controller",
        "",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
        "private-cpa",
        "private-cpa",
        "gpt",
        "gpt",
        "gpt",
        "medium",
        "medium",
        "9" * 64,
        100,
        "2026-09-12T00:01:00Z",
        clock(),
    )


def request(generation_id):
    return LeadRequest(binding(generation_id), 1, LeadInitialContext("inspect artefact"))


def service(tmp_path, model):
    seed_cpa_custody(tmp_path)
    directory = tempfile.TemporaryDirectory(dir="/tmp")
    instance = CPAService(
        endpoint=Path(directory.name) / "private" / "cpa.sock",
        config=CPAConfig(2, 1, ("inspect",), model="gpt"),
        credential="secret-cpa-token",
        model=model,
        execute_tool=execute_inspect,
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-1",
        redactor=Redactor({"CPA": "secret-cpa-token"}),
        timestamp=clock,
    )
    instance._test_directory = directory
    return instance


def test_controlled_private_process_traverses_the_production_lead_port(tmp_path):
    fence = GenerationFence(tmp_path, "run-1", Redactor({}), clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    model = ControlledModel(CPAModelReply(proposal=ProgressProposal("identified archive"), tokens_in=17, tokens_out=4))
    with service(tmp_path, model) as private:
        assert private.credential_cleared
        assert private.endpoint.is_socket()
        assert private.endpoint.stat().st_mode & 0o077 == 0
        assert private.endpoint.parent.stat().st_mode & 0o077 == 0
        controller = LeadController(tmp_path, "run-1", Redactor({}), clock, CPALeadPort(private), fence=fence)
        outcome = controller.handle(request(generation.generation_id))

    assert outcome.classification.value == "accepted", outcome.detail
    assert outcome.proposal == ProgressProposal("identified archive")
    assert outcome.state.transitions[0].measure.tokens_in == 17


def test_boot_composition_builds_the_cpa_service_and_run_lead_route(tmp_path):
    seed_cpa_custody(tmp_path)
    fence = GenerationFence(tmp_path, "run-1", Redactor({}), clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    credential_read, credential_write = os.pipe()
    os.write(credential_write, b"secret-cpa-token")
    os.close(credential_write)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ResponsesHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    stack = ExitStack()
    try:
        adapter = _compose_cpa_lead(
            {
                CPA_CREDENTIAL_FD_ENV: str(credential_read),
                CPA_RESPONSES_URL_ENV: f"http://127.0.0.1:{server.server_port}/responses",
                CPA_ROUTE_ENV: "private-cpa",
            },
            tmp_path,
            SimpleNamespace(run_id="run-1", model="gpt"),
            "boot-1",
            SimpleNamespace(generations=fence),
            stack,
        )
        outcome = adapter(
            V1LeadTurn(
                "inspect artefact",
                Boundary(),
                (Credential("codex-subscription", "gpt", tmp_path),),
                Invocation(reasoning_effort="medium"),
                "run-1",
                "boot-1",
                generation.generation_id,
                "lane-1",
                "attempt-1",
                "challenge-1",
                60,
                dt.datetime(2026, 9, 12, tzinfo=dt.timezone.utc),
                dt.datetime(2026, 9, 12, 0, 1, tzinfo=dt.timezone.utc),
            )
        )
    finally:
        stack.close()
        server.shutdown()
        server.server_close()
        thread.join()

    assert outcome.proposal == ProgressProposal("production CPA route")
    assert outcome.state.binding.harness == "private-cpa"
    assert ResponsesHandler.authorization == "Bearer secret-cpa-token"


def test_capability_denies_executor_before_credential_transport(tmp_path):
    calls = multiprocessing.get_context("spawn").Value("i", 0)
    model = ControlledModel(CPAModelReply(proposal=ProgressProposal("done")), calls=calls)
    with service(tmp_path, model) as private:
        denied = private.denied_probe("attempt-owned-fixture")
        assert calls.value == 0

    assert denied.status is CPAStatus.REFUSED
    assert denied.audit == ("denied:capability",)
    raw = private._store.events_path.read_bytes()
    assert b"secret-cpa-token" not in raw
    assert b"attempt-owned-fixture" not in raw


def test_public_listener_and_wrong_protocol_version_are_denied(tmp_path):
    with pytest.raises(CPAServiceRefused, match="private Unix"):
        CPAService(
            endpoint="0.0.0.0:8080",
            config=CPAConfig(1, 0, ()),
            credential="secret-cpa-token",
            model=ControlledModel(),
            execute_tool=execute_inspect,
            state=tmp_path,
            run_id="run-1",
            boot_id="boot-1",
            redactor=Redactor({}),
            timestamp=clock,
        )

    calls = multiprocessing.get_context("spawn").Value("i", 0)
    model = ControlledModel(CPAModelReply(proposal=ProgressProposal("never")), calls=calls)
    with service(tmp_path, model) as private:
        denied = private.denied_probe("attempt-owned-fixture", version="cpa-harness/v0")

    assert denied.status is CPAStatus.REFUSED
    assert denied.audit == ("denied:service-version",)
    assert calls.value == 0


@pytest.mark.skipif(sys.platform != "linux" or not Path("/proc").is_dir(), reason="full peer identity is Linux")
def test_executor_process_is_denied_even_if_it_learns_the_capability(tmp_path):
    context = multiprocessing.get_context("spawn")
    calls = context.Value("i", 0)
    output = context.Queue()
    with service(tmp_path, ControlledModel(CPAModelReply(proposal=ProgressProposal("never")), calls=calls)) as private:
        executor = context.Process(target=executor_origin_probe, args=(str(private.endpoint), private._handle, output))
        executor.start()
        executor.join(3)
        response = output.get(timeout=1)

    assert response["status"] == "refused"
    assert response["audit"] == ["denied:capability"]
    assert calls.value == 0


def test_closed_tools_parallel_calls_and_turn_bound_remain_inside_worker():
    parallel = ControlledModel(CPAModelReply(tool_calls=(CPAToolCall("inspect"), CPAToolCall("inspect"))))
    forbidden = ControlledModel(CPAModelReply(tool_calls=(CPAToolCall("raw-http"),)))
    bounded = ControlledModel(CPAModelReply(tool_calls=(CPAToolCall("inspect"),)))

    assert (
        CPAHarness(
            CPAConfig(1, 1, ("inspect",)), model=parallel, credential="secret-cpa-token", execute_tool=lambda *_: None
        )
        .execute("x")
        .detail
        == "parallel tool calls are forbidden"
    )
    assert (
        CPAHarness(
            CPAConfig(1, 1, ("inspect",)), model=forbidden, credential="secret-cpa-token", execute_tool=lambda *_: None
        )
        .execute("x")
        .detail
        == "tool is not allowed"
    )
    assert (
        CPAHarness(
            CPAConfig(1, 1, ("inspect",)), model=bounded, credential="secret-cpa-token", execute_tool=lambda *_: "ok"
        )
        .execute("x")
        .status
        is CPAStatus.TIMEOUT
    )


@pytest.mark.parametrize("cancel", [True, False])
def test_inflight_cancel_and_deadline_reap_only_the_cpa_process(tmp_path, cancel):
    model = ControlledModel(CPAModelReply(proposal=ProgressProposal("late")), delay=2)
    native = multiprocessing.get_context("fork").Process(target=time.sleep, args=(3,))
    native.start()
    try:
        with service(tmp_path, model) as private:
            signal = threading.Event()
            if cancel:
                threading.Timer(0.03, signal.set).start()
            outcome = private.execute(
                request("generation-1"),
                deadline=time.monotonic() + (1 if cancel else 0.03),
                cancel=signal,
            )
            assert private._reaped
            assert native.is_alive()
        assert outcome.status is (CPAStatus.CANCELLED if cancel else CPAStatus.TIMEOUT)
        with pytest.raises(CPAServiceRefused, match="cannot be replaced"):
            private.__enter__()
    finally:
        native.terminate()
        native.join()


def test_process_crash_is_typed_and_reaped(tmp_path):
    with service(tmp_path, CrashingModel()) as private:
        outcome = private.execute(request("generation-1"), deadline=time.monotonic() + 1)

    assert outcome.status is CPAStatus.CRASHED
    assert private._reaped
    assert private.credential_cleared


def test_receipt_is_reconstructed_from_canonical_events_after_teardown(tmp_path):
    model = ControlledModel(CPAModelReply(proposal=ProgressProposal("done"), tokens_in=1, tokens_out=1))
    private = service(tmp_path, model)
    with private:
        private.execute(request("generation-1"), deadline=time.monotonic() + 1)

    receipt = build_cpa_receipt(EventStore(tmp_path, run_id="run-1"), private.config)

    validate_cpa_receipt(receipt, store=EventStore(tmp_path, run_id="run-1"), config=private.config)
    assert [row["record"] for row in receipt["audit"]] == ["probe", "request", "teardown"]
    assert receipt["audit"][1]["measured"] is True
    assert receipt["audit"][1]["tokens_in"] == 1
    assert receipt["audit"][1]["tokens_out"] == 1
    assert receipt["audit"][1]["proposal_kind"] == "progress"
    assert "secret-cpa-token" not in str(receipt)
