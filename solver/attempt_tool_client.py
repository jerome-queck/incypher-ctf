"""Narrow client for an ordinary Attempt's generation-bound Tool port."""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
from pathlib import Path

TOOL_SOCKET_ENV = "INCYPHER_TOOL_SOCKET"
TOOL_HANDLE_ENV = "INCYPHER_TOOL_HANDLE"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RESPONSE_TIMEOUT_SECONDS = 125


class AttemptToolClient:
    def __init__(self, path: Path, handle: str) -> None:
        if not handle:
            raise ValueError("Attempt Tool handle is required")
        self._path = Path(path)
        self._handle = handle

    def list(self) -> tuple[str, ...]:
        answer = self._request({"operation": "list"})
        capabilities = answer.get("capabilities")
        if answer.get("outcome") != "listed" or not isinstance(capabilities, list):
            raise PermissionError("Attempt Tool request was refused")
        return tuple(str(capability) for capability in capabilities)

    def run(self, capability_id: str, input_path: str) -> tuple[int, bytes]:
        answer = self._request({"operation": "run", "capability_id": capability_id, "input_path": input_path})
        if answer.get("outcome") != "answered":
            raise PermissionError("Attempt Tool request was refused")
        try:
            exit_code = int(answer["exit_code"])
            output = base64.b64decode(str(answer["output"]), validate=True)
        except (KeyError, ValueError, TypeError) as error:
            raise RuntimeError("Attempt Tool response was malformed") from error
        return exit_code, output

    def _request(self, document: dict[str, object]) -> dict[str, object]:
        document = {**document, "handle": self._handle}
        channel = socket.socket(socket.AF_UNIX)
        channel.settimeout(RESPONSE_TIMEOUT_SECONDS)
        try:
            channel.connect(str(self._path))
            channel.sendall(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
            raw = _receive(channel)
        finally:
            channel.close()
        answer = json.loads(raw)
        if not isinstance(answer, dict):
            raise RuntimeError("Attempt Tool response was malformed")
        return answer


def _receive(channel: socket.socket) -> str:
    held = bytearray()
    while len(held) <= MAX_RESPONSE_BYTES:
        chunk = channel.recv(min(4096, MAX_RESPONSE_BYTES + 1 - len(held)))
        if not chunk:
            break
        held.extend(chunk)
        if held.endswith(b"\n"):
            break
    if not held.endswith(b"\n") or len(held) > MAX_RESPONSE_BYTES:
        raise RuntimeError("Attempt Tool response was malformed")
    return held[:-1].decode()


def main(arguments: list[str]) -> int:
    socket_path = os.environ.get(TOOL_SOCKET_ENV)
    handle = os.environ.get(TOOL_HANDLE_ENV)
    if not socket_path or not handle:
        print("Attempt Tool port is unavailable", file=sys.stderr)
        return 2
    client = AttemptToolClient(Path(socket_path), handle)
    try:
        if arguments == ["list"]:
            print("\n".join(client.list()))
            return 0
        if len(arguments) == 3 and arguments[0] == "run":
            exit_code, output = client.run(arguments[1], arguments[2])
            sys.stdout.buffer.write(output)
            return exit_code
    except (OSError, PermissionError, RuntimeError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print("usage: attempt_tool_client.py list | run <capability> <input-path>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
