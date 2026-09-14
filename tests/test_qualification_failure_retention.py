from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import qualify_attempt_resources as attempt
from scripts import qualify_research_broker as research


@pytest.mark.parametrize(
    ("module", "probe"),
    [
        (attempt, "attempt_resource_probe_command"),
        (research, "research_broker_probe_command"),
    ],
)
def test_failed_qualification_keeps_state_for_review(tmp_path: Path, monkeypatch, module, probe: str) -> None:
    state = tmp_path / module.__name__.split(".")[-1]
    monkeypatch.setattr(module.runtime, "verify", lambda: 0)
    monkeypatch.setattr(module.strict_runtime, "build_image", lambda _runner: SimpleNamespace(image_id="image"))
    monkeypatch.setattr(module.strict_runtime, probe, lambda *_args: ["docker", "probe"])

    def run(command, **_options):
        if command == ["docker", "probe"]:
            raise RuntimeError("probe failed")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="probe failed"):
        module.qualify(state)

    assert state.is_dir()
