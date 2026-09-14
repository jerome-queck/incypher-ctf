"""Read CTFd's authenticated ``window.init`` identity marker."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

_WINDOW_INIT = re.compile(rb"window\.init\s*=\s*(.*?)</script", re.DOTALL | re.IGNORECASE)
_MISSING = object()


def window_init_scalars(body: bytes, names: Iterable[str]) -> dict[str, Any] | None:
    """Return selected scalar fields, or ``None`` when the marker is absent."""

    match = _WINDOW_INIT.search(body)
    if match is None:
        return None
    marker = {name: _javascript_scalar(match.group(1), name) for name in names}
    return {name: value for name, value in marker.items() if value is not _MISSING}


def _javascript_scalar(block: bytes, name: str):
    key = re.escape(name.encode())
    found = re.search(rb"(?:['\"]?" + key + rb"['\"]?)\s*:\s*(null|-?\d+|'[^']*'|\"[^\"]*\")", block)
    if found is None:
        return _MISSING
    raw = found.group(1)
    if raw == b"null":
        return None
    if raw[:1] in (b"'", b'"'):
        return raw[1:-1].decode("utf-8", "strict")
    return int(raw)
