"""Public contracts for verified canonical replay."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

REPLAY_SCHEMA_VERSION = 1
PROJECTION_SCHEMA_VERSION = 1
PROJECTION_VERSION = "v1"
RECEIPT_TYPE = "verified-replay"
RECEIPT_FILENAME = "verified-replay.receipt.json"
PROJECTIONS_DIRECTORY = "projections"
PROJECTION_FILENAME = "observation-v1.jsonl"
MANIFEST_ROW_ID = "core.canonical-state-replay-restart"
MANIFEST_RECEIPT_REF = "receipt:verified-replay"


@dataclass(frozen=True)
class VersionedProjection:
    """An immutable serialized view rebuilt from verified canonical events."""

    version: str
    schema_version: int
    rows: tuple[Mapping[str, Any], ...]
    serialized: bytes
    digest: str


@dataclass(frozen=True)
class ReplayVerification:
    """Canonical state and its independently checkable replay receipt."""

    schema_version: int
    run_id: str
    event_count: int
    chain_head: str
    projection: VersionedProjection
    receipt: Mapping[str, Any]
    receipt_path: Path

    @property
    def projection_path(self) -> Path:
        return self.receipt_path.parent / PROJECTIONS_DIRECTORY / PROJECTION_FILENAME


def freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [thaw(item) for item in value]
    return value
