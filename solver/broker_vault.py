"""Kernel-authenticated child process retaining one broker owner's bytes."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

from solver.broker_contracts import Broker, BrokerReceipt, transfer_digest
from solver.credentials import CHILD_ENVIRONMENT
from solver.event_store_storage import canonical_bytes
from solver.local_ipc import receive_line


class BrokerVaultProcess:
    """A sanitized child that acknowledges and exclusively retains one owner's bytes."""

    def __init__(self, owner: Broker, process: subprocess.Popen[bytes], channel: socket.socket, runtime: Path) -> None:
        self.owner = Broker(owner)
        self._process = process
        self._channel = channel
        self._runtime = runtime
        self._profile_handle = ""

    @property
    def pid(self) -> int:
        return self._process.pid

    @classmethod
    def start(
        cls,
        owner: Broker,
        secrets: Mapping[str, bytearray],
    ) -> tuple[BrokerVaultProcess, BrokerReceipt]:
        owner = Broker(owner)
        if not secrets or any(not name or not material for name, material in secrets.items()):
            raise ValueError("a broker transfer needs named nonempty material")
        runtime = Path(tempfile.mkdtemp(prefix=f"incypher-{owner.value}-broker-", dir="/tmp"))
        channel, process = _spawn_owner(owner, runtime)
        broker = cls(owner, process, channel, runtime)
        try:
            receipt = _transfer(owner, secrets, channel, process.pid)
            return broker, receipt
        except Exception:
            broker.close()
            raise

    @property
    def service_path(self) -> Path:
        if self.owner is Broker.BOARD:
            return self._runtime / "board.sock"
        if self.owner is Broker.CODEX:
            return self._runtime / "codex.sock"
        raise ValueError("this broker owner exposes no service")

    def configure_board(self, *, state: Path, run_id: str, boot_id: str, url: str) -> Path:
        if self.owner is not Broker.BOARD:
            raise ValueError("only the Board owner accepts Board configuration")
        request = {
            "command": "configure-board",
            "state": str(Path(state)),
            "run_id": run_id,
            "boot_id": boot_id,
            "url": url,
        }
        self._channel.sendall(canonical_bytes(request) + b"\n")
        response = json.loads(receive_line(self._channel, failure="Board owner did not configure its service"))
        if (
            not isinstance(response, dict)
            or set(response) != {"path", "status", "profile_handle"}
            or response["path"] != str(self.service_path)
            or response["status"] != "ready"
            or not isinstance(response["profile_handle"], str)
            or not response["profile_handle"]
        ):
            raise RuntimeError("Board owner returned an invalid service endpoint")
        self._profile_handle = response["profile_handle"]
        return self.service_path

    def configure_codex(self, *, state: Path, run_id: str, boot_id: str, model: str, probe) -> Path:
        if self.owner is not Broker.CODEX:
            raise ValueError("only the Codex owner accepts Codex configuration")
        request = {
            "command": "configure-codex",
            "state": str(Path(state)),
            "run_id": run_id,
            "boot_id": boot_id,
            "model": model,
            "probe": probe.document(),
        }
        self._channel.sendall(canonical_bytes(request) + b"\n")
        response = json.loads(receive_line(self._channel, failure="Codex owner did not configure its service"))
        if response != {"path": str(self.service_path), "status": "ready"}:
            raise RuntimeError("Codex owner returned an invalid service endpoint")
        return self.service_path

    @property
    def profile_handle(self) -> str:
        if not self._profile_handle:
            raise RuntimeError("Board owner has no configured profile authority")
        return self._profile_handle

    def close(self) -> None:
        if self._process.poll() is not None:
            self._channel.close()
            self._cleanup_runtime()
            return
        try:
            self._channel.sendall(b"close\n")
        except OSError:
            pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        self._channel.close()
        self._cleanup_runtime()

    def poll(self) -> int | None:
        return self._process.poll()

    def _cleanup_runtime(self) -> None:
        (self._runtime / "board.sock").unlink(missing_ok=True)
        (self._runtime / "codex.sock").unlink(missing_ok=True)
        (self._runtime / "custody.sock").unlink(missing_ok=True)
        try:
            self._runtime.rmdir()
        except FileNotFoundError:
            pass


def _spawn_owner(owner: Broker, runtime: Path) -> tuple[socket.socket, subprocess.Popen[bytes]]:
    runtime.chmod(0o700)
    socket_path = runtime / "custody.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    os.chmod(socket_path, 0o600)
    listener.listen(1)
    listener.settimeout(5)
    environment = {name: value for name in CHILD_ENVIRONMENT if (value := os.environ.get(name)) is not None}
    environment.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "solver.broker_vault_worker", str(socket_path)],
            env=environment,
            start_new_session=True,
        )
        channel, _address = listener.accept()
        return channel, process
    except Exception:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        socket_path.unlink(missing_ok=True)
        runtime.rmdir()
        raise
    finally:
        listener.close()


def _transfer(
    owner: Broker,
    secrets: Mapping[str, bytearray],
    channel: socket.socket,
    process_pid: int,
) -> BrokerReceipt:
    nonce = os.urandom(32)
    request = {
        "owner": owner.value,
        "nonce": nonce.hex(),
        "secrets": [{"name": name, "bytes": len(secrets[name])} for name in sorted(secrets)],
    }
    channel.sendall(canonical_bytes(request) + b"\n")
    peer_pid, peer_uid = _verified_broker_peer(channel, process_pid)
    if receive_line(channel, failure="broker did not return a complete acknowledgement") != "ready":
        raise RuntimeError(f"{owner.value} broker did not accept its transfer header")
    for name in sorted(secrets):
        channel.sendall(memoryview(secrets[name]))
    response = json.loads(receive_line(channel, failure="broker did not return a complete acknowledgement"))
    expected_names = tuple(sorted(secrets))
    expected_digest = transfer_digest(owner, nonce, secrets)
    if (
        not isinstance(response, dict)
        or response.get("owner") != owner.value
        or tuple(response.get("secret_names", ())) != expected_names
        or response.get("transfer_digest") != expected_digest
        or response.get("pid") != peer_pid
        or response.get("uid") != peer_uid
    ):
        raise RuntimeError(f"{owner.value} broker returned an invalid custody acknowledgement")
    identity_digest = hashlib.sha256(
        canonical_bytes({"pid": peer_pid, "uid": peer_uid, "owner": owner.value})
    ).hexdigest()
    return BrokerReceipt(owner, expected_names, expected_digest, peer_pid, peer_uid, identity_digest)


def _verified_broker_peer(channel: socket.socket, process_pid: int) -> tuple[int, int]:
    if hasattr(socket, "SO_PEERCRED") and Path("/proc").is_dir():
        from solver.capability import LinuxPeerIdentity

        peer = LinuxPeerIdentity()(channel)
        if peer.pid != process_pid or peer.uid != os.getuid():
            raise RuntimeError("broker peer identity does not match the spawned owner")
        return peer.pid, peer.uid
    if not hasattr(socket, "LOCAL_PEERCRED"):
        raise RuntimeError("broker peer authentication is unavailable")
    raw = channel.getsockopt(0, socket.LOCAL_PEERCRED, 12)
    peer_uid = struct.unpack_from("I", raw, 4)[0]
    if peer_uid != os.getuid():
        raise RuntimeError("broker peer uid does not match the spawned owner")
    return process_pid, peer_uid


__all__ = ["BrokerVaultProcess"]
