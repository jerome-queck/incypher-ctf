"""A Board profile is discovered, and where it cannot be it fails the Run rather than guessing.

ADR-0008 named the failure this module is shaped around — **a discovered profile can discover the
wrong thing** — with a transient `/mana` 403 reading as *mana disabled* as the shape it had in mind.
So the tests below are mostly about the three-way split: a fact, an absence, and a fault that must
never be read as either.
"""

import json
from pathlib import Path

import pytest
from solver import profile
from solver.board import Board
from solver.boot import Refusal
from solver.profile import ABSENT, INSTALLED, UNREADABLE, Rules, discovered, rules_for, tracked

BOARD = "https://board.example"
TRACKED = "docs/competitions"

CONTROL_REFUSED = (400, b'{"success": false, "errors": {"field": "not a valid enumeration member"}}')
CONTROL_AGREEABLE = (200, b'{"success": true, "data": []}')

LEDGER = (
    "<table><thead><tr><th>Challenge</th><th>Until</th></tr></thead>"
    "<tbody><tr><td>alpha</td><td>later</td></tr></tbody></table>"
).encode()

RULES = Rules(
    event="somewhere",
    url=BOARD,
    flag_wrappers=(r"flag\{[^}]{1,256}\}",),
    window_seconds=5.5 * 3600,
    prohibitions=("do not brute-force flags",),
)

_UNSAID = object()


class Wire:
    """A CTFd that answers from a script, and remembers what it was asked — because most of what is
    on trial here is which requests were made and how many times."""

    def __init__(
        self, *, listed=None, control=CONTROL_REFUSED, mana=_UNSAID, ledger=LEDGER, configs=None, anonymous_reads=False
    ):
        self.listed = [] if listed is None else listed
        self.anonymous_reads = anonymous_reads
        self.control = control
        self.mana = {"used": 0, "total": 4} if mana is _UNSAID else mana
        self.ledger = ledger
        self.configs = configs
        self.asked: list[str] = []

    def transport(self, request):
        path = request.full_url[len(BOARD) :]
        self.asked.append(path)
        if "field=" in path:
            return (*self.control, "")
        if path == "/api/v1/challenges":
            if request.get_header("Authorization") or self.anonymous_reads:
                return self._answer(self.listed)
            return (302, b"", "/login")
        if path.endswith("/mana"):
            if self.mana is None:
                return (404, b'{"success": false}', "")
            return (*self.mana, "") if isinstance(self.mana, tuple) else self._answer(self.mana)
        if path == profile.INSTANCE_LEDGER:
            if self.ledger is None:
                return (404, b"", "")
            if isinstance(self.ledger, tuple):
                return (*self.ledger, "")
            return (200, self.ledger, "")
        if path == "/api/v1/configs":
            if self.configs is None:
                return (403, b'{"success": false}', "")
            return (200, json.dumps({"data": [{"key": k, "value": v} for k, v in self.configs.items()]}).encode(), "")
        return (404, b'{"success": false}', "")

    @staticmethod
    def _answer(data):
        return (200, json.dumps({"success": True, "data": data}).encode(), "")

    def boards(self):
        return Board(BOARD, "token", self.transport), Board(BOARD, "", self.transport)


def instanced(**overrides):
    return {"id": 1, "name": "alpha", "type": "dynamic_iac", **overrides}


# ---------------------------------------------------------------- the tracked half


def test_every_tracked_profile_in_the_repository_reads():
    """The files are baked into the image, so one that does not read is a container that refuses to
    start with a human already gone home."""
    events = {rules.event for rules in tracked(TRACKED)}

    assert {"brunnerctf-2026-global", "incypher-2026-hackathon"} <= events


def test_every_tracked_board_answers_to_its_own_url_and_has_a_rules_snapshot():
    """`scripts/setup-board.sh` finds the rulebook to diff by asking `rules_for` for the URL it was
    pointed at and then reading `<event>.rules.txt` beside the profile. Nothing else joins those two
    files, so an `event` that does not match its own basename — or a second profile claiming one
    Board — is a rules page nobody ever diffs, and the wizard reports it by saying nothing."""
    for known in tracked(TRACKED):
        assert rules_for(known.url, TRACKED) == known
        assert (Path(TRACKED) / f"{known.event}.rules.txt").is_file()


