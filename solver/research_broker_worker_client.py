"""Fixed hostile-side client for the one Research broker pathname."""

from __future__ import annotations

import base64
import json
import os
import socket
import sys


def _request(connection: socket.socket, document: dict[str, object]) -> dict[str, object]:
    connection.sendall(json.dumps(document, separators=(",", ":")).encode() + b"\n")
    line = bytearray()
    while len(line) <= 4096:
        byte = connection.recv(1)
        if not byte or byte == b"\n":
            break
        line.extend(byte)
    answer = json.loads(line)
    if not isinstance(answer, dict):
        raise ValueError("Research broker response is invalid")
    return answer


def main() -> int:
    if len(sys.argv) != 2:
        return 2
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(os.environ["INCYPHER_RESEARCH_SOCKET"])
    if _request(
        connection,
        {"command": "claim", "generation_id": os.environ["INCYPHER_RESEARCH_GENERATION"]},
    ) != {"claimed": True}:
        return 1
    result = _request(connection, {"command": "fetch", "url": sys.argv[1]})
    if result.get("outcome") != "answered":
        return 1
    sys.stdout.buffer.write(base64.b64decode(result.get("body", "")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
