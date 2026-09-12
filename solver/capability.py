"""Opaque, identity-bound broker capabilities with canonical decisions.

The caller sends one handle and no claimed identity.  Trusted control binds the handle once; every
request derives the kernel peer again and checks the durable Work generation before it grants the
fixed scope.  Domain brokers decide what that scope does in their own tickets.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import stat
import struct
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from solver.capability_event_contracts import HandleDecision, PeerAuthenticationFailure
from solver.event_store import EventStore
from solver.event_store_contracts import CapabilityCustodyRecorded, CapabilityRecord
from solver.event_store_storage import canonical_bytes
from solver.redaction import Redactor
from solver.work_generation import GenerationFence

MAX_REQUEST_BYTES = 4096
REFUSAL = "capability refused"


class CapabilityRefused(PermissionError):
    """One non-oracular refusal for every handle and caller failure."""

    def __init__(self, reason: str = "capability-refused") -> None:
        self.reason = reason
        super().__init__(REFUSAL)


class PeerAuthenticationUnavailable(RuntimeError):
    """The runtime cannot derive the full Linux peer identity this profile requires."""


@dataclass(frozen=True)
class PeerIdentity:
    """Kernel-derived process identity pinned against PID reuse."""

    pid: int
    uid: int
    gid: int
    started: str
    cgroup: str

    @property
    def digest(self) -> str:
        return _digest(
            {
                "pid": self.pid,
                "uid": self.uid,
                "gid": self.gid,
                "started": self.started,
                "cgroup": self.cgroup,
            }
        )


class LinuxPeerIdentity:
    """Read SO_PEERCRED and process-generation evidence for each accepted connection."""

    def __call__(self, connection: socket.socket) -> PeerIdentity:
        if not hasattr(socket, "SO_PEERCRED") or not Path("/proc").is_dir():
            raise PeerAuthenticationUnavailable("the strict capability profile requires Linux SO_PEERCRED and procfs")
        raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", raw)
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            cgroup = Path(f"/proc/{pid}/cgroup").read_text()
        except OSError as error:
            raise PeerAuthenticationUnavailable("the capability peer disappeared during authentication") from error
        # comm can contain spaces and parentheses. Everything after its final ')' begins at field 3;
        # starttime is field 22, hence index 19 in that suffix.
        suffix = stat[stat.rfind(")") + 2 :].split()
        if len(suffix) <= 19:
            raise PeerAuthenticationUnavailable("the capability peer has no stable process start identity")
        return PeerIdentity(pid=pid, uid=uid, gid=gid, started=suffix[19], cgroup=cgroup)


def local_peer_identity(connection: socket.socket) -> PeerIdentity:
    """Use full Linux identity in production and a uid-bound local identity in Darwin tests."""

    if hasattr(socket, "SO_PEERCRED") and Path("/proc").is_dir():
        return LinuxPeerIdentity()(connection)
    if not hasattr(socket, "LOCAL_PEERCRED"):
        raise PeerAuthenticationUnavailable("local peer credentials are unavailable")
    raw = connection.getsockopt(0, socket.LOCAL_PEERCRED, 12)
    uid = struct.unpack_from("I", raw, 4)[0]
    return PeerIdentity(uid, uid, uid, "local-peer", "darwin-local-socket")


@dataclass(frozen=True)
class CapabilityBinding:
    """Trusted execution identity attached to a handle, never supplied in a request."""

    run_id: str
    boot_id: str
    generation_id: str
    lane_id: str
    attempt_id: str
    step_id: str

    @property
    def digest(self) -> str:
        return _digest(self.document())

    def document(self) -> dict[str, str]:
        return {
            "run_id": self.run_id,
            "boot_id": self.boot_id,
            "generation_id": self.generation_id,
            "lane_id": self.lane_id,
            "attempt_id": self.attempt_id,
            "step_id": self.step_id,
        }


@dataclass(frozen=True)
class CapabilityGrant:
    binding: CapabilityBinding
    scope: str


@dataclass
class _IssuedCapability:
    handle_digest: str
    binding: CapabilityBinding
    scope: str
    peer: PeerIdentity
    revoked: bool = False


class CapabilityAuthority:
    """Issue, verify, and revoke opaque handles through one canonical interface."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        boot_id: str,
        redactor: Redactor,
        peer_identity: Callable[[object], PeerIdentity] | None = None,
        token_bytes: Callable[[int], bytes] = os.urandom,
        timestamp: Callable[[], str],
    ) -> None:
        self._run_id = run_id
        self._boot_id = boot_id
        self._store = EventStore(state, run_id=run_id, redactor=redactor)
        self._generations = GenerationFence(state, run_id, redactor, timestamp)
        self._peer_identity = peer_identity or LinuxPeerIdentity()
        self._token_bytes = token_bytes
        self._timestamp = timestamp
        self._issued: dict[str, _IssuedCapability] = {}
        self._lock = threading.RLock()
        self._audit_serial = sum(event.event_type == "capability-custody.recorded" for event in self._store.events())

    def issue(self, binding: CapabilityBinding, scope: str, peer: PeerIdentity) -> str:
        """Bind one unguessable handle to trusted identity and one fixed broker scope."""

        with self._lock:
            if binding.run_id != self._run_id or binding.boot_id != self._boot_id:
                raise ValueError("capability binding belongs to another Run or Boot")
            if not all(binding.document().values()) or not scope:
                raise ValueError("capability binding and scope must be complete")
            if not self._is_current(binding.generation_id):
                raise CapabilityRefused("stale-generation")
            while True:
                handle = base64.urlsafe_b64encode(self._token_bytes(32)).rstrip(b"=").decode("ascii")
                if handle and handle not in self._issued:
                    break
            issued = _IssuedCapability(_handle_digest(handle), binding, scope, peer)
            self._issued[handle] = issued
            self._record(CapabilityRecord.ISSUED, issued, peer)
            return handle

    def peer_identity(self, connection: object) -> PeerIdentity:
        """Derive trusted caller identity for one IPC request."""

        return self._peer_identity(connection)

    def authorize(
        self,
        connection: object,
        handle: str,
        *,
        peer: PeerIdentity | None = None,
    ) -> CapabilityGrant:
        """Derive the peer and current generation afresh, or reveal only one refusal."""

        peer = peer or self.peer_identity(connection)
        with self._lock:
            issued = self._issued.get(handle)
            if issued is None:
                self._record_denied(_handle_digest(handle), peer, "unknown-handle")
                raise CapabilityRefused("unknown-handle")
            reason = ""
            if issued.revoked:
                reason = "revoked"
            elif peer != issued.peer:
                reason = "peer-mismatch"
            elif issued.binding.boot_id != self._boot_id:
                reason = "stale-boot"
            elif not self._is_current(issued.binding.generation_id):
                reason = "stale-generation"
            if reason:
                self._record(CapabilityRecord.DENIED, issued, peer, reason=reason)
                raise CapabilityRefused(reason)
            self._record(CapabilityRecord.AUTHORIZED, issued, peer)
            return CapabilityGrant(issued.binding, issued.scope)

    def revoke(self, handle: str, reason: str) -> None:
        """Durably revoke one known handle; repeated equal revocation is idempotent."""

        with self._lock:
            issued = self._issued.get(handle)
            if issued is None or not reason:
                raise CapabilityRefused("unknown-handle")
            if issued.revoked:
                return
            issued.revoked = True
            self._record(CapabilityRecord.REVOKED, issued, issued.peer, reason=reason)

    def reconcile_restart(self, scope: str) -> tuple[str, ...]:
        """Durably revoke prior-Boot handles before replacement authority is issued."""

        with self._lock:
            latest: dict[str, dict[str, object]] = {}
            for event in self._store.events():
                if event.event_type != "capability-custody.recorded":
                    continue
                payload = event.payload
                if payload.get("scope") == scope and payload.get("handle_digest"):
                    latest[str(payload["handle_digest"])] = payload
            stale = [
                payload
                for payload in latest.values()
                if payload.get("boot_id") != self._boot_id
                and payload.get("record") not in {CapabilityRecord.REVOKED.value, CapabilityRecord.DENIED.value}
            ]
            for payload in stale:
                self._audit_serial += 1
                self._store.append(
                    CapabilityCustodyRecorded(
                        event_id=self._event_id(),
                        fact=HandleDecision(
                            record=CapabilityRecord.REVOKED,
                            handle_digest=str(payload["handle_digest"]),
                            run_id=str(payload["run_id"]),
                            boot_id=str(payload["boot_id"]),
                            generation_id=str(payload["generation_id"]),
                            lane_id=str(payload["lane_id"]),
                            attempt_id=str(payload["attempt_id"]),
                            step_id=str(payload["step_id"]),
                            scope=scope,
                            peer_uid=int(payload["peer_uid"]),
                            peer_identity_digest=str(payload["peer_identity_digest"]),
                            reason="restart-reconciled",
                        ),
                        ts=self._timestamp(),
                    ),
                    body=b"",
                )
            return tuple(str(payload["generation_id"]) for payload in stale)

    def _record_denied(self, handle_digest: str, peer: PeerIdentity, reason: str) -> None:
        self._audit_serial += 1
        self._store.append(
            CapabilityCustodyRecorded(
                event_id=self._event_id(),
                fact=HandleDecision(
                    record=CapabilityRecord.DENIED,
                    handle_digest=handle_digest,
                    run_id="",
                    boot_id="",
                    generation_id="",
                    lane_id="",
                    attempt_id="",
                    step_id="",
                    scope="",
                    peer_uid=peer.uid,
                    peer_identity_digest=peer.digest,
                    reason=reason,
                ),
                ts=self._timestamp(),
            ),
            body=b"",
        )

    def _record(
        self,
        record: CapabilityRecord,
        issued: _IssuedCapability,
        peer: PeerIdentity,
        *,
        reason: str = "",
    ) -> None:
        self._audit_serial += 1
        binding = issued.binding
        self._store.append(
            CapabilityCustodyRecorded(
                event_id=self._event_id(),
                fact=HandleDecision(
                    record=record,
                    handle_digest=issued.handle_digest,
                    run_id=binding.run_id,
                    boot_id=binding.boot_id,
                    generation_id=binding.generation_id,
                    lane_id=binding.lane_id,
                    attempt_id=binding.attempt_id,
                    step_id=binding.step_id,
                    scope=issued.scope,
                    peer_uid=peer.uid,
                    peer_identity_digest=peer.digest,
                    reason=reason,
                ),
                ts=self._timestamp(),
            ),
            body=b"",
        )

    def _event_id(self) -> str:
        return f"capability:{self._boot_id}:{self._audit_serial:06d}"

    def record_peer_authentication_failure(self) -> None:
        """Durably record a sanitized failure when no trusted peer can be derived."""

        with self._lock:
            self._audit_serial += 1
            self._store.append(
                CapabilityCustodyRecorded(
                    event_id=self._event_id(),
                    fact=PeerAuthenticationFailure("peer-authentication-unavailable"),
                    ts=self._timestamp(),
                ),
                body=b"",
            )

    def _is_current(self, generation_id: str) -> bool:
        return any(
            state.generation_id == generation_id for state in self._generations.projection().active_by_work.values()
        )


