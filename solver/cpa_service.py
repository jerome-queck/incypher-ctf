"""Private authenticated IPC and process ownership for the CPA Harness."""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import multiprocessing
import os
import secrets
import select
import socket
import time
from pathlib import Path

from solver.board_broker import local_peer_identity
from solver.capability import PeerIdentity
from solver.cpa_contracts import (
    CPAConfig,
    CPAHarnessRecorded,
    CPAOutcome,
    CPAServiceRefused,
    CPAStatus,
    SERVICE_VERSION,
)
from solver.cpa_harness import CPAHarness
from solver.cpa_receipt import config_digest
from solver.event_store import EventStore
from solver.event_store_contracts import CapabilityRecord
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.lead_codec import decode_proposal, proposal_document, proposal_kind
from solver.lead_contracts import LeadRequest, MeasuredLeadTurn, TurnMeasure
from solver.local_ipc import receive_line
from solver.redaction import Redactor

CPA_CREDENTIAL_FD_ENV = "INCYPHER_CPA_CREDENTIAL_FD"
CPA_RESPONSES_URL_ENV = "INCYPHER_CPA_RESPONSES_URL"


def _current_peer() -> PeerIdentity:
    uid, gid = os.getuid(), os.getgid()
    if hasattr(socket, "SO_PEERCRED") and Path("/proc/self/stat").is_file():
        stat = Path("/proc/self/stat").read_text()
        suffix = stat[stat.rfind(")") + 2 :].split()
        return PeerIdentity(os.getpid(), uid, gid, suffix[19], Path("/proc/self/cgroup").read_text())
    return PeerIdentity(uid, uid, uid, "local-peer", "darwin-local-socket")


def _encode_outcome(outcome: CPAOutcome, peer_digest: str) -> dict[str, object]:
    turn = outcome.turn
    return {
        "version": SERVICE_VERSION,
        "status": outcome.status.value,
        "turn_count": outcome.turn_count,
        "tool_count": outcome.tool_count,
        "detail": outcome.detail,
        "audit": list(outcome.audit),
        "peer_digest": peer_digest,
        "turn": None
        if turn is None
        else {
            "approach": turn.approach,
            "proposal": proposal_document(turn.proposal),
            "measure": dataclasses.asdict(turn.measure),
        },
    }


def _decode_outcome(document: object) -> tuple[CPAOutcome, str]:
    if not isinstance(document, dict) or document.get("version") != SERVICE_VERSION:
        raise ValueError("CPA response version is invalid")
    status = CPAStatus(document["status"])
    turn_document = document.get("turn")
    turn = None
    if turn_document is not None:
        if not isinstance(turn_document, dict) or not isinstance(turn_document.get("measure"), dict):
            raise ValueError("CPA response turn is invalid")
        turn = MeasuredLeadTurn(
            str(turn_document["approach"]),
            decode_proposal(turn_document["proposal"]),
            TurnMeasure(**turn_document["measure"]),
        )
    return (
        CPAOutcome(
            status,
            int(document["turn_count"]),
            int(document["tool_count"]),
            turn,
            str(document.get("detail", "")),
            tuple(document.get("audit", ())),
        ),
        str(document["peer_digest"]),
    )


def _serve(endpoint, credential_channel, ready_channel, handle_digest, expected_peer, config, model, execute_tool):
    credential = credential_channel.recv()
    credential_channel.close()
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(endpoint))
    os.chmod(endpoint, 0o600)
    listener.listen(4)
    ready_channel.send("ready")
    ready_channel.close()
    harness = CPAHarness(config, model=model, credential=credential, execute_tool=execute_tool)
    try:
        while True:
            connection, _ = listener.accept()
            with connection:
                try:
                    peer = local_peer_identity(connection)
                    request = json.loads(receive_line(connection, failure="incomplete CPA request"))
                    if not isinstance(request, dict) or request.get("version") != SERVICE_VERSION:
                        outcome = CPAOutcome(
                            CPAStatus.REFUSED,
                            0,
                            0,
                            detail="service version mismatch",
                            audit=("denied:service-version",),
                        )
                    elif peer != expected_peer or not hmac.compare_digest(
                        str(request.get("handle", "")), handle_digest
                    ):
                        outcome = CPAOutcome(
                            CPAStatus.REFUSED, 0, 0, detail="capability refused", audit=("denied:capability",)
                        )
                    else:
                        outcome = harness.execute(str(request.get("prompt", "")))
                    connection.sendall(canonical_bytes(_encode_outcome(outcome, peer.digest)) + b"\n")
                except Exception:
                    connection.sendall(
                        canonical_bytes(_encode_outcome(CPAOutcome(CPAStatus.MALFORMED, 0, 0), expected_peer.digest))
                        + b"\n"
                    )
    finally:
        listener.close()


