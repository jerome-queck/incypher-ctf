"""Attempt-scoped public Research crosses one bounded policy broker."""

from __future__ import annotations

import socket
import tempfile
import hashlib
import datetime as dt
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from solver.capability import CapabilityBinding
from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import NetworkProbeDeclaration, ResourceOutcome, RuntimeBinding
from solver.event_store import EventStore
from solver.redaction import Redactor
from solver.recon import recon
from solver.record import Recorder
from solver.recovery.runtime import DeterministicRecovery
from solver.recovery.incident import RECEIPT
from solver.research_broker import ResearchBrokerRuntime
from solver.research_broker_contracts import (
    RESEARCH_BROKER_RECORDED,
    ResearchKind,
    ResearchLimits,
    ResearchOutcome,
    ResearchQuery,
    ResearchSource,
    ResearchTransportResult,
)
from solver.research_broker_ipc import (
    ResearchBrokerClient,
    ResearchBrokerService,
    ResearchCompatibilityAdapter,
)
from solver.research_broker_receipt import link_manifest, verify_receipt, write_receipt
from solver.work_generation import GenerationFence
from test_manifest import draft
from test_attempt_executor import IMAGE_ID, OutcomeRuntime, isolation_receipt, request as attempt_request


def test_typed_osint_live_and_recorded_queries_share_schema_and_refuse_unknown_sources(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    transported = []

    def transport(url, address, _limits):
        transported.append((url, address))
        return ResearchTransportResult(
            status=200,
            headers={"content-type": "application/rdap+json"},
            body=b'{"ldhName":"example.com"}',
            elapsed_ms=4,
        )

    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(128, 1, 0, 0, max_requests=3, max_total_bytes=256),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda _host: ("8.8.8.8",),
        transport=transport,
        sources={
            "rdap": ResearchSource(
                ResearchKind.DOMAIN,
                "https://research.example/domain/{subject}",
                terms="public-rdap",
                robots="not-applicable-api",
                terms_decision="allow",
                robots_decision="not-applicable",
            ),
        },
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        live = runtime.query(left, handle, ResearchQuery.live(ResearchKind.DOMAIN, "rdap", "example.com"))
        recorded = runtime.query(
            left,
            handle,
            ResearchQuery.recorded(
                ResearchKind.DOMAIN,
                "rdap",
                "example.com",
                b'{"ldhName":"example.com"}',
                content_type="application/rdap+json",
            ),
        )
        denied = runtime.query(left, handle, ResearchQuery.live(ResearchKind.DOMAIN, "arbitrary", "example.com"))
    finally:
        left.close()
        right.close()

    assert live.outcome is ResearchOutcome.ANSWERED
    assert recorded.outcome is ResearchOutcome.ANSWERED
    assert live.body == recorded.body
    assert live.content_type == recorded.content_type
    assert live.provenance.kind is ResearchKind.DOMAIN
    assert live.provenance.source_id == "rdap"
    assert live.provenance.terms == "public-rdap"
    assert live.provenance.robots == "not-applicable-api"
    assert live.provenance.origin == "live"
    assert recorded.provenance.origin == "recorded"
    assert denied.outcome is ResearchOutcome.CAPABILITY_REFUSED
    assert transported == [("https://research.example/domain/example.com", "8.8.8.8")]


def test_typed_osint_recordings_obey_cumulative_request_and_byte_budgets(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(8, 1, 0, 0, max_requests=2, max_total_bytes=5),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        sources={
            "rdap": ResearchSource(
                ResearchKind.DOMAIN,
                "https://unused/{subject}",
                "terms",
                "robots",
                terms_decision="allow",
                robots_decision="not-applicable",
            ),
        },
    )
    left, right = socket.socketpair()
    try:
        runtime.prepare_attempt(CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s"))
        handle = runtime.claim(left, generation.generation_id)
        query = ResearchQuery.recorded(
            ResearchKind.DOMAIN,
            "rdap",
            "example.test",
            b"four",
            content_type="application/rdap+json",
        )
        first = runtime.query(left, handle, query)
        exhausted = runtime.query(left, handle, query)
    finally:
        left.close()
        right.close()

    assert first.outcome is ResearchOutcome.ANSWERED
    assert exhausted.outcome is ResearchOutcome.BUDGET_EXHAUSTED
    assert exhausted.body == b""


def test_research_client_query_decodes_malformed_provenance_consistently() -> None:
    class ReplyingConnection:
        def __init__(self) -> None:
            self._answer = bytearray()

        def sendall(self, _request: bytes) -> None:
            self._answer = bytearray(b'{"outcome":"answered","provenance":"invalid"}\n')

        def recv(self, _size: int) -> bytes:
            if not self._answer:
                return b""
            return bytes((self._answer.pop(0),))

        def close(self) -> None:
            return None

    client = ResearchBrokerClient(ReplyingConnection())  # type: ignore[arg-type]

    first = client.query(ResearchQuery.live(ResearchKind.DOMAIN, "rdap", "example.com"))
    queried = client.query(ResearchQuery.live(ResearchKind.DOMAIN, "rdap", "example.com"))

    assert first == queried


def test_allowed_public_fixture_is_bounded_sealed_and_provenanced(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    observed = []

    def transport(url: str, address: str, limits: ResearchLimits) -> ResearchTransportResult:
        observed.append((url, address, limits))
        return ResearchTransportResult(
            status=200,
            headers={"content-type": "text/plain"},
            body=b"public fixture",
            elapsed_ms=7,
        )

    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(max_body_bytes=64, timeout_seconds=1, max_redirects=2, cache_seconds=60),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda host: ("8.8.8.8",),
        transport=transport,
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        result = runtime.fetch(left, handle, "https://research.example/fact")
    finally:
        left.close()
        right.close()

    assert result.outcome is ResearchOutcome.ANSWERED
    assert result.status == 200
    assert result.body == b"public fixture"
    assert result.content_type == "text/plain"
    assert result.provenance.dns_chain == (("research.example", ("8.8.8.8",)),)
    assert result.provenance.redirect_chain == ("https://research.example/fact",)
    assert result.provenance.body_digest.startswith("sha256:")
    assert result.provenance.observed_at == "2026-09-13T00:00:00+00:00"
    assert observed[0][1] == "8.8.8.8"
    event = next(
        event for event in EventStore(state, run_id="run-1").events() if event.event_type == RESEARCH_BROKER_RECORDED
    )
    assert EventStore(state, run_id="run-1").blob(event.blob_digest) == b"public fixture"
    assert event.payload["blob_digest"] == result.provenance.body_digest.removeprefix("sha256:")


def test_timeout_recovery_contains_an_identical_research_request(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("challenge-7", "attempt-1")
    attempts = []

    def transport(_url: str, _address: str, _limits: ResearchLimits) -> ResearchTransportResult:
        attempts.append(True)
        if len(attempts) == 1:
            return ResearchTransportResult(outcome=ResearchOutcome.TIMEOUT)
        return ResearchTransportResult(status=200, headers={"content-type": "text/plain"}, body=b"answer")

    recovery = DeterministicRecovery(
        state,
        "run-1",
        Redactor({}),
        now=lambda: dt.datetime(2026, 9, 13, tzinfo=dt.timezone.utc),
    )
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 2, 60),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda _host: ("8.8.8.8",),
        transport=transport,
        recovery=recovery,
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        result = runtime.fetch(left, handle, "https://research.example/fact")
        refused = runtime.fetch(left, handle, "https://research.example/fact")
        unrelated = runtime.fetch(left, handle, "https://research.example/other")
    finally:
        left.close()
        right.close()

    assert result.outcome is ResearchOutcome.TIMEOUT
    assert refused.outcome is ResearchOutcome.DENIED
    assert unrelated.outcome is ResearchOutcome.ANSWERED
    assert len(attempts) == 2
    receipt = json.loads((state / "runs" / "run-1" / "canonical" / RECEIPT).read_text())
    assert receipt["changed_action"] == {}
    assert receipt["probe"]["outcome"] == "unsettled"


@pytest.mark.parametrize(
    ("url", "addresses", "denied_hosts"),
    [
        ("http://127.0.0.1/private", ("127.0.0.1",), ()),
        ("http://metadata.test/latest", ("169.254.169.254",), ()),
        ("https://board.example/api", ("8.8.8.8",), ("board.example",)),
        ("https://target.example/", ("8.8.4.4",), ("target.example",)),
        ("https://rig-control.example/", ("1.1.1.1",), ("rig-control.example",)),
    ],
)
def test_non_public_and_reserved_authority_destinations_are_denied(
    tmp_path: Path, url: str, addresses: tuple[str, ...], denied_hosts: tuple[str, ...]
) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    transported = []
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 2, 60),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda _host: addresses,
        transport=lambda *_args: transported.append(True),
        denied_hosts=denied_hosts,
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
        runtime.prepare_attempt(binding)
        result = runtime.fetch(left, runtime.claim(left, generation.generation_id), url)
    finally:
        left.close()
        right.close()

    assert result.outcome is ResearchOutcome.DENIED
    assert transported == []


def test_redirect_is_rechecked_and_cannot_escape_to_private_address(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")

    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 2, 60),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda host: ("8.8.8.8",) if host == "public.example" else ("10.0.0.2",),
        transport=lambda *_args: ResearchTransportResult(
            status=302,
            headers={"location": "http://internal.example/secret"},
            redirect_url="http://internal.example/secret",
        ),
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
        runtime.prepare_attempt(binding)
        result = runtime.fetch(
            left,
            runtime.claim(left, generation.generation_id),
            "https://public.example/start",
        )
    finally:
        left.close()
        right.close()

    assert result.outcome is ResearchOutcome.DENIED
    assert result.provenance.redirect_chain == ("https://public.example/start",)


def test_oversize_body_is_classified_without_sealing_or_returning_it(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(4, 1, 0, 60),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda host: ("127.0.0.1",) if host == "private.example" else ("8.8.8.8",),
        transport=lambda *_args: ResearchTransportResult(
            status=200, headers={"content-type": "text/plain; charset=utf-8"}, body=b"oversize"
        ),
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
        runtime.prepare_attempt(binding)
        result = runtime.fetch(left, runtime.claim(left, generation.generation_id), "https://public.example/")
    finally:
        left.close()
        right.close()

    assert result.outcome is ResearchOutcome.TOO_LARGE
    assert result.body == b""
    assert result.content_type == "text/plain"


def test_cache_retains_original_provenance_but_stale_entry_cannot_become_current_fact(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    current = ["2026-09-13T00:00:00+00:00"]
    transports = iter(
        [
            ResearchTransportResult(status=200, headers={"content-type": "text/plain"}, body=b"fresh"),
            ResearchTransportResult(outcome=ResearchOutcome.UNREACHABLE),
        ]
    )
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 0, 60),
        timestamp=lambda: current[0],
        resolve=lambda _host: ("8.8.8.8",),
        transport=lambda *_args: next(transports),
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        first = runtime.fetch(left, handle, "https://public.example/fact")
        current[0] = "2026-09-13T00:00:30+00:00"
        cached = runtime.fetch(left, handle, "https://public.example/fact")
        current[0] = "2026-09-13T00:02:00+00:00"
        stale = runtime.fetch(left, handle, "https://public.example/fact")
    finally:
        left.close()
        right.close()

    assert cached.cached is True
    assert cached.body == b"fresh"
    assert cached.provenance == first.provenance
    assert stale.outcome is ResearchOutcome.UNREACHABLE
    assert stale.body == b""


def test_cache_hits_consume_the_generation_byte_budget(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(4, 1, 0, 60, max_requests=2, max_total_bytes=5),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda _host: ("8.8.8.8",),
        transport=lambda *_args: ResearchTransportResult(
            status=200,
            headers={"content-type": "text/plain"},
            body=b"four",
        ),
    )
    left, right = socket.socketpair()
    try:
        runtime.prepare_attempt(CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s"))
        handle = runtime.claim(left, generation.generation_id)
        first = runtime.fetch(left, handle, "https://public.example/fact")
        exhausted = runtime.fetch(left, handle, "https://public.example/fact")
    finally:
        left.close()
        right.close()

    assert first.outcome is ResearchOutcome.ANSWERED
    assert exhausted.outcome is ResearchOutcome.BUDGET_EXHAUSTED
    assert exhausted.body == b""


def test_revoked_generation_cannot_return_research_fact(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 0, 60),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda _host: ("8.8.8.8",),
        transport=lambda *_args: ResearchTransportResult(status=200, body=b"must not escape"),
    )
    left, right = socket.socketpair()
    try:
        binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
        runtime.prepare_attempt(binding)
        handle = runtime.claim(left, generation.generation_id)
        runtime.revoke_generation(generation.generation_id)
        result = runtime.fetch(left, handle, "https://public.example/")
    finally:
        left.close()
        right.close()

    assert result.outcome is ResearchOutcome.REVOKED
    assert result.body == b""


def test_generation_revocation_scrubs_an_in_flight_research_fact(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    entered = threading.Event()
    release = threading.Event()

    def transport(*_args):
        entered.set()
        release.wait(2)
        return ResearchTransportResult(
            status=200,
            headers={"content-type": "text/plain"},
            body=b"must-not-escape",
        )

    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 0, 0),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda _host: ("8.8.8.8",),
        transport=transport,
    )
    left, right = socket.socketpair()
    held = []
    try:
        runtime.prepare_attempt(CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s"))
        handle = runtime.claim(left, generation.generation_id)
        thread = threading.Thread(
            target=lambda: held.append(runtime.fetch(left, handle, "https://public.example/fact"))
        )
        thread.start()
        assert entered.wait(1)
        runtime.revoke_generation(generation.generation_id)
        release.set()
        thread.join(1)
    finally:
        left.close()
        right.close()

    assert held[0].outcome is ResearchOutcome.REVOKED
    assert held[0].body == b""


def test_research_service_bounds_stalled_clients(tmp_path: Path) -> None:
    runtime = ResearchBrokerRuntime(
        state=tmp_path / "state",
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 0, 0),
        timestamp=lambda: "now",
    )
    with tempfile.TemporaryDirectory(prefix="research-broker-", dir="/tmp") as directory:
        service = ResearchBrokerService(Path(directory) / "research.sock", runtime)
        service.start()
        slow = []
        try:
            for _ in range(8):
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
        finally:
            for connection in slow:
                connection.close()
            service.close()


def test_hostile_research_service_rejects_arbitrary_fetch_command(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 0, 0),
        timestamp=lambda: "now",
    )
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
    runtime.prepare_attempt(binding)
    with tempfile.TemporaryDirectory(prefix="research-broker-", dir="/tmp") as directory:
        service = ResearchBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            connection.connect(str(service.path))
            connection.sendall(
                json.dumps({"command": "claim", "generation_id": generation.generation_id}).encode() + b"\n"
            )
            assert json.loads(_read_line(connection)) == {"claimed": True}
            connection.sendall(json.dumps({"command": "fetch", "url": "https://arbitrary.example/"}).encode() + b"\n")
            answer = json.loads(_read_line(connection))
        finally:
            connection.close()
            service.close()

    assert answer == {"outcome": ResearchOutcome.CAPABILITY_REFUSED.value}


def test_research_policy_decision_is_enforced_and_sealed(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 0, 0),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        sources={
            "blocked": ResearchSource(
                ResearchKind.DOMAIN,
                "https://research.example/{subject}",
                "terms-policy",
                "robots-policy",
                terms_decision="deny",
                robots_decision="not-applicable",
            )
        },
    )
    left, right = socket.socketpair()
    try:
        runtime.prepare_attempt(CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s"))
        result = runtime.query(
            left,
            runtime.claim(left, generation.generation_id),
            ResearchQuery.live(ResearchKind.DOMAIN, "blocked", "example.test"),
        )
    finally:
        left.close()
        right.close()

    assert result.outcome is ResearchOutcome.DENIED
    assert result.provenance.terms_decision == "deny"
    assert result.provenance.robots_decision == "not-applicable"
    assert result.provenance.policy_decision == "deny"
    event = next(event for event in EventStore(state, "run-1").events() if event.event_type == RESEARCH_BROKER_RECORDED)
    assert event.payload["policy_decision"] == "deny"
    assert event.payload["terms_decision"] == "deny"
    receipt = json.loads(write_receipt(state, "run-1").read_text())
    assert receipt["requests"][0]["query"]["policy_decision"] == "deny"
    assert receipt["requests"][0]["query"]["terms_decision"] == "deny"


def _read_line(connection: socket.socket) -> bytes:
    line = bytearray()
    while True:
        chunk = connection.recv(1)
        if not chunk:
            return bytes(line)
        if chunk == b"\n":
            return bytes(line)
        line.extend(chunk)


def test_worker_uses_only_the_bounded_research_port(tmp_path: Path) -> None:
    state = tmp_path / "state"
    recorder = Recorder(tmp_path / "recon-state", run_id="run-1", redactor=Redactor({}))
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "a")
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 0, 60),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda host: ("127.0.0.1",) if host == "private.example" else ("8.8.8.8",),
        transport=lambda *_args: ResearchTransportResult(status=200, body=b"through-port"),
        sources={
            "rdap": ResearchSource(
                ResearchKind.DOMAIN,
                "https://public.example/{subject}",
                "fixture",
                "not-applicable",
                terms_decision="allow",
                robots_decision="not-applicable",
            ),
            "private": ResearchSource(
                ResearchKind.DOMAIN,
                "https://private.example/{subject}",
                "fixture",
                "not-applicable",
                terms_decision="allow",
                robots_decision="not-applicable",
            ),
        },
    )
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
    runtime.prepare_attempt(binding)
    with tempfile.TemporaryDirectory(prefix="research-broker-", dir="/tmp") as directory:
        service = ResearchBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        try:
            client = ResearchBrokerClient.claim(service.path, generation.generation_id)
            query = ResearchQuery.live(ResearchKind.DOMAIN, "rdap", "public.example")
            opened = recon(
                "challenge",
                (),
                flag_wrappers=(r"flag\{.*?\}",),
                recorder=recorder,
                attempt_id="attempt-research",
                research_urls=("https://public.example/fact",),
                research=ResearchCompatibilityAdapter(
                    client,
                    {"https://public.example/fact": query},
                ),
            )
            result = client.query(query)
            denied = client.query(ResearchQuery.live(ResearchKind.DOMAIN, "private", "secret"))
            client.close()
        finally:
            service.close()

    assert result.outcome is ResearchOutcome.ANSWERED
    assert result.body == b"through-port"
    assert "through-port" in opened.block()
    assert denied.outcome is ResearchOutcome.DENIED
    assert not hasattr(ResearchBrokerClient, "open_socket")

    receipt = write_receipt(state, "run-1")
    assert verify_receipt(receipt) == receipt
    linked = link_manifest(draft(), receipt)
    row = next(row for row in linked["requirements"] if row["row_id"] == "core.brokered-credentials-egress")
    assert row["receipt_ref"] == "receipt:research-broker"


