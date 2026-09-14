"""Host-owned mutable CTFd surface for exact-image qualification only."""

from __future__ import annotations

import argparse
import json
import socket
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes

TOKEN = "controlled-board-token"


class QualificationBoard:
    """Deterministic Board state observed outside the Candidate container."""

    def __init__(self) -> None:
        self.challenges = tuple(
            {
                "id": challenge_id,
                "name": f"controlled-{challenge_id}",
                "category": "qualification",
                "type": "standard",
                "value": 100 if challenge_id == 1 else 500 - challenge_id,
                "solves": 0,
                "solved_by_me": False,
            }
            for challenge_id in range(1, 4)
        )
        self.instances = {}
        self.observations: list[dict[str, object]] = []
        self.submissions: dict[str, str] = {}
        self.ambiguous_remaining = 1
        self.lock = threading.Lock()

    def observe(self, method: str, path: str, body: object, authenticated: bool) -> None:
        with self.lock:
            self.observations.append({"authenticated": authenticated, "body": body, "method": method, "path": path})


def server(board: QualificationBoard, host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _body(self):
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length)) if length else None

        def _send(self, status: int, document, *, content_type: str = "application/json"):
            body = document if isinstance(document, bytes) else canonical_bytes(document)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _handle(self):
            parsed = urllib.parse.urlsplit(self.path)
            authenticated = self.headers.get("Authorization") == f"Token {TOKEN}"
            body = self._body()
            board.observe(self.command, self.path, body, authenticated)
            if parsed.path == "/api/v1/users/me":
                return self._send(200, {"data": {"id": 7, "team_id": None}, "success": True})
            if parsed.path == "/":
                return self._send(
                    200,
                    b'<script>window.init={"userId":7,"teamId":null,"userMode":"users"};</script>',
                    content_type="text/html",
                )
            if parsed.path == "/api/v1/challenges/0":
                return self._send(404, {"message": "Challenge not found", "success": False})
            if parsed.path == "/api/v1/challenges":
                if not authenticated:
                    return self._send(403, {"message": "Authentication required", "success": False})
                return self._send(200, {"data": list(board.challenges), "success": True})
            if parsed.path == "/api/v1/scoreboard/top/10":
                return self._send(200, {"data": [], "success": True})
            if parsed.path.startswith("/api/v1/challenges/") and parsed.path.rsplit("/", 1)[-1].isdigit():
                challenge_id = int(parsed.path.rsplit("/", 1)[-1])
                challenge = board.challenges[challenge_id - 1]
                return self._send(
                    200,
                    {
                        "data": {
                            **challenge,
                            "attempts": 0,
                            "description": "Difficulty: easy. Controlled exact-image final interval.",
                            "files": [],
                        },
                        "success": True,
                    },
                )
            if parsed.path == "/api/v1/challenges/attempt" and self.command == "POST":
                candidate = str(body.get("submission", ""))
                board.submissions[candidate] = "accepted"
                if candidate == "qualification{ambiguous}" and board.ambiguous_remaining:
                    board.ambiguous_remaining -= 1
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return None
                status = "correct" if candidate == "qualification{accepted}" else "incorrect"
                return self._send(200, {"data": {"message": "controlled", "status": status}, "success": True})
            if parsed.path == "/api/v1/configs":
                return self._send(200, {"data": [], "success": True})
            if parsed.path == "/plugins/ctfd-chall-manager/instances":
                return self._send(
                    200,
                    b'<script>window.init={"userId":7,"teamId":null,"userMode":"users"};</script>'
                    b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>",
                    content_type="text/html",
                )
            if parsed.path.endswith("/mana"):
                return self._send(200, {"data": {"total": 1, "used": len(board.instances)}, "success": True})
            if parsed.path == "/api/v1/plugins/ctfd-chall-manager/instances":
                rows = list(board.instances.values()) if authenticated else []
                return self._send(
                    200,
                    {"data": {"page": 1, "rows": rows, "totalPages": 1, "totalRows": len(rows)}, "success": True},
                )
            if parsed.path == "/api/v1/plugins/ctfd-chall-manager/instance":
                query_id = urllib.parse.parse_qs(parsed.query).get("challengeId", [None])[0]
                challenge_id = int(query_id if query_id is not None else body["challengeId"])
                if self.command == "DELETE":
                    board.instances.pop(challenge_id, None)
                    return self._send(200, {"data": {"message": "terminated"}, "success": True})
                if self.command == "POST":
                    board.instances[challenge_id] = {
                        "instanceId": f"controlled-instance-{challenge_id}",
                        "challengeId": challenge_id,
                        "userId": 7,
                        "teamId": None,
                    }
                return self._send(200, {"data": {"connectionInfo": "127.0.0.1:31337"}, "success": True})
            return self._send(404, {"success": False})

        do_GET = do_POST = do_DELETE = _handle

        def log_message(self, *_args):
            pass

    return ThreadingHTTPServer((host, port), Handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=38095)
    parser.add_argument("--observations", type=Path, required=True)
    arguments = parser.parse_args()
    board = QualificationBoard()
    endpoint = server(board, port=arguments.port)
    try:
        endpoint.serve_forever()
    finally:
        atomic_write(arguments.observations, canonical_bytes({"requests": board.observations}) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
