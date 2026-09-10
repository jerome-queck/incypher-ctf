"""Small durability primitives shared by canonical storage and proof writing."""

from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


def digest_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def append_durable(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as output:
        output.write(body)
        output.flush()
        os.fsync(output.fileno())
    fsync_directory(path.parent)


def atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(body)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise


def fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
