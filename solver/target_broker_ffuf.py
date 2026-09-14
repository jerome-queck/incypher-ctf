"""Run ffuf behind the Target broker without granting the Attempt a network socket."""

from __future__ import annotations

import subprocess
import tempfile
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from solver.target_broker_contracts import TargetOutcome, TargetResult

FFUF = Path("/usr/bin/ffuf")


@dataclass(frozen=True)
class FfufResult:
    outcome: TargetOutcome
    found: tuple[str, ...] = ()


class FfufRunner:
    """Own one fixed-argument ffuf process and its loopback-to-Target shim."""

    def __init__(
        self,
        paths: Sequence[str],
        exchange: Callable[[str], TargetResult],
        timeout_seconds: float,
        *,
        executable: Path = FFUF,
    ) -> None:
        self._paths = tuple(paths)
        self._exchange = exchange
        self._timeout_seconds = timeout_seconds
        self._executable = Path(executable)
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._server: HTTPServer | None = None

    def run(self) -> FfufResult:
        if self._closed.is_set():
            return FfufResult(TargetOutcome.REVOKED)
        statuses: dict[str, int] = {}
        failures: list[TargetOutcome] = []
        unexpected: list[str] = []
        admitted = frozenset(self._paths)
        exchange = self._exchange

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler hook
                parsed = urlsplit(self.path)
                path = parsed.path
                if parsed.query or parsed.fragment or path not in admitted:
                    unexpected.append(self.path)
                    self.send_error(400)
                    return
                result = exchange(path)
                if result.outcome is not TargetOutcome.ANSWERED:
                    failures.append(result.outcome)
                    status = 502
                else:
                    status = result.status if 100 <= result.status <= 599 else 502
                    statuses[path] = status
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *_arguments: object) -> None:
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        with self._lock:
            self._server = server
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="incypher-ffuf-broker-") as directory:
                root = Path(directory)
                wordlist = root / "paths.txt"
                output = root / "results.json"
                wordlist.write_text("\n".join(path.lstrip("/") for path in self._paths) + "\n", encoding="utf-8")
                command = [
                    str(self._executable),
                    "-u",
                    f"http://127.0.0.1:{server.server_port}/FUZZ",
                    "-w",
                    str(wordlist),
                    "-mc",
                    "all",
                    "-of",
                    "json",
                    "-o",
                    str(output),
                    "-s",
                    "-t",
                    "1",
                    "-timeout",
                    str(max(1, min(10, int(self._timeout_seconds)))),
                    "-maxtime",
                    str(max(1, int(self._timeout_seconds))),
                ]
                process = subprocess.Popen(
                    command,
                    cwd=root,
                    env={
                        "HOME": str(root),
                        "PATH": "/usr/bin:/bin",
                        "TMPDIR": str(root),
                        "NO_PROXY": "127.0.0.1",
                    },
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                with self._lock:
                    self._process = process
                    closed = self._closed.is_set()
                if closed:
                    process.terminate()
                try:
                    process.communicate(timeout=self._timeout_seconds + 1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    return FfufResult(TargetOutcome.TIMEOUT)
                if self._closed.is_set():
                    return FfufResult(TargetOutcome.REVOKED)
                if process.returncode != 0 or not output.is_file():
                    return FfufResult(TargetOutcome.UNREACHABLE)
        finally:
            with self._lock:
                self._process = None
                self._server = None
            server.shutdown()
            server.server_close()
            thread.join(timeout=1)
        if failures:
            return FfufResult(failures[0])
        if unexpected or set(statuses) != admitted:
            return FfufResult(TargetOutcome.UNREACHABLE)
        return FfufResult(TargetOutcome.ANSWERED, tuple(path for path in self._paths if statuses[path] != 404))

    def close(self) -> None:
        self._closed.set()
        with self._lock:
            process = self._process
            server = self._server
        if process is not None and process.poll() is None:
            process.terminate()
        if server is not None:
            server.shutdown()


__all__ = ["FfufResult", "FfufRunner"]
