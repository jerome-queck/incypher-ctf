"""The restart proof — where "the container came back" is separated from "it never went away".

A reboot happens hours after the probe is armed and nobody is watching either end, so the verdict
is reached by comparing three timestamps rather than by anyone looking at `docker ps`. That
comparison is the seam, and it decides offline.
"""

import restart_probe as probe

ARMED = "2026-09-21T10:00:00Z"
BOOTED = "2026-09-21T11:00:00Z"
CAME_BACK = "2026-09-21T11:01:00Z"


def test_a_container_that_started_after_the_boot_is_the_proof():
    verdict = probe.verdict(armed_at=ARMED, booted_at=BOOTED, started_at=CAME_BACK, running=True)

    assert verdict.proven
    assert "came back" in verdict.summary


def test_a_container_that_is_not_running_fails_however_the_clocks_read():
    verdict = probe.verdict(armed_at=ARMED, booted_at=BOOTED, started_at=CAME_BACK, running=False)

    assert not verdict.proven
    assert "not running" in verdict.summary


def test_a_host_that_has_not_rebooted_since_arming_proves_nothing_yet():
    verdict = probe.verdict(armed_at=ARMED, booted_at="2026-09-21T09:00:00Z", started_at=ARMED, running=True)

    assert not verdict.proven
    assert "has not rebooted" in verdict.summary


def test_a_container_still_up_from_before_the_boot_is_a_stale_reading_not_a_pass():
    verdict = probe.verdict(armed_at=ARMED, booted_at=BOOTED, started_at=ARMED, running=True)

    assert not verdict.proven
    assert "before the boot" in verdict.summary


def test_docker_nanoseconds_do_not_defeat_the_comparison():
    """`docker inspect` prints nine fractional digits, which `datetime` will not parse."""
    assert probe.moment("2026-09-21T11:01:00.123456789Z") == probe.moment("2026-09-21T11:01:00.123456Z")


def test_the_boot_moment_is_read_out_of_what_sysctl_prints():
    assert probe.boot_moment("{ sec = 1789988400, usec = 123456 } Mon Sep 21 11:00:00 2026") == probe.moment(
        "2026-09-21T11:00:00Z"
    )
