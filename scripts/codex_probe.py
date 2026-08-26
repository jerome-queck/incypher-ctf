"""Ask the credential we would spend a Run on whether it answers, before the Run spends it.

    python3 scripts/codex_probe.py [--model gpt-…] [--seconds 120]

The gap this closes is one no offline check can: **which model ids a subscription serves is account
state**, it moves, and a default nobody asked a live login about is a default that fails every
Attempt of an unattended Run with nobody there. Measured on 26 August 2026, `gpt-5` answers *"The
'gpt-5' model is not supported when using Codex"* and `gpt-5-codex` was refused before it — so this
is the pre-flight that would have caught a Solver shipping a brain it could not reach.

It spawns the CLI through `solver/codex.py` rather than building its own command line, so what is
proven is the argv the Solver actually uses — the sandbox, the reasoning effort, the web-search
dial. What it cannot prove is the *container's* copy of the CLI: `scripts/` is in no image
(`Dockerfile` copies `solver/` and the Board profiles), so this runs against the host's `codex` and
the same `CODEX_HOME`. The credential and the account are the thing under test, and those are shared.

Standard library only — it has to run beside every other pre-flight in here.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import ctfd_probe  # noqa: E402
from solver.boot import AUTH, DEFAULT_MODEL, SUBSCRIPTION  # noqa: E402
from solver.codex import CLAIM, CODEX_HOME, Credential, Invocation, run_attempt  # noqa: E402
from solver.record import Recorder  # noqa: E402
from solver.redaction import Redactor  # noqa: E402
from solver.stall import Deadline  # noqa: E402

# A question with one right answer that no tool can produce, so a reply proves the model reached us
# rather than that a shell did.
QUESTION = "Reply with exactly one word and nothing else: PONG"
ANSWER = "PONG"

RUN_ID = "codex-probe"

# Where the CLI caches what the login was offered. Read only to *list* ids when the configured one
# fails, because "that model is not served" and "here is what is" are two halves of one answer.
CATALOGUE = "models_cache.json"


def home_of(environ: Mapping[str, str]) -> Path:
    """The `CODEX_HOME` this probe asks — the Run's own mount by default, so what is proven is the
    login a Run will use rather than the operator's own."""
    return Path(environ.get("CODEX_HOME") or REPO_ROOT / "state" / CODEX_HOME.name)


def offered(home: Path) -> list[str]:
    """Every model id the login was offered, out of the CLI's own cache. Empty where there is no
    cache to read, which is a fact about this login and not a failure of the probe."""
    try:
        catalogue = json.loads((home / CATALOGUE).read_text())
    except (OSError, ValueError):
        return []
    models = catalogue.get("models") if isinstance(catalogue, dict) else None
    entries = models if isinstance(models, list) else []
    return [str(one.get("slug")) for one in entries if isinstance(one, dict) and one.get("slug")]


def answered(model: str, home: Path, seconds: float) -> tuple[bool, str]:
    """Spawn one invocation and say whether the model answered — and what it said if it did not.

    Read-only and off the network, because the question needs neither and a probe that could reach a
    Board is a probe that could spend a submission.
    """
    with tempfile.TemporaryDirectory() as empty:
        recorder = Recorder(REPO_ROOT / "state", RUN_ID, Redactor.for_declared_secrets(os.environ))
        deadline = Deadline(budget=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds))
        said = list(
            run_attempt(
                QUESTION,
                Path(empty),
                deadline,
                recorder=recorder,
                attempt_id=RUN_ID,
                chain=(Credential(slot=SUBSCRIPTION, model=model, home=home),),
                invocation=Invocation(sandbox="read-only", network=False),
            )
        )
    spoken = [taken.shown for taken in said if taken.kind == CLAIM]
    if any(ANSWER in one for one in spoken):
        return True, f"{model} answered"
    told = " | ".join(taken.shown.replace("\n", " ")[:200] for taken in said if ANSWER not in taken.shown)
    return False, told or f"{model} said nothing at all"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="", help=f"the id to ask; defaults to CODEX_MODEL or {DEFAULT_MODEL}")
    parser.add_argument("--seconds", type=float, default=120.0, help="how long to wait for a reply")
    args = parser.parse_args(argv)

    ctfd_probe.load_env(REPO_ROOT / ".env")
    home = home_of(os.environ)
    model = args.model or os.environ.get("CODEX_MODEL", "").strip() or DEFAULT_MODEL

    print(f"CODEX_HOME  {home}")
    if not (home / AUTH).is_file():
        print(f"  FAIL  nothing is logged in — there is no {home / AUTH}.", flush=True)
        print("        Run `codex login --device-auth` in the container before the Run (ADR-0011).")
        return 1

    catalogue = offered(home)
    print(f"offered     {', '.join(catalogue) if catalogue else 'unknown — no cached catalogue yet'}")
    print(f"asking      {model}", flush=True)

    ok, told = answered(model, home, args.seconds)
    if ok:
        print(f"  PASS  {told}")
        return 0
    print(f"  FAIL  {told}")
    if catalogue and model not in catalogue:
        print(f"        {model!r} is not in this login's catalogue. Set CODEX_MODEL to one that is.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
