"""An empty challenge list is several situations wearing one shape.

`{"success": true, "data": []}` is what the board says when the event has not opened, when it is
over, when a stranger is shown less than an account would be, and when there is genuinely nothing
on it. The probe used to answer all four with one line that blamed the clock or the credential —
and a 200 has already cleared the credential, because an absent token answers 302, a rejected one
401 and a request without `Content-Type: application/json` 302. Naming the wrong cause is the
expensive kind of wrong: it sends someone to rotate a token that was never at fault.
"""

import datetime as dt

import ctfd_probe
import pytest
from solver.board import Board

HOUR = dt.timedelta(hours=1)
EMPTY_LIST = b'{"success": true, "data": []}'

BOGUS_FIELD_REJECTION = (
    b'{"success": false, "errors": {"field": "value is not a valid enumeration member; '
    b"permitted: 'name', 'description', 'category', 'type'\"}}"
)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def board_publishing(opens: dt.datetime | None, closes: dt.datetime | None, *, token: str = "") -> Board:
    """A board whose challenge list is empty and whose landing page publishes the given window.

    CTFd embeds the window in `window.init` on every HTML page, which is where `board_window`
    reads it and the only place a competitor can.
    """
    window = "".join(
        f"'{name}': {int(moment.timestamp())},"
        for name, moment in (("start", opens), ("end", closes))
        if moment is not None
    )
    page = f"<script>window.init = {{{window}}}</script>".encode()
    board = Board("https://board.example/", token)

    def answer(_method: str, path: str, *_args, **_kwargs):
        if path == "/":
            return (200, page, "")
        # CTFd validates `field` against an enumeration before any handler runs. A fixture that
        # answered 200 here would be a board no CTFd install behaves like, and would quietly
        # exempt every case below from the control that runs ahead of them.
        if "field=" in path:
            return (400, BOGUS_FIELD_REJECTION, "")
        return (200, EMPTY_LIST, "")

    board.request = answer
    return board


# The four situations, built on call rather than held as values: a board fixed at import carries
# the clock reading of collection time, which is not the one the assertion is about.


def not_yet_open() -> Board:
    return board_publishing(now() + HOUR, now() + 100 * HOUR, token="a-real-token")


def already_closed() -> Board:
    return board_publishing(now() - 100 * HOUR, now() - HOUR, token="a-real-token")


def open_to_a_stranger() -> Board:
    return board_publishing(now() - HOUR, now() + HOUR)


def open_to_an_account() -> Board:
    return board_publishing(now() - HOUR, now() + HOUR, token="a-real-token")


EVERY_SITUATION = (not_yet_open, already_closed, open_to_a_stranger, open_to_an_account)


def test_a_board_that_has_not_opened_is_unproven_rather_than_failed():
    """The clock is not a fault, and `refuse_to_blame_the_token` already treats it as a gap."""
    with pytest.raises(ctfd_probe.Unproven, match="has not started"):
        ctfd_probe.name_the_cause_of_an_empty_list(not_yet_open())


def test_a_board_whose_event_is_over_is_unproven_rather_than_failed():
    with pytest.raises(ctfd_probe.Unproven, match="is over"):
        ctfd_probe.name_the_cause_of_an_empty_list(already_closed())


def test_an_anonymous_reader_is_told_it_is_seeing_a_strangers_board():
    """A stranger's view is not the Solver's view, and the difference is not a fault to fix."""
    with pytest.raises(ctfd_probe.ProbeFailure, match="stranger"):
        ctfd_probe.name_the_cause_of_an_empty_list(open_to_a_stranger())


def test_an_authenticated_empty_board_clears_the_credential_and_names_what_is_left():
    """The one case that is genuinely "nothing is listed for us" — and it is still two things.

    A Challenge withdrawn from the list is indistinguishable from one that was never there, so
    the verdict has to hand the reader the one request that separates them.
    """
    with pytest.raises(ctfd_probe.ProbeFailure, match="/solves") as failure:
        ctfd_probe.name_the_cause_of_an_empty_list(open_to_an_account())

    assert "not a credential fault" in str(failure.value)


@pytest.mark.parametrize("situation", EVERY_SITUATION, ids=lambda build: build.__name__)
def test_no_verdict_claims_the_session_degraded(situation):
    """The message this replaced said "auth degraded silently", which the 200 had already ruled out.

    Every shape that means "we are not being shown the board" is a non-200: no token answers 302,
    a rejected one 401, a request without Content-Type 302, and the edge 403. Sending someone to
    a credential on the strength of a 200 is the mistake worth a test of its own.
    """
    with pytest.raises((ctfd_probe.ProbeFailure, ctfd_probe.Unproven)) as verdict:
        ctfd_probe.name_the_cause_of_an_empty_list(situation())

    assert "degraded" not in str(verdict.value)
    assert "revoked" not in str(verdict.value)


