"""Generation-scoped Tool views, handles, invocation, and receipts."""

import json

from pathlib import Path

import pytest

from solver.capability import CapabilityAuthority, CapabilityBinding, PeerIdentity
from solver.redaction import Redactor
from solver.attempt_executor import AttemptExecutor, EnvelopeSpec, NetworkPolicy, RuntimeBinding
from solver.event_store import EventStore
from solver.tool_control import (
    AttemptToolRuntime,
    ToolComponent,
    ToolController,
    ToolInvocation,
    attempt_components,
    resident_components,
)
from solver.tool_control_receipt import manifest_receipt, receipt_document, verify_receipt, write_receipt
from solver.work_generation import GenerationDisposition, GenerationFence
from test_attempt_executor import CancellableRuntime, IMAGE_ID, ImmediateRuntime, isolation_receipt, request


PEER = PeerIdentity(pid=101, uid=20_000, gid=20_000, started="123", cgroup="/attempt-1")


def test_locked_resident_catalogue_exposes_every_capability_with_bounded_policy() -> None:
    inventory = Path(__file__).resolve().parent.parent / "tool-supply" / "generated" / "inventory.json"

    components = resident_components(inventory)

    assert len(components) == 16
    assert {item.capability_id for item in components} >= {
        "archive.extract",
        "firmware.rootfs",
        "image.inspect",
        "document.pdf",
        "recognition.ocr",
        "recognition.barcode",
        "crypto.primitive",
        "math.symbolic",
        "solver.smt",
    }
    assert all(
        item.component_id == "/bin/dash"
        and item.max_arguments == 4
        and item.fixed_arguments[1] == item.capability_id
        and item.input_paths == 1
        and item.resource_limits
        for item in components
    )


def test_production_attempt_catalogue_includes_qualified_crypto_handles() -> None:
    inventory = Path(__file__).resolve().parent.parent / "tool-supply" / "generated" / "inventory.json"

    components = attempt_components(inventory)

    assert len(components) == 25
    assert {item.capability_id for item in components} >= {"crypto.cas", "crypto.lattice", "crypto.hash-crack"}


def test_production_runtime_dispatches_every_resident_capability_from_its_locked_policy(
    tmp_path: Path, monkeypatch
) -> None:
    inventory = Path(__file__).resolve().parent.parent / "tool-supply" / "generated" / "inventory.json"
    components = resident_components(inventory)
    controller = object.__new__(ToolController)
    controller._components = {item.capability_id: item for item in components}
    runtime = object.__new__(AttemptToolRuntime)
    runtime._controller = controller
    observed = []

    def invoke(**request):
        observed.append(request)
        return request

    monkeypatch.setattr(runtime, "invoke", invoke)
    held = tmp_path / "held"
    held.write_text("input")

    for component in components:
        runtime.invoke_resident(
            generation_id="generation-000001",
            attempt_id="attempt-1",
            step_id="step-1",
            workspace=tmp_path,
            capability_id=component.capability_id,
            input_path=held,
        )

    assert {item["invocation"].capability_id for item in observed} == {item.capability_id for item in components}
    for dispatched in observed:
        component = next(item for item in components if item.capability_id == dispatched["invocation"].capability_id)
        assert dispatched["invocation"].argv == (
            component.component_id,
            *component.fixed_arguments,
            "/work/held",
        )
        assert dispatched["invocation"].resource_request == component.resource_limits
        assert dispatched["envelope"].document() == {
            "cpu_seconds": dict(component.resource_limits)["cpu_seconds"],
            "cpu_quota_us": 100_000,
            "memory_bytes": dict(component.resource_limits)["memory_bytes"],
            "pids": dict(component.resource_limits)["pids"],
            "filesystem_bytes": dict(component.resource_limits)["filesystem_bytes"],
            "network": "deny",
            "wall_seconds": dict(component.resource_limits)["wall_seconds"],
            "cleanup_seconds": 5,
        }