def test_the_board_is_selected_by_the_url_ctfd_url_names():
    """One image for every Board, configured at run time — and `CTFD_URL` stays the whole guard it
    is documented to be, because a Board it names that we hold no rules for matches nothing."""
    assert rules_for("https://global.brunnerctf.dk", TRACKED).event == "brunnerctf-2026-global"
    assert rules_for("https://global.brunnerctf.dk/", TRACKED).event == "brunnerctf-2026-global"


def test_a_board_we_hold_no_rules_for_refuses_the_run():
    """The Danish Brunner board is a strict no-AI platform. Pointing this image at it must not be a
    Run that plays it under Global's profile."""
    with pytest.raises(Refusal, match="no Board profile"):
        rules_for("https://danmark.brunnerctf.dk", TRACKED)


def test_brunners_rules_derived_prohibitions_reach_the_profile():
    rules = rules_for("https://global.brunnerctf.dk", TRACKED)
    forbidden = " ".join(rules.prohibitions).lower()

    assert "broad automated enumeration" in forbidden
    assert "sandbagging" in forbidden


def test_web_search_is_a_profile_value_defaulting_on():
    """ADR-0014: default on, and a Board's rules turn it off. Neither Board we hold rules for bans
    it, so both read true — and the default is what a Board we have never met gets."""
    assert rules_for("https://global.brunnerctf.dk", TRACKED).web_search is True
    assert Rules(event="e", url="u", flag_wrappers=("f",), window_seconds=1).web_search is True


def test_the_board_that_needs_a_team_key_says_so_and_the_one_that_does_not_says_nothing():
    """The tracked profile is where *which* credentials a Board demands lives, because it is a rules
    fact with nothing behind it in the API — IN-CYPHER's team key gates its raw-TCP Challenges, and
    Brunner has never heard of the word."""
    assert rules_for("https://hackathon.in-cypher.com", TRACKED).requires == ("TEAM_KEY",)
    assert rules_for("https://global.brunnerctf.dk", TRACKED).requires == ()


def test_a_required_credential_nothing_declares_is_refused(tmp_path):
    """The boot check reads its holdings from the declared set, so a profile asking for a variable
    nobody declared is asking for something absent by construction."""
    (tmp_path / "x.board.json").write_text(
        json.dumps(
            {
                "event": "x",
                "url": BOARD,
                "flag_wrappers": ["f"],
                "window_seconds": 1,
                "prohibitions": [],
                "requires": ["SOME_KEY_NOBODY_DECLARED"],
            }
        )
    )

    with pytest.raises(Refusal, match="does not declare"):
        rules_for(BOARD, tmp_path)


def test_a_board_that_does_not_answer_at_boot_refuses_rather_than_raising_at_nobody(monkeypatch):
    """At boot there is a human present and a sentence is worth more to them than a stack. Mid-Run
    the same fault is Intake's and is answered the opposite way, because by then there is a snapshot
    worth keeping and nobody to read a sentence."""

    def unplugged(_request):
        raise OSError("Name or service not known")

    with pytest.raises(Refusal, match="could not be read at boot"):
        discovered(Board(BOARD, "token", unplugged), Board(BOARD, "", unplugged), RULES)


def test_a_mistyped_key_is_refused_rather_than_silently_defaulted(tmp_path):
    """The dangerous reading is the permissive one: `prohibition` for `prohibitions` leaves a
    Board's rules out of every Attempt prompt while the file still looks right, and on Brunner one
    of those rules carries an immediate ban."""
    (tmp_path / "x.board.json").write_text(
        json.dumps(
            {
                "event": "x",
                "url": BOARD,
                "flag_wrappers": ["f"],
                "window_seconds": 1,
                "prohibitions": [],
                "prohibition": [],
            }
        )
    )

    with pytest.raises(Refusal, match="prohibition"):
        rules_for(BOARD, tmp_path)


@pytest.mark.parametrize("stated", ["../elsewhere", "two/parts", "", ".hidden"])
def test_an_event_that_is_not_usable_as_a_directory_name_is_refused(tmp_path, stated):
    """The event names the directory under `/state/work` this Board's working directories live in
    (ADR-0025), so a separator or a `..` in it puts a Board's memory outside the root that is
    namespacing it — which is the collision the namespace exists to prevent, arriving by another
    door and just as silently."""
    (tmp_path / "x.board.json").write_text(
        json.dumps({"event": stated, "url": BOARD, "flag_wrappers": ["f"], "window_seconds": 1, "prohibitions": []})
    )

    with pytest.raises(Refusal, match="not usable as a directory name"):
        rules_for(BOARD, tmp_path)


