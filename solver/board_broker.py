"""One deep runtime for typed, generation-scoped Board authority."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import socket
import struct
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path

from solver.board import (
    Board,
    BoardFailure,
    Held,
    MAX_FETCH_BYTES,
    Mana,
    Reply,
    Standing,
    TooLarge,
    Transport,
    Verdict,
    network_transport,
)
from solver.board_broker_contracts import (
    ChallengeValue,
    ChallengesValue,
    BoardBrokerRecorded,
    BoardBrokerResult,
    BoardOperation,
    BoardOutcome,
    BoardProvenance,
    BoardRecord,
    DownloadValue,
    ReadContractValue,
    ScoreboardValue,
    binding_document,
    binding_from,
    decode_result,
    encode_result,
)
from solver.capability import (
    CapabilityAuthority,
    CapabilityBinding,
    CapabilityRefused,
    PeerAuthenticationUnavailable,
)
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.local_ipc import receive_exact, receive_line
from solver.redaction import Redactor

MAX_SEALED_RESPONSE_BYTES = 64 * 1024

_SCOPE = {
    BoardOperation.READ_CONTRACT: "board.read",
    BoardOperation.CHALLENGES: "board.read",
    BoardOperation.CHALLENGE: "board.read",
    BoardOperation.SCOREBOARD: "board.read",
    BoardOperation.DOWNLOAD: "board.read",
    BoardOperation.SUBMIT: "board.submit",
    BoardOperation.INSTANCE_DEPLOY: "board.instance.effect",
    BoardOperation.INSTANCE_READ: "board.instance.read",
    BoardOperation.INSTANCE_RENEW: "board.instance.effect",
    BoardOperation.INSTANCE_TERMINATE: "board.instance.effect",
    BoardOperation.INSTANCE_MANA: "board.instance.read",
    BoardOperation.INSTANCES_HELD: "board.instance.read",
}

_EFFECTS = {
    BoardOperation.SUBMIT,
    BoardOperation.INSTANCE_DEPLOY,
    BoardOperation.INSTANCE_RENEW,
    BoardOperation.INSTANCE_TERMINATE,
}


@dataclasses.dataclass(frozen=True)
class BoardBrokerRecordView:
    event_id: str
    request_id: str
    record: str
    operation: str
    binding_digest: str
    run_id: str
    boot_id: str
    generation_id: str
    lane_id: str
    attempt_id: str
    step_id: str
    scope: str
    peer_identity_digest: str
    request_digest: str
    outcome: str
    endpoint: str
    http_status: int
    response_digest: str
    response_original_bytes: int
    response_truncated: bool
    response_lost_bytes: int
    blob_digest: str


class BoardBrokerRuntime:
    """Authenticate, reserve, execute and classify one closed Board operation."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        authority: CapabilityAuthority,
        url: str,
        token: str,
        team_key: str,
        boot_id: str,
        transport: Transport | None = None,
        timestamp: Callable[[], str],
        sealed_response_bytes: int = MAX_SEALED_RESPONSE_BYTES,
    ) -> None:
        if not token or not boot_id or sealed_response_bytes <= 0:
            raise ValueError("Board broker needs a token, Boot identity and response bound")
        self._run_id = run_id
        self._boot_id = boot_id
        self._authority = authority
        self._timestamp = timestamp
        self._redactor = Redactor(
            {name: value for name, value in {"CTFD_API_TOKEN": token, "TEAM_KEY": team_key}.items() if value}
        )
        self._store = EventStore(state, run_id=run_id, redactor=self._redactor)
        self._serial = sum(event.event_type == "board-broker.recorded" for event in self._store.events())
        self._sealed_response_bytes = sealed_response_bytes
        self._wire_local = threading.local()
        self._serial_lock = threading.Lock()

        bounded_transport = transport or network_transport()

        def observed(request):
            wire = self._wire()
            try:
                status, raw, location = bounded_transport(request)
            except OSError as error:
                wire.append((0, b"", "", type(error).__name__))
                raise
            path = urllib.parse.urlparse(request.full_url).path
            wire.append((status, raw, path, ""))
            return status, raw, location

        self._board = Board(url, token, observed)
        self._public_board = Board(url, "", observed)

    def execute(self, connection: object, handle: str, operation: BoardOperation, **arguments) -> BoardBrokerResult:
        operation = BoardOperation(operation)
        return self._execute(connection, handle, operation, arguments)

    def _execute(
        self,
        connection: object,
        handle: str,
        operation: BoardOperation,
        arguments: dict[str, object],
    ) -> BoardBrokerResult:
        try:
            peer = self._authority.peer_identity(connection)
            grant = self._authority.authorize(connection, handle, peer=peer)
        except PeerAuthenticationUnavailable:
            self._authority.record_peer_authentication_failure()
            return BoardBrokerResult(operation, BoardOutcome.CAPABILITY_REFUSED)
        except CapabilityRefused as refused:
            return BoardBrokerResult(operation, _refusal_outcome(refused))
        except (OSError, RuntimeError, ValueError):
            return BoardBrokerResult(operation, BoardOutcome.RESERVATION_REFUSED)
        scope = _SCOPE[operation]
        if grant.scope != scope:
            return BoardBrokerResult(operation, BoardOutcome.CAPABILITY_REFUSED)
        request_digest = _request_digest(operation, arguments)
        request_id = self._next_id()
        try:
            self._store.append(
                self._record(
                    event_id=request_id,
                    request_id=request_id,
                    record=BoardRecord.RESERVED,
                    operation=operation,
                    binding=grant.binding,
                    scope=scope,
                    peer_digest=peer.digest,
                    request_digest=request_digest,
                ),
                body=b"",
            )
        except (OSError, RuntimeError, ValueError):
            return BoardBrokerResult(operation, BoardOutcome.RESERVATION_REFUSED)
        self._wire().clear()
        value = None
        try:
            value = self._dispatch(operation, arguments)
            outcome = BoardOutcome.ANSWERED
        except TimeoutError:
            outcome = BoardOutcome.TIMEOUT
        except TooLarge:
            outcome = BoardOutcome.TOO_LARGE
        except BoardFailure:
            outcome = self._failure_outcome(operation)
        except OSError:
            outcome = BoardOutcome.UNREACHABLE
        wire = self._wire()
        status, raw, endpoint, transport_error = wire[-1] if wire else (0, b"", "", "")
        if transport_error:
            outcome, value = _transport_failure(operation, transport_error), None
        if status == 401:
            outcome, value = BoardOutcome.AUTH_FAILURE, None
        try:
            self._authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as refused:
            outcome, value = _refusal_outcome(refused), None
        except (OSError, RuntimeError, ValueError):
            outcome, value = BoardOutcome.RESERVATION_REFUSED, None
        sanitized = self._redactor.redact(raw)
        sealed = sanitized[: self._sealed_response_bytes]
        original_bytes = len(raw)
        lost_bytes = max(0, original_bytes - len(sealed))
        try:
            self._store.append(
                self._record(
                    event_id=self._next_id(),
                    request_id=request_id,
                    record=BoardRecord.CLASSIFIED,
                    operation=operation,
                    binding=grant.binding,
                    scope=scope,
                    peer_digest=peer.digest,
                    request_digest=request_digest,
                    outcome=outcome,
                    endpoint=endpoint,
                    http_status=status,
                    response_digest=hashlib.sha256(raw).hexdigest() if raw else "",
                    response_original_bytes=original_bytes,
                    response_truncated=bool(lost_bytes),
                    response_lost_bytes=lost_bytes,
                ),
                body=sealed,
            )
        except (OSError, RuntimeError, ValueError):
            outcome = BoardOutcome.EFFECT_INDETERMINATE if operation in _EFFECTS else BoardOutcome.RESERVATION_REFUSED
            return BoardBrokerResult(operation, outcome, request_id=request_id)
        provenance = BoardProvenance(
            endpoint,
            status,
            hashlib.sha256(raw).hexdigest() if raw else "",
            original_bytes,
            bool(lost_bytes),
            lost_bytes,
        )
        typed = self._typed(operation, value) if outcome is BoardOutcome.ANSWERED else None
        return BoardBrokerResult(operation, outcome, typed, provenance, request_id)

    def execute_compatibility(
        self,
        operation: BoardOperation,
        arguments: dict[str, object],
        *,
        authenticated: bool,
    ) -> BoardBrokerResult:
        self._wire().clear()
        value = None
        try:
            value = self._dispatch(operation, arguments, board=self._board if authenticated else self._public_board)
            outcome = BoardOutcome.ANSWERED
        except TimeoutError:
            outcome = BoardOutcome.TIMEOUT
        except TooLarge:
            outcome = BoardOutcome.TOO_LARGE
        except BoardFailure:
            outcome = self._failure_outcome(operation)
        except OSError:
            outcome = BoardOutcome.UNREACHABLE
        wire = self._wire()
        status, raw, endpoint, error = wire[-1] if wire else (0, b"", "", "")
        if error:
            outcome, value = _transport_failure(operation, error), None
        if status == 401:
            outcome, value = BoardOutcome.AUTH_FAILURE, None
        sanitized = self._redactor.redact(raw)
        lost = max(0, len(raw) - min(len(sanitized), self._sealed_response_bytes))
        provenance = BoardProvenance(
            endpoint,
            status,
            hashlib.sha256(raw).hexdigest() if raw else "",
            len(raw),
            bool(lost),
            lost,
        )
        return BoardBrokerResult(
            operation,
            outcome,
            self._typed(operation, value) if outcome is BoardOutcome.ANSWERED else None,
            provenance,
        )

    def revoke(self, handle: str, reason: str) -> None:
        self._authority.revoke(handle, reason)

    def records(self) -> tuple[BoardBrokerRecordView, ...]:
        return tuple(
            BoardBrokerRecordView(**{name: event.payload[name] for name in BoardBrokerRecordView.__dataclass_fields__})
            for event in self._store.events()
            if event.event_type == "board-broker.recorded"
        )

    def response_body(self, record: BoardBrokerRecordView) -> bytes:
        return self._store.blob(record.blob_digest)

    def _dispatch(self, operation: BoardOperation, arguments: dict[str, object], *, board: Board | None = None):
        board = board or self._board
        if operation is BoardOperation.READ_CONTRACT:
            return board.collection_endpoints_reach_ctfd()
        if operation is BoardOperation.CHALLENGES:
            return board.challenges()
        if operation is BoardOperation.CHALLENGE:
            return board.challenge(arguments["challenge_id"])
        if operation is BoardOperation.SCOREBOARD:
            return board.scoreboard(int(arguments["top"]))
        if operation is BoardOperation.DOWNLOAD:
            return board.download(str(arguments["file_path"]))
        if operation is BoardOperation.SUBMIT:
            verdict = board.submit(arguments["challenge_id"], str(arguments["flag"]))
            if verdict.message:
                verdict = dataclasses.replace(
                    verdict, message=verdict.message.replace(str(arguments["flag"]), "[submitted-candidate]")
                )
            return verdict
        if operation is BoardOperation.INSTANCE_DEPLOY:
            return board.deploy_instance(arguments["challenge_id"])
        if operation is BoardOperation.INSTANCE_READ:
            return board.read_instance(arguments["challenge_id"])
        if operation is BoardOperation.INSTANCE_RENEW:
            return board.renew_instance(arguments["challenge_id"])
        if operation is BoardOperation.INSTANCE_TERMINATE:
            return board.terminate_instance(arguments["challenge_id"])
        if operation is BoardOperation.INSTANCE_MANA:
            return board.mana()
        if operation is BoardOperation.INSTANCES_HELD:
            return board.instances_held()
        raise ValueError("unsupported Board operation")

    def _failure_outcome(self, operation: BoardOperation) -> BoardOutcome:
        wire = self._wire()
        if wire and wire[-1][0] == 200:
            return BoardOutcome.MALFORMED
        if operation is BoardOperation.READ_CONTRACT and wire:
            return BoardOutcome.ANSWERED
        return BoardOutcome.UNREACHABLE

    def _next_id(self) -> str:
        with self._serial_lock:
            self._serial += 1
            return f"board-broker:{self._serial:06d}"

    def _wire(self) -> list[tuple[int, bytes, str, str]]:
        if not hasattr(self._wire_local, "events"):
            self._wire_local.events = []
        return self._wire_local.events

    def _record(self, *, binding: CapabilityBinding, peer_digest: str, **fields) -> BoardBrokerRecorded:
        return BoardBrokerRecorded(
            binding_digest=binding.digest,
            run_id=binding.run_id,
            boot_id=binding.boot_id,
            generation_id=binding.generation_id,
            lane_id=binding.lane_id,
            attempt_id=binding.attempt_id,
            step_id=binding.step_id,
            peer_identity_digest=peer_digest,
            ts=self._timestamp(),
            **fields,
        )

    def _typed(self, operation: BoardOperation, value) -> object:
        value = _sanitize_public(value, self._redactor)
        if operation is BoardOperation.READ_CONTRACT:
            return ReadContractValue(bool(value))
        if operation is BoardOperation.CHALLENGES:
            return ChallengesValue(tuple(value))
        if operation is BoardOperation.CHALLENGE:
            return ChallengeValue(value)
        if operation is BoardOperation.SCOREBOARD:
            return ScoreboardValue(tuple(value))
        if operation is BoardOperation.DOWNLOAD:
            return DownloadValue(value[0], tuple(value[1]))
        return value


