"""One external command, with its wall-clock and its output bounded as they are spent.

Two callers run a command of their own: the recon cascade dispatches on one and the Flag
verification replays one. The rule they share is not "call `subprocess`" — it is that the caps are
enforced **where the bytes are**, rather than applied to a buffer that has already arrived. Brunner
links a 6.15 GB archive from description prose, and `strings` over it will fill memory long before
a cap on a finished buffer is ever consulted.

A tool that is not installed is the ordinary case rather than the exceptional one — this runs on a
laptop as readily as in the image — so it comes back as output like any other failure. Silence
would leave the model to narrate what it thinks happened (`CONTEXT.md`, *Observation*).

Standard library only — this runs inside the Solver image, which has nothing installed.
"""

from __future__ import annotations

import os
import select
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from solver.credentials import CHILD_ENVIRONMENT

# After EOF on its stdout a child has said everything it is going to say, so anything past this
# grace is a process that will not exit rather than one still working — and a caller that waits on
# one is the hang the caps exist to make impossible.
REAP_GRACE_SECONDS = 1.0

READ_BLOCK_BYTES = 65536


def run(
    argv: Sequence[str], *, budget: float, cap: int, mark: str, cwd: Path | None = None
) -> tuple[int | None, bytes]:
    """Run it, and answer with its exit code and what it said — `None` where it had to be killed.

    `mark` is the caller's own prefix, so a line this module wrote about a command is never taken
    for something the command said.
    """
    try:
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # A named environment rather than ours: these commands read challenge-supplied bytes,
            # and a tool that printed its own environment on a malformed input would put every
            # declared credential into an Observation (`solver/credentials.py`).
            env={name: os.environ[name] for name in CHILD_ENVIRONMENT if name in os.environ},
        )
    except OSError as error:
        return None, f"{mark} {argv[0]} did not run — {error}".encode()
    body, note = _captured(process, budget, cap, mark)
    if not note:
        try:
            return process.wait(timeout=REAP_GRACE_SECONDS), body
        except subprocess.TimeoutExpired:
            note = f"\n{mark} killed — it closed its output and did not exit".encode()
    process.kill()
    process.wait()
    return None, body + note


def _captured(process: subprocess.Popen[bytes], budget: float, cap: int, mark: str) -> tuple[bytes, bytes]:
    """Read what the child says until it stops, the cap is reached, or the budget is gone.

    The cap is applied here and not to a finished buffer, which is the difference between a bound
    and an intention.
    """
    deadline = time.monotonic() + budget
    blocks: list[bytes] = []
    taken = 0
    with process.stdout as stream:
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or not select.select([stream], [], [], left)[0]:
                return b"".join(blocks)[:cap], f"\n{mark} killed after {budget:.1f}s".encode()
            block = os.read(stream.fileno(), READ_BLOCK_BYTES)
            if not block:
                return b"".join(blocks), b""
            blocks.append(block)
            taken += len(block)
            if taken >= cap:
                return b"".join(blocks)[:cap], f"\n{mark} output capped at {cap} bytes".encode()
