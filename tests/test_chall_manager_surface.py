"""The chall-manager calls, held at `Board`'s single HTTP call.

Everything on trial here is a shape the plugin says through HTTP and that reads as something else:
a 200 that did not deploy, one 403 body meaning three different things, a 429 from a lock held by
another Challenge entirely, and a team-scoped ledger that is an HTML page. `Board` names each of
them, so nothing above it ever branches on a status code
([ADR-0008](../docs/adr/0008-one-image-for-every-board-and-two-seams-instead-of-one.md)).
"""

import datetime as dt
import json
import urllib.error

import pytest
from solver.board import (
    ABSENT,
    ANSWERED,
    DENIED,
    LOCKED,
    REFUSED,
    UNREACHABLE,
    Board,
    BoardFailure,
)

# The live page, kept as it is actually served (read against `hackathon.in-cypher.com`, 25 Aug
# 2026): the Category is the first column, and every heading cell opens `<th>` and closes `</td>`.
LEDGER = """
<html><body><div class="col-md-12">
  <h2>Chall-Manager by CTFer.io</h2>
  <table class="table table-striped border">
    <thead>
    <tr>
      <th class="sort-col text-center"><b>Category</b></td>
      <th class="sort-col text-center"><b>Challenge</b></td>
      <th class="text-center"><b>Connection Info</b></td>
      <th class="sort-col text-center"><b>Since</b></td>
      <th class="sort-col text-center"><b>Until</b></td>
    </tr>
    </thead>
    <tbody>
      <tr><td>Pwn</td><td>Silent Skies</td><td>nc 10.0.0.1 1337</td><td>2026-08-25 09:00:00</td><td>2026-08-25 10:00:00</td></tr>
      <tr><td>Web</td><td>Vital Signs</td><td>https://a.example</td><td>2026-08-25 09:30:00</td><td>2026-08-25 10:30:00</td></tr>
    </tbody>
  </table>
</div></body></html>
"""

MANA_EXHAUSTED = b'{"success": false, "data": {"message": "You or your team used up all your mana."}}'


def board_of(answers, calls=None):
    """A board answering by `(method, path)`, recording what it was asked."""

    def transport(request):
        path = request.full_url.split("board.example", 1)[1]
        if calls is not None:
            calls.append((request.get_method(), path, request.data))
        answer = answers[(request.get_method(), path)]
        if isinstance(answer, Exception):
            raise answer
        return answer

    return Board("https://board.example", "not-a-real-token", transport)


def test_a_deploy_that_worked_carries_the_address_and_the_deadline():
    board = board_of(
        {
            ("POST", "/api/v1/plugins/ctfd-chall-manager/instance"): (
                200,
                json.dumps(
                    {
                        "success": True,
                        "data": {"connectionInfo": "nc 10.0.0.1 1337", "until": "2026-08-25T10:00:00Z"},
                    }
                ).encode(),
                "",
            )
        }
    )

    reply = board.deploy_instance(42)

    assert reply.outcome == ANSWERED
    assert reply.connection_info == "nc 10.0.0.1 1337"
    assert reply.until == dt.datetime(2026, 8, 25, 10, 0, tzinfo=dt.timezone.utc)


def test_a_deadline_is_a_moment_by_the_time_it_leaves_this_module():
    """Go writes RFC3339 with nanoseconds, which `fromisoformat` will not take. Repairing that text
    is the wire format, and the wire format stops here."""
    board = board_of(
        {
            ("POST", "/api/v1/plugins/ctfd-chall-manager/instance"): (
                200,
                json.dumps({"success": True, "data": {"until": "2026-08-25T10:00:00.123456789Z"}}).encode(),
                "",
            )
        }
    )

    assert board.deploy_instance(42).until == dt.datetime(2026, 8, 25, 10, 0, 0, 123456, tzinfo=dt.timezone.utc)


def test_a_deadline_the_board_wrote_unreadably_is_no_deadline_rather_than_a_wrong_one():
    board = board_of(
        {
            ("POST", "/api/v1/plugins/ctfd-chall-manager/instance"): (
                200,
                json.dumps({"success": True, "data": {"until": "whenever"}}).encode(),
                "",
            )
        }
    )

    assert board.deploy_instance(42).until is None


def test_a_terminate_carries_the_challenge_in_the_query_and_in_a_body():
    """The one source-read of the plugin has the deploy taking a JSON body, the read taking a query
    string and the terminate taking a body, and no board we hold a token for has an instanced
    Challenge to re-verify it against. Sending both is a line; sending the wrong one is every
    terminate, and so every leak sweep."""
    calls = []
    board = board_of(
        {("DELETE", "/api/v1/plugins/ctfd-chall-manager/instance?challengeId=42"): (200, b'{"success": true}', "")},
        calls,
    )

    board.terminate_instance(42)

    assert json.loads(calls[0][2]) == {"challengeId": 42}


def test_a_deploy_names_the_challenge_in_a_json_body():
    calls = []
    board = board_of(
        {("POST", "/api/v1/plugins/ctfd-chall-manager/instance"): (200, b'{"success": true, "data": {}}', "")},
        calls,
    )

    board.deploy_instance(42)

    assert json.loads(calls[0][2]) == {"challengeId": 42}