class CPAService:
    """Transfer one credential into one child and expose only authenticated Lead requests."""

    def __init__(
        self,
        *,
        endpoint: Path,
        config: CPAConfig,
        credential: str,
        model,
        execute_tool,
        state: Path,
        run_id: str,
        boot_id: str,
        redactor: Redactor,
        timestamp,
    ) -> None:
        if not isinstance(endpoint, Path) or not credential:
            raise CPAServiceRefused("CPA endpoint must be a private Unix socket and credential must be present")
        self.endpoint, self.config = endpoint, config
        self._credential = credential
        self._model, self._execute_tool = model, execute_tool
        self._store = EventStore(state, run_id=run_id, redactor=redactor)
        self._probe_evidence = _verified_cpa_custody(self._store, boot_id)
        self._timestamp = timestamp
        self._handle = secrets.token_hex(32)
        self._peer = _current_peer()
        self._process = None
        self._serial = sum(event.event_type == "cpa-harness.recorded" for event in self._store.events())
        self._reaped = False

    @property
    def credential_cleared(self) -> bool:
        return not self._credential

    def __enter__(self):
        if self._reaped or not self._credential:
            raise CPAServiceRefused("CPA credential cannot be replaced after transfer")
        self.endpoint.parent.mkdir(parents=True, mode=0o700)
        os.chmod(self.endpoint.parent, 0o700)
        context = multiprocessing.get_context("spawn")
        credential_read, credential_write = context.Pipe(duplex=False)
        ready_read, ready_write = context.Pipe(duplex=False)
        self._process = context.Process(
            target=_serve,
            args=(
                self.endpoint,
                credential_read,
                ready_write,
                hashlib.sha256(self._handle.encode()).hexdigest(),
                self._peer,
                self.config,
                self._model,
                self._execute_tool,
            ),
            daemon=True,
        )
        self._process.start()
        credential_read.close()
        ready_write.close()
        credential_write.send(self._credential)
        credential_write.close()
        self._credential = ""
        try:
            ready = ready_read.poll(5) and ready_read.recv() == "ready"
        except (EOFError, OSError):
            ready = False
        finally:
            ready_read.close()
        if not ready:
            self._stop()
            raise CPAServiceRefused("CPA child did not attest readiness")
        self._record(
            "attempt-secret-probe",
            "probe",
            CPAOutcome(CPAStatus.PROPOSED, 0, 0, detail=self._probe_evidence),
            self._peer.digest,
        )
        return self

    def __exit__(self, *_errors):
        self._stop()
        try:
            self.endpoint.unlink()
            self.endpoint.parent.rmdir()
        except OSError:
            pass
        self._record("teardown", "teardown", CPAOutcome(CPAStatus.PROPOSED, 0, 0, detail="reaped"), self._peer.digest)

    def execute(self, request: LeadRequest, *, deadline: float, cancel=None) -> CPAOutcome:
        request_id = f"{request.engagement_id}:turn-{request.turn_index:06d}"
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(str(self.endpoint))
        connection.sendall(
            canonical_bytes(
                {
                    "version": SERVICE_VERSION,
                    "handle": hashlib.sha256(self._handle.encode()).hexdigest(),
                    "request_id": request_id,
                    "prompt": request.context,
                }
            )
            + b"\n"
        )
        try:
            while True:
                if cancel is not None and cancel.is_set():
                    self._stop()
                    outcome = CPAOutcome(CPAStatus.CANCELLED, 0, 0, detail="cancelled and reaped")
                    self._record(request_id, "request", outcome, self._peer.digest)
                    return outcome
                if time.monotonic() >= deadline:
                    self._stop()
                    outcome = CPAOutcome(CPAStatus.TIMEOUT, 0, 0, detail="deadline reached and reaped")
                    self._record(request_id, "request", outcome, self._peer.digest)
                    return outcome
                readable, _, _ = select.select([connection], [], [], 0.01)
                if readable:
                    raw = receive_line(connection, failure="incomplete CPA response")
                    break
            outcome, peer_digest = _decode_outcome(json.loads(raw))
        except (EOFError, OSError, ValueError, json.JSONDecodeError):
            self._stop()
            outcome, peer_digest = (
                CPAOutcome(CPAStatus.CRASHED, 0, 0, detail="CPA child crashed and was reaped"),
                self._peer.digest,
            )
        finally:
            connection.close()
        record = "denial" if outcome.status is CPAStatus.REFUSED else "request"
        self._record(request_id, record, outcome, peer_digest)
        return outcome

    def denied_probe(self, fixture: str, *, version: str = SERVICE_VERSION) -> CPAOutcome:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.connect(str(self.endpoint))
        connection.sendall(
            canonical_bytes(
                {
                    "version": version,
                    "handle": hashlib.sha256(fixture.encode()).hexdigest(),
                    "request_id": "executor-probe",
                    "prompt": "probe",
                }
            )
            + b"\n"
        )
        outcome, peer = _decode_outcome(json.loads(receive_line(connection, failure="incomplete CPA probe")))
        connection.close()
        self._record("executor-probe", "probe", outcome, peer)
        return outcome

    def _record(self, request_id, record, outcome, peer_digest):
        self._serial += 1
        measure = outcome.turn.measure if outcome.turn is not None else None
        proposal = outcome.turn.proposal if outcome.turn is not None else None
        self._store.append(
            CPAHarnessRecorded(
                event_id=f"cpa:{self._serial:06d}",
                request_id=request_id,
                record=record,
                status=outcome.status.value,
                config_digest=config_digest(self.config),
                peer_digest=peer_digest,
                turn_count=outcome.turn_count,
                tool_count=outcome.tool_count,
                measured=measure is not None,
                model=measure.model if measure is not None else "",
                duration_ms=measure.duration_ms if measure is not None else 0,
                tokens_in=measure.tokens_in if measure is not None else 0,
                tokens_out=measure.tokens_out if measure is not None else 0,
                proposal_kind=proposal_kind(proposal) if proposal is not None else "",
                proposal_digest=digest_bytes(canonical_bytes(proposal_document(proposal)))
                if proposal is not None
                else "",
                detail=outcome.detail,
                ts=self._timestamp(),
            ),
            body=b"",
        )

    def _stop(self):
        if self._process is not None and not self._reaped:
            self._process.terminate()
            self._process.join(2)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(2)
            self._reaped = True