def test_hostile_research_client_batches_recorded_and_live_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from solver import research_broker_worker_client as worker_client

    queries = {
        "queries": [
            {
                "kind": "domain",
                "source_id": "rdap",
                "subject": "example.test",
                "body": "e30=",
                "content_type": "application/json",
            },
            {"kind": "domain", "source_id": "rdap", "subject": "example.test", "body": "", "content_type": ""},
        ]
    }
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(queries))
    answers = [
        {"claimed": True},
        {"outcome": "answered", "body": "e30=", "provenance": {"origin": "recorded"}},
        {"outcome": "answered", "body": "e30=", "provenance": {"origin": "live"}},
    ]

    class Connection:
        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []
            self.pending = bytearray()

        def connect(self, _path: str) -> None:
            return None

        def sendall(self, raw: bytes) -> None:
            self.sent.append(json.loads(raw))
            self.pending.extend(json.dumps(answers[len(self.sent) - 1]).encode() + b"\n")

        def recv(self, _size: int) -> bytes:
            return bytes((self.pending.pop(0),)) if self.pending else b""

    connection = Connection()
    monkeypatch.setattr(worker_client.socket, "socket", lambda *_args: connection)
    monkeypatch.setenv("INCYPHER_RESEARCH_SOCKET", "/run/research.sock")
    monkeypatch.setenv("INCYPHER_RESEARCH_GENERATION", "generation-1")

    assert worker_client.main(["query", str(path)]) == 0
    assert [item["command"] for item in connection.sent] == ["claim", "query", "query"]
    assert [item["provenance"]["origin"] for item in json.loads(capsys.readouterr().out)] == ["recorded", "live"]
    assert worker_client.main(["https://arbitrary.example/"]) == 2


def test_strict_attempt_denies_raw_research_socket_and_broker_records_the_probe(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generation = GenerationFence(state, "run-1", Redactor({}), lambda: "now").acquire("c", "attempt-1")
    runtime = ResearchBrokerRuntime(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        limits=ResearchLimits(64, 1, 0, 60),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
        resolve=lambda _host: ("8.8.8.8",),
        transport=lambda *_args: ResearchTransportResult(status=200, body=b"broker only"),
    )
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
        generation_fence=GenerationFence(state, "run-1", Redactor({}), lambda: "now"),
        runtime=OutcomeRuntime(ResourceOutcome.NETWORK),
        timestamp=lambda: "2026-09-13T00:00:00+00:00",
    )
    attempted = hashlib.sha256(b"8.8.8.8:443").hexdigest()
    request = replace(
        attempt_request(tmp_path, generation.generation_id),
        network_probe=NetworkProbeDeclaration("research", attempted),
    )
    assert executor.start(request).result().outcome is ResourceOutcome.NETWORK
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
    assert runtime.record_raw_egress_denial(binding, attempted).outcome is ResearchOutcome.DENIED
    executor.close()
