import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
MODULE = "scripts.apply_tool_supply_modes"


def test_git_transport_modes_are_restored_from_the_validated_inventory(tmp_path: Path) -> None:
    root = tmp_path / "root"
    readonly = root / "opt" / "tool" / "notice"
    executable = root / "usr" / "local" / "bin" / "tool"
    readonly.parent.mkdir(parents=True)
    executable.parent.mkdir(parents=True)
    readonly.write_text("notice")
    executable.write_text("tool")
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "components": [
                    {
                        "files": [
                            {"destination": "/opt/tool/notice", "mode": "0444"},
                            {"destination": "/usr/local/bin/tool", "mode": "0755"},
                        ]
                    }
                ]
            }
        )
    )

    result = subprocess.run(
        [sys.executable, "-m", MODULE, "--inventory", str(inventory), "--root", str(root)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert readonly.stat().st_mode & 0o777 == 0o444
    assert executable.stat().st_mode & 0o777 == 0o755


def test_a_destination_outside_the_image_namespace_is_refused(tmp_path: Path) -> None:
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"components": [{"files": [{"destination": "relative", "mode": "0644"}]}]}))

    result = subprocess.run(
        [sys.executable, "-m", MODULE, "--inventory", str(inventory), "--root", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )

    assert result.returncode != 0
    assert "not absolute and contained" in result.stderr
