"""PASS, SKIP and FAIL — the outcome the probe gives a check, and the line it prints for it.

`_report` is private to the script and is still the seam issue #22 named, because the mapping
lives nowhere else: the only public caller is `main`, which needs a live board. What it holds is
the probe's own central claim — a check that could not run is never reported as one that passed.
"""

import json
import urllib.error

import ctfd_probe
import pytest


def raising(error):
    def check() -> str:
        raise error

    return check


def test_a_check_that_returns_a_summary_passes(capsys):
    outcome = ctfd_probe._report("edge is not blocking", lambda: "requests reach CTFd itself")

    assert outcome == ctfd_probe.PASS
    assert "PASS  edge is not blocking: requests reach CTFd itself" in capsys.readouterr().out


def test_a_check_that_could_not_run_is_skipped_rather_than_passed(capsys):
    outcome = ctfd_probe._report("files download", raising(ctfd_probe.Unproven("no file on the board yet")))

    assert outcome == ctfd_probe.SKIP
    assert "SKIP  files download: no file on the board yet" in capsys.readouterr().out


def test_a_check_declined_up_front_is_skipped_too(capsys):
    """`--no-attempt` leaves submission semantics unverified, which is a gap and not a pass."""
    outcome = ctfd_probe._report("attempt verdict", ctfd_probe._unproven("--no-attempt was passed"))

    assert outcome == ctfd_probe.SKIP
    assert "SKIP  attempt verdict: --no-attempt was passed" in capsys.readouterr().out


def test_a_board_the_solver_would_have_misread_fails(capsys):
    outcome = ctfd_probe._report("token is recognised", raising(ctfd_probe.ProbeFailure("answered 401")))

    assert outcome == ctfd_probe.FAIL
    assert "FAIL  token is recognised: answered 401" in capsys.readouterr().out


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.URLError("no route to host"),
        KeyError("data"),
        json.JSONDecodeError("Expecting value", "", 0),
    ],
    ids=["unreachable", "answer missing a field", "answer that is not JSON"],
)
def test_a_board_that_cannot_answer_fails(error):
    assert ctfd_probe._report("token is recognised", raising(error)) == ctfd_probe.FAIL


def test_an_error_the_probe_did_not_expect_is_not_dressed_up_as_a_failed_check():
    """A bug in the probe has to look like a bug, not like a board that answered badly."""
    with pytest.raises(RuntimeError):
        ctfd_probe._report("token is recognised", raising(RuntimeError("bug in the probe")))
