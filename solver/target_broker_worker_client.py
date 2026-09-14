"""Hostile-side CLI for the one inherited Target broker pathname."""

from __future__ import annotations

import base64
import json
import os
import socket
import sys

TARGET_EXCHANGE_COMMAND = "exchange"
TARGET_HTTP_SESSION_COMMAND = "http-session"
TARGET_TCP_SESSION_COMMAND = "tcp-session"
TARGET_BROWSER_COMMAND = "browser"
MAX_SESSION_DOCUMENT_BYTES = 1024 * 1024


def request(document: dict[str, object]) -> dict[str, object]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(os.environ["INCYPHER_TARGET_SOCKET"])
        connection.sendall(json.dumps(document, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        held = bytearray()
        while not held.endswith(b"\n"):
            chunk = connection.recv(65536)
            if not chunk or len(held) + len(chunk) > 1024 * 1024:
                raise ValueError("Target broker response is incomplete")
            held.extend(chunk)
        answer = json.loads(held)
        if not isinstance(answer, dict):
            raise ValueError("Target broker response is invalid")
        return answer
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) not in {2, 3} or arguments[0] not in {
        "tcp",
        "http",
        "http-session",
        "tcp-session",
        "browser",
    }:
        return 2
    if arguments[0] in {"http-session", "tcp-session", "browser"} and len(arguments) != 2:
        return 2
    if arguments[0] == "http" and len(arguments) != 3:
        return 2
    if arguments[0] == "tcp" and len(arguments) != 2:
        return 2
    claimed = request({"command": "claim", "generation_id": os.environ["INCYPHER_TARGET_GENERATION"]})
    handle = claimed.get("handle")
    if claimed.get("status") != "issued" or not isinstance(handle, str):
        return 1
    try:
        if arguments[0] in {"http-session", "tcp-session", "browser"}:
            return _session(arguments[0], arguments[1], handle)
        if arguments[0] == "tcp":
            exchange = {"body": base64.b64encode(arguments[1].encode()).decode()}
        else:
            exchange = {
                "method": arguments[1],
                "path": arguments[2],
                "body": "",
            }
        answer = request({"command": TARGET_EXCHANGE_COMMAND, "handle": handle, "request": exchange})
        result = answer.get("result", {})
        if not isinstance(result, dict) or result.get("outcome") != "answered":
            return 1
        sys.stdout.buffer.write(base64.b64decode(str(result.get("body", ""))))
        return 0
    finally:
        request({"command": "revoke", "handle": handle})


def _session(command: str, path: str, handle: str) -> int:
    try:
        raw = open(path, "rb").read(MAX_SESSION_DOCUMENT_BYTES + 1)
        if len(raw) > MAX_SESSION_DOCUMENT_BYTES:
            return 2
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 2
    documents = supplied if isinstance(supplied, list) else [supplied]
    if not documents or len(documents) > 128 or any(not isinstance(item, dict) for item in documents):
        return 2
    results = []
    for document in documents:
        answer = request({"command": command, "handle": handle, "request": document})
        result = answer.get("result")
        if not isinstance(result, dict):
            return 1
        results.append(result)
        if result.get("outcome") != "answered":
            break
    visible = results if isinstance(supplied, list) else results[0]
    sys.stdout.write(json.dumps(visible, sort_keys=True, separators=(",", ":")) + "\n")
    return 0 if all(result.get("outcome") == "answered" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
