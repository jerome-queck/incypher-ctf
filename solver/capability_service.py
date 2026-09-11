"""Run-controller lifetime for the peer-authenticated capability listener."""

from __future__ import annotations

import os
import socket
import threading
from pathlib import Path

from solver.boot import Refusal
from solver.capability import CapabilityAuthority, CapabilityListener, CapabilityServer
from solver.redaction import Redactor


class CapabilityService:
    """Keep the IPC authority live beside v1 clients until domain migrations use it."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        boot_id: str,
        redactor: Redactor,
        timestamp,
        runtime_root: Path = Path("/tmp/incypher-capabilities"),
    ) -> None:
        self._runtime = Path(runtime_root) / run_id / boot_id
        self.authority = CapabilityAuthority(
            state=state,
            run_id=run_id,
            boot_id=boot_id,
            redactor=redactor,
            timestamp=timestamp,
        )
        self.path = self._runtime / "broker.sock"
        self._listener = CapabilityListener(self.path, CapabilityServer(self.authority))
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> CapabilityService:
        try:
            self._runtime.mkdir(parents=True, mode=0o700)
            os.chmod(self._runtime, 0o700)
            self._listener.__enter__()
        except (OSError, PermissionError, ValueError) as error:
            raise Refusal(f"[boot] capability listener could not start — {error}") from error
        self._thread = threading.Thread(target=self._serve, name="capability-listener", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, _error_type, _error, _traceback) -> None:
        self._stopped.set()
        self._listener.close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        for directory in (self._runtime, self._runtime.parent):
            try:
                directory.rmdir()
            except OSError:
                pass

    def _serve(self) -> None:
        while not self._stopped.is_set():
            try:
                self._listener.serve_once()
            except (OSError, socket.timeout):
                if not self._stopped.is_set():
                    continue


__all__ = ["CapabilityService"]
