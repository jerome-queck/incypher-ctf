"""Attempt-scoped public Research crosses one bounded policy broker."""

from __future__ import annotations

import socket
import tempfile
import hashlib
import datetime as dt
import json
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
    ResearchLimits,
    ResearchOutcome,
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
        result = runtime.fetch(left, runtime.claim(left, generation.generation_id), "https://research.example/fact")
    finally:
        left.close()
        right.close()

    assert result.outcome is ResearchOutcome.TIMEOUT
    assert len(attempts) == 1
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
    )
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "l", "a", "s")
    runtime.prepare_attempt(binding)
    with tempfile.TemporaryDirectory(prefix="research-broker-", dir="/tmp") as directory:
        service = ResearchBrokerService(Path(directory) / "broker.sock", runtime)
        service.start()
        try:
            client = ResearchBrokerClient.claim(service.path, generation.generation_id)
            opened = recon(
                "challenge",
                (),
                flag_wrappers=(r"flag\{.*?\}",),
                recorder=recorder,
                attempt_id="attempt-research",
                research_urls=("https://public.example/fact",),
                research=ResearchCompatibilityAdapter(client),
            )
            result = client.fetch("https://public.example/fact")
            denied = client.fetch("https://private.example/secret")
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
