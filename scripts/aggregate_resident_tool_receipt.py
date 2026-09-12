"""Publish the independently verifiable resident Tool receipt."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from solver.resident_tool_receipt import write_receipt  # noqa: E402


if __name__ == "__main__":
    print(write_receipt(ROOT / "tool-supply"))
