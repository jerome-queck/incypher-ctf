"""Build and prove brokered Research plus raw-egress denial in the strict image."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import runtime  # noqa: E402
import strict_runtime  # noqa: E402
from solver.research_broker_probe import RUN_ID  # noqa: E402
from solver.research_broker_receipt import verify_receipt  # noqa: E402


def qualify(state: Path) -> Path:
    if runtime.verify() != 0:
        raise RuntimeError("the pinned container runtime is unavailable")
    state = Path(state).resolve()
    if state.exists():
        raise ValueError(f"qualification state already exists: {state}")
    state.mkdir(parents=True)
    binding = strict_runtime.build_image(subprocess.run)
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--cgroup-parent",
                strict_runtime.CGROUP_PARENT,
                "--entrypoint",
                "/bin/true",
                binding.image_id,
            ],
            check=True,
        )
        subprocess.run(strict_runtime.research_broker_probe_command(binding, state), check=True)
    except Exception:
        shutil.rmtree(state, ignore_errors=True)
        raise
    finally:
        subprocess.run(
            ["colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE],
            check=True,
        )
    return verify_receipt(state / "runs" / RUN_ID / "canonical" / "research-broker.receipt.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state", type=Path)
    arguments = parser.parse_args(argv)
    try:
        receipt = qualify(arguments.state)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
