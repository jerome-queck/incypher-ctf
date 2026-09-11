"""The Instance path: deploy at the start of an Attempt, terminate when it is cut.

Thirty-two of Brunner's seventy-four Challenges cannot be reconned at all without a running
Instance — the address does not exist until one is deployed — so this is the half of v1's spine
with no model in it and nothing to fall back on
([ADR-0007](../docs/adr/0007-truth-about-an-instance-lives-on-the-board.md)).

What is on trial is that the Solver never mistakes an answer for what it looks like: a 200 that did
not deploy, one 403 body meaning never / after a terminate / in a moment, a 429 from a lock another
Challenge holds, and a deadline that is computed from one read and never polled from another.
"""

import datetime as dt
import json

import pytest
from solver import board as board_module
from solver import instance as instance_module
from solver.board import Board, Reply
from solver.board_broker_contracts import BoardBrokerResult, BoardOperation, BoardOutcome
from solver.instance import (
    CHALL_MANAGER_DOWN_AT_SUBMIT,
    DEPLOY_ALREADY_HELD,
    DEPLOY_COLLISION,
    DEPLOY_REFUSED_MANA,
    DEPLOY_REFUSED_SHARED,
    DEPLOY_REFUSED_TRANSIENT,
    FAILURE_SHAPES,
    INSTANCE_DESTROYED_ON_FLAG,
    INSTANCE_DIED_EARLY,
    INSTANCE_EXPIRED_AT_SUBMIT,
    SHARED_NOT_DEPLOYED,
    Instances,
    Reserves,
    Terms,
    affordable,
    submission_shape,
)
from solver.record import Recorder
from solver.redaction import Redactor


def _kebab(name: str) -> str:
    """A shape constant is one whose value is its own name, so `MARK` and `INSTANCED_TYPE` are not
    shapes and a shape left out of the tuple is not a constant nobody notices."""
    return name.lower().replace("_", "-")


NOON = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)
INSTANCE = "/api/v1/plugins/ctfd-chall-manager/instance"
MANA = "/api/v1/plugins/ctfd-chall-manager/mana"
LEDGER_PAGE = "/plugins/ctfd-chall-manager/instances"

INSTANCED = {"id": 42, "type": "dynamic_iac", "shared": False, "timeout": 600, "mana_cost": 1, "destroy_on_flag": True}

MANA_EXHAUSTED = b'{"success": false, "data": {"message": "You or your team used up all your mana."}}'


def answered(**data):
    return 200, json.dumps({"success": True, "data": data}).encode(), ""


def ledger_of(*names):
    rows = "".join(f"<tr><td>{name}</td><td>2026-09-22 12:30:00</td></tr>" for name in names)
    return (
        200,
        f"<table><thead><tr><th>Challenge</th><th>Until</th></tr></thead><tbody>{rows}</tbody></table>".encode(),
        "",
    )


class Wire:
    """A board answering by `(method, path-without-query)`, in the order the answers were given."""

    def __init__(self, **answers):
        self.answers = {route: list(replies) for route, replies in answers.items()}
        self.asked: list[tuple[str, str]] = []

    def transport(self, request):
        path = request.full_url.split("board.example", 1)[1]
        route = f"{request.get_method()} {path.split('?')[0]}"
        self.asked.append((request.get_method(), path))
        replies = self.answers.get(route) or []
        if not replies:
            raise AssertionError(f"nothing was set up for {route}; the board was asked {self.asked}")
        return replies.pop(0) if len(replies) > 1 else replies[0]

    def times_asked(self, route: str) -> int:
        return sum(1 for method, path in self.asked if f"{method} {path.split('?')[0]}" == route)


@pytest.fixture
def recorder(tmp_path):
    return Recorder(tmp_path / "state", run_id="run-1", redactor=Redactor({}))


def instances_of(wire, recorder, *, now=NOON, reserves=Reserves()):
    board = Board("https://board.example", "not-a-real-token", wire.transport)
    return Instances(board, recorder, now=lambda: now, reserves=reserves)


def records(recorder) -> list[dict]:
    return [json.loads(line) for line in recorder.stream_path.read_text().splitlines()]