class BoardBrokerClient:
    """Pathname-only typed client; the credential owner is never in this process."""

    def __init__(self, socket_path: Path, handle: str) -> None:
        self._socket_path = Path(socket_path)
        self._handle = handle
        self._closed = False

    @classmethod
    def open(cls, socket_path: Path, binding: CapabilityBinding, *, scope: str) -> BoardBrokerClient:
        response = _ipc_request(
            Path(socket_path),
            {"command": "issue", "binding": binding_document(binding), "scope": scope},
        )
        if response.get("status") != "issued" or not isinstance(response.get("handle"), str):
            raise CapabilityRefused()
        return cls(Path(socket_path), str(response["handle"]))

    def close(self) -> None:
        if self._closed:
            return
        _ipc_request(self._socket_path, {"command": "revoke", "handle": self._handle})
        self._closed = True

    def read_contract(self) -> BoardBrokerResult:
        return self._execute(BoardOperation.READ_CONTRACT)

    def challenges(self) -> BoardBrokerResult:
        return self._execute(BoardOperation.CHALLENGES)

    def challenge(self, challenge_id: int | str) -> BoardBrokerResult:
        return self._execute(BoardOperation.CHALLENGE, challenge_id=challenge_id)

    def download(self, file_path: str) -> BoardBrokerResult:
        return self._execute(BoardOperation.DOWNLOAD, file_path=file_path)

    def submit(self, challenge_id: int | str, flag: str) -> BoardBrokerResult:
        return self._execute(BoardOperation.SUBMIT, challenge_id=challenge_id, flag=flag)

    def read_instance(self, challenge_id: int | str) -> BoardBrokerResult:
        return self._execute(BoardOperation.INSTANCE_READ, challenge_id=challenge_id)

    def instance(self, operation: BoardOperation, challenge_id: int | str | None = None) -> BoardBrokerResult:
        return self._execute(operation, **({} if challenge_id is None else {"challenge_id": challenge_id}))

    def _execute(self, operation: BoardOperation, **arguments) -> BoardBrokerResult:
        if self._closed:
            return BoardBrokerResult(operation, BoardOutcome.REVOKED)
        response = _ipc_request(
            self._socket_path,
            {
                "command": "execute",
                "handle": self._handle,
                "operation": operation.value,
                "arguments": arguments,
            },
        )
        if not isinstance(response.get("result"), dict):
            return BoardBrokerResult(operation, BoardOutcome.UNREACHABLE)
        return _decode_ipc_result(response)


