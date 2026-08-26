"""Intake is a sync, and what is on trial is everything a fetch would get away with.

A Board moves underneath the Solver — it releases Challenges mid-event, edits a description at
13:00 and replaces a file that was wrong — so the tests below are mostly about the *second* run:
what it notices, what it must not re-download, and what it must refuse to believe. The one that
matters most is the last kind: a Board answering `{"success": true, "data": []}` from something
that is not CTFd is a Run that terminates cleanly at 10:20 having done nothing wrong by any check
it makes ([ADR-0016](../docs/adr/0016-an-empty-list-is-not-an-empty-board.md)).
"""

import datetime as dt
import json

import pytest
from solver.board import Board
from solver.intake import OVER_THE_CAP, UNCORROBORATED, UNFETCHED, UNNAMED, UNREADABLE, Intake, Limits
from solver.record import Recorder
from solver.redaction import Redactor

BOARD = "https://board.example"

# CTFd's own shape: a directory named by a hash of the content, and a signature minted per read.
# The two halves are the whole of change detection — the first is the file's identity and the
# second is not, and a client that compared whole strings would re-download the Board every cycle.
COVER = "files/1a1a1a/cover.png?token=signed-at-10-00"
COVER_RE_SIGNED = "files/1a1a1a/cover.png?token=signed-at-10-05"
COVER_REPLACED = "files/2b2b2b/cover.png?token=signed-at-10-05"

CONTROL_REFUSED = (400, b'{"success": false, "errors": {"field": "value is not a valid enumeration member"}}')
CONTROL_AGREEABLE = (200, b'{"success": true, "data": []}')

# So that `mana=None` can mean "this Board has no chall-manager" rather than "say nothing about it".
_UNSAID = object()


class Wire:
    """A CTFd that answers from a script, and remembers everything it was asked.

    The counting is the point in most of the tests below: "the second sync downloaded nothing" and
    "mana was read once for the Run" are both statements about which requests were *not* made.
    """

    def __init__(self, *, listed=None, detail=None, files=None, control=CONTROL_REFUSED, mana=_UNSAID, scoreboard=None):
        self.listed = [] if listed is None else listed
        self.detail = detail or {}
        self.files = files or {}
        self.control = control
        self.mana = {"used": 0, "total": 4} if mana is _UNSAID else mana
        self.scoreboard = scoreboard if scoreboard is not None else {"1": {"name": "them", "score": 900}}
        self.asked: list[str] = []
        self.unreachable = False

    def transport(self, request):
        path = request.full_url[len(BOARD) :]
        self.asked.append(path)
        if self.unreachable:
            raise OSError("the board is not answering")
        if "field=" in path:
            return (*self.control, "")
        if path.startswith("/api/v1/challenges/"):
            return self._answer(self.detail.get(path.rsplit("/", 1)[1], {}))
        if path == "/api/v1/challenges":
            return self._answer(self.listed)
        if path.startswith("/api/v1/scoreboard/top/"):
            return self._answer(self.scoreboard)
        if path.endswith("/mana"):
            # `None` is a Board with no chall-manager at all; a tuple is the plugin answering
            # something other than a total — its per-team lock, or a fault.
            if self.mana is None:
                return (404, b'{"success": false}', "")
            return (*self.mana, "") if isinstance(self.mana, tuple) else self._answer(self.mana)
        # The signature is sent — it is what the fetch needs — and is no part of what identifies
        # the file, which is the distinction the tests below turn on.
        if (served := self.files.get(path.split("?", 1)[0].lstrip("/"))) is not None:
            return (200, served, "")
        return (404, b'{"success": false}', "")

    def fetched(self) -> list[str]:
        return [path.split("?", 1)[0] for path in self.asked if path.startswith("/files/")]

    @staticmethod
    def _answer(data):
        return (200, json.dumps({"success": True, "data": data}).encode(), "")


def listing(challenge_id=1, **fields):
    """A LIST entry, which deliberately carries no description — that is what makes the detail GET
    per Challenge unavoidable, and the prose is where a password or a second host is written."""
    return {
        "id": challenge_id,
        "name": f"challenge-{challenge_id}",
        "category": "forensics",
        "type": "standard",
        "value": 500,
        "solves": 3,
        "solved_by_me": False,
        **fields,
    }