def test_the_type_is_the_detector_and_the_detail_get_is_for_the_terms():
    """`dynamic_iac` is hardcoded in the plugin's polymorphic identity, so it is baked into the
    database rows; `shared`, `timeout` and `destroy_on_flag` are what the detail GET is for."""
    terms = Terms.of(INSTANCED)

    assert terms.instanced
    assert (terms.shared, terms.timeout, terms.destroy_on_flag) == (False, 600, True)


def test_an_unknown_type_falls_to_the_default_branch_and_is_attempted_without_a_deploy(recorder):
    wire = Wire()

    answer = instances_of(wire, recorder).deploy(Terms.of({"id": 7, "type": "flightops"}), attempt_id="a1")

    assert (answer.lease, answer.shape) == (None, "")
    assert wire.asked == [], "a Challenge we cannot deploy costs no call, whatever its type is called"


def test_renewability_is_read_before_anything_is_deployed():
    """`min(budget, TTL)` has to be computable at Triage, which spends no mana and starts no clock."""
    assert Terms.of(INSTANCED).renewable
    assert not Terms.of({**INSTANCED, "timeout": None}).renewable


def test_a_deploy_carries_the_address_and_the_deadline_the_response_gave(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [answered(connectionInfo="nc 10.0.0.1 1337", until="2026-09-22T12:20:00Z")]})

    answer = instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1")

    assert answer.shape == ""
    assert answer.lease.connection_info == "nc 10.0.0.1 1337"
    assert answer.lease.until == dt.datetime(2026, 9, 22, 12, 20, tzinfo=dt.timezone.utc)


def test_the_attempt_deadline_is_the_earlier_of_the_budget_and_the_instance_minus_a_reserve(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="2026-09-22T12:20:00Z")]})
    reserves = Reserves(submission_seconds=90)

    lease = instances_of(wire, recorder, reserves=reserves).deploy(Terms.of(INSTANCED), attempt_id="a1").lease

    assert lease.attempt_deadline(NOON + dt.timedelta(hours=1)) == dt.datetime(
        2026, 9, 22, 12, 18, 30, tzinfo=dt.timezone.utc
    )
    assert lease.attempt_deadline(NOON + dt.timedelta(minutes=5)) == NOON + dt.timedelta(minutes=5)


def test_an_instance_with_no_deadline_leaves_the_budget_to_decide(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="")]})

    lease = instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1").lease

    assert lease.until is None
    assert lease.attempt_deadline(NOON + dt.timedelta(minutes=5)) == NOON + dt.timedelta(minutes=5)


def test_renewal_is_lazy_because_a_renew_discards_whatever_remained(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="2026-09-22T12:10:00Z")]})
    reserves = Reserves(submission_seconds=60, renew_round_trip_seconds=10)

    lease = instances_of(wire, recorder, reserves=reserves).deploy(Terms.of(INSTANCED), attempt_id="a1").lease

    assert not lease.due_for_renewal(NOON)
    assert not lease.due_for_renewal(dt.datetime(2026, 9, 22, 12, 8, 49, tzinfo=dt.timezone.utc))
    assert lease.due_for_renewal(dt.datetime(2026, 9, 22, 12, 8, 50, tzinfo=dt.timezone.utc))


def test_an_instance_with_no_timeout_is_never_due_for_renewal(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="2026-09-22T12:00:30Z")]})

    lease = instances_of(wire, recorder).deploy(Terms.of({**INSTANCED, "timeout": None}), attempt_id="a1").lease

    assert not lease.due_for_renewal(NOON), "every PATCH on a Challenge with no timeout is a 403"


def test_a_renew_computes_the_new_deadline_and_never_fetches_it(recorder):
    """`update_instance` sets `until = now + timeout` and the PATCH response carries no `until`, so
    a Solver that read one back would be reading a field that is not there."""
    wire = Wire(
        **{
            f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="2026-09-22T12:10:00Z")],
            f"PATCH {INSTANCE}": [answered()],
        }
    )
    instances = instances_of(wire, recorder, now=NOON)
    lease = instances.deploy(Terms.of(INSTANCED), attempt_id="a1").lease

    renewed = instances.renew(lease, attempt_id="a1")

    assert renewed.lease.until == NOON + dt.timedelta(seconds=600)
    assert wire.times_asked(f"GET {INSTANCE}") == 0


