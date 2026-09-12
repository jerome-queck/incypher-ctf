"""Built-image proof that hostile workers use Research mediation and lose raw egress."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path

from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import (
    AttemptRequest,
    EnvelopeSpec,
    NetworkPolicy,
    NetworkProbeDeclaration,
    ResourceOutcome,
    RuntimeBinding,
)
from solver.attempt_executor_pool import close_attempt_pool, prepare_attempt_pool
from solver.attempt_executor_runtime import AttemptRuntime
from solver.capability import CapabilityBinding
from solver.isolation import IMAGE_ID, strict_preflight
from solver.isolation_receipt import write_receipt as write_isolation_receipt
from solver.redaction import Redactor
from solver.research_broker import ResearchBrokerRuntime
from solver.research_broker_contracts import ResearchLimits, ResearchTransportResult
from solver.research_broker_receipt import verify_receipt, write_receipt
from solver.work_generation import GenerationFence

STATE = Path("/state")
RUN_ID = "research-broker-qualification"


def main() -> int:
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
        generation = fence.acquire("fixture-research", "attempt-1")
        broker = ResearchBrokerRuntime(
            state=STATE,
            run_id=RUN_ID,
            boot_id="boot-qualification",
            limits=ResearchLimits(1024, 2, 2, 60),
            timestamp=timestamp,
            resolve=lambda _host: ("8.8.8.8",),
            transport=lambda *_args: ResearchTransportResult(
                status=200, headers={"content-type": "text/plain"}, body=b"public-fixture"
            ),
        )
        executor = AttemptExecutor(
            state=STATE,
            run_id=RUN_ID,
            isolation_receipt=isolation,
            binding=binding,
            generation_fence=fence,
            runtime=AttemptRuntime(pool, research_broker=broker),  # type: ignore[arg-type]
            timestamp=timestamp,
        )
        workspace = STATE / "research-probe-work"
        workspace.mkdir()
        envelope = EnvelopeSpec(2, 100_000, 64 * 1024 * 1024, 16, 1024 * 1024, NetworkPolicy.DENY, 5, 1)
        allowed = executor.start(
            AttemptRequest(
                generation.generation_id,
                "attempt-1",
                "fixture-public",
                ("python3", "/research-client.py", "https://public.example/fact"),
                workspace,
                envelope,
            )
        ).result()
        if allowed.outcome is not ResourceOutcome.EXITED or allowed.output != b"public-fixture":
            return 1
        attempted = hashlib.sha256(b"1.1.1.1:443").hexdigest()
        denied = executor.start(
            AttemptRequest(
                generation.generation_id,
                "attempt-1",
                "fixture-raw",
                ("python3", "-c", "import socket; socket.create_connection(('1.1.1.1',443),1)"),
                workspace,
                envelope,
                network_probe=NetworkProbeDeclaration("research", attempted),
            )
        ).result()
        if denied.outcome is not ResourceOutcome.NETWORK:
            return 1
        broker.record_raw_egress_denial(
            CapabilityBinding(
                RUN_ID,
                "boot-qualification",
                generation.generation_id,
                "lane-1",
                "attempt-1",
                "fixture-raw",
            ),
            attempted,
        )
        executor.close()
        verify_receipt(write_receipt(STATE, RUN_ID))
        return 0
    finally:
        close_attempt_pool(pool)


if __name__ == "__main__":
    raise SystemExit(main())