def test_a_two_hundred_whose_body_says_it_did_not_happen_is_not_a_success():
    """Trap 1 — a POST for a Challenge that already has an Instance answers HTTP 200."""
    board = board_of(
        {("POST", "/api/v1/plugins/ctfd-chall-manager/instance"): (200, MANA_EXHAUSTED, "")},
    )

    reply = board.deploy_instance(42)

    assert reply.outcome == DENIED
    assert "mana" in reply.detail, "the body's own message reaches the caller for the record"


def test_the_three_armed_forbidden_is_named_once_and_not_interpreted():
    board = board_of({("POST", "/api/v1/plugins/ctfd-chall-manager/instance"): (403, MANA_EXHAUSTED, "")})

    assert board.deploy_instance(42).outcome == REFUSED


def test_the_per_team_lock_is_its_own_name():
    board = board_of({("DELETE", "/api/v1/plugins/ctfd-chall-manager/instance?challengeId=42"): (429, b"", "")})

    assert board.terminate_instance(42).outcome == LOCKED


def test_no_instance_is_absent_rather_than_a_failure():
    board = board_of({("GET", "/api/v1/plugins/ctfd-chall-manager/instance?challengeId=42"): (404, b"", "")})

    assert board.read_instance(42).outcome == ABSENT


def test_a_board_that_cannot_be_reached_answers_like_any_other_shape():
    """chall-manager being down at submission time is one of the ten named shapes, so a transport
    fault has to arrive as an answer rather than as an exception nobody above here expects."""
    board = board_of(
        {("GET", "/api/v1/plugins/ctfd-chall-manager/instance?challengeId=42"): urllib.error.URLError("no route")}
    )

    reply = board.read_instance(42)

    assert reply.outcome == UNREACHABLE
    assert "no route" in reply.detail


def test_a_login_page_where_json_was_expected_is_unreachable_and_never_an_empty_answer():
    board = board_of({("GET", "/api/v1/plugins/ctfd-chall-manager/mana"): (200, b"<html>Log in</html>", "")})

    assert board.mana().outcome == UNREACHABLE


def test_mana_is_read_as_two_numbers():
    board = board_of(
        {
            ("GET", "/api/v1/plugins/ctfd-chall-manager/mana"): (
                200,
                b'{"success": true, "data": {"used": 1, "total": 3}}',
                "",
            )
        }
    )

    mana = board.mana()

    assert (mana.outcome, mana.used, mana.total) == (ANSWERED, 1, 3)


def test_renewing_patches_the_instance_resource():
    calls = []
    board = board_of(
        {
            ("PATCH", "/api/v1/plugins/ctfd-chall-manager/instance?challengeId=42"): (
                200,
                b'{"success": true, "data": {}}',
                "",
            )
        },
        calls,
    )

    assert board.renew_instance(42).outcome == ANSWERED
    assert calls[0][0] == "PATCH"


def test_the_ledger_answers_with_records_and_never_with_html():
    """A heading is the tag that *opened* the cell: the plugin's own template opens every heading
    `<th>` and closes it `</td>`. Trusting the closing tag finds no headings at all, and a page that
    could not be read reported as an empty ledger is the one reading that leaves a leaked Instance
    held for the rest of the Run."""
    board = board_of({("GET", "/plugins/ctfd-chall-manager/instances"): (200, LEDGER.encode(), "")})

    assert [record.challenge_name for record in board.instances_held()] == ["Silent Skies", "Vital Signs"]


def test_the_ledger_is_read_by_its_own_column_headings_rather_than_by_position():
    """The Category is already the first column, and a Mana Cost column arrives in front of it
    wherever mana is enabled — so a fixed index reads a different field on every board."""
    with_mana = LEDGER.replace("<b>Category</b>", "<b>Mana Cost</b></td><th><b>Category</b>").replace(
        "<tr><td>Pwn", "<tr><td>3</td><td>Pwn"
    )
    board = board_of({("GET", "/plugins/ctfd-chall-manager/instances"): (200, with_mana.encode(), "")})

    assert board.instances_held()[0].challenge_name == "Silent Skies"


def test_a_ledger_holding_nothing_is_read_as_holding_nothing():
    empty = LEDGER.split("<tbody>")[0] + "<tbody></tbody></table></div></body></html>"
    board = board_of({("GET", "/plugins/ctfd-chall-manager/instances"): (200, empty.encode(), "")})

    assert board.instances_held() == ()


def test_a_ledger_that_could_not_be_read_is_never_reported_as_holding_nothing():
    """The leak sweep acts on this answer, and "nothing is held" is the one reading that leaves a
    leak in place for the rest of the Run — chall-manager never evicts (ADR-0007)."""
    board = board_of({("GET", "/plugins/ctfd-chall-manager/instances"): (200, b"<html>Log in</html>", "")})

    with pytest.raises(BoardFailure, match="ledger"):
        board.instances_held()


def test_a_redirected_ledger_page_is_a_failure_rather_than_an_empty_one():
    board = board_of({("GET", "/plugins/ctfd-chall-manager/instances"): (302, b"", "/login")})

    with pytest.raises(BoardFailure, match="302"):
        board.instances_held()
