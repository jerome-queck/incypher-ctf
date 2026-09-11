"""Built-image semantic qualification for the production Attempt Resource envelope."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import (
    AttemptRequest,
    EnvelopeSpec,
    NetworkPolicy,
    ResourceOutcome,
    RuntimeBinding,
)
from solver.attempt_executor_pool import AttemptPool, close_attempt_pool, prepare_attempt_pool
from solver.attempt_executor_runtime import AttemptRuntime
from solver.attempt_resource_receipt import verify_receipt
from solver.attempt_process_lifecycle_receipt import verify_receipt as verify_process_receipt
from solver.isolation import IMAGE_ID, strict_preflight
from solver.isolation_receipt import write_receipt as write_isolation_receipt
from solver.redaction import Redactor
from solver.work_generation import GenerationDisposition, GenerationFence


STATE = Path("/state")
RUN_ID = "attempt-resource-qualification"
DENY_PROBE_SECRET = "qualification-secret"


def _binding(environ) -> RuntimeBinding:
    return RuntimeBinding(
        image_id=environ.get(IMAGE_ID, ""),
        image_manifest_digest=environ.get("INCYPHER_IMAGE_MANIFEST", ""),
        image_config_digest=environ.get("INCYPHER_IMAGE_CONFIG", ""),
        platform=environ.get("INCYPHER_IMAGE_PLATFORM", ""),
    )


def _spec(
    *,
    cpu: float = 2.0,
    memory: int = 64 * 1024 * 1024,
    pids: int = 16,
    filesystem: int = 1024 * 1024,
    wall: float = 3.0,
) -> EnvelopeSpec:
    return EnvelopeSpec(
        cpu_seconds=cpu,
        cpu_quota_us=100_000,
        memory_bytes=memory,
        pids=pids,
        filesystem_bytes=filesystem,
        network=NetworkPolicy.DENY,
        wall_seconds=wall,
        cleanup_seconds=1.0,
    )


def _fixtures() -> tuple[tuple[str, tuple[str, ...], EnvelopeSpec, ResourceOutcome], ...]:
    python = "/usr/bin/python3"
    deny_probe = """\
import os
from pathlib import Path
assert os.environ.get('INCYPHER_DENY_PROBE_SECRET') is None
assert not Path('/state').exists()
assert not Path('/run/incypher-attempts/slot-1/work').exists()
try:
    Path('/outside').write_text('escape')
except OSError:
    pass
else:
    raise AssertionError('wrote outside workspace')
sockets = []
for descriptor in Path('/proc/self/fd').iterdir():
    try:
        sockets.append('socket:' in os.readlink(descriptor))
    except OSError:
        pass
assert not any(sockets)
print('attempt-deny-ok')
"""
    process_tree = """\
import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.fork() == 0:
    if os.fork() == 0:
        while True:
            time.sleep(1)
    while True:
        time.sleep(1)
print('process-tree-ready', flush=True)
while True:
    time.sleep(1)
"""
    return (
        ("normal", (python, "-c", deny_probe), _spec(), ResourceOutcome.EXITED),
        ("cpu", (python, "-c", "while True: pass"), _spec(cpu=0.15), ResourceOutcome.CPU),
        (
            "memory",
            (python, "-c", "x=bytearray(256*1024*1024); print(len(x))"),
            _spec(memory=48 * 1024 * 1024),
            ResourceOutcome.MEMORY,
        ),
        (
            "pids",
            ("/bin/sh", "-c", "for i in 1 2 3 4 5 6 7 8; do sleep 2 & done; wait"),
            _spec(pids=4),
            ResourceOutcome.PIDS,
        ),
        (
            "filesystem",
            (python, "-c", "open('fill','wb').write(b'x'*(2*1024*1024))"),
            _spec(filesystem=128 * 1024),
            ResourceOutcome.FILESYSTEM,
        ),
        (
            "network",
            (
                python,
                "-c",
                "import socket,time; s=socket.socket(); print(s.connect_ex(('1.1.1.1',443))); time.sleep(5)",
            ),
            _spec(),
            ResourceOutcome.NETWORK,
        ),
        ("deadline", ("/bin/sleep", "5"), _spec(wall=0.2), ResourceOutcome.DEADLINE),
        ("process-tree", (python, "-c", process_tree), _spec(wall=0.2), ResourceOutcome.DEADLINE),
    )


def qualify(environ, *, state: Path = STATE) -> Path:
    state = Path(state)
    run_root = state / "runs" / RUN_ID
    if run_root.exists():
        raise RuntimeError(f"qualification state already exists: {run_root}")
    if environ.get("INCYPHER_DENY_PROBE_SECRET") != DENY_PROBE_SECRET:
        raise RuntimeError("deny-probe secret was not injected into the trusted controller")
    pool: AttemptPool | None = None

    def prepare() -> None:
        nonlocal pool
        pool = prepare_attempt_pool(slots=2)

    try:
        admitted = strict_preflight(environ, prepare_attempt_runtime=prepare)
        assert pool is not None
        isolation_path = write_isolation_receipt(state, RUN_ID, admitted)

        def timestamp() -> str:
            return dt.datetime.now(dt.timezone.utc).isoformat()

        fence = GenerationFence(state, RUN_ID, Redactor({}), timestamp=timestamp)
        generation = fence.acquire("qualification", "attempt-resource-qualification")
        executor = AttemptExecutor(
            state=state,
            run_id=RUN_ID,
            isolation_receipt=isolation_path,
            binding=_binding(environ),
            generation_fence=fence,
            runtime=AttemptRuntime(pool),
            timestamp=timestamp,
        )
        workspace = state / "qualification-work"
        workspace.mkdir()
        try:
            for name, argv, envelope, expected in _fixtures():
                for child in workspace.iterdir():
                    if child.is_dir() and not child.is_symlink():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                result = executor.start(
                    AttemptRequest(
                        generation_id=generation.generation_id,
                        attempt_id=generation.attempt_id,
                        step_id=f"fixture:{name}",
                        argv=argv,
                        workspace=workspace,
                        envelope=envelope,
                    )
                ).result()
                if result.outcome is not expected or not result.cleanup_complete:
                    raise RuntimeError(
                        f"{name} fixture returned {result.outcome.value}, "
                        f"cleanup={result.cleanup_complete}, observed={dict(result.observed)!r}"
                    )
                if name == "normal" and result.output != b"attempt-deny-ok\n":
                    raise RuntimeError(f"deny probe did not attest the child boundary: {result.output!r}")
        finally:
            executor.close_generation(generation.generation_id, GenerationDisposition.COMPLETE)
            executor.close()
        receipt = run_root / "canonical" / "attempt-resource-envelope.receipt.json"
        verify_receipt(receipt, require_qualified=True)
        verify_process_receipt(
            run_root / "canonical" / "attempt-process-lifecycle.receipt.json",
            require_qualified=True,
        )
        return receipt
    finally:
        close_attempt_pool(pool)


def main() -> int:
    try:
        receipt = qualify(os.environ)
        document = json.loads(receipt.read_bytes())
    except Exception as error:
        print(f"Attempt Resource qualification failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(document, sort_keys=True, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RUN_ID", "qualify"]
