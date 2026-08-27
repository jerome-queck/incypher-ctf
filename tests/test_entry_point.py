"""`python3 -m solver` — what it refuses, what it writes before it does, and what it exits with.

The exit code is the interface: a supervisor at v2 and a human at 16:05 read the same number, and
v1's gate has to be able to tell a clean exit from a restart loop. So a Refusal is a sentence and a
2, a Run that could not reclaim what it held is a 1, and a Run that ended on its own clock with
nothing left behind is a 0.
"""

import json

import pytest
from solver import __main__ as entry
from solver.__main__ import BROKEN, CLEAN, REFUSED, main
from solver.board import Board
from test_board_profile import CONTROL_AGREEABLE, Wire
from test_run_loop import BOARD


@pytest.fixture
def boards(tmp_path):
    """One tracked profile, for a Board nothing in `docs/competitions/` claims."""
    (tmp_path / "one.board.json").write_text(
        json.dumps(
            {
                "event": "offline",
                "url": BOARD,
                "flag_wrappers": [r"brunner\{[^}]{1,256}\}"],
                "window_seconds": 3600,
                "prohibitions": ["no broad automated enumeration"],
            }
        )
    )
    return tmp_path


@pytest.fixture
def logged_in(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "auth.json").write_text("{}")
    monkeypatch.setattr(entry.boot, "CODEX_HOME", home)
    return home


def env(**overrides):
    return {"CTFD_URL": BOARD, "CTFD_API_TOKEN": "token", "RUN_ID": "gate-1", **overrides}


def wired(monkeypatch, wire):
    """Both Boards the entry point builds, over one scripted CTFd."""
    monkeypatch.setattr(entry, "Board", lambda url, token: Board(url, token, wire.transport))


def test_an_empty_environment_refuses_with_a_sentence_and_never_a_traceback(capsys, tmp_path, boards):
    assert main({}, run_state=tmp_path / "state", boards=boards) == REFUSED
    assert "CTFD_URL" in capsys.readouterr().err


def test_a_board_nothing_holds_rules_for_refuses_before_a_single_request(capsys, tmp_path, logged_in, boards):
    code = main(env(CTFD_URL="https://danmark.brunnerctf.dk"), run_state=tmp_path / "state", boards=boards)

    assert code == REFUSED
    assert "no Board profile" in capsys.readouterr().err


def test_a_board_that_fails_the_read_contract_control_refuses_the_run(capsys, monkeypatch, tmp_path, logged_in, boards):
    wired(monkeypatch, Wire(control=CONTROL_AGREEABLE))

    code = main(env(), run_state=tmp_path / "state", boards=boards)

    assert code == REFUSED
    assert "not composed by CTFd" in capsys.readouterr().err


def test_a_first_intake_that_cannot_be_believed_refuses_after_writing_what_it_read(
    capsys, monkeypatch, tmp_path, logged_in, boards
):
    """Two things at once, because they are one moment.

    Mid-Run an unbelievable sync keeps the last snapshot and carries on — a Board that cannot be read
    is not a Board that emptied. At boot there is no last snapshot to keep, so the Run would spend
    its window ranking nothing while reporting success, and it refuses instead. And the profile is
    already on disk when it does: ADR-0008's named failure needs a post-mortem to tell *the Solver
    behaved wrongly* from *the Solver read the Board wrongly*, and a Run that refused without saying
    what it read cannot answer that.
    """
    state = tmp_path / "state"
    wire = Wire(listed=[{"id": 1, "name": "alpha", "type": "standard"}], ledger=None, mana=None)

    def falls_over_after_the_profile(request):
        answered = wire.transport(request)
        if request.full_url.endswith("/api/v1/challenges"):
            wire.listed, wire.control = [], CONTROL_AGREEABLE
        return answered

    monkeypatch.setattr(entry, "Board", lambda url, token: Board(url, token, falls_over_after_the_profile))

    code = main(env(TEAM_KEY="never-print-me"), run_state=state, boards=boards)

    assert code == REFUSED
    assert "did not believe the Board" in capsys.readouterr().err
    written = [json.loads(line) for line in (state / "runs" / "gate-1" / "stream.jsonl").read_text().splitlines()]
    assert [one for one in written if one["record"] == "run-close"][0]["cause"].startswith(entry.REFUSED_AT_BOOT)

    opened = [one for one in written if one["record"] == "run-open"][0]["board_profile"]
    assert opened["event"] == "offline"
    assert opened["prohibitions"] == ["no broad automated enumeration"]
    assert opened["flag_wrappers"] == [r"brunner\{[^}]{1,256}\}"]
    assert opened["chall_manager"] == "absent"
    assert opened["run"]["run_id"] == "gate-1"
    assert opened["run"]["restarted"] is False
    # What is *held*, never what is held. A record that leaked the key it was proving we had would
    # be the worst possible trade (ADR-0010).
    assert opened["run"]["credentials_held"]["TEAM_KEY"] == "set"
    assert "never-print-me" not in json.dumps(opened)


def test_the_read_contract_is_refused_before_a_recorder_is_ever_made(monkeypatch, tmp_path, logged_in, boards):
    """A Board whose replies were not composed by CTFd is not a Run with a hole in its record — it
    is not a Run. Nothing is written, because nothing happened."""
    state = tmp_path / "state"
    wired(monkeypatch, Wire(listed=[], control=CONTROL_AGREEABLE))

    assert main(env(), run_state=state, boards=boards) == REFUSED
    assert not (state / "runs").exists()


def test_a_state_mount_that_is_not_there_refuses_rather_than_raising(capsys, monkeypatch, tmp_path, logged_in, boards):
    """The mount is the one thing outside the image a Run depends on, and Colima mounts `$HOME` and
    nothing else — a `-v` from outside it hands the container an empty directory in silence."""
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("")
    wired(monkeypatch, Wire(listed=[{"id": 1, "name": "alpha", "type": "standard"}], ledger=None, mana=None))

    assert main(env(), run_state=blocked / "state", boards=boards) == REFUSED
    assert "not usable as this Run's state" in capsys.readouterr().err


def test_the_exit_codes_are_the_three_a_reader_has_to_tell_apart():
    assert (CLEAN, BROKEN, REFUSED) == (0, 1, 2)
