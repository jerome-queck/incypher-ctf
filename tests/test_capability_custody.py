"""Opaque, peer-bound capabilities at the broker IPC seam."""

import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from solver.capability import (
    CapabilityAuthority,
    CapabilityBinding,
    CapabilityRefused,
    CapabilityListener,
    CapabilityServer,
    PeerAuthenticationUnavailable,
    PeerIdentity,
)
from solver.event_store import EventStore
from solver.redaction import Redactor
from solver.work_generation import GenerationDisposition, GenerationFence


PEER = PeerIdentity(pid=101, uid=20_000, gid=20_000, started="123", cgroup="/attempt-1")
OTHER_PEER = PeerIdentity(pid=202, uid=20_001, gid=20_001, started="456", cgroup="/attempt-2")


class Peers:
    def __init__(self, *identities: PeerIdentity) -> None:
        self.identities = list(identities)
        self.calls = 0

    def __call__(self, _connection) -> PeerIdentity:
        self.calls += 1
        return self.identities.pop(0)


def binding(generation_id: str = "generation-000001") -> CapabilityBinding:
    return CapabilityBinding(
        run_id="run-1",
        boot_id="boot-000001",
        generation_id=generation_id,
        lane_id="lane-1",
        attempt_id="attempt-1",
        step_id="step-1",
    )


def fence(tmp_path) -> GenerationFence:
    return GenerationFence(
        tmp_path,
        "run-1",
        Redactor({}),
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )


def authority(tmp_path, peers: Peers) -> CapabilityAuthority:
    if not fence(tmp_path).projection().generations:
        fence(tmp_path).acquire("work-1", "attempt-1")
    return CapabilityAuthority(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-000001",
        redactor=Redactor({}),
        peer_identity=peers,
        token_bytes=lambda count: b"h" * count,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )


def audit_records(tmp_path) -> list[dict[str, object]]:
    return [
        dict(event.payload)
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.event_type == "capability-custody.recorded"
    ]


def test_opaque_handle_grants_only_its_trusted_binding_and_scope(tmp_path):
    peers = Peers(PEER)
    capabilities = authority(tmp_path, peers)

    handle = capabilities.issue(binding(), "board.read", PEER)
    grant = capabilities.authorize(object(), handle)

    assert grant.binding == binding()
    assert grant.scope == "board.read"
    assert handle not in json.dumps(audit_records(tmp_path))
    assert [record["record"] for record in audit_records(tmp_path)] == ["issued", "authorized"]
    assert peers.calls == 1


def test_independent_brokers_allocate_one_canonical_capability_event_sequence(tmp_path):
    fence(tmp_path).acquire("work-1", "attempt-1")
    first = CapabilityAuthority(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-000001",
        redactor=Redactor({}),
        token_bytes=lambda count: b"a" * count,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )
    second = CapabilityAuthority(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-000001",
        redactor=Redactor({}),
        token_bytes=lambda count: b"b" * count,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )

    first.issue(binding(), "tool.view:first", PEER)
    second.issue(binding(), "target.exchange", PEER)

    assert [record["event_id"] for record in audit_records(tmp_path)] == [
        "capability:boot-000001:000001",
        "capability:boot-000001:000002",
    ]


def test_concurrent_restart_reconciliation_revokes_each_stale_handle_once(tmp_path):
    old = authority(tmp_path, Peers())
    old.issue(binding(), "target.exchange", PEER)
    replacements = [
        CapabilityAuthority(
            state=tmp_path,
            run_id="run-1",
            boot_id="boot-000002",
            redactor=Redactor({}),
            timestamp=lambda: "2026-09-11T00:00:00Z",
        )
        for _ in range(2)
    ]
    rendezvous = threading.Barrier(2)
    for replacement in replacements:
        append = replacement._append

        def delayed(fact, *, append=append):
            try:
                rendezvous.wait(timeout=0.2)
            except threading.BrokenBarrierError:
                pass
            append(fact)

        replacement._append = delayed  # type: ignore[method-assign]
    threads = [threading.Thread(target=item.reconcile_restart, args=("target.exchange",)) for item in replacements]

    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    reconciled = [record for record in audit_records(tmp_path) if record["reason"] == "restart-reconciled"]
    assert len(reconciled) == 1


@pytest.mark.parametrize(
    ("requested_handle", "observed_peer", "reason"),
    [
        ("not-a-handle", OTHER_PEER, "unknown-handle"),
        (None, OTHER_PEER, "peer-mismatch"),
    ],
)
def test_unknown_handle_and_wrong_peer_have_one_external_refusal(tmp_path, requested_handle, observed_peer, reason):
    peers = Peers(observed_peer)
    capabilities = authority(tmp_path, peers)
    handle = capabilities.issue(binding(), "board.read", PEER)

    with pytest.raises(CapabilityRefused, match="^capability refused$"):
        capabilities.authorize(object(), requested_handle or handle)

    assert audit_records(tmp_path)[-1]["reason"] == reason
    assert peers.calls == 1


