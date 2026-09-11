"""Derive and sanitize one complete evidence capsule before write admission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from solver.evidence_capsule_contracts import (
    CONTENT_DOMAIN,
    BlobSelection,
    CapsuleRefused,
    CapsuleRequest,
    ReceiptRegistry,
)
from solver.evidence_capsule_scan import HostSanitizationAuthority
from solver.evidence_capsule_source import snapshot_terminal_run
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.manifest import attach_capsule_receipt, canonical_manifest_bytes, manifest_digest, parse_manifest
from solver.manifest_contracts import ReleaseCandidateManifestDraft
from solver.strict_json import StrictJSONError, strict_json_object
from solver.write_reservation import Capacity, EffectIdentity


@dataclass(frozen=True)
class PreparedCapsule:
    content_ref: str
    content_basis: dict[str, Any]
    receipt_body: bytes
    receipt_digest: str
    candidate_manifest: ReleaseCandidateManifestDraft
    candidate_body: bytes
    candidate_digest: str
    events_body: bytes
    blobs: tuple[tuple[BlobSelection, bytes], ...]

    @property
    def effect(self) -> EffectIdentity:
        payload = digest_bytes(
            canonical_bytes({"content_ref": self.content_ref, "candidate_manifest_digest": self.candidate_digest})
        )
        return EffectIdentity("evidence-capsule.promote", self.content_ref, payload)

    @property
    def capacity(self) -> Capacity:
        file_count = 4 + len(self.blobs)
        object_count = file_count + 7
        staged_bytes = (
            len(self.receipt_body)
            + len(self.candidate_body)
            + len(self.events_body)
            + sum(len(body) for _, body in self.blobs)
        )
        reserved_bytes = staged_bytes + len(canonical_bytes(self.content_basis)) + 8_192 + object_count * 128
        return Capacity(
            bytes=reserved_bytes,
            objects=object_count,
            operations=3,
            create=object_count,
            rename=file_count + 1,
            unlink=object_count,
            durability=file_count * 2 + 16,
        )


def _strict_receipt(body: bytes) -> dict[str, Any]:
    try:
        return strict_json_object(body, label="receipt")
    except StrictJSONError as error:
        raise CapsuleRefused(str(error)) from None


def prepare_capsule(
    *,
    store: EventStore,
    candidate_manifest: ReleaseCandidateManifestDraft,
    manifest_row_id: str,
    registry: ReceiptRegistry,
    scan_authority: HostSanitizationAuthority,
    request: CapsuleRequest,
) -> PreparedCapsule:
    source = snapshot_terminal_run(store)
    scan_authority.bind_source(source.run_id, source.chain_head)
    base_manifest = parse_manifest(candidate_manifest)
    scan_authority.verify(request.receipt, label="receipt", structured=True)
    receipt = _strict_receipt(request.receipt)
    schema_version, kind, producer = receipt.get("schema_version"), receipt.get("kind"), receipt.get("producer")
    if not isinstance(schema_version, int) or not isinstance(kind, str) or not isinstance(producer, str):
        raise CapsuleRefused("receipt lacks typed schema version, kind, or producer")
    selected = tuple(registry.contract(kind, schema_version, producer).validate(receipt, source))
    if len({item.digest for item in selected}) != len(selected):
        raise CapsuleRefused("receipt contract selected a blob more than once")
    if any(item.digest not in source.referenced_blobs for item in selected):
        raise CapsuleRefused("receipt contract selected a blob outside its causal source")
    blobs = []
    for item in sorted(selected, key=lambda value: value.digest):
        body = store.blob(item.digest)
        if digest_bytes(body) != item.digest:
            raise CapsuleRefused("selected blob digest differs")
        scan_authority.verify(body, label=f"blob {item.digest}", structured=item.structured)
        blobs.append((item, body))

    receipt_body = canonical_bytes(receipt)
    receipt_digest = digest_bytes(receipt_body)
    content_basis = {
        "domain": CONTENT_DOMAIN,
        "candidate": {
            "image_digest": base_manifest["candidate"]["image_digest"],
            "profile_digest": base_manifest["selected_profile"]["profile_digest"],
        },
        "receipt": {
            "ref": request.receipt_ref,
            "kind": kind,
            "schema_version": schema_version,
            "producer": producer,
            "digest": receipt_digest,
        },
        "source": {
            "run_id": source.run_id,
            "first_sequence": 1,
            "last_sequence": len(source.events),
            "event_count": len(source.events),
            "chain_head": source.chain_head,
        },
        "blobs": [
            {
                "digest": item.digest,
                "bytes": len(body),
                "media_type": item.media_type,
                "structured": item.structured,
            }
            for item, body in blobs
        ],
        "excluded_source_blobs": [
            {"digest": blob_digest, "classification": "canonical-private-body"}
            for blob_digest in sorted(source.referenced_blobs - {item.digest for item in selected})
        ],
        "scan_policy": {
            "kind": "exact-values-and-host-paths",
            "version": scan_authority.version,
            "digest": scan_authority.digest,
            "vault": scan_authority.identity,
        },
    }
    content_ref = f"capsule-content:{digest_bytes(canonical_bytes(content_basis))}"
    descriptor = {"ref": request.receipt_ref, "kind": kind, "digest": receipt_digest}
    linked = attach_capsule_receipt(base_manifest, manifest_row_id, descriptor, content_ref)
    candidate_body = canonical_manifest_bytes(linked)
    event_rows = [canonical_bytes(event) for event in source.events]
    scan_authority.verify(receipt_body, label="canonical receipt", structured=True)
    scan_authority.verify(candidate_body, label="candidate manifest", structured=True)
    for index, row in enumerate(event_rows, start=1):
        scan_authority.verify(row, label=f"source event {index}", structured=True)
    return PreparedCapsule(
        content_ref=content_ref,
        content_basis=content_basis,
        receipt_body=receipt_body,
        receipt_digest=receipt_digest,
        candidate_manifest=linked,
        candidate_body=candidate_body,
        candidate_digest=manifest_digest(linked),
        events_body=b"".join(row + b"\n" for row in event_rows),
        blobs=tuple(blobs),
    )
