"""Generation-scoped Tool views, opaque handles, and measured invocations."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from solver.capability import CapabilityAuthority, CapabilityBinding, PeerIdentity
from solver.attempt_executor import AttemptExecutor, AttemptRequest, EnvelopeSpec, NetworkPolicy, ResourceOutcome
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.redaction import Redactor
from solver.tool_control_contracts import ToolControlRecorded, ToolRecord


def _digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _snapshot_digest(path: Path, input_kind: str, max_bytes: int) -> str:
    """Hash one symlink-free regular input tree without following path races."""

    root = Path(path)
    root_stat = root.lstat()
    is_file = stat.S_ISREG(root_stat.st_mode)
    is_directory = stat.S_ISDIR(root_stat.st_mode)
    if input_kind == "file" and not is_file:
        raise ValueError("Tool input must be a regular file")
    if input_kind == "directory" and not is_directory:
        raise ValueError("Tool input must be a directory")
    if input_kind not in {"file", "directory", "file-or-directory"} or not (is_file or is_directory):
        raise ValueError("Tool input kind is unsupported")
    records: list[dict[str, object]] = []
    total = 0
    candidates = [root] if is_file else sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix())
    for candidate in candidates:
        relative = "." if is_file else candidate.relative_to(root).as_posix()
        held = candidate.lstat()
        if stat.S_ISDIR(held.st_mode):
            records.append({"path": relative, "kind": "directory"})
            continue
        if not stat.S_ISREG(held.st_mode):
            raise ValueError("Tool input contains a symlink or special file")
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            before = os.fstat(descriptor)
            content = bytearray()
            while chunk := os.read(descriptor, 64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("Tool input exceeds its byte policy")
                content.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise ValueError("Tool input changed while it was read")
        records.append(
            {
                "path": relative,
                "kind": "file",
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return _digest({"kind": "file" if is_file else "directory", "bytes": total, "records": records})


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
    fixed_arguments: tuple[str, ...] = ()
    input_paths: int = 0
    require_network_denied: bool = False
    input_kind: str = "file-or-directory"
    max_path_bytes: int = 64 * 1024
    output_schema: str = ""
    network_class: str = "deny"
    exact_resource_request: bool = False
    adapter_digest: str = ""
    policy_digest: str = ""


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


RESIDENT_COMMANDS = {
    "archive.extract": ("7z", "7zip"),
    "crypto.primitive": ("python3", "python3-pycryptodome"),
    "data.sqlite": ("sqlite3", "sqlite3"),
    "document.pdf": ("pdftotext", "poppler-utils"),
    "firmware.rootfs": ("unsquashfs", "squashfs-tools"),
    "image.inspect": ("identify", "imagemagick-7.q16"),
    "math.symbolic": ("python3", "python3-sympy"),
    "network.http": ("curl", "curl"),
    "network.tcp": ("nc", "netcat-openbsd"),
    "process.inspect": ("ps", "procps"),
    "recognition.barcode": ("ZXingReader", "zxing-cpp-tools"),
    "recognition.ocr": ("tesseract", "tesseract-ocr"),
    "recon.bytes": ("xxd", "xxd"),
    "recon.mime": ("file", "file"),
    "recon.repository": ("rg", "ripgrep"),
    "solver.smt": ("python3", "python3-z3"),
}

PROFILE_COMMANDS = {
    "resident": RESIDENT_COMMANDS,
    "tool-crypto": {
        "crypto.asymmetric": ("python3", "python3-pycryptodome"),
        "crypto.cas": ("sage", "sagelib"),
        "crypto.classical": ("python3", "python3-pycryptodome"),
        "crypto.encoding": ("python3", "python3-pycryptodome"),
        "crypto.lattice": ("python3", "python3-fpylll"),
        "crypto.number-theory": ("python3", "python3-gmpy2"),
        "crypto.hash-crack": ("john", "john"),
        "crypto.symmetric-hash": ("python3", "python3-pycryptodome"),
        "crypto.certificate": ("openssl", "openssl"),
    },
    "web": {
        "web.discovery": ("ffuf", "ffuf"),
        "web.browser": ("chromium", "playwright"),
    },
    "osint": {
        "osint.dns": ("incypher-osint", None),
        "osint.identity": ("sherlock", "sherlock"),
        "osint.email": ("holehe", "holehe"),
        "osint.domain": ("theharvester", "theharvester"),
        "osint.geo": ("python3", "python3-geopy"),
    },
    "misc-protocols": {
        "misc.transform": ("node", "nodejs"),
        "misc.emulate": ("python3", "qiling"),
        "protocol.relay": ("socat", "socat"),
        "jail.reason": ("dash", "nodejs"),
    },
}
PRODUCTION_PROFILES = ("resident", "tool-crypto", "web", "osint", "misc-protocols")


def profile_components(
    inventory_path: Path, profile_id: str, *, require_complete: bool = True
) -> tuple[ToolComponent, ...]:
    """Load one locked Tool profile with its bounded invocation policies."""

    commands = PROFILE_COMMANDS.get(profile_id)
    if commands is None:
        raise ValueError(f"unknown Tool profile: {profile_id}")
    return _profile_components(inventory_path, profile_id, commands, require_complete=require_complete)


def resident_components(inventory_path: Path, *, require_complete: bool = True) -> tuple[ToolComponent, ...]:
    """Load the locked resident capability catalogue with bounded invocation policy."""

    return profile_components(inventory_path, "resident", require_complete=require_complete)


def attempt_components(inventory_path: Path) -> tuple[ToolComponent, ...]:
    """Load every Tool profile admitted for production Attempts."""

    return tuple(
        component for profile in PRODUCTION_PROFILES for component in profile_components(inventory_path, profile)
    )


def _profile_components(
    inventory_path: Path,
    profile_id: str,
    commands: Mapping[str, tuple[str, str | None]],
    *,
    require_complete: bool,
) -> tuple[ToolComponent, ...]:

    inventory = json.loads(Path(inventory_path).read_text())
    components: list[ToolComponent] = []
    seen: set[str] = set()
    for supplied in inventory["components"]:
        if profile_id not in supplied["profiles"]:
            continue
        versions = {item["name"]: item["version"] for item in supplied["packages"]}
        files = {item["destination"]: item for item in supplied["files"]}
        policies = supplied.get("capability_policies", {})
        for capability_id in supplied["capability_ids"]:
            if capability_id in seen or capability_id not in commands:
                raise ValueError(f"{profile_id} capability catalogue is invalid: {capability_id}")
            _command, package = commands[capability_id]
            policy = policies.get(capability_id)
            if (package is not None and package not in versions) or not isinstance(policy, dict):
                raise ValueError(f"{profile_id} capability has no locked component: {capability_id}")
            argv = policy.get("argv")
            if not isinstance(argv, list) or len(argv) != 4 or argv[-1] != "{input}":
                raise ValueError(f"{profile_id} capability has no safe argv policy: {capability_id}")
            adapter = files.get(supplied["entrypoint"])
            if not isinstance(adapter, dict):
                raise ValueError(f"{profile_id} capability has no locked adapter: {capability_id}")
            resource_limits = (
                ("cpu_seconds", policy["cpu_seconds"]),
                ("filesystem_bytes", policy["filesystem_bytes"]),
                ("memory_bytes", policy["memory_bytes"]),
                ("output_bytes", policy["max_output_bytes"]),
                ("pids", policy["pids"]),
                ("wall_seconds", policy["wall_seconds"]),
            )
            seen.add(capability_id)
            components.append(
                ToolComponent(
                    capability_id=capability_id,
                    component_id=argv[0],
                    version=supplied["version"],
                    profiles=(profile_id,),
                    max_arguments=len(argv),
                    max_input_bytes=0,
                    max_output_bytes=policy["max_output_bytes"],
                    resource_limits=resource_limits,
                    fixed_arguments=tuple(argv[1:-1]),
                    input_paths=1,
                    require_network_denied=True,
                    input_kind=policy["input_kind"],
                    max_path_bytes=policy["max_input_bytes"],
                    output_schema=policy["output_schema"],
                    network_class=policy["network"],
                    exact_resource_request=True,
                    adapter_digest=adapter["sha256"],
                    policy_digest=_digest(policy),
                )
            )
    if require_complete and seen != set(commands):
        raise ValueError(f"{profile_id} capability catalogue is incomplete")
    return tuple(sorted(components, key=lambda item: item.capability_id))


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
        capabilities = self.capability_ids(enabled_profiles=enabled_profiles)
        document = {"capability_ids": capabilities, "image_digest": self._image_digest}
        view = ToolView(capabilities, _digest(document))
        handle = self._authority.issue(binding, f"tool.view:{view.digest}", peer)
        self._views[view.digest] = view
        self._handles[handle] = binding.generation_id
        return view, handle

    def capability_ids(self, *, enabled_profiles: Sequence[str]) -> tuple[str, ...]:
        enabled = set(enabled_profiles)
        return tuple(
            sorted(
                component.capability_id
                for component in self._components.values()
                if enabled.intersection(component.profiles)
            )
        )

    def resident_component(self, capability_id: str) -> ToolComponent:
        component = self._components.get(capability_id)
        if component is None or "resident" not in component.profiles:
            raise PermissionError("resident Tool capability is unavailable")
        return component

    def component(self, capability_id: str) -> ToolComponent:
        """Return a component only when its profile is enabled for this Attempt runtime."""

        component = self._components.get(capability_id)
        if component is None:
            raise PermissionError("Tool capability is unavailable")
        return component

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
        workspace: Path | None = None,
        network: object | None = None,
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
        elif not self._arguments_allowed(component, invocation, workspace, network):
            reason = "argument-policy"
        elif not self._resources_allowed(component, invocation):
            reason = "resource-breach"
        if reason:
            self._record(grant.binding, invocation, component, view, ToolRecord.DENIED, reason=reason)
            raise PermissionError("tool invocation refused")
        assert component is not None and view is not None
        input_digest = self._input_digest(component, invocation, workspace)
        if input_digest is None:
            self._record(grant.binding, invocation, component, view, ToolRecord.DENIED, reason="input-policy")
            raise PermissionError("tool invocation refused")
        invocation_id = self._next_invocation_id()
        self._record(
            grant.binding,
            invocation,
            component,
            view,
            ToolRecord.RESERVED,
            invocation_id=invocation_id,
            input_digest=input_digest,
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
                input_digest=input_digest,
            )
            raise
        self._authority.authorize(connection, handle)
        limits = dict(component.resource_limits)
        if (
            len(output) > component.max_output_bytes
            or (component.output_schema and not output.startswith(f"schema={component.output_schema}\n".encode()))
            or self._input_digest(component, invocation, workspace) != input_digest
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 0
                or (name in limits and value > limits[name])
                for name, value in resources.items()
            )
        ):
            self._record(
                grant.binding,
                invocation,
                component,
                view,
                ToolRecord.DENIED,
                invocation_id=invocation_id,
                reason="resource-breach",
                input_digest=input_digest,
            )
            raise PermissionError("tool invocation refused")
        try:
            receipt = ToolInvocationReceipt(
                component_id=component.component_id,
                version=component.version,
                image_digest=self._image_digest,
                view_digest=view.digest,
                arguments_digest=_digest(invocation.argv),
                input_digest=input_digest,
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
                input_digest=input_digest,
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
                input_digest=input_digest,
            )
            raise
        return ToolResult(exit_code, output, receipt)

    @staticmethod
    def _arguments_allowed(
        component: ToolComponent,
        invocation: ToolInvocation,
        workspace: Path | None,
        network: object | None,
    ) -> bool:
        arguments = invocation.argv[1:]
        fixed = component.fixed_arguments
        if fixed and arguments[: len(fixed)] != fixed:
            return False
        paths = arguments[len(fixed) :]
        if len(paths) != component.input_paths:
            return not fixed and component.input_paths == 0
        if component.require_network_denied and getattr(network, "value", network) != "deny":
            return False
        if not paths:
            return True
        if workspace is None:
            return False
        root = Path(workspace).resolve()
        for value in paths:
            virtual = Path(value)
            try:
                relative = virtual.relative_to("/work")
            except ValueError:
                return False
            candidate = root / relative
            if ".." in relative.parts or not candidate.exists() or not candidate.resolve().is_relative_to(root):
                return False
        return True

    @staticmethod
    def _resources_allowed(component: ToolComponent, invocation: ToolInvocation) -> bool:
        limits = dict(component.resource_limits)
        requested = dict(invocation.resource_request)
        return (
            len(requested) == len(invocation.resource_request)
            and (not component.exact_resource_request or set(requested) == set(limits))
            and all(
                isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= limits.get(name, -1)
                for name, value in requested.items()
            )
        )

    @staticmethod
    def _input_digest(component: ToolComponent, invocation: ToolInvocation, workspace: Path | None) -> str | None:
        if component.input_paths != 1 or workspace is None:
            return hashlib.sha256(invocation.input).hexdigest() if component.input_paths == 0 else None
        value = invocation.argv[-1]
        try:
            relative = Path(value).relative_to("/work")
        except ValueError:
            return None
        if not relative.parts or ".." in relative.parts:
            return None
        candidate = Path(workspace).resolve() / relative
        try:
            return _snapshot_digest(candidate, component.input_kind, component.max_path_bytes)
        except (OSError, ValueError):
            return None

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
        input_digest: str | None = None,
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
                input_digest=input_digest or hashlib.sha256(invocation.input).hexdigest(),
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
        enabled_profiles: Sequence[str] = PRODUCTION_PROFILES,
    ) -> None:
        self._controller = controller
        self._executor = executor
        self._run_id = run_id
        self._boot_id = boot_id
        self._peer = peer
        self._lane_id = lane_id
        self._enabled_profiles = tuple(enabled_profiles)
        executor.add_generation_revocation(controller.revoke_generation)

    @property
    def capability_ids(self) -> tuple[str, ...]:
        return self._controller.capability_ids(enabled_profiles=self._enabled_profiles)

    def invoke(
        self,
        *,
        generation_id: str,
        attempt_id: str,
        lane_id: str | None = None,
        step_id: str,
        workspace: Path,
        envelope: EnvelopeSpec,
        invocation: ToolInvocation,
    ) -> ToolResult:
        component = self._controller.component(invocation.capability_id)
        if not set(component.profiles).intersection(self._enabled_profiles):
            raise PermissionError("Tool capability is unavailable")
        envelope = replace(envelope, network_class=component.network_class)
        binding = CapabilityBinding(
            self._run_id,
            self._boot_id,
            generation_id,
            lane_id or self._lane_id,
            attempt_id,
            step_id,
        )
        _view, handle = self._controller.issue_view(binding, self._peer, enabled_profiles=self._enabled_profiles)
        return self._controller.invoke(
            object(),
            handle,
            invocation,
            execute=AttemptToolExecutor(self._executor, binding, workspace, envelope),
            workspace=workspace,
            network=envelope.network,
        )

    def invoke_resident(
        self,
        *,
        generation_id: str,
        attempt_id: str,
        lane_id: str | None = None,
        step_id: str,
        workspace: Path,
        capability_id: str,
        input_path: Path,
    ) -> ToolResult:
        """Dispatch one resident capability from its locked catalogue policy."""

        self._controller.resident_component(capability_id)
        return self.invoke_capability(
            generation_id=generation_id,
            attempt_id=attempt_id,
            lane_id=lane_id,
            step_id=step_id,
            workspace=workspace,
            capability_id=capability_id,
            input_path=input_path,
        )

    def invoke_capability(
        self,
        *,
        generation_id: str,
        attempt_id: str,
        lane_id: str | None = None,
        step_id: str,
        workspace: Path,
        capability_id: str,
        input_path: Path,
    ) -> ToolResult:
        """Dispatch any enabled profile capability from its immutable policy."""

        component = self._controller.component(capability_id)
        if not set(component.profiles).intersection(self._enabled_profiles):
            raise PermissionError("Tool capability is unavailable")
        if component.input_paths != 1:
            raise PermissionError("Tool capability is unavailable")
        root = Path(workspace).resolve()
        try:
            relative = Path(input_path).resolve().relative_to(root)
        except ValueError:
            raise PermissionError("Tool input is outside its workspace") from None
        resources = dict(component.resource_limits)
        invocation = ToolInvocation(
            capability_id,
            (component.component_id, *component.fixed_arguments, f"/work/{relative.as_posix()}"),
            resource_request=component.resource_limits,
        )
        envelope = EnvelopeSpec(
            cpu_seconds=resources["cpu_seconds"],
            cpu_quota_us=100_000,
            memory_bytes=resources["memory_bytes"],
            pids=resources["pids"],
            filesystem_bytes=resources["filesystem_bytes"],
            network=NetworkPolicy.DENY,
            network_class=component.network_class,
            wall_seconds=resources["wall_seconds"],
            cleanup_seconds=5,
        )
        return self.invoke(
            generation_id=generation_id,
            attempt_id=attempt_id,
            lane_id=lane_id,
            step_id=step_id,
            workspace=root,
            envelope=envelope,
            invocation=invocation,
        )
