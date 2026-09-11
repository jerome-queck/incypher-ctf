"""Reserve, stage, sanitize, verify, and atomically promote evidence capsules."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from solver.evidence_capsule_contracts import (
    CAPSULE_KIND,
    CAPSULE_MANIFEST,
    CAPSULE_SCHEMA_VERSION,
    CONTENT_DOMAIN,
    PUBLICATION_DOMAIN,
    BlobSelection,
    CapsuleInvalid,
    CapsuleRefused,
    CapsuleRequest,
    PromotedCapsule,
)
from solver.evidence_capsule_reader import read_promoted_evidence, verify_evidence_content
from solver.evidence_capsule_scan import policy_digest, verify_sanitized
from solver.evidence_capsule_source import snapshot_terminal_run
from solver.event_store_storage import atomic_write, canonical_bytes, fsync_directory
from solver.manifest import (
    attach_capsule_receipt,
    canonical_manifest_bytes,
    manifest_digest,
    parse_manifest,
)
from solver.write_reservation import Capacity, EffectIdentity, Pool, ReservationState, RetentionPolicy


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _strict_json(body: bytes) -> dict[str, Any]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CapsuleRefused(f"receipt contains duplicate JSON key {key!r}")
            result[key] = value
        return result

    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapsuleRefused(f"receipt is not valid UTF-8 JSON: {error}") from None
    if not isinstance(value, dict):
        raise CapsuleRefused("receipt is not a JSON object")
    return value


def _write(path: Path, body: bytes) -> None:
    atomic_write(path, body)


def _reservation_identity(reservation) -> dict[str, Any]:
    return {
        "key": reservation.key,
        "effect_fingerprint": reservation.effect_fingerprint,
        "boot_id": reservation.boot_id,
        "object_slots": list(reservation.object_slots),
        "pool": reservation.pool.value,
        "need": reservation.need.as_dict(),
        "retention": reservation.retention.value,
    }


def promote_capsule(request: CapsuleRequest) -> PromotedCapsule:
    """Publish one immutable capsule, or Refuse before any evidence lands in ``runs``."""

    source = snapshot_terminal_run(request.store)
    base_manifest = parse_manifest(request.candidate_manifest)
    verify_sanitized(request.receipt, request.sanitization, label="receipt", structured=True)
    receipt = _strict_json(request.receipt)
    schema_version = receipt.get("schema_version")
    kind = receipt.get("kind")
    producer = receipt.get("producer")
    if not isinstance(schema_version, int) or not isinstance(kind, str) or not isinstance(producer, str):
        raise CapsuleRefused("receipt lacks typed schema version, kind, or producer")
    contract = request.registry.contract(kind, schema_version, producer)
    selected = tuple(contract.validate(receipt, source))
    if len({item.digest for item in selected}) != len(selected):
        raise CapsuleRefused("receipt contract selected a blob more than once")
    if any(item.digest not in source.referenced_blobs for item in selected):
        raise CapsuleRefused("receipt contract selected a blob outside its causal source")
    blobs: list[tuple[BlobSelection, bytes]] = []
    for item in sorted(selected, key=lambda value: value.digest):
        body = request.store.blob(item.digest)
        if _digest(body) != item.digest:
            raise CapsuleRefused("selected blob digest differs")
        verify_sanitized(body, request.sanitization, label=f"blob {item.digest}", structured=item.structured)
        blobs.append((item, body))

    receipt_body = canonical_bytes(receipt)
    receipt_digest = _digest(receipt_body)
    blob_basis = [
        {
            "digest": item.digest,
            "bytes": len(body),
            "media_type": item.media_type,
            "structured": item.structured,
        }
        for item, body in blobs
    ]
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
        "blobs": blob_basis,
        "excluded_source_blobs": [
            {"digest": digest, "classification": "canonical-private-body"}
            for digest in sorted(source.referenced_blobs - {item.digest for item in selected})
        ],
        "scan_policy": {
            "kind": "exact-values-and-host-paths",
            "version": request.sanitization.version,
            "digest": policy_digest(request.sanitization),
        },
    }
    content_ref = f"capsule-content:{_digest(canonical_bytes(content_basis))}"
    descriptor = {"ref": request.receipt_ref, "kind": kind, "digest": receipt_digest}
    linked = attach_capsule_receipt(base_manifest, request.manifest_row_id, descriptor, content_ref)
    candidate_body = canonical_manifest_bytes(linked)
    candidate_digest = manifest_digest(linked)
    event_rows = [canonical_bytes(event) for event in source.events]
    events_body = b"".join(row + b"\n" for row in event_rows)
    verify_sanitized(receipt_body, request.sanitization, label="canonical receipt", structured=True)
    verify_sanitized(candidate_body, request.sanitization, label="candidate manifest", structured=True)
    for index, row in enumerate(event_rows, start=1):
        verify_sanitized(row, request.sanitization, label=f"source event {index}", structured=True)
    staged_bytes = len(receipt_body) + len(candidate_body) + len(events_body) + sum(len(body) for _, body in blobs)
    effect = EffectIdentity(
        "evidence-capsule.promote",
        content_ref,
        _digest(canonical_bytes({"content_ref": content_ref, "candidate_manifest_digest": candidate_digest})),
    )
    file_count = 4 + len(blobs)
    object_count = file_count + 7
    reserved_bytes = staged_bytes + len(canonical_bytes(content_basis)) + 8_192 + object_count * 128
    reservation = request.authority.reserve(
        f"evidence-capsule:{content_ref}",
        effect,
        Capacity(
            bytes=reserved_bytes,
            objects=object_count,
            operations=3,
            create=object_count,
            rename=file_count + 1,
            unlink=object_count,
            durability=file_count * 2 + 5,
        ),
        pool=Pool.SHARED,
        retention=RetentionPolicy.RECORD,
    )
    reservation_identity = _reservation_identity(reservation)
    publication_basis = {
        "domain": PUBLICATION_DOMAIN,
        "content_ref": content_ref,
        "candidate_manifest_digest": candidate_digest,
        "candidate_identity": linked["candidate"]["identity"],
        "reservation": reservation_identity,
    }
    capsule_id = _digest(canonical_bytes(publication_basis))
    final = request.runs_directory / "capsules" / capsule_id
    if final.exists():
        read_promoted_evidence(final, request.authority)
        return PromotedCapsule(capsule_id, content_ref, final, linked)
    if reservation.state is not ReservationState.RESERVED:
        raise CapsuleRefused(f"capsule authority is {reservation.state.value} without a published capsule")

    stage_parent = request.runs_directory.parent / ".evidence-capsule-staging"
    stage_parent.mkdir(parents=True, exist_ok=True)
    stage_root = Path(tempfile.mkdtemp(prefix="capsule-", dir=stage_parent))
    staged = stage_root / capsule_id
    started = False
    try:
        (staged / "source").mkdir(parents=True)
        (staged / "blobs").mkdir()
        _write(staged / "receipt.json", receipt_body)
        _write(staged / "candidate-manifest.json", candidate_body)
        _write(staged / "source" / "events.jsonl", events_body)
        for item, body in blobs:
            _write(staged / "blobs" / item.digest, body)
        files = {
            item.relative_to(staged).as_posix(): _digest(item.read_bytes())
            for item in staged.rglob("*")
            if item.is_file()
        }
        capsule_document = {
            "schema_version": CAPSULE_SCHEMA_VERSION,
            "kind": CAPSULE_KIND,
            "capsule_id": capsule_id,
            "content_ref": content_ref,
            "content_basis": content_basis,
            "candidate_manifest_digest": candidate_digest,
            "candidate_identity": linked["candidate"]["identity"],
            "manifest_link": {
                "row_id": request.manifest_row_id,
                "receipt_ref": request.receipt_ref,
                "receipt_digest": receipt_digest,
            },
            "reservation": reservation_identity,
            "publication_basis": publication_basis,
            "files": dict(sorted(files.items())),
        }
        capsule_body = canonical_bytes(capsule_document)
        verify_sanitized(capsule_body, request.sanitization, label="capsule manifest", structured=True)
        _write(staged / CAPSULE_MANIFEST, capsule_body)
        fsync_directory(staged / "source")
        fsync_directory(staged / "blobs")
        fsync_directory(staged)
        verify_evidence_content(staged)
        if request.hook:
            request.hook("after_stage")
        lock_path = request.runs_directory.parent / ".evidence-capsule.lock"
        with lock_path.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            reservation = request.authority.start(reservation)
            started = True
            if request.hook:
                request.hook("before_rename")
            (request.runs_directory / "capsules").mkdir(parents=True, exist_ok=True)
            if final.exists():
                verify_evidence_content(final)
            else:
                os.rename(staged, final)
                fsync_directory(final.parent)
            if request.hook:
                request.hook("after_rename")
        request.authority.commit(reservation, {"capsule_id": capsule_id, "content_ref": content_ref})
        read_promoted_evidence(final, request.authority)
        return PromotedCapsule(capsule_id, content_ref, final, linked)
    except BaseException as error:
        current = request.authority.current(reservation.key)
        if current is not None and current.state is ReservationState.RESERVED:
            request.authority.abort(current, f"capsule-stage-refused:{type(error).__name__}")
        elif started and current is not None and current.state is ReservationState.STARTED:
            request.authority.possibly_sent(current, f"capsule-promotion-interrupted:{type(error).__name__}")
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


__all__ = [
    "CapsuleInvalid",
    "CapsuleRefused",
    "CapsuleRequest",
    "PromotedCapsule",
    "promote_capsule",
]
