"""Boot-owned Board-profile qualification through the credential broker."""

import json
import hashlib
import threading
import tempfile
from pathlib import Path

import pytest

from solver.board import BoardFailure
from solver.board_broker import BoardBrokerRuntime, BoardBrokerService, BoardCompatibilityClient, BoardProfileClient
from solver.event_store import EventStore
from solver.board_profile_receipt import verify_receipt
from solver.board_profile import rules_document
from solver.event_store_storage import canonical_bytes
from solver.profile import ANONYMOUS_ANSWERED, Rules

from test_board_broker import OTHER, PROFILE_HANDLE, _broker


RULES = Rules(
    event="fixture",
    url="https://board.example",
    flag_wrappers=(r"flag\{[^}]+\}",),
    window_seconds=19_800,
    prohibitions=("do not brute-force flags",),
)


def _json(data, *, success=True):
    return 200, json.dumps({"success": success, "data": data}).encode(), "", "application/json; charset=utf-8"


def _profile_transport(calls):
    landing = b'<script>window.init = {"userId": 7, "teamId": null, "userMode": "users"};</script>'
    ledger = landing + b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>"

    def transport(request):
        path = request.full_url.removeprefix("https://board.example")
        calls.append(path)
        if path == "/api/v1/users/me":
            return _json({"id": 7, "team_id": None})
        if path == "/":
            return 200, landing, "", "text/html"
        if path == "/api/v1/challenges/0":
            return 404, b'{"message":"Challenge not found"}', "", "application/json"
        if path == "/api/v1/challenges/1":
            return _json({"id": 1, "name": "alpha", "type": "standard", "category": "misc"})
        if path == "/api/v1/challenges":
            return (
                _json([{"id": 1, "name": "alpha", "type": "standard"}])
                if request.get_header("Authorization")
                else _json([])
            )
        if path == "/plugins/ctfd-chall-manager/instances":
            return 200, ledger, "", "text/html"
        if path.endswith("/mana"):
            return _json({"used": 0, "total": 0})
        if path == "/api/v1/configs":
            return _json([])
        raise AssertionError(path)

    return transport


