"""Challenge files may not live on the board, and following them must stay safe.

CTFd commonly answers `/files/…` with a redirect to object storage holding a presigned URL. The
API path still refuses redirects — that refusal is what stops a login page reading as an empty
board — so the file path is a deliberate exception, and these hold the three things that
exception must not cost: the file still arrives, the token does not travel with it, and a login
redirect is still caught.
"""

import pytest
from solver.board import MAX_FILE_REDIRECTS, Board, BoardFailure

STORAGE = "https://nbg1.your-objectstorage.com/bucket/challenge.zip?X-Amz-Signature=abc"


def board_following(*answers):
    """A board whose transport replays a scripted sequence of answers, one per request."""
    remaining = list(answers)

    def transport(_request):
        return remaining.pop(0)

    return Board("https://board.example/", "not-a-real-token", transport)


def test_a_file_on_object_storage_is_fetched_and_its_host_reported():
    board = board_following(
        (302, b"", STORAGE),
        (200, b"PK\x03\x04 challenge.zip", ""),
    )

    payload, hops = board.download("files/abcdef/challenge.zip")

    assert payload == b"PK\x03\x04 challenge.zip"
    assert hops == ["nbg1.your-objectstorage.com"], "an off-platform host is named, not hidden"


def test_the_board_token_is_not_forwarded_to_the_storage_host():
    """The presigned URL carries its own credentials. Ours would be a gift to a stranger.

    Asserted against the real `urllib.request.Request` the transport is handed, because what is
    on trial is the headers that would have gone on the wire.
    """
    sent = []

    def transport(request):
        sent.append(request)
        return 200, b"", ""

    board = Board("https://board.example", "a-real-looking-token", transport)

    board.request("GET", "/api/v1/challenges")
    board.request("GET", STORAGE)

    assert sent[0].get_header("Authorization") == "Token a-real-looking-token"
    assert sent[1].get_header("Authorization") is None, "the token must not leave the board's host"


def test_a_redirect_chain_that_never_lands_is_reported_rather_than_chased():
    hop = (302, b"", "https://redirector.example/again")
    board = board_following(*[hop] * MAX_FILE_REDIRECTS)

    with pytest.raises(BoardFailure, match="still redirecting"):
        board.download("files/abcdef/challenge.zip")


def test_following_redirects_does_not_let_a_login_screen_through():
    board = board_following((302, b"", "/login?next=%2Ffiles"))

    with pytest.raises(BoardFailure, match="login screen"):
        board.download("files/abcdef/challenge.zip")