def test_resident_policy_denies_flags_code_urls_and_paths_outside_work(tmp_path: Path) -> None:
    state, _generations, authority, binding = tool_authority(tmp_path)
    workspace = tmp_path / "work"
    workspace.mkdir()
    held = workspace / "input"
    held.write_text("held")
    component = ToolComponent(
        "network.http",
        "/bin/dash",
        "1.0.0",
        ("resident",),
        max_arguments=4,
        fixed_arguments=("/usr/local/bin/incypher-resident-network-data", "network.http"),
        input_paths=1,
    )
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(component,),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    _view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))
    called = False

    def execute(_invocation):
        nonlocal called
        called = True
        return 0, b"", {}

    unsafe = (
        ("/bin/dash", "-c", "open('/run/credential').read()", "/work/input"),
        ("/bin/dash", component.fixed_arguments[0], "network.http", "https://example.invalid"),
        ("/bin/dash", component.fixed_arguments[0], "network.http", "/etc/passwd"),
    )
    for argv in unsafe:
        with pytest.raises(PermissionError, match="tool invocation refused"):
            controller.invoke(
                object(),
                handle,
                ToolInvocation("network.http", argv),
                execute=execute,
                workspace=workspace,
                network=NetworkPolicy.DENY,
            )

    assert not called


def test_declared_component_executes_through_handle_with_semantic_receipt(tmp_path: Path) -> None:
    state = tmp_path / "state"
    generations = GenerationFence(state, "run-1", Redactor({}), lambda: "2026-09-12T00:00:00Z")
    generation = generations.acquire("challenge-1", "attempt-1")
    authority = CapabilityAuthority(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        redactor=Redactor({}),
        peer_identity=lambda _connection: PEER,
        token_bytes=lambda count: b"h" * count,
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    binding = CapabilityBinding(
        run_id="run-1",
        boot_id="boot-1",
        generation_id=generation.generation_id,
        lane_id="lane-1",
        attempt_id="attempt-1",
        step_id="step-1",
    )
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(
            ToolComponent(
                capability_id="recon.mime",
                component_id="file",
                version="5.44",
                profiles=("resident",),
            ),
        ),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )

    view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))
    result = controller.invoke(
        object(),
        handle,
        ToolInvocation("recon.mime", ("file", "-b", "sample")),
        execute=lambda invocation: (0, b"ASCII text\n", {"cpu_ms": 2, "output_bytes": 11}),
    )

    assert view.capability_ids == ("recon.mime",)
    assert result.output == b"ASCII text\n"
    assert result.receipt.component_id == "file"
    assert result.receipt.version == "5.44"
    assert result.receipt.image_digest == "sha256:" + "a" * 64
    assert len(result.receipt.view_digest) == 64
    assert len(result.receipt.arguments_digest) == 64
    assert len(result.receipt.input_digest) == 64
    assert len(result.receipt.output_digest) == 64
    assert result.receipt.resources == {"cpu_ms": 2, "output_bytes": 11}
    receipt_path = write_receipt(state, "run-1")
    assert verify_receipt(receipt_path) == receipt_path
    receipt = json.loads(receipt_path.read_bytes())
    assert receipt["receipt_type"] == "tool-handle"
    assert receipt["invocations"][0]["output_digest"] == result.receipt.output_digest
    assert manifest_receipt(receipt_path)["ref"] == "receipt:tool-handle"


def test_declared_component_traverses_the_strict_attempt_executor(tmp_path: Path) -> None:
    state, generations, authority, binding = tool_authority(tmp_path)
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "sample").write_text("hello")
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(
            image_id=IMAGE_ID,
            image_manifest_digest="sha256:" + "b" * 64,
            image_config_digest="sha256:" + "c" * 64,
            platform="linux/arm64",
        ),
        generation_fence=generations,
        runtime=ImmediateRuntime(),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest=IMAGE_ID,
        components=(ToolComponent("recon.mime", "file", "5.44", ("resident",)),),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    runtime = AttemptToolRuntime(
        controller=controller,
        executor=executor,
        run_id="run-1",
        boot_id="boot-1",
        peer=PEER,
    )
    assert runtime._enabled_profiles == ("resident", "tool-crypto")

    result = runtime.invoke(
        generation_id=binding.generation_id,
        attempt_id=binding.attempt_id,
        step_id=binding.step_id,
        workspace=workspace,
        envelope=EnvelopeSpec(
            1.0,
            20_000,
            32 * 1024 * 1024,
            8,
            8 * 1024 * 1024,
            NetworkPolicy.DENY,
            5.0,
            1.0,
        ),
        invocation=ToolInvocation("recon.mime", ("file", "-b", "sample")),
    )

    assert result.output == b"application/octet-stream\n"
    assert any(event.event_type == "attempt-envelope.recorded" for event in EventStore(state, "run-1").events())
    executor.close()


