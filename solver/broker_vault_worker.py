"""Private child process that retains one broker's bootstrap bytes and exposes no read operation."""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

from solver.broker_contracts import Broker, transfer_digest
from solver.event_store_storage import canonical_bytes
from solver.local_ipc import receive_exact, receive_line


def main(socket_path: Path) -> int:
    channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    vault: dict[str, bytearray] = {}
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
        if receive_line(channel, failure="incomplete broker transfer") != "close":
            raise ValueError("unsupported broker command")
        return 0
    except Exception as error:
        print(f"broker custody failed: {type(error).__name__}", file=sys.stderr)
        return 1
    finally:
        for material in vault.values():
            for index in range(len(material)):
                material[index] = 0
        channel.close()


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1])))