class CPALeadPort:
    def __init__(self, service: CPAService, *, turn_seconds: float = 30.0) -> None:
        self._service, self._turn_seconds = service, turn_seconds

    def __call__(self, request: LeadRequest):
        outcome = self._service.execute(request, deadline=time.monotonic() + self._turn_seconds)
        return outcome.turn if outcome.status is CPAStatus.PROPOSED else outcome


def _verified_cpa_custody(store: EventStore, boot_id: str) -> str:
    events = [event.payload for event in store.events() if event.event_type == "capability-custody.recorded"]
    transfers = [
        item
        for item in events
        if item["record"] == CapabilityRecord.TRANSFER_ACCEPTED.value
        and item["boot_id"] == boot_id
        and item["broker"] == "cpa"
        and "CPA_TOKEN" in item["secret_names"]
    ]
    probes = {
        item["probe_kind"]: item
        for item in events
        if item["record"] == CapabilityRecord.PROBE_RECORDED.value
        and item["boot_id"] == boot_id
        and item["probe_kind"] in {"memory", "environment", "argv", "file", "event"}
    }
    if len(transfers) != 1 or set(probes) != {"memory", "environment", "argv", "file", "event"}:
        raise CPAServiceRefused("CPA broker custody and hostile executor proof are incomplete")
    digests = {item["evidence_digest"] for item in probes.values()}
    if any(item["probe_result"] != "clear" for item in probes.values()) or len(digests) != 1:
        raise CPAServiceRefused("CPA hostile executor proof did not attest every surface clear")
    return digests.pop()


__all__ = ["CPA_CREDENTIAL_FD_ENV", "CPA_RESPONSES_URL_ENV", "CPALeadPort", "CPAService"]