def test_cross_generation_replay_is_denied_from_canonical_current_state(tmp_path):
    peers = Peers(PEER)
    capabilities = authority(tmp_path, peers)
    handle = capabilities.issue(binding(), "target.connect", PEER)
    fence(tmp_path).close("generation-000001", GenerationDisposition.SUPERSEDE)
    fence(tmp_path).acquire("work-1", "attempt-2")

    with pytest.raises(CapabilityRefused, match="^capability refused$"):
        capabilities.authorize(object(), handle)

    assert audit_records(tmp_path)[-1]["reason"] == "stale-generation"


def test_revocation_is_durable_and_prevents_later_authorization(tmp_path):
    peers = Peers(PEER)
    capabilities = authority(tmp_path, peers)
    handle = capabilities.issue(binding(), "research.read", PEER)

    capabilities.revoke(handle, "attempt-closed")
    with pytest.raises(CapabilityRefused, match="^capability refused$"):
        capabilities.authorize(object(), handle)

    assert [record["record"] for record in audit_records(tmp_path)] == ["issued", "revoked", "denied"]
    assert audit_records(tmp_path)[-1]["reason"] == "revoked"


def test_server_derives_peer_identity_on_every_ipc_request(tmp_path):
    peers = Peers(PEER, PEER)
    capabilities = authority(tmp_path, peers)
    handle = capabilities.issue(binding(), "tool.invoke", PEER)
    server = CapabilityServer(capabilities)

    for _ in range(2):
        client, accepted = socket.socketpair()
        try:
            client.sendall(json.dumps({"handle": handle}).encode() + b"\n")
            server.serve_request(accepted)
            assert json.loads(client.recv(256)) == {"status": "granted"}
        finally:
            client.close()
            accepted.close()

    assert peers.calls == 2


def test_ipc_request_cannot_supply_its_own_execution_identity(tmp_path):
    peers = Peers(PEER)
    capabilities = authority(tmp_path, peers)
    handle = capabilities.issue(binding(), "tool.invoke", PEER)
    server = CapabilityServer(capabilities)
    client, accepted = socket.socketpair()
    try:
        client.sendall(json.dumps({"handle": handle, "generation_id": "generation-000002"}).encode() + b"\n")
        server.serve_request(accepted)
        assert json.loads(client.recv(256)) == {"status": "refused"}
    finally:
        client.close()
        accepted.close()

    assert peers.calls == 1


def test_unavailable_kernel_peer_authentication_refuses_and_records_sanitized_failure(tmp_path):
    def unavailable(_connection):
        raise PeerAuthenticationUnavailable("private kernel detail")

    capabilities = authority(tmp_path, unavailable)
    server = CapabilityServer(capabilities)
    client, accepted = socket.socketpair()
    try:
        client.sendall(b'{"handle":"anything"}\n')
        server.serve_request(accepted)
        assert json.loads(client.recv(256)) == {"status": "refused"}
    finally:
        client.close()
        accepted.close()

    failure = audit_records(tmp_path)[-1]
    assert failure["record"] == "denied"
    assert failure["reason"] == "peer-authentication-unavailable"
    assert failure["handle_digest"] == ""
    assert "private kernel detail" not in json.dumps(failure)


@pytest.mark.skipif(sys.platform != "linux", reason="the strict peer profile is Linux")
def test_linux_peer_identity_comes_from_the_kernel_for_each_connection():
    from solver.capability import LinuxPeerIdentity

    client, accepted = socket.socketpair()
    try:
        peer = LinuxPeerIdentity()(accepted)
    finally:
        client.close()
        accepted.close()

    assert peer.pid == os.getpid()
    assert peer.uid == os.getuid()
    assert peer.gid == os.getgid()
    assert peer.started
    assert peer.cgroup


def test_pathname_socket_accepts_one_bounded_peer_authenticated_request(tmp_path):
    peers = Peers(PEER)
    capabilities = authority(tmp_path, peers)
    handle = capabilities.issue(binding(), "board.read", PEER)
    with tempfile.TemporaryDirectory(prefix="capability-", dir="/tmp") as directory:
        runtime = Path(directory)
        runtime.chmod(0o700)
        path = runtime / "board.sock"
        with CapabilityListener(path, CapabilityServer(capabilities)) as listener:
            client = socket.socket(socket.AF_UNIX)
            try:
                client.connect(str(path))
                client.sendall(json.dumps({"handle": handle}).encode() + b"\n")
                listener.serve_once()
                assert json.loads(client.recv(256)) == {"status": "granted"}
                assert path.stat().st_mode & 0o777 == 0o600
            finally:
                client.close()
        assert not path.exists()
    assert peers.calls == 1
