"""Strict-image proof that hostile workers reach only their Target broker port."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import socket
import threading
from pathlib import Path

from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import (
    AttemptRequest,
    EnvelopeSpec,
    NetworkPolicy,
    NetworkProbeDeclaration,
    NETWORK_PROBE_KINDS,
    RuntimeBinding,
    ResourceOutcome,
)
from solver.attempt_executor_pool import close_attempt_pool, prepare_attempt_pool
from solver.attempt_executor_runtime import AttemptRuntime
from solver.isolation import IMAGE_ID, strict_preflight
from solver.isolation_receipt import write_receipt as write_isolation_receipt
from solver.redaction import Redactor
from solver.capability import CapabilityBinding
from solver.target_broker import TargetBrokerRuntime
from solver.target_broker_contracts import TargetCandidateBinding, TargetEndpoint, TargetLimits, TargetProtocol
from solver.target_broker_receipt import verify_receipt, write_receipt
from solver.work_generation import GenerationDisposition, GenerationFence

STATE = Path("/state")
RUN_ID = "target-broker-qualification"


def main() -> int:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    deny_kinds = tuple(sorted(NETWORK_PROBE_KINDS))
    deny_listeners = []
    for _kind in deny_kinds:
        denied = socket.socket()
        denied.bind(("127.0.0.1", 0))
        denied.listen(8)
        deny_listeners.append(denied)
    denied_endpoints = {
        kind: TargetEndpoint(TargetProtocol.TCP, "127.0.0.1", denied.getsockname()[1])
        for kind, denied in zip(deny_kinds, deny_listeners, strict=True)
    }

    def target() -> None:
        connection, _ = listener.accept()
        with connection:
            if connection.recv(16) == b"ping":
                connection.sendall(b"pong")

    target_thread = threading.Thread(target=target, daemon=True)
    target_thread.start()
    pool = None
    try:
        environ = os.environ

        def prepare() -> None:
            nonlocal pool
            pool = prepare_attempt_pool(slots=1)

        admitted = strict_preflight(environ, prepare_attempt_runtime=prepare)
        isolation = write_isolation_receipt(STATE, RUN_ID, admitted)
        binding = RuntimeBinding(
            environ[IMAGE_ID],
            environ["INCYPHER_IMAGE_MANIFEST"],
            environ["INCYPHER_IMAGE_CONFIG"],
            environ["INCYPHER_IMAGE_PLATFORM"],
        )

        def timestamp() -> str:
            return dt.datetime.now(dt.timezone.utc).isoformat()

        fence = GenerationFence(STATE, RUN_ID, Redactor({}), timestamp)
        generation = fence.acquire("fixture-target", "attempt-1")
        broker = TargetBrokerRuntime(
            state=STATE,
            run_id=RUN_ID,
            boot_id="boot-qualification",
            challenge_id="fixture-target",
            candidate=TargetCandidateBinding(
                binding.image_id,
                binding.image_manifest_digest,
                binding.image_config_digest,
                binding.platform,
                admitted.profile_digest,
            ),
            endpoint=TargetEndpoint(TargetProtocol.TCP, "127.0.0.1", listener.getsockname()[1]),
            limits=TargetLimits(1, 64, 64, 2),
            timestamp=timestamp,
            denied_endpoints=denied_endpoints,
        )
        executor = AttemptExecutor(
            state=STATE,
            run_id=RUN_ID,
            isolation_receipt=isolation,
            binding=binding,
            generation_fence=fence,
            runtime=AttemptRuntime(pool, target_broker=broker),  # type: ignore[arg-type]
            timestamp=timestamp,
        )
        broker.bind_attempt_executor(executor)
        workspace = STATE / "target-probe-work"
        workspace.mkdir()

        def run(argv: tuple[str, ...], network_probe: NetworkProbeDeclaration | None = None):
            return executor.start(
                AttemptRequest(
                    generation.generation_id,
                    "attempt-1",
                    "fixture",
                    argv,
                    workspace,
                    EnvelopeSpec(2, 100_000, 64 * 1024 * 1024, 16, 1024 * 1024, NetworkPolicy.DENY, 5, 1),
                    network_probe=network_probe,
                )
            ).result()

        solved = run(("python3", "/target-client.py", "tcp", "ping"))
        if solved.outcome is not ResourceOutcome.EXITED or solved.output != b"pong":
            return 1
        attempt_binding = CapabilityBinding(
            RUN_ID, "boot-qualification", generation.generation_id, "lane-1", "attempt-1", "fixture"
        )
        for kind, endpoint in denied_endpoints.items():
            with socket.create_connection((endpoint.host, endpoint.port), 1):
                pass
            denied = run(
                (
                    "python3",
                    "-c",
                    "import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),1)",
                    endpoint.host,
                    str(endpoint.port),
                ),
                NetworkProbeDeclaration(
                    kind,
                    hashlib.sha256(f"{endpoint.host}:{endpoint.port}".encode()).hexdigest(),
                ),
            )
            if denied.outcome is not ResourceOutcome.NETWORK:
                return 1
            broker.record_denial(attempt_binding, kind)
        raw = run(("python3", "-c", "import socket; socket.create_connection(('192.0.2.1',443),1)"))
        if raw.outcome is not ResourceOutcome.NETWORK:
            return 1
        executor.close_generation(generation.generation_id, GenerationDisposition.COMPLETE)
        executor.close()
        verify_receipt(write_receipt(STATE, RUN_ID))
        return 0
    finally:
        listener.close()
        for denied in deny_listeners:
            denied.close()
        target_thread.join(1)
        close_attempt_pool(pool)


if __name__ == "__main__":
    raise SystemExit(main())
