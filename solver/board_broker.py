"""One deep runtime for typed, generation-scoped Board authority."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import socket
import threading
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from solver.board_profile import ProfileCycle, ProfileDecision, ProfileDocument

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
    IntakeReadValue,
    PRIVATE_BOARD_RESPONSE_CLASS,
    ReadContractValue,
    ScoreboardValue,
    binding_document,
    binding_from,
    decode_result,
    encode_result,
)
from solver.board_profile_phase import ProfilePhaseWriter
from solver.board_profile_contracts import BoardProfileObservationRecorded
from solver.intake_evidence import IntakeEvidenceWriter
from solver.capability import (
    CapabilityAuthority,
    CapabilityBinding,
    CapabilityRefused,
    PeerAuthenticationUnavailable,
    local_peer_identity,
)
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.local_ipc import receive_exact, receive_line
from solver.redaction import Redactor

MAX_SEALED_RESPONSE_BYTES = 64 * 1024

_SCOPE = {
    BoardOperation.INTAKE_READ: "board.intake",
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
    response_sanitized_bytes: int
    response_truncated: bool
    response_lost_bytes: int
    raw_blob_digest: str
    raw_blob_bytes: int
    raw_blob_class: str
    profile_digest: str
    response_content_type: str
    response_location: str
    redaction_policy_digest: str
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
        fetch_bytes: int = MAX_FETCH_BYTES,
        profile_required: bool = False,
        profile_handle: str = "",
    ) -> None:
        if not token or not boot_id or sealed_response_bytes <= 0:
            raise ValueError("Board broker needs a token, Boot identity and response bound")
        self._state = Path(state)
        self._run_id = run_id
        self._boot_id = boot_id
        self._authority = authority
        self._timestamp = timestamp
        self._redactor = Redactor(
            {name: value for name, value in {"CTFD_API_TOKEN": token, "TEAM_KEY": team_key}.items() if value}
        )
        self._store = EventStore(state, run_id=run_id, redactor=self._redactor)
        self._fetch_bytes = fetch_bytes
        self._intake_evidence = IntakeEvidenceWriter(state, run_id, max_response_bytes=fetch_bytes + 1)
        self._serial = sum(event.event_type == "board-broker.recorded" for event in self._store.events())
        self._sealed_response_bytes = sealed_response_bytes
        self._wire_local = threading.local()
        self._serial_lock = threading.Lock()
        self._profile_lock = threading.Lock()
        self._profile_phase = ProfilePhaseWriter(self._state, run_id, self._redactor, timestamp)
        self._profile_required = profile_required
        self._profile_handle_digest = hashlib.sha256(profile_handle.encode()).digest() if profile_handle else b""
        self._profile_peer_digest = ""
        self._profile_session_opened = False
        self._profile_receipt_digest = ""

        bounded_transport = transport or network_transport(fetch_bytes)

        def observed(request):
            wire = self._wire()
            parsed = urllib.parse.urlsplit(request.full_url)
            endpoint = parsed.path + (f"?{parsed.query}" if parsed.query else "")
            try:
                result = bounded_transport(request)
            except OSError as error:
                wire.append((0, b"", endpoint, type(error).__name__, "", ""))
                raise
            if len(result) == 3:
                status, raw, location = result
                content_type = ""
            else:
                status, raw, location, content_type = result
            wire.append((status, raw, endpoint, "", content_type, location))
            return status, raw, location, content_type

        self._board = Board(url, token, observed, fetch_bytes=fetch_bytes)
        self._public_board = Board(url, "", observed, fetch_bytes=fetch_bytes)

    def qualify_profile(self, connection: object, profile_handle: str, rules, rules_source: str) -> ProfileDecision:
        """Run one Boot-owned, peer-authenticated profile probe behind credential custody."""

        from solver.board_profile import ProfileProbe, decision_from_document, qualify, rules_document
        from solver.board_profile_receipt import verify_receipt as verify_profile_receipt
        from solver.board_profile_receipt import write_receipt as write_profile_receipt

        peer = self._authority.peer_identity(connection)
        rules_digest = hashlib.sha256(canonical_bytes(rules_document(rules))).hexdigest()
        with self._profile_lock:
            self._authorize_profile(profile_handle, peer)
            phase = self._profile_phase.state()
            if phase.decision:
                if phase.rules_digest != rules_digest:
                    raise ValueError("canonical Board profile belongs to different tracked Rules")
                receipt_path = self._state / "runs" / self._run_id / "canonical" / "board-profile.receipt.json"
                if hashlib.sha256(receipt_path.read_bytes()).hexdigest() != phase.receipt_digest:
                    raise ValueError("canonical Board-profile receipt digest disagrees with its decision")
                receipt_path = verify_profile_receipt(receipt_path)
                self._profile_receipt_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
                receipt = json.loads(receipt_path.read_text())
                if receipt["rules"]["source"] != rules_source:
                    raise ValueError("canonical Board profile belongs to a different Rules source")
                return decision_from_document(receipt["decision"], rules)
            probe_id = self._profile_phase.next_probe_id()
            self._profile_phase.start(probe_id, rules_digest, peer.digest)
            probe = ProfileProbe(
                probe_id,
                rules_source,
                tuple(self._profile_cycle(probe_id, index) for index in (1, 2)),
            )
            decision = qualify(probe, rules)
            receipt_path = write_profile_receipt(self._state, self._run_id, probe, rules, decision)
            self._profile_phase.decide(
                probe_id,
                rules_digest,
                decision.authoritative,
                hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            )
            self._profile_receipt_digest = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            return decision

    def open_profiled_operations(self, connection: object, profile_handle: str) -> None:
        peer = self._authority.peer_identity(connection)
        with self._profile_lock:
            self._authorize_profile(profile_handle, peer)
            self._profile_phase.open_operations()
            if not self._profile_receipt_digest:
                self._profile_receipt_digest = self._profile_phase.state().receipt_digest
            self._profile_session_opened = True

    def _authorize_profile(self, profile_handle: str, peer) -> None:
        supplied = hashlib.sha256(profile_handle.encode()).digest()
        if not self._profile_handle_digest or not hmac.compare_digest(supplied, self._profile_handle_digest):
            raise PermissionError("Board-profile authority refused")
        phase = self._profile_phase.state()
        expected = self._profile_peer_digest or (phase.peer_identity_digest if not phase.decision else "")
        if expected and expected != peer.digest:
            raise PermissionError("Board-profile authority refused")
        self._profile_peer_digest = peer.digest

    def _profile_cycle(self, probe_id: str, cycle: int) -> ProfileCycle:
        from solver.board_profile import collect_cycle

        def reader(board):
            def read(request_id, endpoint, json_content_type):
                document = self._profile_document(
                    board,
                    request_id,
                    endpoint,
                    json_content_type=json_content_type,
                )
                name = request_id.rsplit(":", 1)[-1].replace("-", "_")
                self._record_profile_observation(probe_id, cycle, name, document)
                return document

            return read

        return collect_cycle(probe_id, cycle, reader(self._board), reader(self._public_board))

    def _profile_document(
        self,
        board: Board,
        request_id: str,
        endpoint: str,
        *,
        json_content_type: bool = True,
    ) -> ProfileDocument:
        from solver.board_profile import ProfileDocument

        try:
            answer = board.inspect("GET", endpoint, json_content_type=json_content_type)
        except OSError:
            return ProfileDocument(request_id, endpoint, 0, "", b"")
        body = self._redactor.redact(answer.body)
        if len(body) > self._sealed_response_bytes:
            return ProfileDocument(
                request_id,
                endpoint,
                answer.status,
                answer.content_type,
                body[: self._sealed_response_bytes],
                len(body),
                False,
            )
        return ProfileDocument(request_id, endpoint, answer.status, answer.content_type, body, len(body), True)

    def _record_profile_observation(
        self,
        probe_id: str,
        cycle: int,
        document_name: str,
        document: ProfileDocument,
    ) -> None:
        self._store.append(
            BoardProfileObservationRecorded(
                event_id=f"board-profile-observation:{probe_id}:{cycle}:{document_name}",
                probe_id=probe_id,
                cycle=cycle,
                document_name=document_name,
                request_id=document.request_id,
                endpoint=document.endpoint,
                http_status=document.status,
                content_type=document.content_type,
                original_bytes=document.original_bytes,
                complete=document.complete,
                ts=self._timestamp(),
            ),
            body=document.body,
        )

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
        if not self._profile_allows_operations():
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
        status, raw, endpoint, transport_error, content_type, location = wire[-1] if wire else (0, b"", "", "", "", "")
        if transport_error:
            outcome, value = _transport_failure(operation, transport_error), None
        if operation is BoardOperation.INTAKE_READ and len(raw) > self._fetch_bytes:
            outcome, value = BoardOutcome.TOO_LARGE, None
        if status == 401:
            outcome, value = BoardOutcome.AUTH_FAILURE, None
        try:
            self._authority.authorize(connection, handle, peer=peer)
        except CapabilityRefused as refused:
            outcome, value = _refusal_outcome(refused), None
        except (OSError, RuntimeError, ValueError):
            outcome, value = BoardOutcome.RESERVATION_REFUSED, None
        classified_event_id = self._next_id()
        sanitized, redaction_proof = self._redactor.redact_with_proof(raw)
        raw_blob_digest = (
            self._intake_evidence.seal(
                raw,
                classified_event_id=classified_event_id,
                sanitized=sanitized,
                redaction_proof=redaction_proof,
            )
            if operation is BoardOperation.INTAKE_READ
            else ""
        )
        sealed = sanitized if operation is BoardOperation.INTAKE_READ else sanitized[: self._sealed_response_bytes]
        original_bytes = len(raw)
        lost_bytes = max(0, len(sanitized) - len(sealed))
        classified = None
        try:
            classified = self._store.append(
                self._record(
                    event_id=classified_event_id,
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
                    response_digest=hashlib.sha256(raw).hexdigest(),
                    response_original_bytes=original_bytes,
                    response_sanitized_bytes=len(sanitized),
                    response_truncated=bool(lost_bytes),
                    response_lost_bytes=lost_bytes,
                    raw_blob_digest=raw_blob_digest,
                    raw_blob_bytes=original_bytes if raw_blob_digest else 0,
                    raw_blob_class=PRIVATE_BOARD_RESPONSE_CLASS if raw_blob_digest else "",
                    response_content_type=content_type,
                    response_location=location,
                    redaction_policy_digest=(
                        self._redactor.policy_digest if operation is BoardOperation.INTAKE_READ else ""
                    ),
                ),
                body=sealed,
            )
            if raw_blob_digest:
                self._intake_evidence.commit(
                    classified_event_id,
                    event_sequence=classified.sequence,
                    event_digest=classified.event_digest,
                )
        except (OSError, RuntimeError, ValueError):
            if raw_blob_digest and classified is None:
                self._intake_evidence.abort(classified_event_id)
            outcome = BoardOutcome.EFFECT_INDETERMINATE if operation in _EFFECTS else BoardOutcome.RESERVATION_REFUSED
            return BoardBrokerResult(operation, outcome, request_id=request_id)
        provenance = BoardProvenance(
            endpoint,
            status,
            hashlib.sha256(raw).hexdigest(),
            original_bytes,
            bool(lost_bytes),
            lost_bytes,
            raw_blob_digest,
            original_bytes if raw_blob_digest else 0,
            PRIVATE_BOARD_RESPONSE_CLASS if raw_blob_digest else "",
            classified_event_id,
            grant.binding.digest,
            peer.digest,
            request_digest,
            self._profile_receipt_digest,
            content_type,
            location,
            hashlib.sha256(sealed).hexdigest(),
            self._redactor.policy_digest,
        )
        typed = (
            IntakeReadValue(raw, location)
            if operation is BoardOperation.INTAKE_READ
            else self._typed(operation, value)
            if outcome is BoardOutcome.ANSWERED
            else None
        )
        return BoardBrokerResult(operation, outcome, typed, provenance, request_id)

    def issue_intake(
        self,
        connection: object,
        profile_handle: str,
        binding: CapabilityBinding,
    ) -> tuple[str, str, str]:
        """Mint the profile- and peer-bound controller Intake capability."""

        peer = self._authority.peer_identity(connection)
        with self._profile_lock:
            self._authorize_profile(profile_handle, peer)
            if not self._profile_session_opened or not self._profile_receipt_digest:
                raise PermissionError("Board-profile authority is not open for Intake")
            handle = self._authority.issue(binding, "board.intake", peer)
            return handle, peer.digest, self._profile_receipt_digest

    def execute_compatibility(
        self,
        operation: BoardOperation,
        arguments: dict[str, object],
        *,
        authenticated: bool,
    ) -> BoardBrokerResult:
        if not self._profile_allows_operations():
            return BoardBrokerResult(operation, BoardOutcome.CAPABILITY_REFUSED)
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
        status, raw, endpoint, error, content_type, location = wire[-1] if wire else (0, b"", "", "", "", "")
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
            "",
            0,
            "",
            "",
            "",
            "",
            "",
            "",
            content_type,
            location,
            hashlib.sha256(sanitized[: self._sealed_response_bytes]).hexdigest(),
            "",
        )
        return BoardBrokerResult(
            operation,
            outcome,
            self._typed(operation, value) if outcome is BoardOutcome.ANSWERED else None,
            provenance,
        )

    def _profile_allows_operations(self) -> bool:
        if self._profile_session_opened:
            return True
        if self._profile_phase.state().probe_id:
            return False
        return not self._profile_required

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
        if operation is BoardOperation.INTAKE_READ:
            return board.inspect("GET", str(arguments["path"]))
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

    def _wire(self) -> list[tuple[int, bytes, str, str, str, str]]:
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
            profile_digest=self._profile_receipt_digest,
            ts=self._timestamp(),
            **fields,
        )

    def _typed(self, operation: BoardOperation, value) -> object:
        if operation is BoardOperation.INTAKE_READ:
            return IntakeReadValue(value.body, value.location)
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
        if scope == "board.intake":
            raise CapabilityRefused()
        response = _ipc_request(
            Path(socket_path),
            {"command": "issue", "binding": binding_document(binding), "scope": scope},
        )
        if response.get("status") != "issued" or not isinstance(response.get("handle"), str):
            raise CapabilityRefused()
        return cls(Path(socket_path), str(response["handle"]))

    @classmethod
    def open_intake(
        cls,
        socket_path: Path,
        binding: CapabilityBinding,
        profile_handle: str,
    ) -> tuple[BoardBrokerClient, str, str]:
        response = _ipc_request(
            Path(socket_path),
            {
                "command": "issue-intake",
                "binding": binding_document(binding),
                "profile_handle": profile_handle,
            },
        )
        if (
            response.get("status") != "issued"
            or not isinstance(response.get("handle"), str)
            or not isinstance(response.get("controller_peer_digest"), str)
            or not isinstance(response.get("profile_digest"), str)
        ):
            raise CapabilityRefused()
        return (
            cls(Path(socket_path), str(response["handle"])),
            str(response["controller_peer_digest"]),
            str(response["profile_digest"]),
        )

    def close(self) -> None:
        if self._closed:
            return
        _ipc_request(self._socket_path, {"command": "revoke", "handle": self._handle})
        self._closed = True

    def read_contract(self) -> BoardBrokerResult:
        return self._execute(BoardOperation.READ_CONTRACT)

    def intake_read(self, path: str) -> BoardBrokerResult:
        return self._execute(BoardOperation.INTAKE_READ, path=path)

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


class BoardProfileClient:
    """Boot-owned profile client; no Work-generation identity is fabricated."""

    def __init__(self, socket_path: Path, profile_handle: str) -> None:
        self._socket_path = Path(socket_path)
        self._profile_handle = profile_handle

    def qualify(self, rules, rules_source: str) -> ProfileDecision:
        from solver.board_profile import decision_from_document, rules_document

        response = _ipc_request(
            self._socket_path,
            {
                "command": "profile-probe",
                "profile_handle": self._profile_handle,
                "rules": rules_document(rules),
                "rules_source": rules_source,
            },
        )
        decision = response.get("decision")
        if not isinstance(decision, dict):
            raise BoardFailure("Board-profile broker refused the probe")
        return decision_from_document(decision, rules)

    def open_operations(self) -> None:
        response = _ipc_request(
            self._socket_path,
            {"command": "open-profiled-operations", "profile_handle": self._profile_handle},
        )
        if response.get("status") != "opened":
            raise BoardFailure("Board-profile operation gate remained closed")


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
            if request.get("scope") == "board.intake":
                raise CapabilityRefused()
            binding = binding_from(request["binding"])
            peer = self._runtime._authority.peer_identity(connection)
            handle = self._runtime._authority.issue(binding, str(request["scope"]), peer)
            return {"status": "issued", "handle": handle}, b""
        if command == "issue-intake":
            binding = binding_from(request["binding"])
            handle, peer_digest, profile_digest = self._runtime.issue_intake(
                connection,
                str(request.get("profile_handle", "")),
                binding,
            )
            return {
                "status": "issued",
                "handle": handle,
                "controller_peer_digest": peer_digest,
                "profile_digest": profile_digest,
            }, b""
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
        if command == "profile-probe":
            from solver.board_profile import decision_document, rules_from_document

            rules = request.get("rules")
            if not isinstance(rules, dict):
                raise ValueError("Board-profile rules are absent")
            parsed_rules = rules_from_document(rules)
            decision = self._runtime.qualify_profile(
                connection,
                str(request.get("profile_handle", "")),
                parsed_rules,
                str(request.get("rules_source", "")),
            )
            return {"status": "decided", "decision": decision_document(decision)}, b""
        if command == "open-profiled-operations":
            self._runtime.open_profiled_operations(connection, str(request.get("profile_handle", "")))
            return {"status": "opened"}, b""
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
    elif isinstance(result.value, IntakeReadValue):
        payload = result.value.body
        result = dataclasses.replace(result, value=dataclasses.replace(result.value, body=b""))
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
    if not isinstance(payload, bytes) or not isinstance(result.value, (DownloadValue, IntakeReadValue)):
        raise ValueError("Board broker binary response does not match its typed result")
    if isinstance(result.value, DownloadValue):
        if result.value.content:
            raise ValueError("Board broker binary response duplicates its typed payload")
        return dataclasses.replace(result, value=dataclasses.replace(result.value, content=payload))
    if result.value.body:
        raise ValueError("Board broker binary response duplicates its typed payload")
    return dataclasses.replace(result, value=dataclasses.replace(result.value, body=payload))


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
            operation = request.get("operation") if request.get("command") == "execute" else None
            binary_limit = MAX_FETCH_BYTES + 1 if operation == BoardOperation.INTAKE_READ.value else MAX_FETCH_BYTES
            if length <= 0 or length > binary_limit:
                raise ValueError("Board broker binary response exceeds the download bound")
            connection.sendall(b"ready\n")
            payload = bytes(receive_exact(connection, length, failure="incomplete Board broker binary response"))
            if hashlib.sha256(payload).hexdigest() != binary["digest"]:
                raise ValueError("Board broker binary response digest disagrees")
            response["_binary_payload"] = payload
        return response
    finally:
        connection.close()


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
    "BoardProfileClient",
    "MAX_SEALED_RESPONSE_BYTES",
    "denied_probe",
    "local_peer_identity",
]