def detail(challenge_id=1, **fields):
    return {"id": challenge_id, "description": "open the archive", "attempts": 0, "files": [], **fields}


def board_of(wire, **kwargs):
    return Board(BOARD, "not-a-real-token", wire.transport, **kwargs)


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))


def intake_over(wire, recorder, **kwargs):
    return Intake(board_of(wire, **kwargs.pop("board", {})), recorder, **kwargs)


def records(recorder, kind="intake") -> list[dict]:
    lines = (json.loads(line) for line in recorder.stream_path.read_text().splitlines())
    return [line for line in lines if line["record"] == kind]


def test_the_detail_get_is_what_carries_the_prose_and_our_own_submission_count(recorder):
    """The LIST payload has neither. `attempts` is held server-side, which makes it the one thing
    about a Run that survives the container being restarted."""
    wire = Wire(listed=[listing(7)], detail={"7": detail(7, description="a hint lives here", attempts=2)})

    sighted = intake_over(wire, recorder).sync().challenges[0]

    assert sighted.description == "a hint lives here"
    assert sighted.attempts == 2


def test_a_second_sync_over_an_unmoved_board_fetches_no_file_again(recorder):
    """Re-running Intake is how a moving Board is noticed, so it runs often — and a sync that
    re-downloaded every attachment would make "often" unaffordable on a rate-limited Board."""
    wire = Wire(
        listed=[listing(1)],
        detail={"1": detail(1, files=[COVER])},
        files={"files/1a1a1a/cover.png": b"PNG-bytes"},
    )
    intake = intake_over(wire, recorder)

    first = intake.sync()
    second = intake.sync()

    assert wire.fetched() == ["/files/1a1a1a/cover.png"]
    assert first.challenges[0].changed and not second.challenges[0].changed
    assert second.challenges[0].attachments[0].path.read_bytes() == b"PNG-bytes"


def test_a_re_signed_url_is_not_a_replaced_file(recorder):
    """CTFd mints the signature per read. Comparing whole URLs would report every file replaced on
    every cycle, and re-download the whole Board once a minute for nothing."""
    wire = Wire(
        listed=[listing(1)],
        detail={"1": detail(1, files=[COVER])},
        files={"files/1a1a1a/cover.png": b"PNG-bytes"},
    )
    intake = intake_over(wire, recorder)
    intake.sync()

    wire.detail["1"] = detail(1, files=[COVER_RE_SIGNED])
    second = intake.sync()

    assert wire.fetched() == ["/files/1a1a1a/cover.png"]
    assert not second.challenges[0].changed


def test_a_replaced_file_is_noticed_by_the_boards_own_address_and_costs_no_tokens(recorder):
    """The content hash is in the URL, so what changed is decided by comparing strings."""
    wire = Wire(
        listed=[listing(1)],
        detail={"1": detail(1, files=[COVER])},
        files={"files/1a1a1a/cover.png": b"first", "files/2b2b2b/cover.png": b"corrected"},
    )
    intake = intake_over(wire, recorder)
    intake.sync()

    wire.detail["1"] = detail(1, files=[COVER_REPLACED])
    second = intake.sync()

    assert wire.fetched() == ["/files/1a1a1a/cover.png", "/files/2b2b2b/cover.png"]
    assert second.challenges[0].changed
    assert second.challenges[0].attachments[0].path.read_bytes() == b"corrected"