def test_view_excludes_disabled_components_and_denies_undeclared_or_raw_shell(tmp_path: Path) -> None:
    state, generations, authority, binding = tool_authority(tmp_path)
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(
            ToolComponent("recon.mime", "file", "5.44", ("resident",)),
            ToolComponent("web.discovery", "ffuf", "2.1", ("web",)),
        ),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))
    called = False

    def execute(_invocation):
        nonlocal called
        called = True
        return 0, b"", {}

    assert view.capability_ids == ("recon.mime",)
    for invocation in (
        ToolInvocation("web.discovery", ("ffuf", "-u", "https://example.invalid")),
        ToolInvocation("recon.mime", ("sh", "-c", "cat /run/control/token")),
    ):
        try:
            controller.invoke(object(), handle, invocation, execute=execute)
        except PermissionError as error:
            assert str(error) == "tool invocation refused"
        else:
            raise AssertionError("undeclared Tool authority was granted")
    assert not called
    receipt = json.loads(write_receipt(state, "run-1").read_bytes())
    assert [item["reason"] for item in receipt["denials"]] == [
        "undeclared-component",
        "raw-shell-denied",
    ]


def test_argument_and_resource_breaches_are_denied_before_execution(tmp_path: Path) -> None:
    state, _generations, authority, binding = tool_authority(tmp_path)
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(
            ToolComponent(
                "recon.mime",
                "file",
                "5.44",
                ("resident",),
                max_arguments=2,
                resource_limits=(("cpu_ms", 10),),
            ),
        ),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    _view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))
    called = False

    def execute(_invocation):
        nonlocal called
        called = True
        return 0, b"", {}

    for invocation in (
        ToolInvocation("recon.mime", ("file", "-b", "sample")),
        ToolInvocation("recon.mime", ("file", "sample"), resource_request=(("cpu_ms", 11),)),
    ):
        try:
            controller.invoke(object(), handle, invocation, execute=execute)
        except PermissionError:
            pass
        else:
            raise AssertionError("Tool bounds were widened")
    assert not called


def test_observed_output_breach_and_execution_failure_are_recorded_without_receipt(tmp_path: Path) -> None:
    state, _generations, authority, binding = tool_authority(tmp_path)
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(ToolComponent("recon.mime", "file", "5.44", ("resident",), max_output_bytes=4),),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    _view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))

    with pytest.raises(PermissionError, match="tool invocation refused"):
        controller.invoke(
            object(),
            handle,
            ToolInvocation("recon.mime", ("file", "sample")),
            execute=lambda _invocation: (0, b"too large", {"output_bytes": 9}),
        )
    with pytest.raises(RuntimeError, match="component failed"):
        controller.invoke(
            object(),
            handle,
            ToolInvocation("recon.mime", ("file", "sample")),
            execute=lambda _invocation: (_ for _ in ()).throw(RuntimeError("component failed")),
        )

    receipt = json.loads(write_receipt(state, "run-1").read_bytes())
    assert receipt["invocations"] == []
    assert [item["reason"] for item in receipt["denials"]] == ["resource-breach", "execution-failed"]


def test_cancellation_revokes_before_process_teardown(tmp_path: Path) -> None:
    state, _generations, authority, binding = tool_authority(tmp_path)
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(ToolComponent("recon.mime", "file", "5.44", ("resident",)),),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    _view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))
    revoked_before_teardown = False

    def teardown() -> None:
        nonlocal revoked_before_teardown
        with pytest.raises(PermissionError):
            authority.authorize(object(), handle)
        revoked_before_teardown = True

    controller.cancel(handle, teardown=teardown)

    assert revoked_before_teardown
    receipt = json.loads(write_receipt(state, "run-1").read_bytes())
    assert receipt["revocations"][0]["reason"] == "cancelled"


