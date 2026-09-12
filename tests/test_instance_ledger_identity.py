"""Authenticated Instance-ledger identity at its public Board-read seam."""

import json
import hashlib
from dataclasses import replace

import pytest
from solver.event_store_storage import canonical_bytes, digest_bytes

from solver.instance_ledger import (
    AuthenticatedIdentity,
    LedgerPage,
    LedgerRow,
    identify_ledger,
    read_profiled_instance_ledger,
    verify_receipt,
    write_receipt,
)


def test_plugin_absence_is_not_an_authenticated_empty_ledger():
    result = identify_ledger(
        mode="teams",
        user_id=7,
        team_id=11,
        pages=(LedgerPage(status=404, body=b"", response_digest="absent"),),
    )

    assert result.outcome == "absent"
    assert result.owned == ()


def page(*rows, page=1, total_pages=1, total_rows=None, complete=True, digest=None):
    return LedgerPage(
        status=200,
        rows=tuple(rows),
        page=page,
        total_pages=total_pages,
        total_rows=len(rows) if total_rows is None else total_rows,
        complete=complete,
        response_digest=digest or hashlib.sha256(f"page-{page}".encode()).hexdigest(),
    )


OWNED = LedgerRow("instance-1", 42, user_id=7, team_id=11)
FOREIGN = LedgerRow("instance-2", 43, user_id=8, team_id=12)


@pytest.mark.parametrize(
    ("pages", "expected"),
    [
        ((LedgerPage(status=503, response_digest="fault"),), "unsettled"),
        ((page(FOREIGN),), "not-ours"),
        ((page(),), "empty"),
        ((page(OWNED),), "populated"),
    ],
)
def test_public_contract_fixtures_keep_each_ledger_outcome_distinct(pages, expected):
    assert identify_ledger(mode="teams", user_id=7, team_id=11, pages=pages).outcome == expected


def test_team_and_user_modes_use_their_authenticated_source_key():
    team = identify_ledger(mode="teams", user_id=7, team_id=11, pages=(page(OWNED, FOREIGN),))
    user = identify_ledger(mode="users", user_id=8, team_id=None, pages=(page(OWNED, FOREIGN),))

    assert ([row.row_id for row in team.owned], [row.row_id for row in team.foreign]) == (
        ["instance-1"],
        ["instance-2"],
    )
    assert ([row.row_id for row in user.owned], [row.row_id for row in user.foreign]) == (
        ["instance-2"],
        ["instance-1"],
    )


def test_ledger_read_is_lazy_until_authenticated_identity_has_settled():
    asked = []

    class Broker:
        def instance_ledger_page(self, number):
            from solver.board_broker_contracts import BoardBrokerResult, BoardOperation, BoardOutcome, BoardProvenance

            asked.append(number)
            value = page(OWNED)
            return BoardBrokerResult(
                BoardOperation.INSTANCE_LEDGER_PAGE,
                BoardOutcome.ANSWERED,
                value,
                BoardProvenance(http_status=200, response_digest=value.digest),
            )

    broker = Broker()

    unsettled = read_profiled_instance_ledger(AuthenticatedIdentity("teams", 0, None), broker)
    populated = read_profiled_instance_ledger(AuthenticatedIdentity("teams", 7, 11), broker)

    assert unsettled.outcome == "unsettled"
    assert populated.outcome == "populated"
    assert asked == [1]
    assert populated.owned[0].source_page == 1
    assert populated.owned[0].response_digest == hashlib.sha256(b"page-1").hexdigest()


def broker_for(pages, asked):
    class Broker:
        def instance_ledger_page(self, number):
            from solver.board_broker_contracts import BoardBrokerResult, BoardOperation, BoardOutcome, BoardProvenance

            asked.append(number)
            value = pages[number]
            return BoardBrokerResult(
                BoardOperation.INSTANCE_LEDGER_PAGE,
                BoardOutcome.ANSWERED,
                value,
                BoardProvenance(http_status=200, response_digest=value.digest),
            )

    return Broker()


