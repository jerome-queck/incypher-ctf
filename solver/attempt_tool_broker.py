"""Attempt-owned IPC port for model-orchestrated governed Tools."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import threading
from pathlib import Path
from typing import Protocol

from solver.event_store_storage import canonical_bytes
from solver.local_ipc import receive_line
from solver.tool_control import ToolResult

CLIENT_TIMEOUT_SECONDS = 1.0


class AttemptTools(Protocol):
    @property
    def capability_ids(self) -> tuple[str, ...]: ...

    def invoke_capability(
        self,
        *,
        generation_id: str,
        attempt_id: str,
        lane_id: str,
        step_id: str,
        workspace: Path,
        capability_id: str,
        input_path: Path,
    ) -> ToolResult: ...


class AttemptToolBrokerService:
    """Serialize narrow model requests onto one fixed Attempt binding."""

    def __init__(
        self,
        path: Path,
        runtime: AttemptTools,
        *,
        generation_id: str,
        attempt_id: str,
        lane_id: str,
        workspace: Path,
        handle: str,
    ) -> None:
        if not handle:
            raise ValueError("Attempt Tool broker requires an opaque handle")
        self.path = Path(path)
        self._runtime = runtime
        self._generation_id = generation_id
        self._attempt_id = attempt_id
        self._lane_id = lane_id
        self._workspace = Path(workspace).resolve()
        self._handle_digest = hashlib.sha256(handle.encode()).digest()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._serial = 0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, daemon=True, name="attempt-tool-broker")
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("Attempt Tool broker did not start")

    def close(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(5)
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener = listener
        listener.bind(str(self.path))
        self.path.chmod(0o600)
        listener.listen(1)
        listener.settimeout(0.2)
        self._ready.set()
        while not self._stop.is_set():
            try:
                connection, _ = listener.accept()
            except (OSError, socket.timeout):
                continue
            connection.settimeout(CLIENT_TIMEOUT_SECONDS)
            self._one(connection)

    def _one(self, connection: socket.socket) -> None:
        try:
            request = json.loads(receive_line(connection, failure="malformed Attempt Tool request"))
            answer = self._answer(request) if isinstance(request, dict) else {"outcome": "refused"}
        except Exception:
            answer = {"outcome": "refused"}
        try:
            connection.sendall(canonical_bytes(answer) + b"\n")
        except OSError:
            pass
        finally:
            connection.close()

    def _answer(self, request: dict[str, object]) -> dict[str, object]:
        handle = request.get("handle")
        supplied = hashlib.sha256(handle.encode()).digest() if isinstance(handle, str) else b""
        if not hmac.compare_digest(supplied, self._handle_digest):
            return {"outcome": "refused"}
        operation = request.get("operation")
        if operation == "list":
            return {"outcome": "listed", "capabilities": list(self._runtime.capability_ids)}
        if operation != "run":
            return {"outcome": "refused"}
        capability_id = request.get("capability_id")
        input_value = request.get("input_path")
        if not isinstance(capability_id, str) or not isinstance(input_value, str):
            return {"outcome": "refused"}
        relative = Path(input_value)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            return {"outcome": "refused"}
        self._serial += 1
        result = self._runtime.invoke_capability(
            generation_id=self._generation_id,
            attempt_id=self._attempt_id,
            lane_id=self._lane_id,
            step_id=f"model-tool-{self._serial:06d}",
            workspace=self._workspace,
            capability_id=capability_id,
            input_path=self._workspace / relative,
        )
        return {
            "outcome": "answered",
            "exit_code": result.exit_code,
            "output": base64.b64encode(result.output).decode(),
        }


__all__ = ["AttemptToolBrokerService"]
