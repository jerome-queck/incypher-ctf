"""Exercise one catalogued semantic fixture through the production Tool handle."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import EnvelopeSpec, NetworkPolicy, RuntimeBinding
from solver.attempt_executor_pool import AttemptPool, close_attempt_pool, prepare_attempt_pool
from solver.attempt_executor_runtime import AttemptRuntime
from solver.capability import CapabilityAuthority, PeerIdentity
from solver.isolation import IMAGE_ID, IsolationReceipt, strict_preflight
from solver.isolation_receipt import write_receipt as write_isolation_receipt
from solver.redaction import Redactor
from solver.tool_control import AttemptToolRuntime, ToolComponent, ToolController, ToolInvocation
from solver.tool_control_receipt import verify_receipt, write_receipt
from solver.work_generation import GenerationDisposition, GenerationFence

STATE = Path("/state")
INVENTORY = Path("/opt/solver/tool-supply/inventory.json")
RUN_ID = "tool-handle-qualification"


def _binding(environ: Mapping[str, str]) -> RuntimeBinding:
    return RuntimeBinding(
        image_id=environ.get(IMAGE_ID, ""),
        image_manifest_digest=environ.get("INCYPHER_IMAGE_MANIFEST", ""),
        image_config_digest=environ.get("INCYPHER_IMAGE_CONFIG", ""),
        platform=environ.get("INCYPHER_IMAGE_PLATFORM", ""),
    )


def _component(document: Mapping[str, object], component_id: str) -> Mapping[str, object]:
    try:
        components = document["components"]
        component = next(item for item in components if item["component_id"] == component_id)  # type: ignore[union-attr]
    except (KeyError, StopIteration, TypeError) as error:
        raise ValueError(f"component is absent from the built-image catalogue: {component_id}") from error
    return component


def qualify(
    environ: Mapping[str, str],
    component_id: str,
    *,
    state: Path = STATE,
    inventory_path: Path = INVENTORY,
    preflight: Callable[..., IsolationReceipt] = strict_preflight,
    runtime: object | None = None,
) -> Path:
    """Return a verified Tool-handle receipt or refuse the qualification."""

    state = Path(state)
    run_root = state / "runs" / RUN_ID
    if run_root.exists():
        raise RuntimeError(f"qualification state already exists: {run_root}")
    binding = _binding(environ)
    catalogue = json.loads(inventory_path.read_text())
    component = _component(catalogue, component_id)
    pool: AttemptPool | None = None

    def prepare() -> None:
        nonlocal pool
        pool = prepare_attempt_pool(slots=1)

    try:
        admitted = preflight(environ, prepare_attempt_runtime=prepare if runtime is None else None)
        selected_runtime = runtime if runtime is not None else AttemptRuntime(pool)
        isolation_path = write_isolation_receipt(state, RUN_ID, admitted)

        def timestamp() -> str:
            return dt.datetime.now(dt.timezone.utc).isoformat()

        fence = GenerationFence(state, RUN_ID, Redactor({}), timestamp)
        generation = fence.acquire("fixture", "tool-handle-qualification")
        peer = PeerIdentity(os.getpid(), os.getuid(), os.getgid(), "qualification", "/qualification")
        authority = CapabilityAuthority(
            state=state,
            run_id=RUN_ID,
            boot_id="boot-qualification",
            redactor=Redactor({}),
            peer_identity=lambda _connection: peer,
            timestamp=timestamp,
        )
        controller = ToolController(
            state=state,
            run_id=RUN_ID,
            authority=authority,
            image_digest=binding.image_manifest_digest,
            components=(
                ToolComponent(
                    component_id,
                    str(component["entrypoint"]),
                    str(component["version"]),
                    ("resident",),
                ),
            ),
            timestamp=timestamp,
        )
        executor = AttemptExecutor(
            state=state,
            run_id=RUN_ID,
            isolation_receipt=isolation_path,
            binding=binding,
            generation_fence=fence,
            runtime=selected_runtime,  # type: ignore[arg-type]
            timestamp=timestamp,
        )
        workspace = state / "qualification-work"
        workspace.mkdir()
        fixture = component["fixture"]  # type: ignore[index]
        argv = (str(component["entrypoint"]), *(str(item) for item in fixture["argv"]))  # type: ignore[index]
        try:
            result = AttemptToolRuntime(
                controller=controller,
                executor=executor,
                run_id=RUN_ID,
                boot_id="boot-qualification",
                peer=peer,
            ).invoke(
                generation_id=generation.generation_id,
                attempt_id=generation.attempt_id,
                step_id="fixture:solve",
                workspace=workspace,
                envelope=EnvelopeSpec(
                    cpu_seconds=2.0,
                    cpu_quota_us=100_000,
                    memory_bytes=64 * 1024 * 1024,
                    pids=16,
                    filesystem_bytes=1024 * 1024,
                    network=NetworkPolicy.DENY,
                    wall_seconds=float(fixture["timeout_seconds"]),  # type: ignore[index]
                    cleanup_seconds=1.0,
                ),
                invocation=ToolInvocation(component_id, argv),
            )
            if hashlib.sha256(result.output).hexdigest() != fixture["expected_stdout_sha256"]:  # type: ignore[index]
                raise RuntimeError("catalogued fixture output did not match")
        finally:
            executor.close_generation(generation.generation_id, GenerationDisposition.COMPLETE)
            executor.close()
        return verify_receipt(write_receipt(state, RUN_ID))
    finally:
        close_attempt_pool(pool)


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        print("usage: python -m solver.tool_handle_probe COMPONENT_ID", file=sys.stderr)
        return 2
    try:
        receipt = qualify(os.environ, arguments[0])
    except Exception as error:
        print(f"Tool-handle qualification failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RUN_ID", "qualify"]