def test_a_renew_answered_by_a_missing_instance_says_it_died(recorder):
    wire = Wire(
        **{
            f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="2026-09-22T12:10:00Z")],
            f"PATCH {INSTANCE}": [(404, b"", "")],
        }
    )
    instances = instances_of(wire, recorder)
    lease = instances.deploy(Terms.of(INSTANCED), attempt_id="a1").lease

    assert instances.renew(lease, attempt_id="a1").shape == INSTANCE_DIED_EARLY


def test_a_two_hundred_that_did_not_deploy_is_recovered_by_reading_rather_than_retried(recorder):
    """Trap 1 — a POST for a Challenge that already has an Instance answers HTTP 200 with
    `success: false` and no `connectionInfo`."""
    wire = Wire(
        **{
            f"POST {INSTANCE}": [(200, MANA_EXHAUSTED, "")],
            f"GET {INSTANCE}": [answered(connectionInfo="nc 10.0.0.1 1337", until="2026-09-22T12:20:00Z")],
        }
    )

    answer = instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1")

    assert answer.shape == DEPLOY_ALREADY_HELD
    assert answer.lease.connection_info == "nc 10.0.0.1 1337"


def test_deploy_recovery_uses_the_generation_scoped_broker_read(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [(200, MANA_EXHAUSTED, "")]})

    class BrokerRead:
        def read_instance(self, challenge_id):
            assert challenge_id == 42
            return BoardBrokerResult(
                BoardOperation.INSTANCE_READ,
                BoardOutcome.ANSWERED,
                Reply("answered", "nc 10.0.0.1 1337", NOON + dt.timedelta(minutes=20)),
            )

    answer = instances_of(wire, recorder).deploy(
        Terms.of(INSTANCED),
        attempt_id="a1",
        recovery_reader=BrokerRead(),
    )

    assert answer.shape == DEPLOY_ALREADY_HELD
    assert wire.times_asked(f"GET {INSTANCE}") == 0


def test_a_two_hundred_that_did_not_deploy_and_holds_nothing_is_transient(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [(200, MANA_EXHAUSTED, "")], f"GET {INSTANCE}": [(404, b"", "")]})

    answer = instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1")

    assert (answer.shape, answer.lease) == (DEPLOY_REFUSED_TRANSIENT, None)


def test_the_forbidden_is_disambiguated_by_the_flag_we_already_hold_before_any_mana_is_read(recorder):
    """Trap 2, first arm — `shared` is a detail-GET field we read before deploying, and reading it
    first is what stops a never-retry becoming a retry after a terminate."""
    wire = Wire(**{f"POST {INSTANCE}": [(403, MANA_EXHAUSTED, "")], f"GET {INSTANCE}": [(404, b"", "")]})

    answer = instances_of(wire, recorder).deploy(Terms.of({**INSTANCED, "shared": True}), attempt_id="a1")

    assert answer.shape == SHARED_NOT_DEPLOYED, "nobody has deployed it; that is a requeue, not a never"
    assert wire.times_asked(f"GET {MANA}") == 0


def test_a_shared_challenge_an_admin_has_deployed_is_a_never_rather_than_a_not_yet(recorder):
    """Both names mean ineligible; they mean it for opposite reasons, and whoever picks the next
    Challenge needs the difference."""
    wire = Wire(
        **{
            f"POST {INSTANCE}": [(403, MANA_EXHAUSTED, "")],
            f"GET {INSTANCE}": [answered(connectionInfo="https://a.example", until="2026-09-22T12:20:00Z")],
        }
    )

    answer = instances_of(wire, recorder).deploy(Terms.of({**INSTANCED, "shared": True}), attempt_id="a1")

    assert (answer.shape, answer.lease) == (DEPLOY_REFUSED_SHARED, None)
    assert "https://a.example" in answer.shown, "the record keeps the address it may not manage"


def test_mana_genuinely_short_is_named_from_exactly_one_read(recorder):
    """Trap 2, second arm. One read because `GET /mana` takes the same per-team lock as a deploy and
    blocks rather than failing, so a second one is a hang rather than a retry."""
    wire = Wire(
        **{
            f"POST {INSTANCE}": [(403, MANA_EXHAUSTED, "")],
            f"GET {MANA}": [answered(used=3, total=3)],
        }
    )

    answer = instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1")

    assert answer.shape == DEPLOY_REFUSED_MANA
    assert wire.times_asked(f"GET {MANA}") == 1


