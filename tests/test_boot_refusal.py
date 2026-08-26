"""A Run refuses to start rather than spend a window on a fact nobody set.

The failure every test here is about is silent and unattended: a container comes up at 10:30 with
one variable quietly unset, authenticates with nothing, and runs the full window on a credential
that was never there — indistinguishable afterwards from bad luck. **A loud refusal at 10:15, with a
human standing there, is setup rather than Intervention** (`CONTEXT.md`, *Refusal*).
"""

import datetime as dt

import pytest
from solver import boot
from solver.boot import ABSENT, EMPTY, SET, Refusal, holdings_of, lasting, settings
from solver.credentials import NOT_SECRETS, SECRETS

BOARD = "https://board.example"
NOON = dt.datetime(2026, 9, 22, 12, 0, tzinfo=dt.timezone.utc)


@pytest.fixture
def homes(tmp_path):
    """Two `CODEX_HOME` directories, only the first of them logged in — which is the shape a
    practice Run has, and the shape ADR-0010 calls *absence is the control*."""
    subscription, metered = tmp_path / "codex", tmp_path / "codex-metered"
    subscription.mkdir()
    (subscription / boot.AUTH).write_text("{}")
    metered.mkdir()
    return {boot.SUBSCRIPTION: subscription, boot.METERED: metered}


def env(**overrides):
    return {"CTFD_URL": BOARD, "CTFD_API_TOKEN": "token", "RUN_ID": "gate-1", **overrides}


def test_the_environment_is_read_once_and_yields_everything_a_run_is_pointed_at(homes):
    read = settings(env(), homes=homes)

    assert read.url == BOARD
    assert read.token == "token"
    assert read.run_id == "gate-1"
    assert [rung.slot for rung in read.chain] == [boot.SUBSCRIPTION]


@pytest.mark.parametrize("name", ["CTFD_URL", "CTFD_API_TOKEN"])
def test_a_missing_credential_refuses_the_run_rather_than_beginning_the_loop(name, homes):
    with pytest.raises(Refusal, match=name):
        settings(env(**{name: ""}), homes=homes)


@pytest.mark.parametrize("name", ["TEAM_KEY", "OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"])
def test_an_empty_value_is_not_an_unset_one(name, homes):
    """The trap `docs/credentials.md` names, now enforced. An empty variable occupies its slot in a
    client's credential search the moment it exists, so it authenticates with nothing and falls
    through to a 401 an hour in — and `docker run --env-file` exports it, so the file still looks
    right. None of these three is required, which is the point: absent is fine and empty is not."""
    with pytest.raises(Refusal, match="empty value"):
        settings(env(**{name: ""}), homes=homes)


def test_absence_is_the_control_and_costs_nothing(homes):
    """The metered credential lives only in the scored Board's overlay, so a practice Run does not
    hold it at all — and structurally cannot spend money. Its absence must therefore be ordinary."""
    read = settings(env(), homes=homes)

    assert read.holdings["OPENAI_API_KEY"] == ABSENT
    assert read.holdings["CTFD_API_TOKEN"] == SET


def test_the_boot_check_reads_the_same_declared_set_the_redactor_reads():
    """One list, two readers. A credential declared for the redactor and forgotten by the boot check
    is a Run that starts holding a secret nothing will redact; the reverse is one that refuses for a
    name nothing protects. Neither is possible while this holds (ADR-0010)."""
    assert set(holdings_of({})) == set(SECRETS) | set(NOT_SECRETS)


def test_the_three_states_are_told_apart():
    holdings = holdings_of({"CTFD_URL": BOARD, "TEAM_KEY": "  "})

    assert holdings["CTFD_URL"] == SET
    assert holdings["TEAM_KEY"] == EMPTY
    assert holdings["OPENAI_API_KEY"] == ABSENT


def test_a_run_id_is_never_minted_here(homes):
    """`schedule.Window` consults a duration only when there is no window on disk, so a `run_id`
    generated at startup hands every restart a fresh window and the absolute deadline evaporates
    with nobody there to see it. It comes from something that survives a boot or the Run does not
    start."""
    with pytest.raises(Refusal, match="RUN_ID"):
        settings(env(RUN_ID=""), homes=homes)


@pytest.mark.parametrize("run_id", ["../elsewhere", "runs/one", "a\\b"])
def test_a_run_id_that_is_not_one_path_component_is_refused(run_id, homes):
    """It becomes a directory under `/state/runs`, and `..` would put a Run's window somewhere no
    restart would look for it — which is the same silent extension by another route."""
    with pytest.raises(Refusal, match="one path component"):
        settings(env(RUN_ID=run_id), homes=homes)


def test_a_chain_with_no_rung_logged_in_refuses(tmp_path):
    """ADR-0011 puts the `codex login` minutes before the Run with a human present. Forgetting it is
    a Run that spends its whole window discovering it has no brain, so it is loud here instead."""
    with pytest.raises(Refusal, match="no inference credential"):
        settings(env(), homes={boot.SUBSCRIPTION: tmp_path / "nothing"})


def test_the_chain_is_subscription_first_and_metered_last(homes):
    """ADR-0010's order, and the whole meaning of the term: a fallback that waits for a human to
    reach for it is Intervention, which is the penalised act."""
    (homes[boot.METERED] / boot.AUTH).write_text("{}")

    read = settings(env(), homes=homes)

    assert [rung.slot for rung in read.chain] == [boot.SUBSCRIPTION, boot.METERED]


def test_one_brain_per_run(homes):
    """v1 switches on exhaustion and on nothing else, so every rung runs the same model — and which
    model a subscription serves is account state, so it is config rather than a constant."""
    (homes[boot.METERED] / boot.AUTH).write_text("{}")

    read = settings(env(CODEX_MODEL="gpt-5-mini"), homes=homes)

    assert {rung.model for rung in read.chain} == {"gpt-5-mini"}


def test_run_seconds_may_shorten_a_window_and_may_never_lengthen_one():
    whole = 5.5 * 3600

    assert lasting(None, whole, 1800, NOON) == 1800
    assert lasting(None, whole, 99 * 3600, NOON) == whole


def test_the_close_a_boards_rules_state_is_a_ceiling_the_duration_cannot_cross():
    closes = NOON + dt.timedelta(hours=2)

    assert lasting(closes, 5.5 * 3600, None, NOON) == 2 * 3600


def test_a_window_with_nothing_left_in_it_refuses():
    """Opening one would be a Run that immediately runs its own tail and exits, which reads in the
    record as a Run that had nothing to do rather than as one that was started too late."""
    with pytest.raises(Refusal, match="no window left"):
        lasting(NOON - dt.timedelta(minutes=1), 5.5 * 3600, None, NOON)


def test_settings_never_carry_a_credential_value_into_the_record(homes):
    """`recorded()` is written at run-open, so it says what is *held* and never what is held. A
    record that leaked the key it was proving we had would be the worst possible trade."""
    recorded = settings(env(TEAM_KEY="never-print-me"), homes=homes).recorded()

    assert "never-print-me" not in repr(recorded)
    assert recorded["credentials_held"]["TEAM_KEY"] == SET