def test_attempt_handle_cancellation_revokes_registered_tool_generation(tmp_path: Path) -> None:
    state, generations, authority, binding = tool_authority(tmp_path)
    process_runtime = CancellableRuntime()
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(
            image_id=IMAGE_ID,
            image_manifest_digest="sha256:" + "b" * 64,
            image_config_digest="sha256:" + "c" * 64,
            platform="linux/arm64",
        ),
        generation_fence=generations,
        runtime=process_runtime,
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(ToolComponent("recon.mime", "file", "5.44", ("resident",)),),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    AttemptToolRuntime(
        controller=controller,
        executor=executor,
        run_id="run-1",
        boot_id="boot-1",
        peer=PEER,
    )
    _view, tool_handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))
    process_handle = executor.start(request(tmp_path, binding.generation_id))
    assert process_runtime.started.wait(1)

    process_handle.cancel()
    process_handle.result()

    with pytest.raises(PermissionError):
        authority.authorize(object(), tool_handle)
    executor.close()


def test_output_finishing_after_revocation_cannot_emit_a_receipt(tmp_path: Path) -> None:
    state, _generations, authority, binding = tool_authority(tmp_path)
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(ToolComponent("recon.mime", "file", "5.44", ("resident",)),),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    _view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))

    def completes_late(_invocation):
        controller.cancel(handle, teardown=lambda: None)
        return 0, b"late", {}

    with pytest.raises(PermissionError):
        controller.invoke(
            object(),
            handle,
            ToolInvocation("recon.mime", ("file", "sample")),
            execute=completes_late,
        )

    assert json.loads(write_receipt(state, "run-1").read_bytes())["invocations"] == []


def test_receipt_write_failure_records_denial_and_returns_no_result(tmp_path: Path, monkeypatch) -> None:
    state, _generations, authority, binding = tool_authority(tmp_path)
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(ToolComponent("recon.mime", "file", "5.44", ("resident",)),),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    _view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))

    def receipt_failed(_state, _run_id):
        raise OSError("disk unavailable")

    monkeypatch.setattr("solver.tool_control_receipt.write_receipt", receipt_failed)
    with pytest.raises(OSError, match="disk unavailable"):
        controller.invoke(
            object(),
            handle,
            ToolInvocation("recon.mime", ("file", "sample")),
            execute=lambda _invocation: (0, b"text", {}),
        )

    receipt = receipt_document("run-1", EventStore(state, "run-1").events())
    assert receipt["invocations"] == []
    assert receipt["denials"][-1]["reason"] == "receipt-failed"


def test_generation_close_revokes_before_teardown_and_rejects_late_receipt(tmp_path: Path) -> None:
    state, generations, authority, binding = tool_authority(tmp_path)
    controller = ToolController(
        state=state,
        run_id="run-1",
        authority=authority,
        image_digest="sha256:" + "a" * 64,
        components=(ToolComponent("recon.mime", "file", "5.44", ("resident",)),),
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    _view, handle = controller.issue_view(binding, PEER, enabled_profiles=("resident",))
    teardown_observed_revoked = False

    def teardown() -> None:
        nonlocal teardown_observed_revoked
        try:
            authority.authorize(object(), handle)
        except PermissionError:
            teardown_observed_revoked = True

    controller.close_generation(binding.generation_id, teardown=teardown)
    generations.close(binding.generation_id, GenerationDisposition.COMPLETE)

    assert teardown_observed_revoked
    try:
        controller.invoke(
            object(),
            handle,
            ToolInvocation("recon.mime", ("file", "sample")),
            execute=lambda _invocation: (0, b"late", {}),
        )
    except PermissionError:
        pass
    else:
        raise AssertionError("late Tool receipt was admitted")
    receipt = json.loads(write_receipt(state, "run-1").read_bytes())
    assert receipt["revocations"][0]["reason"] == "generation-closed"


def tool_authority(tmp_path: Path):
    state = tmp_path / "state"
    generations = GenerationFence(state, "run-1", Redactor({}), lambda: "2026-09-12T00:00:00Z")
    generation = generations.acquire("challenge-1", "attempt-1")
    authority = CapabilityAuthority(
        state=state,
        run_id="run-1",
        boot_id="boot-1",
        redactor=Redactor({}),
        peer_identity=lambda _connection: PEER,
        token_bytes=lambda count: b"h" * count,
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    binding = CapabilityBinding("run-1", "boot-1", generation.generation_id, "lane-1", "attempt-1", "step-1")
    return state, generations, authority, binding
