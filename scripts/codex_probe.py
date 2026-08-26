"""Ask the image whether the brain we would spend a Run on can actually run a command.

    python3 scripts/codex_probe.py [--image solver] [--model gpt-…] [--seconds 240]

Three things fail silently between a green build and a working Solver, and this is the only check
that catches any of them. All three were met on 26 August 2026, in this order, each one hidden
behind the last:

1. **The model id is not served.** `gpt-5` answers *"not supported when using Codex with a ChatGPT
   account"*, as `gpt-5-codex` did before it. Which ids a subscription serves is account state.
2. **The CLI ships in two halves.** From 0.147.0 the shell tool routes through a separate
   `codex-code-mode-host`, and without it every command fails before it runs — *"Code mode will fail
   closed"* is the CLI's own wording, and `codex --version` answers perfectly meanwhile.
3. **The sandbox cannot be built.** `bubblewrap` needs a mount namespace Docker will not grant an
   unprivileged container, so every command dies at `bwrap: Failed to make / slave`.

**It runs inside the image, and that is the point.** A probe run on the host would have passed all
three — the host has a working CLI and no container confinement — while the thing that competes
could not execute one command. So this drives `docker run` against the image a Run will use, and
what it proves is the argv `solver/codex.py` builds, the binaries the `Dockerfile` installed, and
the sandbox `Invocation` asks for.

Standard library only, and no import of `solver/` on the host side: the repository and the image
are two different trees, and the one under test is the image.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import ctfd_probe  # noqa: E402

# A sentinel no model would emit on its own and no tool would print by accident, so seeing it come
# back is a command having run rather than prose about one.
SENTINEL = "CODEX-PROBE-RAN-A-COMMAND"

ASK = f"Run exactly this command, then stop and say nothing else:\n\n    printf '%s\\\\n' '{SENTINEL}'\n"

# What runs *inside* the image: `solver/` is there, `scripts/` is not, so the probe's own body
# travels over stdin. It builds the invocation the way an Attempt does and reads the stream the way
# the Run loop does, so a pass here is a pass for the thing that competes.
INSIDE = f"""
import datetime as dt, json, sys, tempfile
from pathlib import Path
from solver.codex import ADAPTER, CLAIM, COMMAND, Credential, Invocation, run_attempt
from solver.record import Recorder
from solver.redaction import Redactor
from solver.stall import Deadline

model, seconds = sys.argv[1], float(sys.argv[2])
with tempfile.TemporaryDirectory() as workdir:
    taken = list(run_attempt(
        {ASK!r},
        Path(workdir),
        Deadline(budget=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)),
        recorder=Recorder(Path("/state"), "codex-probe", Redactor.for_declared_secrets({{}})),
        attempt_id="codex-probe",
        chain=(Credential(slot="codex-subscription", model=model),),
        invocation=Invocation(network=False),
    ))
ran = [one for one in taken if one.kind == COMMAND and one.tool != ADAPTER and {SENTINEL!r} in one.shown]
print(json.dumps({{
    "ran": bool(ran),
    "sandbox": Invocation().sandbox,
    "commands": [one.command for one in taken if one.kind == COMMAND and one.tool != ADAPTER],
    "told": [one.shown.replace(chr(10), " ")[:300] for one in taken if not ran or one.kind != CLAIM][:6],
}}))
"""


def offered(home: Path) -> list[str]:
    """Every model id this login was offered, out of the CLI's own cache — the other half of the
    answer when the configured one is refused."""
    try:
        catalogue = json.loads((home / "models_cache.json").read_text())
    except (OSError, ValueError):
        return []
    models = catalogue.get("models") if isinstance(catalogue, dict) else None
    return [str(one["slug"]) for one in (models or []) if isinstance(one, dict) and one.get("slug")]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prove the image can run a command through its model.")
    parser.add_argument("--image", default="solver", help="the built image to ask")
    parser.add_argument("--model", default="", help="the id to ask; defaults to CODEX_MODEL, then the image's own")
    parser.add_argument("--seconds", type=float, default=240.0)
    parser.add_argument("--state", default=str(REPO_ROOT / "state"), help="the host mount holding the login")
    args = parser.parse_args(argv)

    ctfd_probe.load_env(REPO_ROOT / ".env")
    state = Path(args.state)
    home = state / "codex"
    if not (home / "auth.json").is_file():
        print(f"  FAIL  nothing is logged in — there is no {home / 'auth.json'}.")
        print("        Run `codex login --device-auth` in the container before the Run (ADR-0011).")
        return 1

    model = args.model or os.environ.get("CODEX_MODEL", "").strip() or _default_of(args.image)
    print(f"image       {args.image}")
    print(f"offered     {', '.join(offered(home)) or 'unknown — no cached catalogue yet'}")
    print(f"asking      {model}", flush=True)

    told = _inside(args.image, state, model, args.seconds)
    if told is None:
        return 1
    print(f"sandbox     {told['sandbox']}")
    if told["ran"]:
        print(f"  PASS  a command ran and its output came back: {told['commands']}")
        return 0
    print("  FAIL  no command ran. What the invocation said:")
    for line in told["told"]:
        print(f"          {line}")
    return 1


def _default_of(image: str) -> str:
    """The image's own default, asked of the image rather than imported from the checkout — the two
    trees can differ, and the one that matters is the one that competes."""
    asked = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python3",
            image,
            "-c",
            "from solver.boot import DEFAULT_MODEL; print(DEFAULT_MODEL)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return asked.stdout.strip() or "unknown"


def _inside(image: str, state: Path, model: str, seconds: float) -> dict | None:
    asked = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{state}:/state",
            "--entrypoint",
            "python3",
            image,
            "-",
            model,
            str(seconds),
        ],
        input=INSIDE,
        capture_output=True,
        text=True,
        check=False,
    )
    for line in reversed(asked.stdout.splitlines()):
        try:
            return json.loads(line)
        except ValueError:
            continue
    print(f"  FAIL  the probe did not run inside {image} — {(asked.stderr or asked.stdout).strip()[:400]}")
    return None


if __name__ == "__main__":
    sys.exit(main())