def test_an_edited_description_is_a_change_and_a_moving_solve_count_is_not(recorder):
    """A hint added mid-event is what Intake exists to notice. `solves` moves on its own all day
    and is snapshotted rather than diffed — treating it as a change would mark the whole Board
    changed every cycle and make the flag mean nothing."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)})
    intake = intake_over(wire, recorder)
    intake.sync()

    wire.listed = [listing(1, solves=99)]
    assert not intake.sync().challenges[0].changed

    wire.detail["1"] = detail(1, description="open the archive. hint: the password is in the title")
    assert intake.sync().challenges[0].changed


def test_a_fetch_over_the_cap_is_refused_whole_and_the_challenge_is_still_worked(recorder):
    """Brunner links a 6.15 GB `.7z`. A naive fetch destroys the Run — and a truncated one is worse
    than none, because half an archive under the right name is an artefact every later Step reasons
    over as though it were the file."""
    wire = Wire(
        listed=[listing(1)],
        detail={"1": detail(1, files=[COVER])},
        files={"files/1a1a1a/cover.png": b"x" * 4096},
    )

    sighted = intake_over(wire, recorder, board={"fetch_bytes": 64}).sync().challenges[0]

    refused = sighted.attachments[0]
    assert refused.outcome == OVER_THE_CAP and "64-byte cap" in refused.detail
    assert not list((recorder.run_dir / "intake").rglob("*.png"))
    assert sighted.name == "challenge-1"


def test_a_file_over_the_cap_is_refused_once_and_not_re_refused_every_cycle(recorder):
    """The file has not changed and our cap has not changed, so asking again spends the whole cap
    again to reach the same refusal. Brunner has three of these, and a 5.5-hour Run on a five-minute
    cycle would spend gigabytes on them — which is what the first live probe run reported."""
    wire = Wire(
        listed=[listing(1)],
        detail={"1": detail(1, files=[COVER])},
        files={"files/1a1a1a/cover.png": b"x" * 4096},
    )
    intake = intake_over(wire, recorder, board={"fetch_bytes": 64})

    intake.sync()
    second = intake.sync()

    assert wire.fetched() == ["/files/1a1a1a/cover.png"]
    assert second.challenges[0].attachments[0].outcome == OVER_THE_CAP
    assert not second.challenges[0].changed


def test_a_fetch_that_merely_failed_is_asked_for_again_next_cycle(recorder):
    """A 500 at 11:00 is not a 500 at 11:05. The two refusals are named apart precisely so that one
    is never revisited and the other always is."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1, files=[COVER])})
    intake = intake_over(wire, recorder)

    first = intake.sync()
    wire.files["files/1a1a1a/cover.png"] = b"PNG-bytes"
    second = intake.sync()

    assert first.challenges[0].attachments[0].outcome == UNFETCHED
    assert second.challenges[0].attachments[0].held
    assert wire.fetched() == ["/files/1a1a1a/cover.png"] * 2


def test_an_empty_list_the_control_does_not_corroborate_is_a_failed_sync(recorder):
    """The shape ADR-0016 exists for: 200, `success: true`, the right Content-Type, no redirect —
    every shape a client reads as an honest answer, from something that is not CTFd."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)})
    intake = intake_over(wire, recorder)
    intake.sync()

    wire.listed, wire.control = [], CONTROL_AGREEABLE
    kept = intake.sync()

    assert kept.outcome == UNCORROBORATED
    assert "evidence of nothing" in kept.detail
    assert [one.challenge_id for one in kept.challenges] == [1]
    assert records(recorder)[-1]["outcome"] == UNCORROBORATED


def test_an_empty_list_the_control_corroborates_is_believed(recorder):
    """A Board that refuses the query CTFd refuses is a Board whose empty list is CTFd's own answer.
    What the Solver does about an empty Board is the Run's question, and Intake's job is only to
    say that this one was read."""
    intake = intake_over(Wire(listed=[], control=CONTROL_REFUSED), recorder)

    snapshot = intake.sync()

    assert snapshot.believable and snapshot.challenges == ()


def test_the_control_is_only_asked_where_the_list_came_back_empty(recorder):
    """One request, and only on the path that already went wrong — a Board that lists Challenges
    never pays for it."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)})

    intake_over(wire, recorder).sync()

    assert not [path for path in wire.asked if "field=" in path]


def test_a_board_that_stops_answering_is_a_failed_sync_and_not_an_emptied_board(recorder):
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)})
    intake = intake_over(wire, recorder)
    intake.sync()

    wire.unreachable = True
    kept = intake.sync()

    assert kept.outcome == UNREADABLE
    assert [one.challenge_id for one in kept.challenges] == [1]


