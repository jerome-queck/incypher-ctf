"""One serialized owner for native Codex requests and observed limit truth."""

from __future__ import annotations

import json
import socket
import threading
import datetime as dt
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from solver.capability import (
    CapabilityAuthority,
    CapabilityBinding,
    CapabilityRefused,
    PeerAuthenticationUnavailable,
)
from solver.broker_contracts import BrokerReceipt

from solver.codex_control_contracts import (
    CodexCatalogueEntry,
    CodexControlResult,
    CodexRequest,
    CodexTurn,
    LimitObservation,
    NativeResponse,
)
from solver.local_ipc import receive_line
from solver.event_store import EventStore
from solver.event_store_contracts import ObservationRecorded
from solver.event_store_storage import canonical_bytes
from solver.executor_secret_probe import ExecutorProbeResult


class NativeTransport(Protocol):
    def __call__(self, request: CodexRequest) -> NativeResponse: ...

    def cancel(self, request_id: str) -> bool: ...


class ObservedExhaustion(RuntimeError):
    """Native control positively observed an applicable exhausted limit."""

    def __init__(self, observation: LimitObservation | None = None) -> None:
        super().__init__("observed subscription exhaustion")
        self.observation = observation


class CodexControl:
    """Validate controller authority and the frozen catalogue before transport."""

    def __init__(
        self,
        catalogue: Sequence[CodexCatalogueEntry],
        transport: NativeTransport,
        *,
        state: Path | None = None,
        run_id: str = "",
    ) -> None:
        if not catalogue or any(not entry.model or not entry.efforts for entry in catalogue):
            raise ValueError("Codex Control requires a nonempty model catalogue")
        self._catalogue = {entry.model: frozenset(entry.efforts) for entry in catalogue}
        if len(self._catalogue) != len(catalogue):
            raise ValueError("Codex Control catalogue models must be unique")
        self._transport = transport
        self._state = Path(state) if state is not None else None
        self._run_id = run_id
        self._store = EventStore(self._state, run_id=run_id) if self._state is not None and run_id else None
        self._catalogue_document = [{"model": entry.model, "efforts": list(entry.efforts)} for entry in catalogue]
        self._limits: dict[str, LimitObservation] = {}
        self._probes: dict[str, str] = {}
        self._custody: dict[str, object] = {}
        self._last_request: CodexRequest | None = None
        self._last_result: CodexControlResult | None = None
        self._lock = threading.Lock()

    def request(
        self,
        request: CodexRequest,
        *,
        origin: str,
    ) -> CodexControlResult:
        if origin != "run-controller":
            return CodexControlResult("capability-refused")
        if (
            not request.request_id
            or not request.turn_id
            or request.model not in self._catalogue
            or request.effort not in self._catalogue.get(request.model, ())
        ):
            return CodexControlResult("catalogue-refused")
        with self._lock:
            try:
                response = self._transport(request)
            except TimeoutError:
                return CodexControlResult("timeout")
            except PermissionError:
                return CodexControlResult("auth-failure")
            except InterruptedError:
                return CodexControlResult("cancelled")
            except ObservedExhaustion as exhausted:
                if exhausted.observation is not None:
                    self._limits[exhausted.observation.limit_id] = exhausted.observation
                return CodexControlResult("observed-exhaustion", limits=self.limits())
            except (ValueError, TypeError):
                return CodexControlResult("malformed-stream")
            turn = response.turn
            if turn.model != request.model:
                return CodexControlResult("model-mismatch")
            if (
                turn.request_id != request.request_id
                or turn.turn_id != request.turn_id
                or min(turn.tokens_in, turn.tokens_out, turn.duration_ms) < 0
            ):
                return CodexControlResult("malformed-stream")
            for observation in response.limits:
                if not observation.limit_id or not observation.source or not observation.observed_at:
                    return CodexControlResult("malformed-limit")
                self._limits[observation.limit_id] = observation
            result = CodexControlResult("answered", turn, response.limits)
            self._last_request, self._last_result = request, result
            self._record(request, result)
            return result

    def cancel(self, request_id: str, *, origin: str) -> CodexControlResult:
        if origin != "run-controller":
            return CodexControlResult("capability-refused")
        if not request_id:
            return CodexControlResult("malformed-request")
        return CodexControlResult("cancelled" if self._transport.cancel(request_id) else "not-active")

    def limit(self, limit_id: str) -> LimitObservation | None:
        return self._limits.get(limit_id)

    def limits(self) -> tuple[LimitObservation, ...]:
        now = dt.datetime.now(dt.timezone.utc)
        fresh = []
        for observation in self._limits.values():
            reset = observation.resets_at
            try:
                elapsed = bool(reset) and dt.datetime.fromisoformat(str(reset)).astimezone(dt.timezone.utc) <= now
            except ValueError:
                elapsed = False
            fresh.append(
                LimitObservation(
                    observation.limit_id,
                    0.0 if elapsed else observation.used_percent,
                    observation.resets_at,
                    observation.source,
                    now.isoformat() if elapsed else observation.observed_at,
                )
            )
        return tuple(fresh)

    def record_attempt_probe(self, probe: ExecutorProbeResult) -> None:
        checks = dict(probe.checks)
        required = {name: checks.get(name) for name in ("environment", "file", "event")}
        if set(required.values()) != {True}:
            raise PermissionError("Attempt secret probe found Codex subscription material")
        self._probes = {name: "clear" for name in required}
        self._record(self._last_request, self._last_result)

    def record_custody(self, receipt: BrokerReceipt) -> None:
        self._custody = {
            "owner": receipt.owner.value,
            "pid": receipt.pid,
            "uid": receipt.uid,
            "identity_digest": receipt.identity_digest,
            "secret_names": list(receipt.secret_names),
        }
        self._record(self._last_request, self._last_result)

    def _record(self, request: CodexRequest | None, result: CodexControlResult | None) -> None:
        if self._state is None or not self._run_id:
            return
        document = {
            "schema_version": 1,
            "run_id": self._run_id,
            "catalogue": self._catalogue_document,
            "request": _recordable_request(request),
            "result": _result_document(result) if result else None,
            "limits": [item.__dict__ for item in self._limits.values()],
            "secret_probes": self._probes,
            "custody": self._custody,
        }
        assert self._store is not None
        step = 1 + sum(
            event.event_type == "observation.recorded"
            and event.payload.get("attempt_id") == "codex-control"
            and event.payload.get("tool") == "codex-control-state"
            for event in self._store.events()
        )
        self._store.append(
            ObservationRecorded(
                attempt_id="codex-control",
                step_index=step,
                command_raw="record native Codex Control state",
                command_normalised="record native Codex Control state",
                tool="codex-control-state",
                source="codex-control",
                event_id=f"codex-control:{step:06d}",
            ),
            body=canonical_bytes(document),
        )


