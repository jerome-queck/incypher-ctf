"""Production Run-controller lifetime for capability IPC."""

import json
import socket
import tempfile
from pathlib import Path

from solver.capability_service import CapabilityService
from solver.event_store import EventStore
from solver.redaction import Redactor


def test_service_opens_bounded_peer_authenticated_ipc_beside_v1_clients(tmp_path):
    raw_handle = "raw-handle-canary-7f91d2"
    with tempfile.TemporaryDirectory(prefix="capability-service-", dir="/tmp") as directory:
        with CapabilityService(
            state=tmp_path,
            run_id="run-1",
            boot_id="boot-000001",
            redactor=Redactor({}),
            timestamp=lambda: "2026-09-11T00:00:00Z",
            runtime_root=Path(directory),
        ) as service:
            client = socket.socket(socket.AF_UNIX)
            try:
                client.connect(str(service.path))
                client.sendall(json.dumps({"handle": raw_handle}).encode() + b"\n")
                assert json.loads(client.recv(256)) == {"status": "refused"}
            finally:
                client.close()

            denial = [
                event.payload
                for event in EventStore(tmp_path, run_id="run-1").events()
                if event.event_type == "capability-custody.recorded"
            ][-1]
            assert denial["reason"] in {"unknown-handle", "peer-authentication-unavailable"}
            assert raw_handle not in json.dumps(denial)

        assert not service.path.exists()
