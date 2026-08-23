"""Prove that a container comes back on its own after the build machine reboots.

    python3 scripts/restart_probe.py arm     # before the reboot
    python3 scripts/restart_probe.py check   # after it

The competition run is unattended, so "the Mac rebooted overnight" has to end with the Solver
running rather than with someone noticing in the morning. Two things carry that and neither is
visible from `docker ps`: the VM has to start at login, and Docker has to re-honour
`--restart unless-stopped`. This decides both by clock — a container whose last start is later
than the host's boot is one that came back by itself.

Standard library only, and no import of anything else in this repository.
"""

from __future__ import annotations

import datetime as dt
import re
import subprocess
import sys
from dataclasses import dataclass

CONTAINER = "restart-probe"
IMAGE = "alpine"
ARMED_AT = "armed-at"


@dataclass(frozen=True)
class Verdict:
    proven: bool
    summary: str


def moment(timestamp: str) -> dt.datetime:
    """An instant from what Docker prints — nine fractional digits, which `datetime` refuses."""
    return dt.datetime.fromisoformat(re.sub(r"(\.\d{6})\d+", r"\1", timestamp))


def boot_moment(sysctl_output: str) -> dt.datetime:
    """The host's boot instant out of `sysctl -n kern.boottime`."""
    seconds = int(re.search(r"sec = (\d+)", sysctl_output).group(1))
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc)


def verdict(*, armed_at: str, booted_at: str, started_at: str, running: bool) -> Verdict:
    """Whether this container came back by itself, or which part of the proof is still missing."""
    if not running:
        return Verdict(False, f"{CONTAINER} is not running — it did not come back after the reboot")

    armed, booted, started = moment(armed_at), moment(booted_at), moment(started_at)
    if booted <= armed:
        return Verdict(False, f"the host has not rebooted since the probe was armed at {armed_at} — nothing proven yet")
    if started < booted:
        return Verdict(False, f"{CONTAINER} has been running since before the boot at {booted_at} — a stale reading")

    return Verdict(True, f"{CONTAINER} came back at {started_at}, unattended, after the host booted at {booted_at}")


def _docker(*arguments: str) -> str:
    return subprocess.run(["docker", *arguments], capture_output=True, text=True, check=True).stdout.strip()


def _inspect(template: str) -> str:
    return _docker("inspect", "-f", template, CONTAINER)


def arm() -> int:
    """Leave a container running that has every reason to come back, and nothing else to do."""
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
    armed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    _docker(
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
    )
    print(f"armed at {armed_at}")
    print("now reboot the machine, log in, and run: python3 scripts/restart_probe.py check")
    return 0


def check() -> int:
    result = verdict(
        armed_at=_inspect(f'{{{{index .Config.Labels "{ARMED_AT}"}}}}'),
        booted_at=boot_moment(
            subprocess.run(["sysctl", "-n", "kern.boottime"], capture_output=True, text=True).stdout
        ).isoformat(),
        started_at=_inspect("{{.State.StartedAt}}"),
        running=_inspect("{{.State.Running}}") == "true",
    )
    print(result.summary)
    return 0 if result.proven else 1


COMMANDS = {"arm": arm, "check": check}


def main(argv: list[str]) -> int:
    command = COMMANDS.get(argv[0] if argv else "")
    if command is None:
        print(f"usage: python3 scripts/restart_probe.py [{'|'.join(COMMANDS)}]", file=sys.stderr)
        return 2
    return command()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
