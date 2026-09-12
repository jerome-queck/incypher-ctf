"""Generation-scoped Tool views, opaque handles, and measured invocations."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from solver.capability import CapabilityAuthority, CapabilityBinding, PeerIdentity
from solver.attempt_executor import AttemptExecutor, AttemptRequest, EnvelopeSpec, ResourceOutcome
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.redaction import Redactor
from solver.tool_control_contracts import ToolControlRecorded, ToolRecord


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


@dataclass(frozen=True)
class ToolComponent:
    capability_id: str
    component_id: str
    version: str
    profiles: tuple[str, ...]
    max_arguments: int = 32
    max_input_bytes: int = 64 * 1024
    max_output_bytes: int = 64 * 1024
    resource_limits: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ToolView:
    capability_ids: tuple[str, ...]
    digest: str


@dataclass(frozen=True)
class ToolInvocation:
    capability_id: str
    argv: tuple[str, ...]
    input: bytes = b""
    resource_request: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ToolInvocationReceipt:
    component_id: str
    version: str
    image_digest: str
    view_digest: str
    arguments_digest: str
    input_digest: str
    output_digest: str
    resources: Mapping[str, int]


@dataclass(frozen=True)
class ToolResult:
    exit_code: int
    output: bytes
    receipt: ToolInvocationReceipt


class ToolController:
    """Issue one filtered Tool view and enforce it at invocation."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        authority: CapabilityAuthority,
        image_digest: str,
        components: Sequence[ToolComponent],
        timestamp: Callable[[], str],
    ) -> None:
        self._run_id = run_id
        self._state = Path(state)
        self._authority = authority
        self._image_digest = image_digest
        self._store = EventStore(state, run_id=run_id, redactor=Redactor({}))
        self._timestamp = timestamp
        self._serial = sum(event.event_type == "tool-control.recorded" for event in self._store.events())
        self._components = {component.capability_id: component for component in components}
        self._views: dict[str, ToolView] = {}
        self._handles: dict[str, str] = {}

    def issue_view(
        self,
        binding: CapabilityBinding,
        peer: PeerIdentity,
        *,
        enabled_profiles: Sequence[str],
    ) -> tuple[ToolView, str]:
        enabled = set(enabled_profiles)
        capabilities = tuple(
            sorted(
                component.capability_id
                for component in self._components.values()
                if enabled.intersection(component.profiles)
            )
        )
        document = {"capability_ids": capabilities, "image_digest": self._image_digest}
        view = ToolView(capabilities, _digest(document))
        handle = self._authority.issue(binding, f"tool.view:{view.digest}", peer)
        self._views[view.digest] = view
        self._handles[handle] = binding.generation_id
        return view, handle

    def close_generation(self, generation_id: str, *, teardown: Callable[[], None]) -> None:
        """Revoke all Tool authority before the owning process tree is removed."""

        self.revoke_generation(generation_id)
        teardown()

    def revoke_generation(self, generation_id: str) -> None:
        """Remove every Tool handle owned by one Work generation."""

        for handle, owned_generation in tuple(self._handles.items()):
            if owned_generation == generation_id:
                self._authority.revoke(handle, "generation-closed")

    def cancel(self, handle: str, *, teardown: Callable[[], None]) -> None:
        """Remove one handle's authority before cancelling its owned process."""

        if handle not in self._handles:
            raise PermissionError("tool invocation refused")
        self._authority.revoke(handle, "cancelled")
        teardown()

    def invoke(
        self,
        connection: object,
        handle: str,
        invocation: ToolInvocation,
        *,
        execute: Callable[[ToolInvocation], tuple[int, bytes, Mapping[str, int]]],
    ) -> ToolResult:
        grant = self._authority.authorize(connection, handle)
        prefix = "tool.view:"
        view = self._views.get(grant.scope.removeprefix(prefix)) if grant.scope.startswith(prefix) else None
        component = self._components.get(invocation.capability_id)
        reason = ""
        if view is None or invocation.capability_id not in view.capability_ids or component is None:
            reason = "undeclared-component"
        elif not invocation.argv or invocation.argv[0] != component.component_id:
            reason = "raw-shell-denied"
        elif len(invocation.argv) > component.max_arguments or len(invocation.input) > component.max_input_bytes:
            reason = "argument-breach"
        elif not self._resources_allowed(component, invocation):
            reason = "resource-breach"
        if reason:
            self._record(grant.binding, invocation, component, view, ToolRecord.DENIED, reason=reason)
            raise PermissionError("tool invocation refused")
        assert component is not None and view is not None
        invocation_id = self._next_invocation_id()
        self._record(
            grant.binding,
            invocation,
            component,
            view,
            ToolRecord.RESERVED,
            invocation_id=invocation_id,
        )
        try:
            exit_code, output, resources = execute(invocation)
        except Exception:
            self._record(
                grant.binding,
                invocation,
                component,
                view,
                ToolRecord.DENIED,
                invocation_id=invocation_id,
                reason="execution-failed",
            )
            raise
        self._authority.authorize(connection, handle)
        limits = dict(component.resource_limits)
        if len(output) > component.max_output_bytes or any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
            or (name in limits and value > limits[name])
            for name, value in resources.items()
        ):
            self._record(
                grant.binding,
                invocation,
                component,
                view,
                ToolRecord.DENIED,
                invocation_id=invocation_id,
                reason="resource-breach",
            )
            raise PermissionError("tool invocation refused")
        try:
            receipt = ToolInvocationReceipt(
                component_id=component.component_id,
                version=component.version,
                image_digest=self._image_digest,
                view_digest=view.digest,
                arguments_digest=_digest(invocation.argv),
                input_digest=hashlib.sha256(invocation.input).hexdigest(),
                output_digest=hashlib.sha256(output).hexdigest(),
                resources=dict(resources),
            )
            self._record(
                grant.binding,
                invocation,
                component,
                view,
                ToolRecord.COMPLETED,
                invocation_id=invocation_id,
                exit_code=exit_code,
                resources=resources,
                body=output,
            )
            from solver.tool_control_receipt import write_receipt

            write_receipt(self._state, self._run_id)
        except Exception:
            self._record(
                grant.binding,
                invocation,
                component,
                view,
                ToolRecord.DENIED,
                invocation_id=invocation_id,
                reason="receipt-failed",
            )
            raise
        return ToolResult(exit_code, output, receipt)

    @staticmethod
    def _resources_allowed(component: ToolComponent, invocation: ToolInvocation) -> bool:
        limits = dict(component.resource_limits)
        requested = dict(invocation.resource_request)
        return len(requested) == len(invocation.resource_request) and all(
            isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limits.get(name, -1)
            for name, value in requested.items()
        )

    def _next_invocation_id(self) -> str:
        self._serial += 1
        return f"tool:{self._serial:06d}"

    def _record(
        self,
        binding: CapabilityBinding,
        invocation: ToolInvocation,
        component: ToolComponent | None,
        view: ToolView | None,
        record: ToolRecord,
        *,
        invocation_id: str = "",
        reason: str = "",
        exit_code: int = 0,
        resources: Mapping[str, int] | None = None,
        body: bytes = b"",
    ) -> None:
        if not invocation_id:
            invocation_id = self._next_invocation_id()
        self._store.append(
            ToolControlRecorded(
                event_id=f"{invocation_id}:{record.value}",
                invocation_id=invocation_id,
                record=record,
                binding=binding.document(),
                capability_id=invocation.capability_id,
                component_id=component.component_id if component else "unknown",
                version=component.version if component else "unknown",
                image_digest=self._image_digest,
                view_digest=view.digest if view else "0" * 64,
                arguments_digest=_digest(invocation.argv),
                input_digest=hashlib.sha256(invocation.input).hexdigest(),
                resources=dict(resources if resources is not None else invocation.resource_request),
                exit_code=exit_code,
                reason=reason,
                ts=self._timestamp(),
            ),
            body=body,
        )