def test_a_failed_detail_get_keeps_the_last_prose_and_says_it_is_stale(recorder):
    """A Challenge with no description is one recon opens onto a filename. The last description is
    a better frame than none — and a reader has to be able to tell which they are looking at."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1, description="the real prose", files=[COVER], attempts=2)})
    wire.files["files/1a1a1a/cover.png"] = b"PNG-bytes"
    intake = intake_over(wire, recorder)
    intake.sync()

    wire.detail = {}
    sighted = intake.sync().challenges[0]

    assert sighted.stale
    assert sighted.description == "the real prose"
    assert sighted.attachments[0].name == "cover.png"
    assert sighted.attempts == 2


def test_the_solves_and_value_pair_and_the_scoreboard_are_snapshotted_every_cycle(recorder):
    """Stored and never acted on in v1: a later version fits the scoring curve out of the stream
    rather than out of new Solver code."""
    wire = Wire(listed=[listing(1, value=350, solves=12)], detail={"1": detail(1)})
    intake = intake_over(wire, recorder)

    intake.sync()
    intake.sync()

    written = records(recorder)
    assert [line["challenges"][0]["solves"] for line in written] == [12, 12]
    assert [line["challenges"][0]["value"] for line in written] == [350, 350]
    assert written[-1]["scoreboard"] == [{"rank": 1, "name": "them", "score": 900}]


def test_the_mana_total_is_read_once_for_the_run_and_branched_on(recorder):
    """`/mana` takes the same per-team lock as a deploy and **blocks** rather than failing while one
    is in flight, so a read on every cycle is a read that can hang for minutes."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)}, mana={"used": 1, "total": 0})
    intake = intake_over(wire, recorder)

    intake.sync()
    snapshot = intake.sync()

    assert [path for path in wire.asked if path.endswith("/mana")] == ["/api/v1/plugins/ctfd-chall-manager/mana"]
    assert not snapshot.mana_enabled
    assert records(recorder)[-1]["mana"] == {"outcome": "answered", "used": 1, "total": 0, "enabled": False}


def test_a_mana_read_that_failed_is_not_a_total_and_is_asked_again(recorder):
    """ADR-0008's own named failure: a discovered profile discovering the wrong thing, with a
    transient `/mana` 403 as the example. Cached, that fault reads as `total: 0` — mana switched
    off — and a mana-limited Board would be treated as having no cap for the rest of the Run."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)}, mana=(429, b""))
    intake = intake_over(wire, recorder)

    first = intake.sync()
    wire.mana = {"used": 1, "total": 4}
    second = intake.sync()

    assert not first.mana_enabled
    assert second.mana_enabled and second.mana.total == 4


def test_a_board_with_no_chall_manager_is_asked_for_mana_once_and_not_again(recorder):
    """A 404 is a settled fact about this Board rather than weather, so it is remembered — otherwise
    a Board without the plugin pays for the read on every cycle for the whole Run."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)}, mana=None)
    intake = intake_over(wire, recorder)

    intake.sync()
    snapshot = intake.sync()

    assert len([path for path in wire.asked if path.endswith("/mana")]) == 1
    assert not snapshot.mana_enabled


def test_a_control_that_failed_once_is_never_retried_into_a_pass(recorder):
    """ADR-0016: "failing the control is not transient and must not be retried into a pass." A Board
    that answered the too-agreeable 200 at 12:00 does not get its empty list believed at 12:05."""
    wire = Wire(listed=[], control=CONTROL_AGREEABLE)
    intake = intake_over(wire, recorder)
    assert intake.sync().outcome == UNCORROBORATED

    wire.control = CONTROL_REFUSED
    second = intake.sync()

    assert second.outcome == UNCORROBORATED
    assert len([path for path in wire.asked if "field=" in path]) == 1


def test_category_and_type_are_read_as_the_open_strings_they_are(recorder):
    """IN-CYPHER namespaces its practice categories and Brunner ships a `flightops`. Code that
    switches on a fixed list does not fail loudly here — it silently drops the Challenge."""
    wire = Wire(
        listed=[listing(1, category="(Practice) forensics", type="flightops")],
        detail={"1": detail(1)},
    )

    sighted = intake_over(wire, recorder).sync().challenges[0]

    assert (sighted.category, sighted.challenge_type) == ("(Practice) forensics", "flightops")