def test_a_forbidden_with_mana_to_spare_is_transient_rather_than_a_never(recorder):
    """Trap 2, third arm — chall-manager errored while computing mana, which means retry in a
    moment and is the arm a Solver that stopped at the first two would never retry."""
    wire = Wire(**{f"POST {INSTANCE}": [(403, MANA_EXHAUSTED, "")], f"GET {MANA}": [answered(used=0, total=3)]})

    assert instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1").shape == DEPLOY_REFUSED_TRANSIENT


def test_a_mana_read_that_failed_is_never_read_as_mana_exhausted(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [(403, MANA_EXHAUSTED, "")], f"GET {MANA}": [(500, b"", "")]})

    assert instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1").shape == DEPLOY_REFUSED_TRANSIENT


def test_the_per_team_lock_is_a_collision_and_not_a_refusal(recorder):
    """Trap 4 — the lock serialises deploy and terminate across every Challenge, so terminating one
    and deploying the next back to back is a real collision."""
    wire = Wire(**{f"POST {INSTANCE}": [(429, b"", "")]})

    assert instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1").shape == DEPLOY_COLLISION


def test_mana_switched_off_and_a_free_challenge_short_circuit_affordability():
    from solver.board import ANSWERED, Mana

    assert affordable(Terms.of(INSTANCED), Mana(ANSWERED, used=0, total=0)), "total <= 0 is the feature switched off"
    assert affordable(Terms.of({**INSTANCED, "mana_cost": 0}), Mana(ANSWERED, used=3, total=3))
    assert not affordable(Terms.of(INSTANCED), Mana(ANSWERED, used=3, total=3))


def test_liveness_is_bought_by_a_cause_and_answered_by_exactly_one_read(recorder):
    wire = Wire(
        **{
            f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="2026-09-22T12:20:00Z")],
            f"GET {INSTANCE}": [(404, b"", "")],
        }
    )
    instances = instances_of(wire, recorder)
    lease = instances.deploy(Terms.of(INSTANCED), attempt_id="a1").lease

    answer = instances.liveness(lease, attempt_id="a1", because="nc 10.0.0.1 1337: connection refused")

    assert answer.shape == INSTANCE_DIED_EARLY
    assert wire.times_asked(f"GET {INSTANCE}") == 1
    assert "connection refused" in answer.shown, "the record says what bought the read"


def test_nothing_on_the_path_polls_the_per_challenge_read(recorder):
    """The per-Challenge GET is served from a sixty-second cache that can report an Instance alive a
    minute after it died, so expiry is computed and never detected."""
    wire = Wire(
        **{
            f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="2026-09-22T12:10:00Z")],
            f"PATCH {INSTANCE}": [answered()],
            f"DELETE {INSTANCE}": [answered()],
        }
    )
    instances = instances_of(wire, recorder)

    lease = instances.deploy(Terms.of(INSTANCED), attempt_id="a1").lease
    lease = instances.renew(lease, attempt_id="a1").lease
    instances.terminate(lease.challenge_id, attempt_id="a1")

    assert wire.times_asked(f"GET {INSTANCE}") == 0


def test_a_terminate_answered_by_a_missing_instance_is_a_success(recorder):
    wire = Wire(**{f"DELETE {INSTANCE}": [(404, b"", "")]})

    assert instances_of(wire, recorder).terminate(42, attempt_id="a1").shape == ""


def test_a_terminate_after_a_correct_flag_names_what_destroy_on_flag_already_did(recorder):
    wire = Wire(**{f"DELETE {INSTANCE}": [(404, b"", "")]})

    answer = instances_of(wire, recorder).terminate(42, attempt_id="a1", after_flag=True)

    assert answer.shape == INSTANCE_DESTROYED_ON_FLAG


def test_the_sweep_terminates_everything_held_that_is_not_this_attempts_challenge(recorder):
    wire = Wire(
        **{
            f"GET {LEDGER_PAGE}": [ledger_of("Silent Skies", "Vital Signs")],
            f"DELETE {INSTANCE}": [answered()],
        }
    )

    swept = instances_of(wire, recorder).sweep(
        attempt_id="a1", keeping="Silent Skies", known={"Silent Skies": 42, "Vital Signs": 43}
    )

    assert swept.terminated == ("Vital Signs",)
    assert [path for method, path in wire.asked if method == "DELETE"] == [f"{INSTANCE}?challengeId=43"]