def test_ledger_read_follows_the_settled_page_count_once():
    pages = {
        1: page(OWNED, page=1, total_pages=2, total_rows=2),
        2: page(FOREIGN, page=2, total_pages=2, total_rows=2),
    }
    asked = []

    result = read_profiled_instance_ledger(AuthenticatedIdentity("teams", 7, 11), broker_for(pages, asked))

    assert result.outcome == "populated"
    assert asked == [1, 2]
    assert [row.row_id for row in result.foreign] == ["instance-2"]


@pytest.mark.parametrize(
    "pages",
    [
        (page(OWNED, total_pages=2, total_rows=1),),
        (page(OWNED, complete=False),),
        (page(page=1, total_pages=2, total_rows=0, digest="same"), page(page=2, total_pages=2, digest="same")),
        (page(total_rows=1),),
    ],
    ids=("missing-page", "truncated", "repeated-page", "lying-empty"),
)
def test_incomplete_or_self_contradictory_pagination_remains_unsettled(pages):
    assert identify_ledger(mode="teams", user_id=7, team_id=11, pages=pages).outcome == "unsettled"


def test_controlled_mixed_ledger_writes_a_sanitized_identity_bound_receipt(tmp_path):
    secret = "board-token-must-not-appear"
    result = identify_ledger(
        mode="teams",
        user_id=7,
        team_id=11,
        pages=(replace(page(OWNED, FOREIGN, digest="a" * 64), body=secret.encode()),),
    )

    receipt_path = write_receipt(tmp_path, "run-1", result)
    receipt = json.loads(receipt_path.read_text())

    assert verify_receipt(receipt_path) == receipt_path
    assert receipt["receipt_type"] == "instance-ledger-identity"
    assert receipt["mode"] == "teams"
    assert receipt["outcome"] == "populated"
    assert receipt["row_ownership_counts"] == {"owned": 1, "foreign": 1}
    assert receipt["authenticated_identity_digest"]
    assert receipt["pagination_trace"][0]["response_digest"] == "a" * 64
    assert secret not in receipt_path.read_text()


def test_receipt_verification_rejects_tampered_ownership_counts(tmp_path):
    result = identify_ledger(mode="teams", user_id=7, team_id=11, pages=(page(OWNED),))
    receipt_path = write_receipt(tmp_path, "run-1", result)
    receipt = json.loads(receipt_path.read_text())
    receipt["row_ownership_counts"]["owned"] = 0
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(ValueError, match="ownership counts"):
        verify_receipt(receipt_path)


def test_receipt_verification_recomputes_the_outcome(tmp_path):
    result = identify_ledger(mode="teams", user_id=7, team_id=11, pages=(page(OWNED),))
    receipt_path = write_receipt(tmp_path, "run-1", result)
    receipt = json.loads(receipt_path.read_text())
    receipt["outcome"] = "empty"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(ValueError, match="outcome disagrees"):
        verify_receipt(receipt_path)


def test_receipt_verification_rejects_forged_empty_without_a_complete_trace(tmp_path):
    result = identify_ledger(mode="teams", user_id=7, team_id=11, pages=(page(),))
    receipt_path = write_receipt(tmp_path, "run-1", result)
    receipt = json.loads(receipt_path.read_text())
    receipt["pagination_trace"] = []
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(ValueError, match="outcome disagrees"):
        verify_receipt(receipt_path)


@pytest.mark.parametrize(
    ("mode", "identity"),
    [
        ("users", {"user_id": 0, "team_id": None}),
        ("teams", {"user_id": 7, "team_id": 0}),
        ("users", {"user_id": True, "team_id": None}),
        ("teams", {"user_id": 7, "team_id": True}),
    ],
)
def test_receipt_verification_rejects_forged_unsettled_identity_even_with_matching_digest(tmp_path, mode, identity):
    result = identify_ledger(mode="users", user_id=7, team_id=None, pages=(page(),))
    receipt_path = write_receipt(tmp_path, "run-1", result)
    receipt = json.loads(receipt_path.read_text())
    receipt["mode"] = mode
    receipt["authenticated_identity"] = identity
    receipt["authenticated_identity_digest"] = digest_bytes(canonical_bytes({"mode": mode, **identity}))
    receipt_path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(ValueError, match="authenticated identity is invalid"):
        verify_receipt(receipt_path)
