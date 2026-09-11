"""Bootstrap secrets cross once into owner-specific broker processes."""

import json
import os

import pytest

from solver.bootstrap_custody import BootstrapCustody, Broker, SecretSource
from solver.event_store import EventStore
from solver.redaction import Redactor


def records(tmp_path) -> list[dict[str, object]]:
    return [
        dict(event.payload)
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.event_type == "capability-custody.recorded"
    ]


def test_each_secret_crosses_once_to_its_own_broker_then_sources_are_cleared(tmp_path):
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "executor-home"),
        "LANG": "C.UTF-8",
        "TEAM_KEY": "board-team-secret",
    }
    codex_file = tmp_path / "codex-bootstrap"
    cpa_file = tmp_path / "cpa-bootstrap"
    codex_file.write_text("codex-subscription-secret")
    cpa_file.write_text("cpa-oauth-secret")
    codex_file.chmod(0o600)
    cpa_file.chmod(0o600)
    sources = [
        SecretSource.from_environment(environment, "TEAM_KEY", Broker.BOARD),
        SecretSource.from_temporary_file(codex_file, "CODEX_AUTH", Broker.CODEX),
        SecretSource.from_temporary_file(cpa_file, "CPA_AUTH", Broker.CPA),
    ]
    custody = BootstrapCustody(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-000001",
        redactor=Redactor(
            {
                "TEAM_KEY": "board-team-secret",
                "CODEX_AUTH": "codex-subscription-secret",
                "CPA_AUTH": "cpa-oauth-secret",
            }
        ),
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )

    result = custody.transfer(sources, environment)
    try:
        assert set(result.brokers) == {Broker.BOARD, Broker.CODEX, Broker.CPA}
        assert len({broker.pid for broker in result.brokers.values()}) == 3
        assert all(broker.pid != os.getpid() for broker in result.brokers.values())
        assert all(result.receipts[owner].pid == broker.pid for owner, broker in result.brokers.items())
        assert all(receipt.uid == os.getuid() for receipt in result.receipts.values())
        assert {owner: receipt.secret_names for owner, receipt in result.receipts.items()} == {
            Broker.BOARD: ("TEAM_KEY",),
            Broker.CODEX: ("CODEX_AUTH",),
            Broker.CPA: ("CPA_AUTH",),
        }
        assert result.executor_environment == {
            "PATH": environment["PATH"],
            "HOME": environment["HOME"],
            "LANG": "C.UTF-8",
        }
        assert "TEAM_KEY" not in environment
        assert all(source.cleared for source in sources)
        assert all(source.zeroed for source in sources)
        assert not codex_file.exists()
        assert not cpa_file.exists()

        audit = json.dumps(records(tmp_path), sort_keys=True)
        assert "board-team-secret" not in audit
        assert "codex-subscription-secret" not in audit
        assert "cpa-oauth-secret" not in audit
        assert [record["record"] for record in records(tmp_path)] == [
            "transfer-accepted",
            "transfer-accepted",
            "transfer-accepted",
            "source-cleared",
            "source-cleared",
            "source-cleared",
        ]
    finally:
        result.close()

    assert all(broker.poll() is not None for broker in result.brokers.values())


def test_failed_transfer_clears_every_source_and_stops_started_brokers(tmp_path):
    environment = {"TEAM_KEY": "board-secret", "CPA_TOKEN": "cpa-secret"}
    sources = [
        SecretSource.from_environment(environment, "TEAM_KEY", Broker.BOARD),
        SecretSource.from_environment(environment, "CPA_TOKEN", Broker.CPA),
    ]
    started = []

    def fail_second(owner, secrets):
        if started:
            raise RuntimeError("second broker did not acknowledge")
        from solver.bootstrap_custody import BrokerVaultProcess

        broker, receipt = BrokerVaultProcess.start(owner, secrets)
        started.append(broker)
        return broker, receipt

    custody = BootstrapCustody(
        state=tmp_path,
        run_id="run-1",
        boot_id="boot-000001",
        redactor=Redactor({"TEAM_KEY": "board-secret", "CPA_TOKEN": "cpa-secret"}),
        timestamp=lambda: "2026-09-11T00:00:00Z",
        start_broker=fail_second,
    )

    try:
        custody.transfer(sources, environment)
    except RuntimeError as error:
        assert str(error) == "second broker did not acknowledge"
    else:
        raise AssertionError("the failed transfer returned")

    assert environment == {}
    assert all(source.cleared for source in sources)
    assert all(source.zeroed for source in sources)
    assert started[0].poll() is not None


def test_temporary_secret_source_rejects_symlink_and_nonprivate_file(tmp_path):
    public = tmp_path / "public-secret"
    public.write_text("secret")
    public.chmod(0o644)
    link = tmp_path / "linked-secret"
    link.symlink_to(public)

    with pytest.raises(PermissionError, match="private and regular"):
        SecretSource.from_temporary_file(public, "CODEX_AUTH", Broker.CODEX)
    with pytest.raises(OSError):
        SecretSource.from_temporary_file(link, "CODEX_AUTH", Broker.CODEX)


def test_temporary_source_does_not_clear_a_path_swapped_after_read(tmp_path):
    path = tmp_path / "bootstrap-secret"
    path.write_text("original")
    path.chmod(0o600)
    source = SecretSource.from_temporary_file(path, "CODEX_AUTH", Broker.CODEX)
    replacement = tmp_path / "replacement"
    replacement.write_text("do-not-touch")
    replacement.chmod(0o600)
    os.replace(replacement, path)

    with pytest.raises(PermissionError, match="changed before clear"):
        source.clear()

    assert path.read_text() == "do-not-touch"