class BoardCompatibilityClient:
    """Temporary v1 Board facade whose credential and transport remain inside the owner."""

    def __init__(self, socket_path: Path, *, authenticated: bool = True) -> None:
        self._socket_path = Path(socket_path)
        self._authenticated = authenticated

    def collection_endpoints_reach_ctfd(self) -> bool:
        return self._value(BoardOperation.READ_CONTRACT).reaches_ctfd

    def challenges(self) -> list[dict[str, object]]:
        return [dict(entry) for entry in self._value(BoardOperation.CHALLENGES).entries]

    def challenge(self, challenge_id: int | str) -> dict[str, object]:
        return dict(self._value(BoardOperation.CHALLENGE, challenge_id=challenge_id).fields)

    def scoreboard(self, top: int) -> tuple[Standing, ...]:
        return self._value(BoardOperation.SCOREBOARD, top=top).standings

    def download(self, file_path: str) -> tuple[bytes, list[str]]:
        value = self._value(BoardOperation.DOWNLOAD, file_path=file_path)
        return value.content, list(value.hops)

    def submit(self, challenge_id: int | str, flag: str) -> Verdict:
        return self._value(BoardOperation.SUBMIT, challenge_id=challenge_id, flag=flag)

    def deploy_instance(self, challenge_id: int | str) -> Reply:
        return self._value(BoardOperation.INSTANCE_DEPLOY, challenge_id=challenge_id)

    def read_instance(self, challenge_id: int | str) -> Reply:
        return self._value(BoardOperation.INSTANCE_READ, challenge_id=challenge_id)

    def renew_instance(self, challenge_id: int | str) -> Reply:
        return self._value(BoardOperation.INSTANCE_RENEW, challenge_id=challenge_id)

    def terminate_instance(self, challenge_id: int | str) -> Reply:
        return self._value(BoardOperation.INSTANCE_TERMINATE, challenge_id=challenge_id)

    def mana(self) -> Mana:
        return self._value(BoardOperation.INSTANCE_MANA)

    def instances_held(self) -> tuple[Held, ...]:
        return self._value(BoardOperation.INSTANCES_HELD)

    def _value(self, operation: BoardOperation, **arguments):
        response = _ipc_request(
            self._socket_path,
            {
                "command": "compatibility",
                "authenticated": self._authenticated,
                "operation": operation.value,
                "arguments": arguments,
            },
        )
        result = _decode_ipc_result(response)
        if result.outcome is not BoardOutcome.ANSWERED or result.value is None:
            raise BoardFailure(f"Board broker classified {operation.value} as {result.outcome.value}")
        return result.value


