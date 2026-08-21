"""A login page must never read as data.

CTFd answers an unauthenticated request with a redirect to `/login`, and a session that has
quietly degraded answers with the login page itself at HTTP 200. Both shapes parse downstream as
"this board has nothing on it", which is the silent failure the probe exists to catch, so both
are held here.
"""

import ctfd_probe
import pytest

LOGIN_PAGE = b"<!DOCTYPE HTML>\n<HTML><head><title>Login</title></head><body>Log in</body></HTML>"


def board_answering(status, payload, location=""):
    """A board whose transport is stubbed at `request`, its one edge to the network."""
    board = ctfd_probe.Board("https://board.example/", "not-a-real-token")

    def answer(*_args, **_kwargs):
        return status, payload, location

    board.request = answer
    return board


def test_a_challenge_file_is_returned_as_bytes():
    board = board_answering(200, b"PK\x03\x04 challenge.zip")

    payload, hops = board.download("files/abcdef/challenge.zip")

    assert payload == b"PK\x03\x04 challenge.zip"
    assert hops == [], "a file served by the board itself passed through no other host"


def test_a_login_page_served_in_place_of_a_file_is_rejected():
    board = board_answering(200, LOGIN_PAGE)

    with pytest.raises(ctfd_probe.ProbeFailure, match="HTML page"):
        board.download("files/abcdef/challenge.zip")


def test_a_file_request_redirected_to_the_login_page_says_where_it_went():
    board = board_answering(302, b"", "/login")

    with pytest.raises(ctfd_probe.ProbeFailure, match="/login"):
        board.download("files/abcdef/challenge.zip")


def test_a_login_page_served_at_two_hundred_is_not_read_as_an_empty_board():
    board = board_answering(200, LOGIN_PAGE)

    with pytest.raises(ctfd_probe.ProbeFailure, match="not JSON"):
        board.json("GET", "/api/v1/challenges")


def test_an_api_request_redirected_to_the_login_page_names_its_status_and_destination():
    board = board_answering(302, b"", "/login")

    with pytest.raises(ctfd_probe.ProbeFailure, match="302.*/login"):
        board.json("GET", "/api/v1/challenges")


def test_a_body_reporting_its_own_failure_is_not_read_as_data():
    board = board_answering(200, b'{"success": false, "message": "token expired"}')

    with pytest.raises(ctfd_probe.ProbeFailure, match="success=false"):
        board.json("GET", "/api/v1/challenges")
