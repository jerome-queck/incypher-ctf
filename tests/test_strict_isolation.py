"""The strict Isolation profile must qualify before the Run controller opens."""

import copy
import json
import subprocess

import pytest

from solver import isolation_runtime
from solver.isolation import (
    STRICT_CONTROLS,
    STRICT_PROFILE_ID,
    STRICT_RUNTIME_PIN,
    IsolationReason,
    IsolationRefusal,
    ProbeResult,
    strict_preflight,
)
from solver.isolation_receipt import manifest_receipt, verify_receipt, write_receipt
from solver.isolation_runtime import (
    CAP_SYS_ADMIN,
    STRICT_PROBE_COMMAND,
    drop_outer_capabilities,
    run_strict_probe,
)
from solver.manifest import generate_manifest
from test_manifest import release_candidate_profile


def test_an_ordinary_container_refuses_without_an_exact_image_identity():
    with pytest.raises(IsolationRefusal) as refused:
        strict_preflight({})

    assert refused.value.reason is IsolationReason.IMAGE_IDENTITY
    assert str(refused.value) == (
        "[boot] isolation:image-identity — INCYPHER_STRICT_IMAGE must name the exact sha256 image"
    )


def test_a_drifted_boundary_refuses_before_capabilities_can_drop():
    dropped = []
    result = ProbeResult(
        checks=tuple((control, control != "network") for control in STRICT_CONTROLS),
        broker_peer_uid=20000,
        processes_before_kill=4,
        processes_after_kill=0,
        owned_residue=(),
    )

    with pytest.raises(IsolationRefusal) as refused:
        strict_preflight(
            {"INCYPHER_STRICT_IMAGE": "sha256:" + "a" * 64},
            run_probe=lambda: result,
            drop_outer_capabilities=lambda: dropped.append(True),
        )

    assert refused.value.reason is IsolationReason.BOUNDARY_PROBE
    assert str(refused.value) == "[boot] isolation:boundary-probe — network=fail"
    assert dropped == []


def test_every_boundary_passes_before_outer_namespace_capabilities_are_dropped():
    trace = []

    def probe() -> ProbeResult:
        trace.append("probe")
        return ProbeResult(
            checks=tuple((control, True) for control in STRICT_CONTROLS),
            broker_peer_uid=20000,
            processes_before_kill=4,
            processes_after_kill=0,
            owned_residue=(),
        )

    def drop() -> None:
        trace.append("drop")

    receipt = strict_preflight(
        {"INCYPHER_STRICT_IMAGE": "sha256:" + "a" * 64},
        run_probe=probe,
        drop_outer_capabilities=drop,
        prepare_attempt_runtime=lambda: trace.append("attempt-pool"),
    )

    assert trace == ["probe", "attempt-pool", "drop"]
    assert receipt.profile_id == STRICT_PROFILE_ID
    assert receipt.image_id == "sha256:" + "a" * 64
    assert dict(receipt.runtime_pin) == STRICT_RUNTIME_PIN
    assert receipt.outer_capabilities == "empty"
    assert receipt.checks == tuple((control, "pass") for control in STRICT_CONTROLS)
    assert receipt.processes_after_kill == 0
    assert receipt.owned_residue == ()


def test_the_hostile_probe_has_one_fixed_command_and_parses_only_its_receipt():
    trace = []
    supplied = {
        "checks": {control: True for control in STRICT_CONTROLS},
        "broker_peer_uid": 20000,
        "processes_before_kill": 4,
        "processes_after_kill": 0,
        "owned_residue": [],
    }

    def execute(command, **options):
        trace.append((command, options))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(supplied) + "\n", stderr="")

    result = run_strict_probe(execute=execute, prepare=lambda: trace.append("control-cgroup"))

    assert trace == [
        "control-cgroup",
        (
            STRICT_PROBE_COMMAND,
            {
                "capture_output": True,
                "check": False,
                "env": {"PATH": "/usr/local/bin:/usr/bin:/bin"},
                "text": True,
                "timeout": 45,
            },
        ),
    ]
    assert result == ProbeResult(
        checks=tuple((control, True) for control in STRICT_CONTROLS),
        broker_peer_uid=20000,
        processes_before_kill=4,
        processes_after_kill=0,
        owned_residue=(),
    )


