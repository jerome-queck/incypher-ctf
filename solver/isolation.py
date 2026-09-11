"""Strict hostile-execution admission before any Board or inference authority opens."""

from __future__ import annotations

import enum
import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from solver import boot
from solver.boot import Refusal

IMAGE_ID = "INCYPHER_STRICT_IMAGE"
SHA256_IMAGE = re.compile(r"sha256:[0-9a-f]{64}\Z")
STRICT_PROFILE_ID = "colima-namespace-cgroup-v1"
STRICT_RUNTIME_PIN = {
    "colima": "0.10.3",
    "docker": "29.7.2",
    "cpu": 8,
    "memory_gib": 24,
    "disk_gib": 100,
}
STRICT_CONTROLS = (
    "user",
    "mount",
    "pid",
    "network",
    "target-broker",
    "syscall",
    "filesystem",
    "control",
    "host-mount",
    "sibling",
    "board",
    "codex",
    "cpa",
    "credential",
    "process-tree",
    "resource",
)
STRICT_PROFILE = {
    "profile_id": STRICT_PROFILE_ID,
    "runtime_pin": STRICT_RUNTIME_PIN,
    "controls": list(STRICT_CONTROLS),
    "uids": {"attempt": 20000, "broker": 30000},
    "cgroup": {"cpu": "20000 100000", "memory": 33554432, "pids": 16, "work_bytes": 8388608},
    "network": "private-with-unix-target-broker",
    "filesystem": "readonly-tools-one-workdir",
    "syscall": "seccomp-deny-privilege",
    "outer_setup_caps": [
        "CAP_NET_ADMIN",
        "CAP_SETGID",
        "CAP_SETPCAP",
        "CAP_SETUID",
        "CAP_SYS_ADMIN",
    ],
}
STRICT_PROFILE_DIGEST = hashlib.sha256(
    json.dumps(STRICT_PROFILE, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()


class IsolationReason(str, enum.Enum):
    """Stable pre-Run reasons the strict profile can Refuse."""

    IMAGE_IDENTITY = "image-identity"
    BOUNDARY_PROBE = "boundary-probe"
    PROCESS_TEARDOWN = "process-teardown"
    RESIDUE = "residue"
    CAPABILITY_DROP = "capability-drop"


class IsolationRefusal(Refusal):
    """A typed failure to admit the sealed strict Isolation profile."""

    def __init__(self, reason: IsolationReason, detail: str) -> None:
        self.reason = reason
        super().__init__(f"{boot.MARK} isolation:{reason.value} — {detail}")


@dataclass(frozen=True)
class ProbeResult:
    """Observed allow/deny and cleanup facts from the fixed hostile probe."""

    checks: tuple[tuple[str, bool], ...]
    broker_peer_uid: int
    processes_before_kill: int
    processes_after_kill: int
    owned_residue: tuple[str, ...]


@dataclass(frozen=True)
class IsolationReceipt:
    """Sanitized facts binding one exact image to the admitted strict profile."""

    profile_id: str
    profile_digest: str
    image_id: str
    runtime_pin: tuple[tuple[str, str | int], ...]
    outer_capabilities: str
    checks: tuple[tuple[str, str], ...]
    broker_peer_uid: int
    processes_before_kill: int
    processes_after_kill: int
    owned_residue: tuple[str, ...]


def strict_preflight(
    environ: Mapping[str, str],
    *,
    run_probe: Callable[[], ProbeResult] | None = None,
    drop_outer_capabilities: Callable[[], None] | None = None,
    prepare_attempt_runtime: Callable[[], object] | None = None,
) -> IsolationReceipt:
    """Prove the fixed boundary, remove setup authority, then admit the exact image."""

    image_id = environ.get(IMAGE_ID, "")
    if not SHA256_IMAGE.fullmatch(image_id):
        raise IsolationRefusal(
            IsolationReason.IMAGE_IDENTITY,
            f"{IMAGE_ID} must name the exact sha256 image",
        )
    if run_probe is None or drop_outer_capabilities is None:
        from solver.isolation_runtime import drop_outer_capabilities as drop_capabilities
        from solver.isolation_runtime import run_strict_probe

        run_probe = run_probe or run_strict_probe
        drop_outer_capabilities = drop_outer_capabilities or drop_capabilities
    result = run_probe()
    supplied = dict(result.checks)
    failed = [control for control in STRICT_CONTROLS if supplied.get(control) is not True]
    unknown = sorted(set(supplied) - set(STRICT_CONTROLS))
    if failed or unknown:
        detail = ", ".join([*(f"{control}=fail" for control in failed), *(f"{control}=unknown" for control in unknown)])
        raise IsolationRefusal(IsolationReason.BOUNDARY_PROBE, detail)
    if result.processes_after_kill != 0:
        raise IsolationRefusal(
            IsolationReason.PROCESS_TEARDOWN,
            f"{result.processes_after_kill} hostile processes survived cgroup.kill",
        )
    if result.owned_residue:
        raise IsolationRefusal(IsolationReason.RESIDUE, f"owned residue remains: {', '.join(result.owned_residue)}")
    if prepare_attempt_runtime is not None:
        prepare_attempt_runtime()
    drop_outer_capabilities()
    return IsolationReceipt(
        profile_id=STRICT_PROFILE_ID,
        profile_digest=STRICT_PROFILE_DIGEST,
        image_id=image_id,
        runtime_pin=tuple(STRICT_RUNTIME_PIN.items()),
        outer_capabilities="empty",
        checks=tuple((control, "pass") for control in STRICT_CONTROLS),
        broker_peer_uid=result.broker_peer_uid,
        processes_before_kill=result.processes_before_kill,
        processes_after_kill=result.processes_after_kill,
        owned_residue=result.owned_residue,
    )


__all__ = [
    "STRICT_CONTROLS",
    "STRICT_PROFILE_DIGEST",
    "STRICT_PROFILE_ID",
    "STRICT_RUNTIME_PIN",
    "IsolationReason",
    "IsolationReceipt",
    "IsolationRefusal",
    "ProbeResult",
    "strict_preflight",
]
