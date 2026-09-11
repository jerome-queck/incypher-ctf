#!/usr/bin/python3
"""Small real-input Tool fixture for the supply qualification boundary."""

import sys
from pathlib import Path

VERSION = "1.0.0"


def main(argv: list[str]) -> int:
    if argv == ["--version"]:
        print(VERSION)
        return 0
    if len(argv) != 1:
        return 2
    words = Path(argv[0]).read_text().split()
    print(" ".join(reversed(" ".join(words).upper().split())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
