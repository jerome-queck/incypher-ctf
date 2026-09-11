"""The board setup wizard stages env edits before its final atomic cutover."""

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "scripts" / "setup-board.sh"
STAGES = "# STAGES — point the Solver at a CTFd board and prove it can reach it."


def test_legacy_overlay_refuses_before_the_first_prompt_and_preserves_active_env(tmp_path):
    active = tmp_path / ".env"
    old_bytes = b"BOARD=old\n"
    active.write_bytes(old_bytes)
    (tmp_path / ".env.incypher").write_text("BOARD=legacy-secret\n")

    environment = os.environ.copy()
    environment["ENV_FILE"] = str(active)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=ROOT,
        env=environment,
        input="",
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )

    assert result.returncode != 0
    assert "Ready to start?" not in result.stdout
    assert active.read_bytes() == old_bytes
    assert "legacy-secret" not in result.stdout + result.stderr


def test_staging_and_atomic_activation_are_wired_below_the_generated_library():
    source = SCRIPT.read_text()
    stages = source.index(STAGES)
    banner = source.index('banner "Point the Solver at a CTF board"', stages)
    staging = source.index("STAGED_ENV_FILE", stages)
    activation = source.index("activate_staged_env() {", stages)
    atomic = source.index("env_file.atomic_replace", activation)
    first_write = source.index("write_env CTFD_URL", stages)
    restore = source.index('ENV_FILE="$ACTIVE_ENV_FILE"', activation)
    activation_call = source.rindex("\nactivate_staged_env\n")
    finish_call = source.rindex("\nfinish\n")

    assert staging < banner
    assert "assert_unambiguous" in source[stages:banner]
    assert 'sys.path.insert(0, "scripts")' in source[stages:banner]
    assert atomic < restore
    assert staging < first_write
    assert activation < first_write
    assert source.count("finish() {") == 1
    assert activation_call < finish_call
    assert source[:stages].count("STAGED_ENV_FILE") == 0
    assert ".setup-env-stage-" in source
    assert ".env-stage-" not in source
