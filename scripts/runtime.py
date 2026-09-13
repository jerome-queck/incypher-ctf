"""The container runtime this repository runs on, pinned, and the guard that it still matches.

    python3 scripts/runtime.py start            # bring the VM up on the pinned allocation
    python3 scripts/runtime.py verify           # report every way this machine is off the pin
    python3 scripts/runtime.py enable-at-login  # start the VM at login, and hold the versions

Colima rather than Docker Desktop because the allocation below is a reviewable line rather than a
slider on one laptop (ADR-0012). That only stays true while this file is the source of truth:
Colima remembers whatever allocation it was last started with, so a hand-run `colima start
--cpu 2` would otherwise stick with nothing to say so. `verify` is what closes that.

Standard library only, and no import of anything else in this repository — it has to run on a
machine where the Solver image does not exist yet.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

GIB = 1024**3
EXTERNAL_PROJECT_ROOT = Path("/Volumes/Working/001 Projects")

# One profile, named by Colima's own default, so every `docker` in this repository and every
# `colima` typed by hand address the same VM without a flag.
PROFILE = "default"


@dataclass(frozen=True)
class Pin:
    colima: str
    docker: str
    cpu: int
    memory_gib: int
    disk_gib: int


# Eight of the build machine's 14 cores and half its 48 GiB. The Owner accepted the live VM's
# 8 CPU, 24 GiB and 100 GiB allocation on 5 September 2026; keeping that measured allocation avoids
# a `colima stop` that would invalidate the unattended restart proof. Moving it again remains one
# reviewable line here rather than a slider on one laptop.
PIN = Pin(colima="0.10.3", docker="29.7.2", cpu=8, memory_gib=24, disk_gib=100)


def version_in(text: str) -> str | None:
    """The first dotted version in whatever a tool prints around it."""
    match = re.search(r"\d+\.\d+\.\d+", text)
    return match.group(0) if match else None


def drift(versions: Mapping[str, str], vm: Mapping[str, Any] | None, pin: Pin = PIN) -> list[str]:
    """Every way a machine differs from the pin, in the words a reader needs to fix it.

    Empty means this machine is the machine the repository describes. A VM that is absent or
    stopped is reported on its own, because its allocation says nothing until it is running.
    """
    found = [
        f"{tool} is {versions.get(tool) or 'not installed'}, pinned at {pinned}"
        for tool, pinned in (("colima", pin.colima), ("docker", pin.docker))
        if versions.get(tool) != pinned
    ]

    if vm is None:
        found.append("no Colima VM: `python3 scripts/runtime.py start` creates it on the pinned allocation")
        return found

    if vm["status"] != "Running":
        found.append(f"the VM is {vm['status']}, not Running")
        return found

    # Colima reports bytes it derived from the GiB it was asked for, so the comparison rounds back
    # rather than demanding the byte count survive the round trip exactly.
    for measured, pinned, unit in (
        (vm["cpus"], pin.cpu, "CPUs"),
        (round(vm["memory"] / GIB), pin.memory_gib, "GiB of memory"),
        (round(vm["disk"] / GIB), pin.disk_gib, "GiB of disk"),
    ):
        if measured != pinned:
            found.append(f"the VM has {measured} {unit}, pinned at {pinned}")

    return found


def _output(*command: str) -> str:
    result = subprocess.run(command, capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


def installed_versions() -> dict[str, str]:
    versions = {
        "colima": version_in(_output("colima", "version")),
        "docker": version_in(_output("docker", "--version")),
    }
    return {tool: version for tool, version in versions.items() if version}


def observed_vm() -> dict[str, Any] | None:
    """The pinned profile as Colima reports it, or None when it has never been created."""
    for line in _output("colima", "list", "--json").splitlines():
        profile = json.loads(line)
        if profile["name"] == PROFILE:
            return profile
    return None


def storage_drift(
    colima_home: Path,
    project_root: Path,
    *,
    volume_mounted: bool,
    mount_reaches_vm: bool | None,
) -> list[str]:
    """Every way the external runtime location differs from ADR-0057."""
    found = []
    if not volume_mounted:
        found.append(f"the Working volume is not mounted at {project_root.parent}")
    expected = project_root / "incypher-colima"
    if not colima_home.is_symlink() or colima_home.resolve() != expected:
        found.append(f"{colima_home} does not resolve to {expected}")
    if not expected.is_dir():
        found.append(f"the external Colima data directory is absent: {expected}")
    if mount_reaches_vm is False:
        found.append(f"the VM cannot write the declared host mount {project_root}")
    return found


def _storage_drift(vm: Mapping[str, Any] | None, *, probe_vm: bool) -> list[str]:
    reaches_vm = None
    if probe_vm and vm and vm["status"] == "Running":
        reaches_vm = (
            subprocess.run(
                ["colima", "ssh", "--", "test", "-w", str(EXTERNAL_PROJECT_ROOT)],
                capture_output=True,
            ).returncode
            == 0
        )
    return storage_drift(
        Path.home() / ".colima",
        EXTERNAL_PROJECT_ROOT,
        volume_mounted=EXTERNAL_PROJECT_ROOT.parent.is_mount(),
        mount_reaches_vm=reaches_vm,
    )


def start() -> int:
    vm = observed_vm()
    found = _storage_drift(vm, probe_vm=False)
    if found:
        for line in found:
            print(f"drift: {line}")
        return 1
    if vm and vm["status"] == "Running":
        print(f"the {PROFILE} VM is already running")
        return verify()

    subprocess.run(
        ["colima", "start", "--cpu", str(PIN.cpu), "--memory", str(PIN.memory_gib), "--disk", str(PIN.disk_gib)],
        check=True,
    )
    return verify()


def verify() -> int:
    vm = observed_vm()
    found = drift(installed_versions(), vm) + _storage_drift(vm, probe_vm=True)
    for line in found:
        print(f"drift: {line}")
    if found:
        print("\nthis machine is not the one the repository describes — `colima stop` then `start` re-applies the pin")
        return 1
    print(
        f"on the pin: colima {PIN.colima}, docker {PIN.docker}, {PIN.cpu} CPUs, {PIN.memory_gib} GiB, {PIN.disk_gib} GiB disk"
    )
    return 0


def _starts_at_login() -> bool:
    return subprocess.run(["launchctl", "list", "homebrew.mxcl.colima"], capture_output=True).returncode == 0


def _wait_until_running(seconds: int = 120) -> bool:
    for _ in range(seconds):
        vm = observed_vm()
        if vm and vm["status"] == "Running":
            return True
        time.sleep(1)
    return False


def enable_at_login() -> int:
    """Start the VM at login, and stop `brew upgrade` from moving either tool off the pin.

    `brew services` runs `colima start -f`, which stays in the foreground — the shape launchd
    expects, and the reason no plist is written by hand here. A VM that is already up is stopped
    first so launchd's copy is the one that owns it: against a running VM `colima start -f`
    returns immediately, and `keep_alive successful_exit` turns that into a restart loop
    (colima#490).
    """
    vm = observed_vm()
    found = _storage_drift(vm, probe_vm=False)
    if found:
        for line in found:
            print(f"drift: {line}")
        return 1
    if _starts_at_login() and vm and vm["status"] == "Running":
        # Doing this twice is not merely wasteful: the stop-and-start below would give every
        # container a fresh start time, which is exactly the evidence `restart_probe.py` reads.
        # A second run would forge the restart proof rather than repeat the setup.
        print("colima already starts at login and the VM is up — nothing to change")
        return verify()

    if vm and vm["status"] == "Running":
        subprocess.run(["colima", "stop"], check=True)

    subprocess.run(["brew", "services", "start", "colima"], check=True)
    subprocess.run(["brew", "pin", "colima", "docker"], check=True)
    if not _wait_until_running():
        print("the login service did not bring the VM up — `brew services info colima` says why", file=sys.stderr)
        return 1

    print("\ncolima starts at login, and both tools are held at their pinned versions")
    print("a Mac that reboots to a login screen starts nothing — auto-login is the other half:")
    print("  bash scripts/setup-runtime.sh")
    return verify()


COMMANDS = {"start": start, "verify": verify, "enable-at-login": enable_at_login}


def main(argv: list[str]) -> int:
    command = COMMANDS.get(argv[0] if argv else "verify")
    if command is None:
        print(f"usage: python3 scripts/runtime.py [{'|'.join(COMMANDS)}]", file=sys.stderr)
        return 2
    return command()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
