"""The restart proof — where "the container came back on its own" is separated from three things
that look identical to it: it never went away, the host never rebooted, and a human unlocked the
machine before anything could start.

The reboot happens hours after the probe is armed with nobody watching either end, so the verdict
is reached by comparing clocks rather than by anyone reading `docker ps`. That comparison decides
offline, which is what puts it here.
"""

import datetime as dt

import restart_probe as probe

ARMED = probe.docker_moment("2026-09-21T10:00:00Z")
BOOTED = probe.docker_moment("2026-09-21T11:00:00Z")
CAME_BACK = probe.docker_moment("2026-09-21T11:01:00Z")


def reading(**overrides):
    return {
        "armed_at": ARMED,
        "booted_at": BOOTED,
        "started_at": CAME_BACK,
        "running": True,
        "login_needs_a_human": False,
    } | overrides


def test_a_container_that_started_after_the_boot_on_a_machine_that_logs_itself_in_is_the_proof():
    result = probe.verdict(**reading())

    assert result.outcome is probe.Outcome.PROVEN
    assert "came back" in result.summary


def test_a_reboot_that_needed_someone_to_log_in_is_recovery_with_a_human_in_it():
    """The criterion is unattended. A container that waits for a password did not meet it."""
    result = probe.verdict(**reading(login_needs_a_human=True))

    assert result.outcome is probe.Outcome.NOT_PROVEN
    assert "logged in" in result.summary


def test_a_container_that_is_not_running_fails_however_the_clocks_read():
    result = probe.verdict(**reading(running=False))

    assert result.outcome is probe.Outcome.NOT_PROVEN
    assert "not running" in result.summary


def test_a_host_that_has_not_rebooted_yet_is_pending_rather_than_failed():
    """Kept apart from failure because the wizard re-arms on one and must not on the other."""
    result = probe.verdict(**reading(booted_at=probe.docker_moment("2026-09-21T09:00:00Z")))

    assert result.outcome is probe.Outcome.PENDING
    assert "has not rebooted" in result.summary


def test_a_container_still_up_from_before_the_boot_is_a_stale_reading_not_a_pass():
    result = probe.verdict(**reading(started_at=ARMED))

    assert result.outcome is probe.Outcome.NOT_PROVEN
    assert "before the boot" in result.summary


def test_every_outcome_has_an_exit_code_of_its_own():
    """The wizard branches on these: it re-arms on one and would destroy evidence on another."""
    assert {outcome.value for outcome in probe.Outcome} == {0, 1, 2}


def test_docker_nanoseconds_do_not_defeat_the_comparison():
    """`docker inspect` prints nine fractional digits, which `datetime` will not parse."""
    assert probe.docker_moment("2026-09-21T11:01:00.123456789Z") == probe.docker_moment("2026-09-21T11:01:00.123456Z")


def test_the_boot_moment_is_read_out_of_what_sysctl_prints():
    assert probe.boot_moment("{ sec = 1789988400, usec = 123456 } Mon Sep 21 11:00:00 2026") == dt.datetime(
        2026, 9, 21, 11, 0, tzinfo=dt.timezone.utc
    )


def test_filevault_means_a_human_however_auto_login_is_configured():
    """macOS refuses automatic login while FileVault is on, whatever the preference says."""
    assert probe.login_needs_a_human("jerome", "FileVault is On.")


def test_a_machine_with_no_auto_login_user_needs_a_human():
    assert probe.login_needs_a_human("", "FileVault is Off.")


def test_an_auto_login_user_without_filevault_is_the_one_case_that_needs_nobody():
    assert not probe.login_needs_a_human("jerome", "FileVault is Off.")
