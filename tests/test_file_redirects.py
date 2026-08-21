"""Challenge files may not live on the board, and following them must stay safe.

CTFd commonly answers `/files/…` with a redirect to object storage holding a presigned URL. The
API path still refuses redirects — that refusal is what stops a login page reading as an empty
board — so the file path is a deliberate exception, and these hold the three things that
exception must not cost: the file still arrives, the token does not travel with it, and a login
redirect is still caught.
"""

import ctfd_probe
import pytest

STORAGE = "https://nbg1.your-objectstorage.com/bucket/challenge.zip?X-Amz-Signature=abc"


def board_following(*answers):
    """A board whose transport replays a scripted sequence of answers, one per request."""
    board = ctfd_probe.Board("https://board.example/", "not-a-real-token")
    remaining = list(answers)

    def answer(_method, path, *_args, **_kwargs):
        board.asked.append(path)
        return remaining.pop(0)

    board.asked = []
    board.request = answer
    return board


def test_a_file_on_object_storage_is_fetched_and_its_host_reported():
    board = board_following(
        (302, b"", STORAGE),
        (200, b"PK\x03\x04 challenge.zip", ""),
    )

    payload, hops = board.download("files/abcdef/challenge.zip")

    assert payload == b"PK\x03\x04 challenge.zip"
    assert hops == ["nbg1.your-objectstorage.com"], "an off-platform host is named, not hidden"


def test_the_board_token_is_not_forwarded_to_the_storage_host():
    """The presigned URL carries its own credentials. Ours would be a gift to a stranger."""
    board = ctfd_probe.Board("https://board.example", "a-real-looking-token")
    sent = []

    class Recorder:
        def open(self, request, timeout=None):
            sent.append(request)
            raise ctfd_probe.urllib.error.HTTPError(request.full_url, 200, "ok", {}, None)

    board._opener = Recorder()

    board.request("GET", "/api/v1/challenges")
    board.request("GET", STORAGE)

    assert sent[0].get_header("Authorization") == "Token a-real-looking-token"
    assert sent[1].get_header("Authorization") is None, "the token must not leave the board's host"


def test_a_redirect_chain_that_never_lands_is_reported_rather_than_chased():
    hop = (302, b"", "https://redirector.example/again")
    board = board_following(*[hop] * ctfd_probe.MAX_FILE_REDIRECTS)

    with pytest.raises(ctfd_probe.ProbeFailure, match="still redirecting"):
        board.download("files/abcdef/challenge.zip")


def test_following_redirects_does_not_let_a_login_screen_through():
    board = board_following((302, b"", "/login?next=%2Ffiles"))

    with pytest.raises(ctfd_probe.ProbeFailure, match="login screen"):
        board.download("files/abcdef/challenge.zip")
