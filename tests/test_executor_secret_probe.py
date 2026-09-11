"""Executor probes retain only sanitized clear/found outcomes."""

import json
import os
import sys

import pytest

from solver.executor_secret_probe import classify_surfaces, probe_executor


SECRETS = (b"board-team-secret", b"codex-subscription-secret", b"cpa-oauth-secret")


def test_each_surface_is_classified_without_retaining_secret_bytes():
    surfaces = {
        "memory": b"ordinary heap bytes with board-team-secret inside",
        "environment": b"PATH=/usr/bin",
        "argv": b"python\0worker.py",
        "file": b"codex-subscription-secret",
        "event": b'{"record":"safe"}',
    }

    result = classify_surfaces(surfaces, SECRETS)

    assert dict(result.checks) == {
        "memory": False,
        "environment": True,
        "argv": True,
        "file": False,
        "event": True,
    }
    serialized = json.dumps(result.document(), sort_keys=True)
    assert all(secret.decode() not in serialized for secret in SECRETS)


@pytest.mark.skipif(sys.platform != "linux", reason="the sealed strict profile is Linux")
def test_a_real_exec_has_no_bootstrap_secret_on_any_executor_surface(tmp_path):
    environment = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "LANG": "C.UTF-8"}
    (tmp_path / "ordinary.txt").write_text("ordinary work")
    event_path = tmp_path / "events.jsonl"
    event_path.write_text('{"record":"ordinary"}\n')

    result = probe_executor(
        SECRETS,
        environment=environment,
        workdir=tmp_path,
        event_paths=(event_path,),
    )

    assert dict(result.checks) == {
        "memory": True,
        "environment": True,
        "argv": True,
        "file": True,
        "event": True,
    }
    assert result.memory_complete is True