class AttemptToolExecutor:
    """Run a declared Tool invocation through the strict Attempt envelope."""

    def __init__(
        self,
        executor: AttemptExecutor,
        binding: CapabilityBinding,
        workspace: Path,
        envelope: EnvelopeSpec,
        controller: ToolController | None = None,
    ) -> None:
        self._executor = executor
        self._binding = binding
        self._workspace = workspace
        self._envelope = envelope
        if controller is not None:
            executor.add_generation_revocation(controller.revoke_generation)

    def __call__(self, invocation: ToolInvocation) -> tuple[int, bytes, Mapping[str, int]]:
        if invocation.input:
            raise ValueError("Attempt Tool stdin is unsupported")
        result = self._executor.start(
            AttemptRequest(
                generation_id=self._binding.generation_id,
                attempt_id=self._binding.attempt_id,
                step_id=self._binding.step_id,
                argv=invocation.argv,
                workspace=self._workspace,
                envelope=self._envelope,
            )
        ).result()
        if result.outcome is not ResourceOutcome.EXITED or result.exit_code is None:
            raise RuntimeError(f"Tool execution ended as {result.outcome.value}")
        resources = {
            name: int(value)
            for name, value in result.observed.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
        }
        resources["output_bytes"] = len(result.output)
        return result.exit_code, result.output, resources


class AttemptToolRuntime:
    """Production composition for declared Tool work inside Attempt execution."""

    def __init__(
        self,
        *,
        controller: ToolController,
        executor: AttemptExecutor,
        run_id: str,
        boot_id: str,
        peer: PeerIdentity,
        lane_id: str = "lane-1",
    ) -> None:
        self._controller = controller
        self._executor = executor
        self._run_id = run_id
        self._boot_id = boot_id
        self._peer = peer
        self._lane_id = lane_id
        executor.add_generation_revocation(controller.revoke_generation)

    def invoke(
        self,
        *,
        generation_id: str,
        attempt_id: str,
        step_id: str,
        workspace: Path,
        envelope: EnvelopeSpec,
        invocation: ToolInvocation,
    ) -> ToolResult:
        binding = CapabilityBinding(
            self._run_id,
            self._boot_id,
            generation_id,
            self._lane_id,
            attempt_id,
            step_id,
        )
        _view, handle = self._controller.issue_view(binding, self._peer, enabled_profiles=("resident",))
        return self._controller.invoke(
            object(),
            handle,
            invocation,
            execute=AttemptToolExecutor(self._executor, binding, workspace, envelope),
        )
