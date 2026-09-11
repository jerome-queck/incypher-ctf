"""Durable staging and atomic directory publication for evidence capsules."""

from __future__ import annotations

import fcntl
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from solver.evidence_capsule_authority import reservation_identity
from solver.evidence_capsule_build import PreparedCapsule
from solver.evidence_capsule_contracts import (
    CAPSULE_KIND,
    CAPSULE_MANIFEST,
    CAPSULE_SCHEMA_VERSION,
    PUBLICATION_DOMAIN,
)
from solver.evidence_capsule_reader import verify_evidence_content
from solver.evidence_capsule_scan import HostSanitizationAuthority
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes, fsync_directory
from solver.write_reservation import WriteReservation


@dataclass(frozen=True)
class Publication:
    capsule_id: str
    reservation: dict[str, Any]
    publication_basis: dict[str, Any]
    final: Path
    state_directory: Path


@dataclass(frozen=True)
class StagedPublication:
    root: Path
    capsule: Path
    parent: Path


def publication_for(prepared: PreparedCapsule, reservation: WriteReservation, runs_directory: Path) -> Publication:
    identity = reservation_identity(reservation)
    basis = {
        "domain": PUBLICATION_DOMAIN,
        "content_ref": prepared.content_ref,
        "candidate_manifest_digest": prepared.candidate_digest,
        "candidate_identity": prepared.candidate_manifest["candidate"]["identity"],
        "reservation": identity,
    }
    capsule_id = digest_bytes(canonical_bytes(basis))
    return Publication(capsule_id, identity, basis, runs_directory / "capsules" / capsule_id, runs_directory.parent)


def ensure_directory_durable(path: Path) -> None:
    """Create each missing directory and durably publish its parent entry."""

    missing = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        directory.mkdir(exist_ok=True)
        fsync_directory(directory.parent)


def stage_publication(
    prepared: PreparedCapsule,
    publication: Publication,
    *,
    manifest_row_id: str,
    receipt_ref: str,
    scan_authority: HostSanitizationAuthority,
) -> StagedPublication:
    stage_parent = publication.state_directory / ".evidence-capsule-staging"
    ensure_directory_durable(stage_parent)
    stage_root = Path(tempfile.mkdtemp(prefix="capsule-", dir=stage_parent))
    fsync_directory(stage_parent)
    staged = stage_root / publication.capsule_id
    try:
        ensure_directory_durable(staged / "source")
        ensure_directory_durable(staged / "blobs")
        atomic_write(staged / "receipt.json", prepared.receipt_body)
        atomic_write(staged / "candidate-manifest.json", prepared.candidate_body)
        atomic_write(staged / "source" / "events.jsonl", prepared.events_body)
        for item, body in prepared.blobs:
            atomic_write(staged / "blobs" / item.digest, body)
        files = {
            item.relative_to(staged).as_posix(): digest_bytes(item.read_bytes())
            for item in staged.rglob("*")
            if item.is_file()
        }
        capsule = {
            "schema_version": CAPSULE_SCHEMA_VERSION,
            "kind": CAPSULE_KIND,
            "capsule_id": publication.capsule_id,
            "content_ref": prepared.content_ref,
            "content_basis": prepared.content_basis,
            "candidate_manifest_digest": prepared.candidate_digest,
            "candidate_identity": prepared.candidate_manifest["candidate"]["identity"],
            "manifest_link": {
                "row_id": manifest_row_id,
                "receipt_ref": receipt_ref,
                "receipt_digest": prepared.receipt_digest,
            },
            "reservation": publication.reservation,
            "publication_basis": publication.publication_basis,
            "files": dict(sorted(files.items())),
        }
        capsule_body = canonical_bytes(capsule)
        scan_authority.verify(capsule_body, label="capsule manifest", structured=True)
        atomic_write(staged / CAPSULE_MANIFEST, capsule_body)
        fsync_directory(staged / "source")
        fsync_directory(staged / "blobs")
        fsync_directory(staged)
        verify_evidence_content(staged)
        return StagedPublication(stage_root, staged, stage_parent)
    except BaseException:
        shutil.rmtree(stage_root, ignore_errors=True)
        fsync_directory(stage_parent)
        raise


def atomic_publish(
    staged: StagedPublication,
    publication: Publication,
    *,
    validate: Callable[[], None],
    admit: Callable[[], WriteReservation],
    hook: Callable[[str], None] | None,
) -> WriteReservation:
    lock_path = publication.state_directory / ".evidence-capsule.lock"
    new_lock = not lock_path.exists()
    with lock_path.open("a+b") as lock:
        if new_lock:
            lock.flush()
            os.fsync(lock.fileno())
            fsync_directory(lock_path.parent)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        validate()
        if hook:
            hook("before_rename")
        validate()
        reservation = admit()
        ensure_directory_durable(publication.final.parent)
        if publication.final.exists():
            verify_evidence_content(publication.final)
        else:
            os.rename(staged.capsule, publication.final)
            fsync_directory(publication.final.parent)
        if hook:
            hook("after_rename")
        return reservation


def discard_staging(staged: StagedPublication) -> None:
    shutil.rmtree(staged.root, ignore_errors=True)
    fsync_directory(staged.parent)
