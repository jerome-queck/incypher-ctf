import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
ASSEMBLER = REPO_ROOT / "scripts" / "assemble_tool_supply.py"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def test_the_repository_fragment_assembles_once_and_is_copied_into_the_image(tmp_path: Path):
    output = tmp_path / "generated"
    result = subprocess.run(
        [
            sys.executable,
            str(ASSEMBLER),
            "--source",
            str(REPO_ROOT / "tool-supply"),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    inventory = json.loads((output / "inventory.json").read_text())
    components = inventory["components"]
    assert [component["component_id"] for component in components].count("fixture.identity") == 1
    assert (output / "rootfs" / "opt" / "solver" / "tool-supply" / "identity.txt").read_bytes() == (
        b"tool-supply-probe\n"
    )
    assert (output / "apt-packages.txt").read_text() == ""
    assert (output / "receipt.json").is_file()

    dockerfile = DOCKERFILE.read_text()
    copy_lines = [line.strip() for line in dockerfile.splitlines() if line.strip().startswith("COPY ")]
    assert copy_lines.count("COPY tool-supply/generated/rootfs/ /") == 1
    assert copy_lines.count("COPY tool-supply/generated/apt-packages.txt /tmp/tool-supply-apt-packages.txt") == 1
    assert (
        copy_lines.count(
            "COPY tool-supply/generated/inventory.json tool-supply/generated/receipt.json /opt/solver/tool-supply/"
        )
        == 1
    )
    install_position = dockerfile.index("xargs apt-get install --yes --no-install-recommends")
    assert install_position < dockerfile.index("COPY tool-supply/generated/rootfs/ /")
    assert install_position < dockerfile.index(
        "COPY tool-supply/generated/inventory.json tool-supply/generated/receipt.json"
    )

    committed = REPO_ROOT / "tool-supply" / "generated"
    assembled_files = {
        path.relative_to(output): (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in output.rglob("*")
        if path.is_file()
    }
    committed_files = {
        path.relative_to(committed): (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in committed.rglob("*")
        if path.is_file()
    }
    assert assembled_files == committed_files
