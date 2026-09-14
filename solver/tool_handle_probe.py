"""Exercise one catalogued component through production Tool handles."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import socket
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from solver.attempt_executor import AttemptExecutor
from solver.attempt_executor_contracts import EnvelopeSpec, NetworkPolicy, RuntimeBinding
from solver.attempt_executor_pool import AttemptPool, close_attempt_pool, prepare_attempt_pool
from solver.attempt_executor_runtime import AttemptRuntime
from solver.capability import CapabilityAuthority, PeerIdentity
from solver.capability import CapabilityBinding
from solver.isolation import IMAGE_ID, IsolationReceipt, strict_preflight
from solver.isolation_receipt import write_receipt as write_isolation_receipt
from solver.redaction import Redactor
from solver.resident_handle_receipt import qualification_target_limits
from solver.target_broker import TargetBrokerRuntime
from solver.target_broker_browser import close_browser_launcher, prepare_browser_launcher
from solver.target_broker_contracts import TargetCandidateBinding, TargetEndpoint, TargetProtocol
from solver.research_broker import ResearchBrokerRuntime
from solver.research_broker_contracts import ResearchKind, ResearchLimits, ResearchSource, ResearchTransportResult
from solver.tool_control import AttemptToolRuntime, ToolController, ToolInvocation, profile_components
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
        return next(item for item in components if item["component_id"] == component_id)  # type: ignore[union-attr]
    except (KeyError, StopIteration, TypeError) as error:
        raise ValueError(f"component is absent from the built-image catalogue: {component_id}") from error


def _copy_input(source: Path, workspace: Path, capability_id: str) -> tuple[Path, str]:
    root = workspace / "inputs" / capability_id
    root.mkdir(parents=True)
    destination = root / source.name
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, destination)
    elif source.is_file() and not source.is_symlink():
        shutil.copy2(source, destination)
    else:
        raise ValueError("Tool-handle fixture input is unsafe")
    return destination, f"/work/{destination.relative_to(workspace).as_posix()}"


def _target(protocol: TargetProtocol) -> tuple[socket.socket, threading.Thread]:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)

    def serve() -> None:
        while True:
            try:
                connection, _address = listener.accept()
            except OSError:
                return
            with connection:
                request = connection.recv(4096)
                if protocol is TargetProtocol.HTTP:
                    path = request.split(b" ", 2)[1] if request.startswith((b"GET ", b"POST ")) else b""
                    if path in {b"/resident-fixture", b"/hidden-route"}:
                        body, status = b"resident fixture payload\n", b"200 OK"
                    elif path == b"/browser-fixture":
                        body = (
                            b"<!doctype html><html><body><script>localStorage.setItem('browser-state','held')"
                            b"</script><div id=held>browser-state=held</div></body></html>"
                        )
                        status = b"200 OK"
                    else:
                        body, status = b"missing\n", b"404 Not Found"
                    connection.sendall(
                        b"HTTP/1.1 "
                        + status
                        + b"\r\nContent-Type: text/html\r\nContent-Length: "
                        + str(len(body)).encode()
                        + b"\r\nConnection: close\r\n\r\n"
                        + body
                    )
                elif protocol is TargetProtocol.TCP and request:
                    connection.sendall(b"pong" if request == b"ping" else request)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener, thread


def _fixture_protocol(capability_id: str) -> TargetProtocol:
    return (
        TargetProtocol.HTTP
        if capability_id.startswith("web.") or capability_id == "network.http"
        else TargetProtocol.TCP
    )


class _QualificationTargets:
    """Route each network fixture generation to its declared protocol broker."""

    def __init__(self, brokers: Mapping[str, TargetBrokerRuntime]) -> None:
        self._brokers = dict(brokers)
        first = next(iter(self._brokers.values()))
        self._default = first
        self.boot_id = first.boot_id
        self.candidate = first.candidate
        self._generation_brokers: dict[str, TargetBrokerRuntime] = {}
        self._handle_brokers: dict[str, TargetBrokerRuntime] = {}

    def available(self, generation_id: str) -> bool:
        return any(broker.available(generation_id) for broker in self._brokers.values())

    def prepare_attempt(self, binding: CapabilityBinding) -> None:
        broker = self._brokers.get(binding.step_id, self._default)
        broker.prepare_attempt(binding)
        self._generation_brokers[binding.generation_id] = broker

    def claim(self, connection: socket.socket, generation_id: str) -> str:
        broker = self._generation_brokers[generation_id]
        handle = broker.claim(connection, generation_id)
        self._handle_brokers[handle] = broker
        return handle

    def exchange(self, connection: socket.socket, handle: str, request: Mapping[str, object]):
        return self._handle_brokers[handle].exchange(connection, handle, request)

    def http_session(self, connection: socket.socket, handle: str, request: Mapping[str, object]):
        return self._handle_brokers[handle].http_session(connection, handle, request)

    def http_fuzz(self, connection: socket.socket, handle: str, request: Mapping[str, object]):
        return self._handle_brokers[handle].http_fuzz(connection, handle, request)

    def tcp_session(self, connection: socket.socket, handle: str, request: Mapping[str, object]):
        return self._handle_brokers[handle].tcp_session(connection, handle, request)

    def browser(self, connection: socket.socket, handle: str, request: Mapping[str, object]):
        return self._handle_brokers[handle].browser(connection, handle, request)

    def revoke(self, handle: str) -> None:
        broker = self._handle_brokers.pop(handle, None)
        if broker is not None:
            broker.revoke(handle)

    def revoke_generation(self, generation_id: str) -> None:
        broker = self._generation_brokers.pop(generation_id, None)
        if broker is not None:
            broker.revoke_generation(generation_id)
        for handle, owner in tuple(self._handle_brokers.items()):
            if owner is broker:
                self._handle_brokers.pop(handle, None)


def qualify(
    environ: Mapping[str, str],
    component_id: str,
    *,
    state: Path = STATE,
    inventory_path: Path = INVENTORY,
    preflight: Callable[..., IsolationReceipt] = strict_preflight,
    runtime: object | None = None,
) -> Path:
    """Return a verified receipt after every capability-specific assertion passes."""

    state = Path(state)
    run_root = state / "runs" / RUN_ID
    if run_root.exists():
        raise RuntimeError(f"qualification state already exists: {run_root}")
    binding = _binding(environ)
    catalogue = json.loads(inventory_path.read_text())
    component = _component(catalogue, component_id)
    capability_ids = tuple(str(item) for item in component["capability_ids"])  # type: ignore[index]
    pool: AttemptPool | None = None
    browser_prepared = False
    browser_launcher = None

    def prepare() -> None:
        nonlocal browser_launcher, browser_prepared, pool
        pool = prepare_attempt_pool(slots=1)
        if "web.browser" in capability_ids:
            browser_launcher = prepare_browser_launcher()
            browser_prepared = True

    try:
        admitted = preflight(environ, prepare_attempt_runtime=prepare if runtime is None else None)
        isolation_path = write_isolation_receipt(state, RUN_ID, admitted)

        def timestamp() -> str:
            return dt.datetime.now(dt.timezone.utc).isoformat()

        fence = GenerationFence(state, RUN_ID, Redactor({}), timestamp)
        peer = PeerIdentity(os.getpid(), os.getuid(), os.getgid(), "qualification", "/qualification")
        authority = CapabilityAuthority(
            state=state,
            run_id=RUN_ID,
            boot_id="boot-qualification",
            redactor=Redactor({}),
            peer_identity=lambda _connection: peer,
            timestamp=timestamp,
        )
        profile_id = str(component["profiles"][0])  # type: ignore[index]
        available = {
            item.capability_id: item for item in profile_components(inventory_path, profile_id, require_complete=False)
        }
        controller = ToolController(
            state=state,
            run_id=RUN_ID,
            authority=authority,
            image_digest=binding.image_manifest_digest,
            components=tuple(available[capability_id] for capability_id in capability_ids),
            timestamp=timestamp,
        )
        workspace = state / "qualification-work"
        workspace.mkdir()
        fixture = component["fixture"]  # type: ignore[index]
        policies = component["capability_policies"]  # type: ignore[index]
        capability_argv = fixture["capability_argv"]  # type: ignore[index]
        expected_outputs = fixture["capability_stdout_sha256"]  # type: ignore[index]
        expected_facts = fixture["capability_expected_facts"]  # type: ignore[index]
        if any(
            set(value) != set(capability_ids) for value in (policies, capability_argv, expected_outputs, expected_facts)
        ):
            raise ValueError("Tool-handle fixture lacks one assertion per capability")

        listeners: list[socket.socket] = []
        target_threads: list[threading.Thread] = []
        brokers: dict[str, TargetBrokerRuntime] = {}
        research_broker = None
        if runtime is None:
            for capability_id in capability_ids:
                if policies[capability_id]["network"] != "target-broker":
                    continue
                protocol = _fixture_protocol(capability_id)
                listener, target_thread = _target(protocol)
                listeners.append(listener)
                target_threads.append(target_thread)
                brokers[f"fixture:{capability_id}"] = TargetBrokerRuntime(
                    state=state,
                    run_id=RUN_ID,
                    boot_id="boot-qualification",
                    challenge_id=f"fixture:{capability_id}",
                    candidate=TargetCandidateBinding(
                        binding.image_id,
                        binding.image_manifest_digest,
                        binding.image_config_digest,
                        binding.platform,
                        admitted.profile_digest,
                    ),
                    endpoint=TargetEndpoint(protocol, "127.0.0.1", listener.getsockname()[1]),
                    limits=qualification_target_limits(capability_id),
                    timestamp=timestamp,
                    request_namespace=capability_id,
                    browser_launcher=browser_launcher,
                )
            if any(policies[capability_id]["network"] == "research-broker" for capability_id in capability_ids):
                research_broker = ResearchBrokerRuntime(
                    state=state,
                    run_id=RUN_ID,
                    boot_id="boot-qualification",
                    limits=ResearchLimits(1024 * 1024, 10, 2, 0, max_requests=16),
                    timestamp=timestamp,
                    resolve=lambda _host: ("8.8.8.8",),
                    transport=lambda *_args: ResearchTransportResult(
                        status=200,
                        headers={"content-type": "application/json"},
                        body=b'{"controlled_live":true}',
                        elapsed_ms=1,
                    ),
                    sources={
                        "github": ResearchSource(
                            ResearchKind.IDENTITY,
                            "https://example.test/{subject}",
                            "fixture",
                            "fixture",
                            terms_decision="allow",
                            robots_decision="not-applicable",
                        ),
                        "gravatar": ResearchSource(
                            ResearchKind.EMAIL,
                            "https://example.test/{subject}",
                            "fixture",
                            "fixture",
                            terms_decision="allow",
                            robots_decision="not-applicable",
                        ),
                        "rdap": ResearchSource(
                            ResearchKind.DOMAIN,
                            "https://example.test/{subject}",
                            "fixture",
                            "fixture",
                            terms_decision="allow",
                            robots_decision="not-applicable",
                        ),
                        "nominatim": ResearchSource(
                            ResearchKind.GEO,
                            "https://example.test/{subject}",
                            "fixture",
                            "fixture",
                            terms_decision="allow",
                            robots_decision="not-applicable",
                        ),
                    },
                )
        selected_runtime = (
            runtime
            if runtime is not None
            else AttemptRuntime(
                pool,
                target_broker=_QualificationTargets(brokers) if brokers else None,
                research_broker=research_broker,
            )
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
        if brokers:
            executor.add_generation_revocation(selected_runtime.target_broker.revoke_generation)  # type: ignore[union-attr]
        if research_broker is not None:
            executor.add_generation_revocation(research_broker.revoke_generation)
        try:
            for capability_id in capability_ids:
                policy = policies[capability_id]
                supplied_argv = capability_argv[capability_id]
                if not isinstance(supplied_argv, list) or len(supplied_argv) != 1:
                    raise ValueError("Tool-handle capability fixture must name one input")
                _input, virtual_input = _copy_input(Path(str(supplied_argv[0])), workspace, capability_id)
                generation = fence.acquire(f"fixture:{capability_id}", f"qualification:{capability_id}")
                try:
                    argv = tuple(virtual_input if item == "{input}" else str(item) for item in policy["argv"])
                    result = AttemptToolRuntime(
                        controller=controller,
                        executor=executor,
                        run_id=RUN_ID,
                        boot_id="boot-qualification",
                        peer=peer,
                        enabled_profiles=(profile_id,),
                    ).invoke(
                        generation_id=generation.generation_id,
                        attempt_id=generation.attempt_id,
                        step_id=f"fixture:{capability_id}",
                        workspace=workspace,
                        envelope=EnvelopeSpec(
                            cpu_seconds=float(policy["cpu_seconds"]),
                            cpu_quota_us=100_000,
                            memory_bytes=int(policy["memory_bytes"]),
                            pids=int(policy["pids"]),
                            filesystem_bytes=int(policy["filesystem_bytes"]),
                            network=NetworkPolicy.DENY,
                            wall_seconds=float(policy["wall_seconds"]),
                            cleanup_seconds=1.0,
                        ),
                        invocation=ToolInvocation(
                            capability_id,
                            argv,
                            resource_request=available[capability_id].resource_limits,
                        ),
                    )
                    if any(str(fact).encode() not in result.output for fact in expected_facts[capability_id]):
                        raise RuntimeError(f"catalogued fixture fact did not match: {capability_id}")
                    observed_digest = hashlib.sha256(result.output).hexdigest()
                    if observed_digest != expected_outputs[capability_id]:
                        raise RuntimeError(f"catalogued fixture output did not match: {capability_id}")
                finally:
                    executor.close_generation(generation.generation_id, GenerationDisposition.COMPLETE)
        finally:
            executor.close()
            for listener in listeners:
                listener.close()
            for target_thread in target_threads:
                target_thread.join(1)
        return verify_receipt(write_receipt(state, RUN_ID))
    finally:
        if browser_prepared:
            close_browser_launcher()
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
