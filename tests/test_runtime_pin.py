"""The pin guard — where a machine's actual runtime is held against the one this repository names.

Colima remembers the last allocation it was started with, so the pin in `scripts/runtime.py` is
only the source of truth while something compares the two. That comparison is decidable offline,
which is what puts it here rather than in a script nobody runs.
"""

from pathlib import Path

import runtime

PINNED_VM = {
    "name": "default",
    "status": "Running",
    "arch": "aarch64",
    "cpus": runtime.PIN.cpu,
    "memory": runtime.PIN.memory_gib * runtime.GIB,
    "disk": runtime.PIN.disk_gib * runtime.GIB,
    "runtime": "docker",
}

PINNED_VERSIONS = {"colima": runtime.PIN.colima, "docker": runtime.PIN.docker}


def test_the_accepted_allocation_is_the_pin():
    assert (runtime.PIN.cpu, runtime.PIN.memory_gib, runtime.PIN.disk_gib) == (8, 24, 700)


def test_a_machine_on_the_pin_has_drifted_on_nothing():
    assert runtime.drift(PINNED_VERSIONS, PINNED_VM) == []


def test_a_missing_vm_is_drift_rather_than_an_empty_answer():
    assert runtime.drift(PINNED_VERSIONS, None) == [
        "no Colima VM: `python3 scripts/runtime.py start` creates it on the pinned allocation"
    ]


def test_a_stopped_vm_is_reported_before_its_allocation():
    stopped = PINNED_VM | {"status": "Stopped"}

    assert runtime.drift(PINNED_VERSIONS, stopped) == ["the VM is Stopped, not Running"]


def test_an_allocation_below_the_pin_names_both_numbers():
    halved = PINNED_VM | {"cpus": 2, "memory": 2 * runtime.GIB}

    assert runtime.drift(PINNED_VERSIONS, halved) == [
        "the VM has 2 CPUs, pinned at 8",
        "the VM has 2 GiB of memory, pinned at 24",
    ]


def test_memory_is_compared_in_gib_so_a_rounded_byte_count_is_not_drift():
    rounded = PINNED_VM | {"memory": runtime.PIN.memory_gib * runtime.GIB - 1}

    assert runtime.drift(PINNED_VERSIONS, rounded) == []


def test_a_larger_disk_is_accepted_as_available_capacity():
    larger = PINNED_VM | {"disk": (runtime.PIN.disk_gib + 100) * runtime.GIB}

    assert runtime.drift(PINNED_VERSIONS, larger) == []


def test_disk_size_is_not_a_strict_runtime_identity():
    smaller = PINNED_VM | {"disk": (runtime.PIN.disk_gib - 1) * runtime.GIB}

    assert runtime.drift(PINNED_VERSIONS, smaller) == []


def test_a_tool_off_the_pin_is_named_with_the_version_that_is_installed():
    assert runtime.drift({"colima": "0.9.0", "docker": runtime.PIN.docker}, PINNED_VM) == [
        "colima is 0.9.0, pinned at 0.10.3"
    ]


def test_a_tool_that_is_not_installed_at_all_says_so():
    assert runtime.drift({"docker": runtime.PIN.docker}, PINNED_VM) == ["colima is not installed, pinned at 0.10.3"]


def test_a_version_is_read_out_of_whatever_the_tool_prints_around_it():
    assert runtime.version_in("colima version 0.10.3\ngit commit: 00f6c29") == "0.10.3"
    assert runtime.version_in("Docker version 29.7.2, build a7dcaa6fdb") == "29.7.2"
    assert runtime.version_in("command not found") is None


def test_external_location_and_writable_vm_mount_are_part_of_the_pin(tmp_path: Path):
    project_root = tmp_path / "Working" / "001 Projects"
    expected = project_root / "incypher-colima"
    expected.mkdir(parents=True)
    colima_home = tmp_path / ".colima"
    colima_home.symlink_to(expected)

    assert runtime.storage_drift(colima_home, project_root, volume_mounted=True, mount_reaches_vm=True) == []
    assert runtime.storage_drift(colima_home, project_root, volume_mounted=False, mount_reaches_vm=False) == [
        f"the Working volume is not mounted at {project_root.parent}",
        f"the VM cannot write the declared host mount {project_root}",
    ]


def test_internal_colima_home_is_location_drift(tmp_path: Path):
    colima_home = tmp_path / ".colima"
    colima_home.mkdir()
    project_root = tmp_path / "Working" / "001 Projects"
    (project_root / "incypher-colima").mkdir(parents=True)

    assert runtime.storage_drift(colima_home, project_root, volume_mounted=True, mount_reaches_vm=None) == [
        f"{colima_home} does not resolve to {project_root / 'incypher-colima'}"
    ]


def test_missing_external_colima_data_is_location_drift(tmp_path: Path):
    project_root = tmp_path / "Working" / "001 Projects"
    project_root.mkdir(parents=True)
    expected = project_root / "incypher-colima"
    colima_home = tmp_path / ".colima"
    colima_home.symlink_to(expected)

    assert runtime.storage_drift(colima_home, project_root, volume_mounted=True, mount_reaches_vm=None) == [
        f"the external Colima data directory is absent: {expected}"
    ]