def test_a_window_that_buys_no_attempt_is_refused_where_it_is_read(tmp_path):
    """So the close a Board's rules state is the one bound that can ever have run out, and the
    refusal at boot can name it rather than guessing between three."""
    (tmp_path / "x.board.json").write_text(
        json.dumps({"event": "x", "url": BOARD, "flag_wrappers": ["f"], "window_seconds": 0, "prohibitions": []})
    )

    with pytest.raises(Refusal, match="buys no Attempt"):
        rules_for(BOARD, tmp_path)


def test_a_profile_still_stating_one_wrapper_under_the_old_key_fails_loudly(tmp_path):
    """The singular key is retired rather than aliased. A profile carrying it fails twice over —
    missing `flag_wrappers`, and an unknown `flag_wrapper` — which is the loud failure a silently
    defaulted pattern would not have been: a Run sweeping for nothing finds nothing and says so
    only five and a half hours later."""
    (tmp_path / "x.board.json").write_text(
        json.dumps({"event": "x", "url": BOARD, "flag_wrapper": "f", "window_seconds": 1, "prohibitions": []})
    )

    with pytest.raises(Refusal, match="flag_wrappers"):
        rules_for(BOARD, tmp_path)


@pytest.mark.parametrize("stated", ["f", [], {}, None])
def test_a_wrapper_list_that_is_not_a_non_empty_list_is_refused(tmp_path, stated):
    """A bare string is the shape someone migrating from the old key writes, and it would iterate
    character by character into a set of one-character patterns. A Board with no wrapper at all is a
    Board nothing could ever be swept for."""
    (tmp_path / "x.board.json").write_text(
        json.dumps({"event": "x", "url": BOARD, "flag_wrappers": stated, "window_seconds": 1, "prohibitions": []})
    )

    with pytest.raises(Refusal, match="not a non-empty list"):
        rules_for(BOARD, tmp_path)


def test_a_wrapper_stated_twice_is_refused(tmp_path):
    """It buys no match one entry would not, and costs a second full pass over every artefact the
    cascade reads — inside a deadline the whole cascade shares."""
    (tmp_path / "x.board.json").write_text(
        json.dumps(
            {"event": "x", "url": BOARD, "flag_wrappers": ["f", "g", "f"], "window_seconds": 1, "prohibitions": []}
        )
    )

    with pytest.raises(Refusal, match="twice"):
        rules_for(BOARD, tmp_path)


def test_a_missing_key_is_refused(tmp_path):
    (tmp_path / "x.board.json").write_text(json.dumps({"event": "x", "url": BOARD}))

    with pytest.raises(Refusal, match="states no"):
        rules_for(BOARD, tmp_path)


def test_a_close_with_no_timezone_is_refused(tmp_path):
    """The container's clock is UTC and every event window we hold is written in the venue's, so a
    naive moment here is a window that is wrong by hours and says nothing about it."""
    (tmp_path / "x.board.json").write_text(
        json.dumps(
            {
                "event": "x",
                "url": BOARD,
                "flag_wrappers": ["f"],
                "window_seconds": 1,
                "prohibitions": [],
                "closes_at": "2026-09-22T16:00:00",
            }
        )
    )

    with pytest.raises(Refusal, match="timezone"):
        rules_for(BOARD, tmp_path)


# ---------------------------------------------------------------- the discovered half


def test_a_board_that_fails_the_read_contract_control_refuses_the_run():
    """`solves` and `value` ride the same LIST payload, so a Board that answers a canned success
    would let Order rank an empty set while reporting success for five and a half hours."""
    wire = Wire(control=CONTROL_AGREEABLE)

    with pytest.raises(Refusal, match="not composed by CTFd"):
        discovered(*wire.boards(), RULES)


def test_the_control_is_never_retried_into_a_pass():
    """ADR-0016 is explicit that failing it is not transient. One ask, one verdict."""
    wire = Wire(control=CONTROL_AGREEABLE)

    with pytest.raises(Refusal):
        discovered(*wire.boards(), RULES)

    assert sum("field=" in path for path in wire.asked) == 1


def test_a_flag_wrapper_that_does_not_compile_refuses_before_anything_is_swept():
    """Inside an Attempt this is an Observation and correct; at boot it is five and a half hours of
    sweeping nothing, silently."""
    with pytest.raises(Refusal, match="does not compile"):
        discovered(*Wire().boards(), Rules(event="x", url=BOARD, flag_wrappers=("flag{[",), window_seconds=1))


