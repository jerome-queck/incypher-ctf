"""Persistent paths for qualification material kept until the work is merged."""

from __future__ import annotations

import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RETENTION_ROOT = REPO_ROOT / ".cache" / "qualification"


def new_retained_directory(kind: str, prefix: str) -> Path:
    """Create one uniquely named qualification directory that is never auto-removed."""

    root = RETENTION_ROOT / kind
    root.mkdir(parents=True, exist_ok=True)
    directory = root / f"{prefix}{uuid.uuid4().hex}"
    directory.mkdir()
    return directory


__all__ = ["RETENTION_ROOT", "new_retained_directory"]
