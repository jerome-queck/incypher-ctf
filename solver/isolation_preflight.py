"""Standalone strict-Isolation preflight used by the pinned host launcher."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict

from solver.isolation import IsolationRefusal, strict_preflight


def main() -> int:
    try:
        receipt = strict_preflight(os.environ)
    except IsolationRefusal as refused:
        print(refused, file=sys.stderr)
        return 2
    print(json.dumps(asdict(receipt), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