def test_mana_that_is_neither_a_total_nor_an_absence_refuses_the_run():
    """ADR-0008's own named failure: a transient 403 kept as a total reads as `total: 0`, which is
    *mana switched off*, and a mana-limited Board is then treated as having no cap at all."""
    wire = Wire(listed=[instanced()], mana=(403, b'{"success": false, "data": {"message": "denied"}}'))

    with pytest.raises(Refusal, match="/mana answered"):
        discovered(*wire.boards(), RULES)


def test_a_board_with_no_plugin_is_an_absence_and_not_a_fault():
    """Brunner runs no chall-manager at all. That is a Board without the feature, not a Board that
    failed — and it must cost no refusal."""
    found = discovered(*Wire(ledger=None, mana=None).boards(), RULES)

    assert found.chall_manager == ABSENT
    assert found.mana is None
    assert found.instances_reachable is False


def test_deploying_what_we_could_not_sweep_refuses_the_run():
    """chall-manager never evicts, so an Instance we cannot see in the ledger is capacity nobody
    reclaims for the rest of the event."""
    wire = Wire(listed=[instanced()], ledger=(403, b""))

    with pytest.raises(Refusal, match="ledger reads"):
        discovered(*wire.boards(), RULES)


def test_a_readable_ledger_beside_instanced_challenges_is_the_path_being_open():
    found = discovered(*Wire(listed=[instanced()]).boards(), RULES)

    assert found.chall_manager == INSTALLED
    assert found.instanced_challenges == 1
    assert found.instances_reachable is True


def test_an_unreadable_ledger_on_a_board_with_nothing_to_deploy_is_recorded_and_not_fatal():
    """Nothing will be deployed, so nothing can leak. The reading is still written down, because a
    Run that could not read the ledger and one that had no ledger are different facts."""
    found = discovered(*Wire(ledger=(500, b"")).boards(), RULES)

    assert found.chall_manager == UNREADABLE
    assert found.instances_reachable is False


def test_whether_an_unauthenticated_read_is_answered_is_discovered_both_ways():
    """A fact about the Board and never a route we take: every read in a Run is authenticated,
    because our own submission counts ride the authenticated payload."""
    listed = [{"id": 1, "name": "alpha", "type": "standard"}]

    assert discovered(*Wire(listed=listed).boards(), RULES).unauthenticated_read is False
    assert discovered(*Wire(listed=listed, anonymous_reads=True).boards(), RULES).unauthenticated_read is True


def test_the_submission_limit_is_stated_where_the_board_says_so_and_assumed_where_it_does_not():
    stated = discovered(*Wire(configs={"incorrect_submissions_per_min": 3}).boards(), RULES)
    assumed = discovered(*Wire().boards(), RULES)

    assert (stated.submissions_per_minute, stated.submissions_per_minute_source) == (3, profile.STATED)
    assert (assumed.submissions_per_minute, assumed.submissions_per_minute_source) == (
        profile.CTFD_DEFAULT_INCORRECT_PER_MIN,
        profile.ASSUMED,
    )


def test_the_boards_own_window_is_recorded_beside_the_configured_one_rather_than_instead_of_it():
    """They genuinely disagree: IN-CYPHER's CTFd window is the *practice* window and closes ten and
    a half hours before the scored Run begins, so taking the wire value would end the Run before it
    started."""
    found = discovered(*Wire(configs={"start": "1782835200", "end": "1790006400"}).boards(), RULES)

    assert found.board_window == {"start": "1782835200", "end": "1790006400"}
    assert found.rules.window_seconds == RULES.window_seconds


def test_the_whole_profile_as_discovered_is_writable_at_run_open():
    """Without it a post-mortem cannot tell *the Solver behaved wrongly* from *the Solver read the
    Board wrongly*, and those want completely different fixes (ADR-0008)."""
    recorded = discovered(*Wire(listed=[instanced()]).boards(), RULES).recorded()

    assert json.loads(json.dumps(recorded))["chall_manager"] == INSTALLED
    assert set(recorded) >= {
        "event",
        "url",
        "flag_wrappers",
        "window_seconds",
        "closes_at",
        "web_search",
        "prohibitions",
        "chall_manager",
        "instanced_challenges",
        "unauthenticated_read",
        "mana",
        "submissions_per_minute",
        "submissions_per_minute_source",
        "board_window",
    }