class BoardBrokerService:
    """Peer-authenticated pathname listener hosted only by the Board owner process."""

    def __init__(self, socket_path: Path, runtime: BoardBrokerRuntime) -> None:
        self.path = Path(socket_path)
        self._runtime = runtime
        self._stopped = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._workers: list[threading.Thread] = []

    def start(self) -> None:
        self._thread = threading.Thread(target=self._serve, name="board-broker", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("Board broker listener did not start")

    def close(self) -> None:
        self._stopped.set()
        if self._listener is not None:
            self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        for worker in self._workers:
            worker.join(timeout=5)
        self.path.unlink(missing_ok=True)

    def _serve(self) -> None:
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._listener = listener
        listener.bind(str(self.path))
        self.path.chmod(0o600)
        listener.listen(16)
        listener.settimeout(0.2)
        self._ready.set()
        while not self._stopped.is_set():
            try:
                connection, _address = listener.accept()
            except (OSError, socket.timeout):
                continue
            worker = threading.Thread(target=self._serve_one, args=(connection,), daemon=True)
            self._workers.append(worker)
            worker.start()

    def _serve_one(self, connection: socket.socket) -> None:
        payload = b""
        try:
            request = json.loads(receive_line(connection, failure="incomplete Board broker request"))
            response, payload = self._dispatch_request(connection, request)
        except Exception:
            response = {"status": "refused"}
        try:
            connection.sendall(canonical_bytes(response) + b"\n")
            if payload:
                if receive_line(connection, failure="incomplete Board broker binary acknowledgement") != "ready":
                    raise ValueError("Board broker binary transfer was not accepted")
                connection.sendall(payload)
        except (OSError, ValueError):
            pass
        finally:
            connection.close()

    def _dispatch_request(
        self,
        connection: socket.socket,
        request: dict[str, object],
    ) -> tuple[dict[str, object], bytes]:
        command = request.get("command")
        if command == "issue":
            binding = binding_from(request["binding"])
            peer = self._runtime._authority.peer_identity(connection)
            handle = self._runtime._authority.issue(binding, str(request["scope"]), peer)
            return {"status": "issued", "handle": handle}, b""
        if command == "execute":
            arguments = request.get("arguments")
            if not isinstance(arguments, dict):
                raise ValueError("Board broker arguments are invalid")
            result = self._runtime.execute(
                connection,
                str(request.get("handle", "")),
                BoardOperation(str(request["operation"])),
                **arguments,
            )
            return _result_response(result)
        if command == "revoke":
            self._runtime.revoke(str(request.get("handle", "")), "board-client-closed")
            return {"status": "revoked"}, b""
        if command == "compatibility":
            arguments = request.get("arguments")
            if not isinstance(arguments, dict):
                raise ValueError("Board compatibility arguments are invalid")
            result = self._runtime.execute_compatibility(
                BoardOperation(str(request["operation"])),
                arguments,
                authenticated=bool(request.get("authenticated", True)),
            )
            return _result_response(result)
        raise ValueError("unsupported Board broker command")


def _request_digest(operation: BoardOperation, arguments: dict[str, object]) -> str:
    safe = {
        name: hashlib.sha256(str(value).encode()).hexdigest() if name == "flag" else value
        for name, value in arguments.items()
    }
    return hashlib.sha256(canonical_bytes({"operation": operation.value, "arguments": safe})).hexdigest()


def _sanitize_public(value, redactor: Redactor):
    if isinstance(value, str):
        return redactor.redact(value).decode("utf-8", "replace")
    if isinstance(value, bytes):
        return redactor.redact(value)
    if dataclasses.is_dataclass(value):
        return type(value)(
            **{
                field.name: _sanitize_public(getattr(value, field.name), redactor)
                for field in dataclasses.fields(value)
            }
        )
    if isinstance(value, tuple):
        return tuple(_sanitize_public(item, redactor) for item in value)
    if isinstance(value, list):
        return [_sanitize_public(item, redactor) for item in value]
    if isinstance(value, dict):
        return {str(name): _sanitize_public(item, redactor) for name, item in value.items()}
    return value


def _refusal_outcome(refused: CapabilityRefused) -> BoardOutcome:
    return BoardOutcome.REVOKED if refused.reason == "revoked" else BoardOutcome.CAPABILITY_REFUSED


def _transport_failure(operation: BoardOperation, error: str) -> BoardOutcome:
    if operation in _EFFECTS:
        return BoardOutcome.EFFECT_INDETERMINATE
    return BoardOutcome.TIMEOUT if error == "TimeoutError" else BoardOutcome.UNREACHABLE


def _result_response(result: BoardBrokerResult) -> tuple[dict[str, object], bytes]:
    payload = b""
    if isinstance(result.value, DownloadValue):
        payload = result.value.content
        result = dataclasses.replace(result, value=dataclasses.replace(result.value, content=b""))
    response: dict[str, object] = {"status": "answered", "result": encode_result(result)}
    if payload:
        response["binary"] = {
            "bytes": len(payload),
            "digest": hashlib.sha256(payload).hexdigest(),
        }
    return response, payload


def _decode_ipc_result(response: dict[str, object]) -> BoardBrokerResult:
    document = response.get("result")
    if not isinstance(document, dict):
        raise ValueError("Board broker result is absent")
    result = decode_result(document)
    payload = response.get("_binary_payload")
    if payload is None:
        return result
    if not isinstance(payload, bytes) or not isinstance(result.value, DownloadValue) or result.value.content:
        raise ValueError("Board broker binary response does not match its typed result")
    return dataclasses.replace(result, value=dataclasses.replace(result.value, content=payload))


def _ipc_request(path: Path, request: dict[str, object]) -> dict[str, object]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(str(path))
        connection.sendall(canonical_bytes(request) + b"\n")
        response = json.loads(receive_line(connection, failure="incomplete Board broker response"))
        if not isinstance(response, dict):
            raise ValueError("Board broker response is not an object")
        binary = response.get("binary")
        if binary is not None:
            if (
                not isinstance(binary, dict)
                or set(binary) != {"bytes", "digest"}
                or not isinstance(binary["bytes"], int)
                or isinstance(binary["bytes"], bool)
                or not isinstance(binary["digest"], str)
            ):
                raise ValueError("Board broker binary response metadata is invalid")
            length = binary["bytes"]
            if length <= 0 or length > MAX_FETCH_BYTES:
                raise ValueError("Board broker binary response exceeds the download bound")
            connection.sendall(b"ready\n")
            payload = bytes(receive_exact(connection, length, failure="incomplete Board broker binary response"))
            if hashlib.sha256(payload).hexdigest() != binary["digest"]:
                raise ValueError("Board broker binary response digest disagrees")
            response["_binary_payload"] = payload
        return response
    finally:
        connection.close()


def local_peer_identity(connection: socket.socket):
    """Use full Linux identity in production and a uid-bound local identity in Darwin tests."""

    from solver.capability import LinuxPeerIdentity, PeerIdentity

    if hasattr(socket, "SO_PEERCRED") and Path("/proc").is_dir():
        return LinuxPeerIdentity()(connection)
    if not hasattr(socket, "LOCAL_PEERCRED"):
        raise PeerAuthenticationUnavailable("local peer credentials are unavailable")
    raw = connection.getsockopt(0, socket.LOCAL_PEERCRED, 12)
    uid = struct.unpack_from("I", raw, 4)[0]
    return PeerIdentity(uid, uid, uid, "local-peer", "darwin-local-socket")


def denied_probe(path: Path, fixture: bytes) -> bytes:
    """Exercise an actual refused request without exposing the supplied fixture."""

    response = _ipc_request(
        path,
        {
            "command": "execute",
            "handle": fixture.hex(),
            "operation": BoardOperation.INSTANCE_READ.value,
            "arguments": {"challenge_id": "probe"},
        },
    )
    return canonical_bytes(response)


__all__ = [
    "BoardBrokerClient",
    "BoardBrokerRuntime",
    "BoardBrokerService",
    "BoardCompatibilityClient",
    "MAX_SEALED_RESPONSE_BYTES",
    "denied_probe",
    "local_peer_identity",
]
