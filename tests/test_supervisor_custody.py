"""PID-1 production wiring for bootstrap custody and executor probes."""

import os

import pytest

from solver.boot import Refusal
from solver.bootstrap_custody import Broker
from solver.event_store import EventStore
from solver.executor_secret_probe import classify_surfaces
from solver.redaction import Redactor
from solver.supervisor_custody import SupervisorCustody


def _probe_result(secrets, *, found=False):
    surfaces = {name: b"ordinary" for name in ("memory", "environment", "argv", "file", "event")}
    if found:
        surfaces["file"] = secrets[0]
    return classify_surfaces(surfaces, secrets)


def test_supervisor_transfers_and_clears_inherited_secrets_then_probes_a_clean_executor(tmp_path):
    environment = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "LANG": "C.UTF-8",
        "CTFD_API_TOKEN": "board-token",
        "TEAM_KEY": "team-key",
    }
    observed = {}

    def probe(secrets, **options):
        observed.update(options)
        assert "CTFD_API_TOKEN" not in environment
        assert "TEAM_KEY" not in environment
        return _probe_result(secrets)

    custody = SupervisorCustody(
        state=tmp_path,
        run_id="run-1",
        environment=environment,
        redactor=Redactor({"CTFD_API_TOKEN": "board-token", "TEAM_KEY": "team-key"}),
        timestamp=lambda: "2026-09-11T00:00:00Z",
        probe=probe,
    )

    result = custody.open("boot-000001")
    try:
        assert set(result.brokers) == {Broker.BOARD}
        assert result.receipts[Broker.BOARD].secret_names == ("CTFD_API_TOKEN", "TEAM_KEY")
        assert environment == result.executor_environment
        assert observed["environment"] == result.executor_environment
        records = [
            event.payload["record"]
            for event in EventStore(tmp_path, run_id="run-1").events()
            if event.event_type == "capability-custody.recorded"
        ]
        assert records == [
            "transfer-accepted",
            "source-cleared",
            "source-cleared",
            "probe-recorded",
            "probe-recorded",
            "probe-recorded",
            "probe-recorded",
            "probe-recorded",
        ]
    finally:
        result.close()


def test_nonclear_executor_probe_refuses_and_closes_broker(tmp_path):
    environment = {"PATH": os.environ["PATH"], "TEAM_KEY": "team-key"}

    def probe(secrets, **_options):
        return _probe_result(secrets, found=True)

    custody = SupervisorCustody(
        state=tmp_path,
        run_id="run-1",
        environment=environment,
        redactor=Redactor({"TEAM_KEY": "team-key"}),
        timestamp=lambda: "2026-09-11T00:00:00Z",
        probe=probe,
    )

    with pytest.raises(Refusal, match="did not prove every surface clear"):
        custody.open("boot-000001")

    assert environment == {"PATH": os.environ["PATH"]}