def test_boot_profile_client_qualifies_two_authenticated_cycles_and_writes_receipt(tmp_path: Path) -> None:
    calls = []
    landing = b'<html><script>window.init = {"userId": 7, "teamId": 3, "userMode": "teams"};</script></html>'
    ledger = (
        b'<html><script>window.init = {"userId": 7, "teamId": 3, "userMode": "teams"};</script>'
        b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table></html>"
    )

    def transport(request):
        path = request.full_url.removeprefix("https://board.example")
        calls.append((path, request.get_header("Authorization"), request.get_header("Content-type")))
        if path == "/api/v1/users/me":
            return _json({"id": 7, "team_id": 3})
        if path == "/":
            return 200, landing, "", "text/html; charset=utf-8"
        if path == "/api/v1/challenges/0":
            return 404, b'{"message":"Challenge not found"}', "", "application/json"
        if path == "/api/v1/challenges/1":
            return _json({"id": 1, "name": "alpha", "type": "standard", "category": "misc"})
        if path == "/api/v1/challenges":
            if request.get_header("Authorization"):
                return _json([{"id": 1, "name": "alpha", "type": "standard"}])
            return _json([])
        if path == "/plugins/ctfd-chall-manager/instances":
            return 200, ledger, "", "text/html"
        if path.endswith("/mana"):
            return _json({"used": 0, "total": 0})
        if path == "/api/v1/configs":
            return _json([])
        raise AssertionError(path)

    state, _authority, _binding, _handle, runtime = _broker(tmp_path, transport)
    socket_root = Path(tempfile.mkdtemp(prefix="profile-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    try:
        decision = BoardProfileClient(service.path, PROFILE_HANDLE).qualify(
            RULES, "docs/competitions/fixture.board.json"
        )
    finally:
        service.close()
        socket_root.rmdir()

    assert decision.authoritative is True
    assert decision.profile is not None
    assert decision.profile.unauthenticated_read == ANONYMOUS_ANSWERED
    assert len(calls) == 18
    assert all(
        token == "Token board-token" for path, token, _content_type in calls if path != "/api/v1/challenges" or token
    )
    assert {content_type for _path, _token, content_type in calls} == {"application/json"}
    verify_receipt(state / "runs" / "run-1" / "canonical" / "board-profile.receipt.json")
    observations = [
        event
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "board-profile-observation.recorded"
    ]
    assert len(observations) == 18
    assert all(event.body for event in observations if event.payload["document_name"] != "anonymous_challenges")


def test_profile_decision_and_operation_gate_replay_without_reprobing_after_restart(tmp_path: Path) -> None:
    calls = []
    landing = b'<script>window.init = {"userId": 7, "teamId": null, "userMode": "users"};</script>'
    ledger = landing + b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>"

    def transport(request):
        path = request.full_url.removeprefix("https://board.example")
        calls.append(path)
        if path == "/api/v1/users/me":
            return _json({"id": 7, "team_id": None})
        if path == "/":
            return 200, landing, "", "text/html"
        if path == "/api/v1/challenges/0":
            return 404, b'{"message":"Challenge not found"}', "", "application/json"
        if path == "/api/v1/challenges/1":
            return _json({"id": 1, "name": "alpha", "type": "standard", "category": "misc"})
        if path == "/api/v1/challenges":
            if request.get_header("Authorization"):
                return _json([{"id": 1, "name": "alpha", "type": "standard"}])
            return _json([])
        if path == "/plugins/ctfd-chall-manager/instances":
            return 200, ledger, "", "text/html"
        if path.endswith("/mana"):
            return _json({"used": 0, "total": 0})
        if path == "/api/v1/configs":
            return _json([])
        raise AssertionError(path)

    state, authority, _binding, _handle, runtime = _broker(tmp_path, transport)
    socket_root = Path(tempfile.mkdtemp(prefix="profile-", dir="/tmp"))
    first_service = BoardBrokerService(socket_root / "first.sock", runtime)
    first_service.start()
    try:
        first = BoardProfileClient(first_service.path, PROFILE_HANDLE)
        assert first.qualify(RULES, "docs/competitions/fixture.board.json").authoritative is True
        first.open_operations()
    finally:
        first_service.close()

    assert [
        event.payload["record"]
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "board-profile-phase.recorded"
    ] == ["probe-started", "profile-decided", "operations-opened"]
    assert len(calls) == 18

    restarted = BoardBrokerRuntime(
        state=state,
        run_id="run-1",
        authority=authority,
        url="https://board.example",
        token="board-token",
        team_key="team-key",
        boot_id="boot-000001",
        transport=lambda _request: (_ for _ in ()).throw(AssertionError("re-probed")),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
        profile_handle=PROFILE_HANDLE,
    )
    second_service = BoardBrokerService(socket_root / "second.sock", restarted)
    second_service.start()
    try:
        second = BoardProfileClient(second_service.path, PROFILE_HANDLE)
        assert second.qualify(RULES, "docs/competitions/fixture.board.json").authoritative is True
        second.open_operations()
    finally:
        second_service.close()
        socket_root.rmdir()

    assert len(calls) == 18


def test_refused_profile_opens_no_later_board_operation(tmp_path: Path) -> None:
    calls = []
    landing = b'<script>window.init = {"userId": 7, "teamId": null, "userMode": "users"};</script>'
    ledger = landing + b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>"

    def transport(request):
        path = request.full_url.removeprefix("https://board.example")
        calls.append(path)
        if path == "/api/v1/users/me":
            return _json({"id": 7, "team_id": None})
        if path == "/":
            return 200, landing, "", "text/html"
        if path == "/api/v1/challenges/0":
            return 404, b'{"message":"Challenge not found"}', "", "application/json"
        if path == "/api/v1/challenges":
            if request.get_header("Authorization"):
                return 200, landing, "", "text/html"
            return 302, b"", "/login", "text/html"
        if path == "/plugins/ctfd-chall-manager/instances":
            return 200, ledger, "", "text/html"
        if path.endswith("/mana"):
            return _json({"used": 0, "total": 0})
        if path == "/api/v1/configs":
            return _json([])
        raise AssertionError(path)

    _state, _authority, _binding, _handle, runtime = _broker(tmp_path, transport)
    socket_root = Path(tempfile.mkdtemp(prefix="profile-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    try:
        client = BoardProfileClient(service.path, PROFILE_HANDLE)
        assert client.qualify(RULES, "docs/competitions/fixture.board.json").authoritative is False
        calls_after_decision = len(calls)
        with pytest.raises(BoardFailure, match="gate remained closed"):
            client.open_operations()
        with pytest.raises(BoardFailure):
            BoardCompatibilityClient(service.path).challenges()
    finally:
        service.close()
        socket_root.rmdir()

    assert len(calls) == calls_after_decision


def test_truncated_profile_response_is_recorded_incomplete_and_never_qualifies(tmp_path: Path) -> None:
    landing = b'<script>window.init = {"userId": 7, "teamId": null, "userMode": "users"};</script>'

    def transport(request):
        path = request.full_url.removeprefix("https://board.example")
        if path == "/api/v1/users/me":
            return _json({"id": 7, "team_id": None})
        return 200, landing, "", "text/html"

    state, _authority, _binding, _handle, runtime = _broker(tmp_path, transport, sealed_response_bytes=32)
    socket_root = Path(tempfile.mkdtemp(prefix="profile-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    try:
        decision = BoardProfileClient(service.path, PROFILE_HANDLE).qualify(
            RULES, "docs/competitions/fixture.board.json"
        )
    finally:
        service.close()
        socket_root.rmdir()

    assert decision.authoritative is False
    receipt = json.loads((state / "runs" / "run-1" / "canonical" / "board-profile.receipt.json").read_text())
    observed = receipt["probe"]["cycles"][0]["documents"]["landing"]
    assert observed["complete"] is False
    assert observed["body_bytes"] == 32
    assert observed["original_bytes"] == len(landing)


def test_production_broker_permits_no_ordinary_request_before_profile_authority(tmp_path: Path) -> None:
    calls = []
    state, authority, _binding, _handle, _runtime = _broker(
        tmp_path,
        lambda request: calls.append(request) or _json([]),
    )
    runtime = BoardBrokerRuntime(
        state=state,
        run_id="run-1",
        authority=authority,
        url="https://board.example",
        token="board-token",
        team_key="team-key",
        boot_id="boot-000001",
        transport=lambda request: calls.append(request) or _json([]),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
        profile_required=True,
        profile_handle=PROFILE_HANDLE,
    )
    socket_root = Path(tempfile.mkdtemp(prefix="profile-", dir="/tmp"))
    service = BoardBrokerService(socket_root / "board.sock", runtime)
    service.start()
    try:
        with pytest.raises(BoardFailure):
            BoardCompatibilityClient(service.path).challenges()
    finally:
        service.close()
        socket_root.rmdir()

    assert calls == []


def test_wrong_profile_handle_and_later_wrong_peer_open_no_authority(tmp_path: Path) -> None:
    calls = []
    state, authority, _binding, _handle, runtime = _broker(tmp_path, _profile_transport(calls))

    with pytest.raises(PermissionError, match="authority refused"):
        runtime.qualify_profile(object(), "wrong", RULES, "docs/competitions/fixture.board.json")
    assert calls == []
    assert not any(event.event_type.startswith("board-profile") for event in EventStore(state, run_id="run-1").events())

    assert runtime.qualify_profile(
        object(), PROFILE_HANDLE, RULES, "docs/competitions/fixture.board.json"
    ).authoritative
    authority._peer_identity = lambda _connection: OTHER
    with pytest.raises(PermissionError, match="authority refused"):
        runtime.open_profiled_operations(object(), PROFILE_HANDLE)
    assert not runtime._profile_phase.state().operations_opened


def test_incomplete_probe_is_abandoned_and_restart_uses_a_fresh_identity(tmp_path: Path) -> None:
    calls = []
    state, _authority, _binding, _handle, runtime = _broker(tmp_path, _profile_transport(calls))
    rules_digest = hashlib.sha256(canonical_bytes(rules_document(RULES))).hexdigest()
    runtime._profile_phase.start(
        "profile-probe-000001", rules_digest, runtime._authority.peer_identity(object()).digest
    )

    decision = runtime.qualify_profile(object(), PROFILE_HANDLE, RULES, "docs/competitions/fixture.board.json")

    assert decision.authoritative
    starts = [
        event.payload["probe_id"]
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "board-profile-phase.recorded" and event.payload["record"] == "probe-started"
    ]
    assert starts == ["profile-probe-000001", "profile-probe-000002"]
    assert len(calls) == 18


def test_crash_after_receipt_before_decision_reprobes_without_trusting_the_orphan(tmp_path: Path) -> None:
    calls = []
    state, authority, _binding, _handle, runtime = _broker(tmp_path, _profile_transport(calls))
    runtime._profile_phase.decide = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash"))
    with pytest.raises(RuntimeError, match="crash"):
        runtime.qualify_profile(object(), PROFILE_HANDLE, RULES, "docs/competitions/fixture.board.json")
    assert (state / "runs" / "run-1" / "canonical" / "board-profile.receipt.json").is_file()

    restarted = BoardBrokerRuntime(
        state=state,
        run_id="run-1",
        authority=authority,
        url="https://board.example",
        token="board-token",
        team_key="team-key",
        boot_id="boot-000001",
        transport=_profile_transport(calls),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
        profile_handle=PROFILE_HANDLE,
    )
    assert restarted.qualify_profile(
        object(), PROFILE_HANDLE, RULES, "docs/competitions/fixture.board.json"
    ).authoritative
    assert len(calls) == 36


def test_competing_profile_clients_share_one_canonical_probe(tmp_path: Path) -> None:
    calls = []
    state, _authority, _binding, _handle, runtime = _broker(tmp_path, _profile_transport(calls))
    decisions = []

    def qualify_once():
        decisions.append(
            runtime.qualify_profile(object(), PROFILE_HANDLE, RULES, "docs/competitions/fixture.board.json")
        )

    clients = [threading.Thread(target=qualify_once) for _ in range(2)]
    for client in clients:
        client.start()
    for client in clients:
        client.join()

    assert [decision.authoritative for decision in decisions] == [True, True]
    assert len(calls) == 18
    assert (
        sum(
            event.event_type == "board-profile-observation.recorded"
            for event in EventStore(state, run_id="run-1").events()
        )
        == 18
    )


def test_open_race_and_receipt_substitution_fail_closed_without_network(tmp_path: Path) -> None:
    calls = []
    state, authority, _binding, _handle, runtime = _broker(tmp_path, _profile_transport(calls))
    with pytest.raises(PermissionError, match="authoritative profile"):
        runtime.open_profiled_operations(object(), PROFILE_HANDLE)
    assert calls == []
    assert runtime.qualify_profile(
        object(), PROFILE_HANDLE, RULES, "docs/competitions/fixture.board.json"
    ).authoritative
    receipt = state / "runs" / "run-1" / "canonical" / "board-profile.receipt.json"
    receipt.write_bytes(receipt.read_bytes() + b"\n")
    before = len(calls)
    restarted = BoardBrokerRuntime(
        state=state,
        run_id="run-1",
        authority=authority,
        url="https://board.example",
        token="board-token",
        team_key="team-key",
        boot_id="boot-000001",
        transport=lambda _request: (_ for _ in ()).throw(AssertionError("network")),
        timestamp=lambda: "2026-09-12T00:00:00+00:00",
        profile_handle=PROFILE_HANDLE,
    )
    with pytest.raises(ValueError, match="receipt digest disagrees"):
        restarted.qualify_profile(object(), PROFILE_HANDLE, RULES, "docs/competitions/fixture.board.json")
    assert len(calls) == before
