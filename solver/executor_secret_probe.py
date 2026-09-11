"""Black-box secret probes over one freshly exec'd hostile-process surface."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from solver.event_store_storage import canonical_bytes

SURFACE_NAMES = ("memory", "environment", "argv", "file", "event")
MAX_SURFACE_BYTES = 256 * 1024 * 1024
READ_CHUNK = 1024 * 1024
UNREADABLE_KERNEL_MAPS = ("[vvar]", "[vvar_vclock]")


@dataclass(frozen=True)
class ExecutorProbeResult:
    checks: tuple[tuple[str, bool], ...]
    evidence_digest: str
    memory_complete: bool

    def document(self) -> dict[str, object]:
        return {
            "checks": {name: "clear" if clear else "found" for name, clear in self.checks},
            "evidence_digest": self.evidence_digest,
            "memory_complete": self.memory_complete,
        }


def classify_surfaces(
    surfaces: Mapping[str, bytes],
    secrets: Sequence[bytes],
    *,
    memory_complete: bool = True,
) -> ExecutorProbeResult:
    """Classify fixed surface bytes without retaining raw evidence or secret digests."""

    if set(surfaces) != set(SURFACE_NAMES):
        raise ValueError("executor probe must supply every fixed surface exactly once")
    if not secrets or any(not secret for secret in secrets):
        raise ValueError("executor probe requires nonempty secret fixtures")
    checks = []
    evidence = {}
    for name in SURFACE_NAMES:
        raw = surfaces[name]
        clear = all(secret not in raw for secret in secrets)
        if name == "memory" and not memory_complete:
            clear = False
        checks.append((name, clear))
        evidence[name] = {"bytes": len(raw), "digest": hashlib.sha256(raw).hexdigest()}
    return ExecutorProbeResult(
        checks=tuple(checks),
        evidence_digest=hashlib.sha256(canonical_bytes(evidence)).hexdigest(),
        memory_complete=memory_complete,
    )


def probe_executor(
    secrets: Sequence[bytes],
    *,
    environment: Mapping[str, str],
    workdir: Path,
    event_paths: Sequence[Path],
) -> ExecutorProbeResult:
    """Exec a clean child, inspect all five surfaces, then destroy it."""

    if sys.platform != "linux" or not Path("/proc").is_dir():
        raise RuntimeError("executor memory probing requires the sealed Linux profile")
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys,time; print('ready',flush=True); time.sleep(30)",
        ],
        cwd=workdir,
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        assert process.stdout is not None
        if process.stdout.readline() != b"ready\n":
            raise RuntimeError("executor probe child did not reach its inspected state")
        memory, complete = _process_memory(process.pid)
        surfaces = {
            "memory": memory,
            "environment": Path(f"/proc/{process.pid}/environ").read_bytes(),
            "argv": Path(f"/proc/{process.pid}/cmdline").read_bytes(),
            "file": _tree_bytes(Path(workdir), excluded={Path(path).resolve() for path in event_paths}),
            "event": _paths_bytes(event_paths),
        }
        return classify_surfaces(surfaces, secrets, memory_complete=complete)
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _process_memory(pid: int) -> tuple[bytes, bool]:
    maps = Path(f"/proc/{pid}/maps").read_text().splitlines()
    descriptor = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
    held = bytearray()
    complete = True
    try:
        for line in maps:
            address, permissions, *_rest = line.split(maxsplit=2)
            # Linux marks vvar readable but deliberately rejects /proc/<pid>/mem reads. It is a
            # kernel-populated time-data page, not executor-owned memory in which a secret can live.
            if not permissions.startswith("r") or line.endswith(UNREADABLE_KERNEL_MAPS):
                continue
            start_text, end_text = address.split("-", 1)
            start, end = int(start_text, 16), int(end_text, 16)
            length = end - start
            if length <= 0 or len(held) + length > MAX_SURFACE_BYTES:
                complete = False
                continue
            offset = 0
            while offset < length:
                wanted = min(READ_CHUNK, length - offset)
                try:
                    chunk = os.pread(descriptor, wanted, start + offset)
                except OSError:
                    complete = False
                    break
                if not chunk:
                    complete = False
                    break
                held.extend(chunk)
                offset += len(chunk)
    finally:
        os.close(descriptor)
    return bytes(held), complete


def _tree_bytes(root: Path, *, excluded: set[Path]) -> bytes:
    paths = [path for path in root.rglob("*") if path.is_file() and path.resolve() not in excluded]
    return _paths_bytes(paths)


def _paths_bytes(paths: Sequence[Path]) -> bytes:
    held = bytearray()
    for path in sorted((Path(item) for item in paths), key=lambda item: str(item)):
        raw = path.read_bytes()
        if len(held) + len(raw) > MAX_SURFACE_BYTES:
            raise RuntimeError("executor probe surface exceeds its evidence bound")
        held.extend(raw)
    return bytes(held)


__all__ = ["ExecutorProbeResult", "classify_surfaces", "probe_executor"]