class CodexControlServer:
    """Serve one bounded request over an already peer-confined local channel."""

    def __init__(self, control: CodexControl, *, authority: CapabilityAuthority) -> None:
        self._control = control
        self._authority = authority

    def serve(self, channel: socket.socket) -> None:
        try:
            document = json.loads(receive_line(channel, failure="malformed Codex Control request"))
            grant = self._authority.authorize(channel, str(document.get("handle", "")))
            if grant.scope != "codex.engage":
                raise CapabilityRefused("wrong-scope")
            operation = document.get("operation", "request")
            if operation == "cancel":
                result = self._control.cancel(str(document.get("request_id", "")), origin="run-controller")
            elif operation == "limits":
                result = CodexControlResult("limits", limits=self._control.limits())
            else:
                request = CodexRequest(**document["request"])
                result = self._control.request(request, origin="run-controller")
            response = _result_document(result)
        except (CapabilityRefused, PeerAuthenticationUnavailable):
            response = _result_document(CodexControlResult("capability-refused"))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            response = _result_document(CodexControlResult("malformed-request"))
        channel.sendall(json.dumps(response, sort_keys=True, separators=(",", ":")).encode() + b"\n")


class CodexControlClient:
    def __init__(self, channel: socket.socket, handle: str) -> None:
        self._channel = channel
        self._handle = handle

    @classmethod
    def open(cls, socket_path: Path, binding: CapabilityBinding) -> CodexControlClient:
        channel = socket.socket(socket.AF_UNIX)
        channel.connect(str(socket_path))
        channel.sendall(
            canonical_bytes({"operation": "issue", "binding": binding.document(), "scope": "codex.engage"}) + b"\n"
        )
        response = json.loads(receive_line(channel, failure="Codex Control did not issue a capability"))
        channel.close()
        if response.get("outcome") != "issued" or not isinstance(response.get("handle"), str):
            raise CapabilityRefused()
        request_channel = socket.socket(socket.AF_UNIX)
        request_channel.connect(str(socket_path))
        return cls(request_channel, response["handle"])

    def request(self, request: CodexRequest) -> CodexControlResult:
        document = {"operation": "request", "handle": self._handle, "request": request.__dict__}
        self._channel.sendall(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        response = json.loads(receive_line(self._channel, failure="malformed Codex Control response"))
        if response.get("turn"):
            turn_document = dict(response["turn"])
            turn_document["stream"] = tuple(turn_document.get("stream", ()))
            turn = CodexTurn(**turn_document)
        else:
            turn = None
        limits = tuple(LimitObservation(**item) for item in response.get("limits", ()))
        return CodexControlResult(response["outcome"], turn, limits)

    def close(self) -> None:
        self._channel.close()

    def limits(self) -> tuple[LimitObservation, ...]:
        document = {"operation": "limits", "handle": self._handle}
        self._channel.sendall(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        response = json.loads(receive_line(self._channel, failure="malformed Codex Control response"))
        return tuple(LimitObservation(**item) for item in response.get("limits", ()))

    def cancel(self, request_id: str) -> CodexControlResult:
        document = {"operation": "cancel", "handle": self._handle, "request_id": request_id}
        self._channel.sendall(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        response = json.loads(receive_line(self._channel, failure="malformed Codex Control response"))
        return CodexControlResult(response["outcome"])


def _result_document(result: CodexControlResult) -> dict[str, object]:
    return {
        "outcome": result.outcome,
        "turn": result.turn.__dict__ if result.turn else None,
        "limits": [item.__dict__ for item in result.limits],
    }


def _recordable_request(request: CodexRequest | None) -> dict[str, object] | None:
    if request is None:
        return None
    return {**request.__dict__, "tool_handle": ""}


class CodexControlService:
    """Pathname listener hosted only by the post-bootstrap Codex owner process."""

    def __init__(self, path: Path, server: CodexControlServer, authority: CapabilityAuthority) -> None:
        self.path, self._server, self._authority = Path(path), server, authority
        self._stopped = threading.Event()
        self._listener: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name="codex-control", daemon=True)
        self._thread.start()
        for _ in range(100):
            if self.path.exists():
                return
            self._stopped.wait(0.01)
        raise RuntimeError("Codex Control listener did not start")

    def close(self) -> None:
        self._stopped.set()
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        for worker in self._workers:
            worker.join(timeout=2)
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        listener = socket.socket(socket.AF_UNIX)
        self._listener = listener
        listener.bind(str(self.path))
        self.path.chmod(0o600)
        listener.listen(8)
        listener.settimeout(0.2)
        while not self._stopped.is_set():
            try:
                connection, _address = listener.accept()
            except (OSError, socket.timeout):
                continue
            worker = threading.Thread(target=self._serve_one, args=(connection,), daemon=True)
            self._workers.append(worker)
            worker.start()

    def _serve_one(self, connection: socket.socket) -> None:
        try:
            raw = receive_line(connection, failure="malformed Codex Control request")
            document = json.loads(raw)
            if document.get("operation") == "issue":
                fields = document["binding"]
                binding = CapabilityBinding(**fields)
                peer = self._authority.peer_identity(connection)
                handle = self._authority.issue(binding, "codex.engage", peer)
                connection.sendall(canonical_bytes({"outcome": "issued", "handle": handle}) + b"\n")
            else:

                class Prefixed:
                    def __init__(self, channel: socket.socket, line: str) -> None:
                        self._channel, self._line = channel, line

                    def recv(self, size: int) -> bytes:
                        if self._line:
                            raw_line, self._line = (self._line + "\n").encode(), ""
                            return raw_line[:size]
                        return self._channel.recv(size)

                    def sendall(self, body: bytes) -> None:
                        self._channel.sendall(body)

                    def getsockopt(self, *args):
                        return self._channel.getsockopt(*args)

                self._server.serve(Prefixed(connection, raw))  # type: ignore[arg-type]
        except Exception:
            try:
                connection.sendall(canonical_bytes({"outcome": "capability-refused"}) + b"\n")
            except OSError:
                pass
        finally:
            connection.close()


__all__ = [
    "CodexControl",
    "CodexControlClient",
    "CodexControlServer",
    "CodexControlService",
    "NativeTransport",
    "ObservedExhaustion",
]
