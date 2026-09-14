"""Fixed hostile-side client for the one Research broker pathname."""

from __future__ import annotations

import json
import os
import socket
import sys


def _request(connection: socket.socket, document: dict[str, object]) -> dict[str, object]:
    connection.sendall(json.dumps(document, separators=(",", ":")).encode() + b"\n")
    line = bytearray()
    while len(line) <= 1024 * 1024:
        byte = connection.recv(1)
        if not byte or byte == b"\n":
            break
        line.extend(byte)
    answer = json.loads(line)
    if not isinstance(answer, dict):
        raise ValueError("Research broker response is invalid")
    return answer


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 2 or arguments[0] != "query":
        return 2
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(os.environ["INCYPHER_RESEARCH_SOCKET"])
    if _request(
        connection,
        {"command": "claim", "generation_id": os.environ["INCYPHER_RESEARCH_GENERATION"]},
    ) != {"claimed": True}:
        return 1
    try:
        with open(arguments[1], "rb") as source:
            raw = source.read(1024 * 1024 + 1)
        query = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return 2
    if len(raw) > 1024 * 1024 or not isinstance(query, dict):
        return 2
    if set(query) == {"queries"}:
        queries = query["queries"]
        if (
            not isinstance(queries, list)
            or not 1 <= len(queries) <= 8
            or not all(isinstance(item, dict) for item in queries)
        ):
            return 2
        results = [_request(connection, {"command": "query", "query": item}) for item in queries]
        if any(result.get("outcome") != "answered" for result in results):
            return 1
        sys.stdout.write(json.dumps(results, sort_keys=True, separators=(",", ":")) + "\n")
        return 0
    result = _request(connection, {"command": "query", "query": query})
    if result.get("outcome") != "answered":
        return 1
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