def test_a_hung_hostile_probe_is_a_typed_pre_authority_refusal():
    def execute(command, **_options):
        raise subprocess.TimeoutExpired(command, 45)

    with pytest.raises(IsolationRefusal) as refused:
        run_strict_probe(execute=execute, prepare=lambda: None)

    assert refused.value.reason is IsolationReason.BOUNDARY_PROBE
    assert str(refused.value) == "[boot] isolation:boundary-probe — fixed probe timed out after 45 seconds"


def test_outer_namespace_setup_capabilities_are_removed_from_every_set(monkeypatch: pytest.MonkeyPatch):
    class Libc:
        def __init__(self) -> None:
            self.bounding_drops = []
            self.ambient_cleared = False
            self.written = None

        def capget(self, _header, data) -> int:
            for word in data._obj:
                word.effective = 0xFFFFFFFF
                word.permitted = 0xFFFFFFFF
                word.inheritable = 0xFFFFFFFF
            return 0

        def prctl(self, operation, capability, *_args) -> int:
            if operation == isolation_runtime.PR_CAPBSET_DROP:
                self.bounding_drops.append(capability)
            else:
                self.ambient_cleared = True
            return 0

        def capset(self, _header, data) -> int:
            self.written = tuple((word.effective, word.permitted, word.inheritable) for word in data._obj)
            return 0

    library = Libc()
    monkeypatch.setattr(isolation_runtime.os.path, "exists", lambda path: path == "/proc/self/status")

    drop_outer_capabilities(
        libc=library,
        cap_last=CAP_SYS_ADMIN,
        read_status=lambda: "\n".join(
            f"{name}:\t0000000000000000" for name in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
        ),
    )

    assert library.bounding_drops == list(range(CAP_SYS_ADMIN + 1))
    assert library.ambient_cleared is True
    assert library.written == ((0, 0, 0), (0, 0, 0))


def test_the_sanitized_receipt_is_canonical_verifiable_and_manifest_linked(tmp_path):
    result = ProbeResult(
        checks=tuple((control, True) for control in STRICT_CONTROLS),
        broker_peer_uid=20000,
        processes_before_kill=4,
        processes_after_kill=0,
        owned_residue=(),
    )
    admitted = strict_preflight(
        {"INCYPHER_STRICT_IMAGE": "sha256:" + "b" * 64},
        run_probe=lambda: result,
        drop_outer_capabilities=lambda: None,
    )

    path = write_receipt(tmp_path, "run-1", admitted)

    assert verify_receipt(path) == path
    document = json.loads(path.read_text())
    assert document["receipt_type"] == "strict-isolation-preflight"
    assert document["runtime_pin"] == STRICT_RUNTIME_PIN
    assert document["owned_residue"] == []
    assert document["manifest_link"] == {
        "row_id": "core.strict-isolation",
        "receipt_ref": "receipt:strict-isolation-preflight",
    }
    descriptor = manifest_receipt(path)
    assert descriptor["ref"] == "receipt:strict-isolation-preflight"
    assert descriptor["kind"] == "strict-isolation-preflight"

    draft = generate_manifest(
        image_digest="sha256:" + "c" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    requirements = copy.deepcopy(draft["requirements"])
    isolation_row = next(row for row in requirements if row["row_id"] == "core.strict-isolation")
    isolation_row.update(status="implemented", receipt_ref=descriptor["ref"])
    linked = generate_manifest(
        image_digest=draft["candidate"]["image_digest"],
        release_candidate_profile=draft["selected_profile"],
        requirements=requirements,
        receipts=[descriptor],
    )
    linked_row = next(row for row in linked["requirements"] if row["row_id"] == "core.strict-isolation")
    assert linked_row["receipt_ref"] == descriptor["ref"]
    assert linked["lifecycle"] == "provisional"
