"""Atomic cutover of the active environment file."""

import os

import env_file
import pytest


def test_atomic_replace_writes_new_utf8_bytes_and_private_mode(tmp_path):
    path = tmp_path / ".env"
    path.write_bytes(b"OLD=bytes\n")

    result = env_file.atomic_replace(path, "BOARD=naïve\n")

    assert result.path == path
    assert result.completed_atomic_env_cutover
    assert path.read_bytes() == "BOARD=naïve\n".encode("utf-8")
    assert path.stat().st_mode & 0o777 == 0o600


def test_env_example_is_not_an_ambiguous_authority(tmp_path):
    (tmp_path / ".env.example").write_text("BOARD=template\n")
    path = tmp_path / ".env"

    env_file.atomic_replace(path, "BOARD=active\n")

    assert path.read_text() == "BOARD=active\n"


@pytest.mark.parametrize("legacy_name", [".env.incypher", ".env.event"])
def test_legacy_overlay_rejects_before_changing_existing_env(tmp_path, legacy_name):
    path = tmp_path / ".env"
    old_bytes = b"BOARD=old\n"
    path.write_bytes(old_bytes)
    (tmp_path / legacy_name).write_text("BOARD=legacy-secret\n")
    before = sorted(item.name for item in tmp_path.iterdir())

    with pytest.raises(env_file.AmbiguousEnvironmentAuthority) as error:
        env_file.atomic_replace(path, "BOARD=new\n")

    assert path.read_bytes() == old_bytes
    assert sorted(item.name for item in tmp_path.iterdir()) == before
    assert legacy_name in str(error.value)
    assert "legacy-secret" not in str(error.value)


def test_only_dot_env_can_become_environment_authority(tmp_path):
    with pytest.raises(env_file.AmbiguousEnvironmentAuthority, match="sanctioned environment"):
        env_file.atomic_replace(tmp_path / ".env.event", "BOARD=new\n")


def test_setup_staging_file_is_not_itself_legacy_authority(tmp_path):
    active = tmp_path / ".env"
    staged = tmp_path / ".setup-env-stage-fixture"
    staged.write_text("BOARD=new\n")

    env_file.atomic_replace(active, staged.read_text())

    assert active.read_text() == "BOARD=new\n"


def test_pre_replace_os_failure_preserves_old_bytes_and_cleans_temp(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    old_bytes = b"BOARD=old\n"
    path.write_bytes(old_bytes)

    def fail_fsync(_fd):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="injected fsync failure"):
        env_file.atomic_replace(path, "BOARD=new\n")

    assert path.read_bytes() == old_bytes
    assert list(tmp_path.iterdir()) == [path]