def test_the_run_close_sweep_keeps_nothing(recorder):
    wire = Wire(**{f"GET {LEDGER_PAGE}": [ledger_of("Silent Skies")], f"DELETE {INSTANCE}": [answered()]})

    swept = instances_of(wire, recorder).sweep(attempt_id="", keeping=None, known={"Silent Skies": 42})

    assert swept.terminated == ("Silent Skies",)


def test_a_held_instance_the_board_never_listed_is_reported_rather_than_dropped(recorder):
    """The ledger keys its rows by challenge name and a terminate takes an id, so a row Intake
    cannot name is capacity nobody reclaims — and chall-manager never evicts."""
    wire = Wire(**{f"GET {LEDGER_PAGE}": [ledger_of("A Challenge Renamed Yesterday")]})

    swept = instances_of(wire, recorder).sweep(attempt_id="a1", keeping=None, known={})

    assert swept.unresolved == ("A Challenge Renamed Yesterday",)
    assert "A Challenge Renamed Yesterday" in swept.shown


def test_a_liveness_read_that_failed_is_never_reported_as_alive(recorder):
    """The read is bought by a cause and a cause buys exactly one, so a reply that establishes
    neither has to say so — answering "alive" would answer with the one thing it cannot show."""
    wire = Wire(
        **{
            f"POST {INSTANCE}": [answered(connectionInfo="nc a 1", until="2026-09-22T12:20:00Z")],
            f"GET {INSTANCE}": [(500, b"", "")],
        }
    )
    instances = instances_of(wire, recorder)
    lease = instances.deploy(Terms.of(INSTANCED), attempt_id="a1").lease

    answer = instances.liveness(lease, attempt_id="a1", because="nc a 1: connection refused")

    assert answer.shape == ""
    assert "not established" in answer.shown
    assert wire.times_asked(f"GET {INSTANCE}") == 1


def test_a_terminate_the_lock_refused_is_never_swept_up_as_released(recorder):
    """Trap 4 again, and its likeliest trigger: the per-team lock 429s a DELETE immediately, and a
    sweep issues them back to back. A leak recorded as reclaimed is capacity lost for the Run."""
    wire = Wire(**{f"GET {LEDGER_PAGE}": [ledger_of("Silent Skies")], f"DELETE {INSTANCE}": [(429, b"", "")]})

    swept = instances_of(wire, recorder).sweep(attempt_id="a1", keeping=None, known={"Silent Skies": 42})

    assert (swept.terminated, swept.still_held) == ((), ("Silent Skies",))
    assert "still held" in swept.shown


def test_the_two_submission_messages_are_named_apart():
    """A late Flag grades `incorrect` and spends a slot of the Board-wide budget, so which of the
    two happened is the difference between our clock being wrong and the platform being down."""
    expired = {"status": "incorrect", "message": "Expired (the instance must be running to submit)"}
    down = {"status": "incorrect", "message": "Error occurred, contact admins!"}

    assert submission_shape(expired) == INSTANCE_EXPIRED_AT_SUBMIT
    assert submission_shape(down) == CHALL_MANAGER_DOWN_AT_SUBMIT
    assert submission_shape({"status": "incorrect", "message": "Incorrect"}) == ""


def test_the_vocabulary_is_closed():
    """Closed means the tuple and the constants cannot drift apart: a shape added as a constant and
    not to the tuple is a failure that reaches the orchestrator unnamed after all."""
    # The Board's own outcome names are the same shape and are imported rather than declared here,
    # so they are subtracted rather than matched around.
    imported = set(vars(board_module))
    declared = {
        value
        for name, value in vars(instance_module).items()
        if name not in imported and isinstance(value, str) and value == _kebab(name)
    }

    assert set(FAILURE_SHAPES) == declared
    assert len(FAILURE_SHAPES) == 10


def test_every_operation_leaves_a_named_observation_in_the_stream(recorder):
    wire = Wire(**{f"POST {INSTANCE}": [(429, b"", "")]})

    answer = instances_of(wire, recorder).deploy(Terms.of(INSTANCED), attempt_id="a1")

    kinds = [record["record"] for record in records(recorder)]
    assert kinds == ["step-begin", "step-end"], "a Step that hung mid-deploy has to be visible"
    body = (recorder.run_dir / records(recorder)[-1]["observation_ref"]).read_text()
    assert DEPLOY_COLLISION in body
    assert answer.shape in body