def test_a_board_that_publishes_no_window_still_reaches_a_verdict():
    """`board_window` returns nothing for a board that publishes no schedule, and that is not a
    reason to fall back on the message the clock would have produced."""
    board = board_publishing(None, None, token="a-real-token")

    with pytest.raises(ctfd_probe.ProbeFailure, match="not a credential fault"):
        ctfd_probe.name_the_cause_of_an_empty_list(board)


def test_the_enumeration_check_routes_an_empty_list_through_the_diagnosis():
    """The seam that matters: `check_challenges_enumerate` is where an empty list arrives."""
    with pytest.raises(ctfd_probe.ProbeFailure, match="/solves"):
        ctfd_probe.check_challenges_enumerate(open_to_an_account())


def test_a_populated_board_is_still_summarised_rather_than_diagnosed():
    """The diagnosis is for the empty case only; a board with Challenges on it reports them."""
    board = Board("https://board.example/", "a-real-token")
    board.request = lambda *_args, **_kwargs: (
        200,
        b'{"success": true, "data": [{"id": 8, "type": "standard", "category": "(Practice) forensics"}]}',
        "",
    )

    summary, challenges = ctfd_probe.check_challenges_enumerate(board)

    assert "1 challenges" in summary
    assert "(Practice) forensics" in summary
    assert [challenge["id"] for challenge in challenges] == [8]


# The diagnosis above reasons about a board that answered. Whether CTFd answered at all is prior
# to every cause it names, and is the one thing an empty list cannot tell you.


def board_answering_the_control(
    status: int,
    body: bytes,
    *,
    token: str = "a-real-token",
    opens: dt.datetime | None = None,
    closes: dt.datetime | None = None,
) -> Board:
    """A board whose challenge list is empty and which answers the given status to the control.

    The window defaults to open-now, because most of these assertions are about the control alone.
    Passing one is how a test asks what happens when a second cause is *also* true.
    """
    board = board_publishing(opens or now() - HOUR, closes or now() + HOUR, token=token)
    listing = board.request

    def answer(method: str, path: str, *args, **kwargs):
        return (status, body, "") if "field=" in path else listing(method, path, *args, **kwargs)

    board.request = answer
    return board


def test_a_board_that_accepts_an_invalid_field_is_not_answering_from_ctfd():
    """CTFd rejects an unknown `field` in `validate_args` before any handler runs, so a 200 to it
    is proof the reply was composed somewhere else — and an empty collection from that somewhere
    is evidence of nothing. Measured on the IN-CYPHER arena, where every collection endpoint
    answers this way while `/api/v1/challenges/8/solves` returns real rows."""
    board = board_answering_the_control(200, EMPTY_LIST)

    with pytest.raises(ctfd_probe.ProbeFailure, match="did not come from CTFd"):
        ctfd_probe.name_the_cause_of_an_empty_list(board)


def test_the_interposed_layer_is_named_before_the_clock_or_the_account():
    """Ordering is the whole point. A board that is also outside its window would otherwise be
    told it is closed, which is a true statement about a reply CTFd never composed."""
    board = board_answering_the_control(200, EMPTY_LIST, opens=now() - 100 * HOUR, closes=now() - HOUR)

    with pytest.raises(ctfd_probe.ProbeFailure, match="did not come from CTFd"):
        ctfd_probe.name_the_cause_of_an_empty_list(board)


@pytest.mark.parametrize("status,body", [(400, BOGUS_FIELD_REJECTION), (403, b""), (302, b"")])
def test_a_board_that_refuses_the_invalid_field_is_diagnosed_on_its_own_terms(status: int, body: bytes):
    """Any refusal is CTFd-shaped enough to proceed. The control exists to catch the reply that is
    too agreeable, not to certify the stack that produced a normal one."""
    board = board_answering_the_control(status, body)

    with pytest.raises(ctfd_probe.ProbeFailure, match="not a credential fault"):
        ctfd_probe.name_the_cause_of_an_empty_list(board)


def test_the_control_costs_nothing_on_a_board_that_lists_challenges():
    """It runs only where an empty list already arrived, so a working board never pays for it."""
    asked: list[str] = []
    board = Board("https://board.example/", "a-real-token")

    def answer(_method: str, path: str, *_args, **_kwargs):
        asked.append(path)
        return (200, b'{"success": true, "data": [{"id": 8, "type": "standard", "category": "web"}]}', "")

    board.request = answer
    ctfd_probe.check_challenges_enumerate(board)

    assert not [path for path in asked if "field=" in path]
