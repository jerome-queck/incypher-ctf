"""Small public contracts shared by bootstrap broker owners."""

from __future__ import annotations

import enum
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from solver.event_store_storage import canonical_bytes


class Broker(str, enum.Enum):
    BOARD = "board"
    CODEX = "codex"
    CPA = "cpa"


@dataclass(frozen=True)
class BrokerReceipt:
    owner: Broker
    secret_names: tuple[str, ...]
    transfer_digest: str
    pid: int
    uid: int
    identity_digest: str


def transfer_digest(owner: Broker, nonce: bytes, secrets: Mapping[str, bytes | bytearray]) -> str:
    header = {
        "owner": Broker(owner).value,
        "nonce": nonce.hex(),
        "secrets": [{"name": name, "bytes": len(secrets[name])} for name in sorted(secrets)],
    }
    digest = hashlib.sha256(b"incypher-broker-transfer\0" + canonical_bytes(header))
    for name in sorted(secrets):
        digest.update(b"\0" + name.encode() + b"\0")
        digest.update(memoryview(secrets[name]))
    return digest.hexdigest()


__all__ = ["Broker", "BrokerReceipt", "transfer_digest"]
