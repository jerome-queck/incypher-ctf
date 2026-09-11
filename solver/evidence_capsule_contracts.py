"""Versioned public contracts for transactional evidence capsules."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solver.manifest_contracts import ReleaseCandidateManifestDraft

CAPSULE_SCHEMA_VERSION = 1
CAPSULE_KIND = "evidence-capsule"
CAPSULE_MANIFEST = "capsule.json"
CONTENT_DOMAIN = "incypher.evidence-capsule.content.v1"
PUBLICATION_DOMAIN = "incypher.evidence-capsule.publication.v1"
PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN = "publication_present_authority_uncertain"


class CapsuleRefused(RuntimeError):
    """Promotion stopped before publishing unverified or sensitive evidence."""

    def __init__(self, message: str, *, classification: str = "promotion_refused") -> None:
        super().__init__(message)
        self.classification = classification


class CapsuleInvalid(ValueError):
    """A promoted capsule cannot be independently verified."""


@dataclass(frozen=True)
class BlobSelection:
    """One causal blob derived by a registered receipt contract."""

    digest: str
    media_type: str = "application/octet-stream"
    structured: bool = False


@dataclass(frozen=True)
class SourceSnapshot:
    run_id: str
    events: tuple[Mapping[str, Any], ...]
    chain_head: str
    referenced_blobs: frozenset[str]


ReceiptValidator = Callable[[Mapping[str, Any], SourceSnapshot], Sequence[BlobSelection]]


@dataclass(frozen=True)
class ReceiptContract:
    kind: str
    schema_version: int
    producer: str
    validate: ReceiptValidator


class ReceiptRegistry:
    """Closed receipt-contract registry; callers cannot choose arbitrary blobs."""

    def __init__(self, contracts: Sequence[ReceiptContract] = ()) -> None:
        self._contracts = {(item.kind, item.schema_version, item.producer): item for item in contracts}
        if len(self._contracts) != len(contracts):
            raise ValueError("receipt registry contains a duplicate contract")

    def contract(self, kind: str, schema_version: int, producer: str) -> ReceiptContract:
        try:
            return self._contracts[(kind, schema_version, producer)]
        except KeyError:
            raise CapsuleRefused("receipt kind, schema version, and producer are not registered") from None


@dataclass(frozen=True)
class SanitizationPolicy:
    """Exact historical values and host paths which publication must not carry."""

    secrets: tuple[tuple[str, str], ...]
    forbidden_paths: tuple[str, ...]
    version: int = 1


@dataclass(frozen=True)
class CapsuleRequest:
    receipt: bytes
    receipt_ref: str


@dataclass(frozen=True)
class PromotedCapsule:
    capsule_id: str
    content_ref: str
    path: Path
    candidate_manifest: ReleaseCandidateManifestDraft


@dataclass(frozen=True)
class ReadEvidence:
    schema_version: int
    kind: str
    path: Path
    capsule_id: str | None = None
    manifest: Mapping[str, Any] | None = None
    raw: bytes | None = None
