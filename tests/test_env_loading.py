"""`.env` parsing — where a file on disk becomes the credentials the Solver runs on.

`load_env` decides what a line means before anything touches the network, so what it does is
decidable offline. That is why it is one of the three seams issue #22 put under test.
"""

import ctfd_probe
import pytest


@pytest.fixture
def environment(monkeypatch):
    """A throwaway `os.environ` for the duration of one test.

    `load_env` writes through the real one, and `monkeypatch` is what puts it back afterwards —
    without that, a variable a test wrote would outlive it and be read by the next.
    """
    replacement: dict[str, str] = {}
    monkeypatch.setattr(ctfd_probe.os, "environ", replacement)
    return replacement


def env_file(tmp_path, body):
    path = tmp_path / ".env"
    path.write_text(body)
    return path


def test_a_key_and_value_become_an_environment_variable(tmp_path, environment):
    ctfd_probe.load_env(env_file(tmp_path, "CTFD_URL=https://global.brunnerctf.dk\n"))

    assert environment["CTFD_URL"] == "https://global.brunnerctf.dk"


def test_comments_and_blank_lines_carry_nothing(tmp_path, environment):
    ctfd_probe.load_env(env_file(tmp_path, "# CTFD_URL=https://danmark.brunnerctf.dk\n\n   \n"))

    assert "CTFD_URL" not in environment


def test_a_line_that_assigns_nothing_is_ignored(tmp_path, environment):
    ctfd_probe.load_env(env_file(tmp_path, "export CTFD_URL\n"))

    assert "CTFD_URL" not in environment


def test_only_the_first_equals_sign_splits_a_line(tmp_path, environment):
    ctfd_probe.load_env(env_file(tmp_path, "CTFD_URL=https://board.example/ctf?season=2026\n"))

    assert environment["CTFD_URL"] == "https://board.example/ctf?season=2026"


def test_surrounding_whitespace_is_not_part_of_the_value(tmp_path, environment):
    ctfd_probe.load_env(env_file(tmp_path, "  CTFD_URL = https://board.example  \n"))

    assert environment["CTFD_URL"] == "https://board.example"


def test_the_environment_wins_over_the_file(tmp_path, environment):
    """A stale `.env` beside the repository must not override what the container was handed.

    Pointing the Solver at the wrong board is a disqualification rather than a misconfiguration
    (`docs/competitions/brunnerctf-2026-global.md`), so which of the two wins is worth pinning.
    """
    environment["CTFD_URL"] = "https://global.brunnerctf.dk"

    ctfd_probe.load_env(env_file(tmp_path, "CTFD_URL=https://danmark.brunnerctf.dk\n"))

    assert environment["CTFD_URL"] == "https://global.brunnerctf.dk"


def test_a_missing_file_is_not_an_error(tmp_path, environment):
    """The container is handed its credentials by `--env-file`, with no `.env` on disk at all."""
    ctfd_probe.load_env(tmp_path / ".env")

    assert "CTFD_URL" not in environment
