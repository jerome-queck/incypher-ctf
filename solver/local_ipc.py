"""Bounded framing shared by local broker control channels."""

from __future__ import annotations

import socket

MAX_LINE_BYTES = 65536


def receive_line(channel: socket.socket, *, failure: str) -> str:
    held = bytearray()
    while len(held) <= MAX_LINE_BYTES:
        chunk = channel.recv(min(4096, MAX_LINE_BYTES + 1 - len(held)))
        if not chunk:
            break
        held.extend(chunk)
        if held.endswith(b"\n"):
            break
    if not held.endswith(b"\n") or len(held) > MAX_LINE_BYTES:
        raise ValueError(failure)
    return held[:-1].decode()


def receive_exact(channel: socket.socket, length: int, *, failure: str) -> bytearray:
    held = bytearray()
    while len(held) < length:
        chunk = channel.recv(length - len(held))
        if not chunk:
            raise ValueError(failure)
        held.extend(chunk)
    return held


__all__ = ["receive_exact", "receive_line"]
