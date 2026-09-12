"""Private child process that retains one broker's bootstrap bytes and exposes no read operation."""

from __future__ import annotations

import json
import base64
import os
import socket
import sys
import datetime as dt
from pathlib import Path

from solver.board_broker import BoardBrokerRuntime, BoardBrokerService, local_peer_identity
from solver.broker_contracts import Broker, transfer_digest
from solver.capability import CapabilityAuthority
from solver.event_store_storage import canonical_bytes
from solver.local_ipc import receive_exact, receive_line
from solver.redaction import Redactor


def main(socket_path: Path) -> int:
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    vault: dict[str, bytearray] = {}
    service: BoardBrokerService | None = None
    try:
        channel.connect(str(socket_path))
        request = json.loads(receive_line(channel, failure="incomplete broker transfer"))
        owner = Broker(request["owner"])
        nonce = bytes.fromhex(request["nonce"])
        supplied = request["secrets"]
        if not isinstance(supplied, list) or not supplied:
            raise ValueError("empty broker transfer")
        channel.sendall(b"ready\n")
        for descriptor in supplied:
            if not isinstance(descriptor, dict) or set(descriptor) != {"name", "bytes"}:
                raise ValueError("invalid broker transfer descriptor")
            name, length = descriptor["name"], descriptor["bytes"]
            if not isinstance(name, str) or not name or not isinstance(length, int) or length <= 0 or name in vault:
                raise ValueError("invalid broker transfer descriptor")
            vault[name] = receive_exact(channel, length, failure="incomplete broker secret transfer")
        response = {
            "owner": owner.value,
            "secret_names": sorted(vault),
            "transfer_digest": transfer_digest(owner, nonce, vault),
            "pid": os.getpid(),
            "uid": os.getuid(),
        }
        channel.sendall(canonical_bytes(response) + b"\n")
        while True:
            command = receive_line(channel, failure="incomplete broker command")
            if command == "close":
                break
            request = json.loads(command)
            if owner is not Broker.BOARD or request.get("command") != "configure-board" or service is not None:
                raise ValueError("unsupported broker command")
            state = Path(str(request["state"]))
            run_id = str(request["run_id"])
            boot_id = str(request["boot_id"])
            secrets = {name: material.decode() for name, material in vault.items()}
            redactor = Redactor(secrets)

            def timestamp() -> str:
                return dt.datetime.now(dt.timezone.utc).isoformat()

            authority = CapabilityAuthority(
                state=state,
                run_id=run_id,
                boot_id=boot_id,
                redactor=redactor,
                peer_identity=local_peer_identity,
                timestamp=timestamp,
            )
            profile_handle = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode("ascii")
            runtime = BoardBrokerRuntime(
                state=state,
                run_id=run_id,
                authority=authority,
                url=str(request["url"]),
                token=secrets.get("CTFD_API_TOKEN", ""),
                team_key=secrets.get("TEAM_KEY", ""),
                boot_id=boot_id,
                timestamp=timestamp,
                profile_required=True,
                profile_handle=profile_handle,
            )
            service = BoardBrokerService(socket_path.with_name("board.sock"), runtime)
            service.start()
            channel.sendall(
                canonical_bytes({"path": str(service.path), "status": "ready", "profile_handle": profile_handle})
                + b"\n"
            )
        return 0
    except Exception as error:
        print(f"broker custody failed: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        if service is not None:
            service.close()
        for material in vault.values():
            for index in range(len(material)):
                material[index] = 0
        channel.close()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