def test_a_board_supplied_filename_cannot_reach_out_of_the_run_directory(recorder):
    """The Board names the file and we write it under `/state`, which makes the name an input:
    `../../codex/auth.json` is a traversal into the one live credential on disk."""
    traversal, nameless = "files/1a1a1a/../../codex/auth.json", "files/1a1a1a/.."
    wire = Wire(
        listed=[listing(1)],
        detail={"1": detail(1, files=[traversal, nameless])},
        files={traversal: b"not-a-credential", nameless: b"nameless"},
    )

    held = intake_over(wire, recorder).sync().challenges[0].attachments

    written = [one.path for one in held if one.held]
    assert [path.name for path in written] == ["auth.json", UNNAMED]
    assert all(path.is_relative_to(recorder.run_dir / "intake") for path in written)


def test_a_file_deleted_from_state_mid_run_is_fetched_again(recorder):
    """`/state` is deletable mid-Run without costing the ability to solve, so an identity match over
    a file that is gone would leave a Challenge permanently missing its attachment."""
    wire = Wire(
        listed=[listing(1)],
        detail={"1": detail(1, files=[COVER])},
        files={"files/1a1a1a/cover.png": b"PNG-bytes"},
    )
    intake = intake_over(wire, recorder)
    intake.sync().challenges[0].attachments[0].path.unlink()

    intake.sync()

    assert wire.fetched() == ["/files/1a1a1a/cover.png"] * 2


def test_the_cycle_is_due_before_the_first_sync_and_not_again_until_it_comes_round(recorder):
    """The first sync is the Run's whole picture of the Board rather than a refresh of it, so it is
    always due; after that the cycle is what decides, and it is a parameter."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)})
    ticking = [dt.datetime(2026, 9, 22, 10, 0, tzinfo=dt.timezone.utc)]
    intake = Intake(board_of(wire), recorder, limits=Limits(cycle_seconds=300.0), now=lambda: ticking[0])

    assert intake.due()
    intake.sync()
    assert not intake.due()

    ticking[0] += dt.timedelta(seconds=300)
    assert intake.due()


def test_a_board_that_stopped_answering_is_asked_again_on_the_next_cycle_and_not_at_once(recorder):
    """A tight retry against a Board that stopped answering is a tight retry against its rate
    limiter — and the caller is a loop that asks `due()` as often as it likes."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1)})
    wire.unreachable = True
    ticking = [dt.datetime(2026, 9, 22, 10, 0, tzinfo=dt.timezone.utc)]
    intake = Intake(board_of(wire), recorder, limits=Limits(cycle_seconds=300.0), now=lambda: ticking[0])

    assert not intake.sync().believable
    assert not intake.due()

    ticking[0] += dt.timedelta(seconds=300)
    assert intake.due()


def test_an_attachment_records_the_hosts_a_fetch_passed_through(recorder):
    """An attachment served from object storage is not a problem; one nobody noticed came from
    somewhere else is. The token is stripped on the way there, which is `Board`'s rule and not
    Intake's — what Intake owes is saying where the bytes came from."""
    wire = Wire(listed=[listing(1)], detail={"1": detail(1, files=[COVER])})
    elsewhere = "https://storage.example/blob/cover.png"

    def redirected(request):
        if request.full_url == elsewhere:
            return (200, b"PNG-bytes", "")
        if request.full_url.startswith(f"{BOARD}/files/1a1a1a/cover.png"):
            wire.asked.append("/files/1a1a1a/cover.png")
            return (302, b"", elsewhere)
        return wire.transport(request)

    held = Intake(Board(BOARD, "not-a-real-token", redirected), recorder).sync().challenges[0].attachments[0]

    assert held.held and held.hosts == ("storage.example",)
    assert records(recorder)[-1]["challenges"][0]["files"][0]["hosts"] == ["storage.example"]
