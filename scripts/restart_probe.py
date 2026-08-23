"""Prove that a container comes back on its own after the build machine reboots.

    python3 scripts/restart_probe.py arm     # before the reboot
    python3 scripts/restart_probe.py check   # after it

The competition run is unattended, so "the Mac rebooted overnight" has to end with the Solver
running rather than with someone noticing in the morning. Three things carry that and none of them
is visible from `docker ps`: the machine has to reach a logged-in session without a human, the VM
has to start from its LaunchAgent, and Docker has to re-honour `--restart unless-stopped`.

This decides all three by clock. A container whose last start is later than the host's boot came
back by itself — but only counts as *unattended* on a machine that can log itself in, which
FileVault rules out (ADR-0012). Exit codes are distinct because the wizard branches on them, and
because re-arming a probe that has just reported a real failure would erase the finding.

Standard library only, and no import of anything else in this repository.
"""

from __future__ import annotations

import datetime as dt
import enum
import re
import subprocess
import sys
from dataclasses import dataclass

CONTAINER = "restart-probe"
IMAGE = "alpine"
ARMED_AT = "armed-at"


class Outcome(enum.Enum):
    PROVEN = 0
    NOT_PROVEN = 1
    PENDING = 2


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    summary: str


def docker_moment(timestamp: str) -> dt.datetime:
    """An instant from what `docker inspect` prints — nine fractional digits, which `datetime`
    refuses to parse."""
    return dt.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", timestamp))


def boot_moment(sysctl_output: str) -> dt.datetime:
    """The host's boot instant out of `sysctl -n kern.boottime`."""
    seconds = int(re.search(r"sec = (\d+)", sysctl_output).group(1))
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)


def login_needs_a_human(auto_login_user: str, filevault_status: str) -> bool:
    """Whether this machine reboots to something somebody has to type into.

    FileVault outranks the preference: macOS refuses automatic login while it is on, so a
    configured auto-login user is not one that will be used.
    """
    return not auto_login_user or "FileVault is On" in filevault_status


def verdict(
    *,
    armed_at: dt.datetime,
    booted_at: dt.datetime,
    started_at: dt.datetime,
    running: bool,
    login_needs_a_human: bool,
) -> Verdict:
    """Whether this container came back by itself, or which part of the proof is still missing."""
    if booted_at <= armed_at:
        return Verdict(
            Outcome.PENDING,
            f"the host has not rebooted since the probe was armed at {armed_at:%c} — reboot, then check again",
        )

    if not running:
        return Verdict(Outcome.NOT_PROVEN, f"{CONTAINER} is not running — it did not come back after the reboot")

    if started_at < booted_at:
        return Verdict(Outcome.NOT_PROVEN, f"{CONTAINER} has been running since before the boot — a stale reading")

    if login_needs_a_human:
        return Verdict(
            Outcome.NOT_PROVEN,
            f"{CONTAINER} came back, but only once you logged in — automatic login is unavailable on this "
            "machine, so this is recovery with a human in it rather than the unattended kind",
        )

    return Verdict(Outcome.PROVEN, f"{CONTAINER} came back at {started_at:%c}, unattended, after the host booted")


def _output(*command: str) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def _inspect(template: str) -> str:
    return _output("docker", "inspect", "-f", template, CONTAINER)


def armed() -> bool:
    return bool(_inspect("{{.Id}}"))


def arm() -> int:
    """Leave a container running that has every reason to come back, and nothing else to do."""
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    armed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER,
            "--restart",
            "unless-stopped",
            "--label",
            f"{ARMED_AT}={armed_at}",
            IMAGE,
            "sleep",
            "infinity",
        ],
        capture_output=True,
        check=True,
    )
    print(f"armed at {armed_at}")
    print("now reboot the machine and run: python3 scripts/restart_probe.py check")
    return Outcome.PENDING.value


def check() -> int:
    if not armed():
        print(f"no {CONTAINER} to read — `python3 scripts/restart_probe.py arm` comes first", file=sys.stderr)
        return Outcome.PENDING.value

    result = verdict(
        armed_at=dt.datetime.fromisoformat(_inspect(f'{{{{index .Config.Labels "{ARMED_AT}"}}}}')),
        booted_at=boot_moment(_output("sysctl", "-n", "kern.boottime")),
        started_at=docker_moment(_inspect("{{.State.StartedAt}}")),
        running=_inspect("{{.State.Running}}") == "true",
        login_needs_a_human=login_needs_a_human(
            _output("defaults", "read", "/Library/Preferences/com.apple.loginwindow", "autoLoginUser"),
            _output("fdesetup", "status"),
        ),
    )
    print(result.summary)
    return result.outcome.value


COMMANDS = {"arm": arm, "check": check}


def main(argv: list[str]) -> int:
    command = COMMANDS.get(argv[0] if argv else "")
    if command is None:
        print(f"usage: python3 scripts/restart_probe.py [{'|'.join(COMMANDS)}]", file=sys.stderr)
        return 2
    return command()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