class CapabilityServer:
    """One bounded JSON request over an already accepted pathname Unix socket."""

    def __init__(self, authority: CapabilityAuthority) -> None:
        self._authority = authority

    def serve_request(self, connection: socket.socket) -> None:
        response = {"status": "refused"}
        try:
            peer = self._authority.peer_identity(connection)
            raw = _read_line(connection)
            request = json.loads(raw)
            if not isinstance(request, dict) or set(request) != {"handle"} or not isinstance(request["handle"], str):
                raise ValueError("invalid capability request")
            self._authority.authorize(connection, request["handle"], peer=peer)
            response = {"status": "granted"}
        except PeerAuthenticationUnavailable:
            self._authority.record_peer_authentication_failure()
        except (CapabilityRefused, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            pass
        connection.sendall(canonical_bytes(response) + b"\n")


class CapabilityListener:
    """A pathname Unix listener rooted in controller-only storage."""

    def __init__(self, path: Path, server: CapabilityServer) -> None:
        self.path = Path(path)
        self._server = server
        self._socket: socket.socket | None = None
        self._inode: int | None = None

    def __enter__(self) -> CapabilityListener:
        parent = self.path.parent
        mode = parent.stat().st_mode
        if not parent.is_dir() or mode & 0o077 or parent.stat().st_uid != os.getuid():
            raise PermissionError("capability socket directory is not controller-only")
        if self.path.exists() or self.path.is_symlink():
            raise FileExistsError(f"capability socket already exists: {self.path.name}")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(str(self.path))
            os.chmod(self.path, 0o600)
            listener.listen(16)
            listener.settimeout(0.2)
            self._inode = self.path.stat().st_ino
            self._socket = listener
        except Exception:
            listener.close()
            raise
        return self

    def __exit__(self, _error_type, _error, _traceback) -> None:
        self.close()

    def serve_once(self) -> None:
        listener = self._socket
        if listener is None:
            raise RuntimeError("capability listener is not open")
        connection, _address = listener.accept()
        try:
            self._server.serve_request(connection)
        finally:
            connection.close()

    def close(self) -> None:
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        try:
            current = self.path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISSOCK(current.st_mode) and current.st_ino == self._inode:
            self.path.unlink()


def _read_line(connection: socket.socket) -> bytes:
    held = bytearray()
    while len(held) <= MAX_REQUEST_BYTES:
        chunk = connection.recv(min(1024, MAX_REQUEST_BYTES + 1 - len(held)))
        if not chunk:
            break
        held.extend(chunk)
        if b"\n" in chunk:
            break
    if not held.endswith(b"\n") or len(held) > MAX_REQUEST_BYTES:
        raise ValueError("invalid capability request framing")
    return bytes(held[:-1])


def _handle_digest(handle: str) -> str:
    return hashlib.sha256(b"incypher-capability-handle\0" + handle.encode("utf-8", "replace")).hexdigest()


def _digest(document: object) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


__all__ = [
    "CapabilityAuthority",
    "CapabilityBinding",
    "CapabilityGrant",
    "CapabilityListener",
    "CapabilityRefused",
    "CapabilityServer",
    "LinuxPeerIdentity",
    "PeerAuthenticationUnavailable",
    "PeerIdentity",
]
