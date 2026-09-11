"""Trusted controller for transactional evidence-capsule publication."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from solver.evidence_capsule_build import prepare_capsule
from solver.evidence_capsule_contracts import (
    CapsuleInvalid,
    CapsuleRefused,
    CapsuleRequest,
    PromotedCapsule,
    ReceiptRegistry,
)
from solver.evidence_capsule_reader import read_evidence
from solver.evidence_capsule_scan import HostSanitizationAuthority
from solver.evidence_capsule_storage import (
    atomic_publish,
    discard_staging,
    publication_for,
    stage_publication,
)
from solver.event_store import EventStore
from solver.manifest_contracts import ReleaseCandidateManifestDraft
from solver.write_reservation import Pool, ReservationState, RetentionPolicy, WriteAuthority


class EvidenceCapsulePromoter:
    """System-composed source, scan, registry, storage, and authority boundary."""

    def __init__(
        self,
        *,
        store: EventStore,
        candidate_manifest: ReleaseCandidateManifestDraft,
        manifest_row_id: str,
        registry: ReceiptRegistry,
        scan_authority: HostSanitizationAuthority,
        write_authority: WriteAuthority,
        runs_directory: Path,
        hook: Callable[[str], None] | None = None,
    ) -> None:
        self._store = store
        self._candidate_manifest = candidate_manifest
        self._manifest_row_id = manifest_row_id
        self._registry = registry
        self._scan_authority = scan_authority
        self._write_authority = write_authority
        self._runs_directory = runs_directory
        self._hook = hook

    def promote(self, request: CapsuleRequest) -> PromotedCapsule:
        """Publish once after trusted derivation, or Refuse without a weaker fallback."""

        prepared = prepare_capsule(
            store=self._store,
            candidate_manifest=self._candidate_manifest,
            manifest_row_id=self._manifest_row_id,
            registry=self._registry,
            scan_authority=self._scan_authority,
            request=request,
        )
        reservation = self._write_authority.reserve(
            f"evidence-capsule:{prepared.content_ref}",
            prepared.effect,
            prepared.capacity,
            pool=Pool.SHARED,
            retention=RetentionPolicy.RECORD,
        )
        publication = publication_for(prepared, reservation, self._runs_directory)
        if publication.final.exists():
            read_evidence(publication.final, self._write_authority)
            return PromotedCapsule(
                publication.capsule_id,
                prepared.content_ref,
                publication.final,
                prepared.candidate_manifest,
            )
        if reservation.state is not ReservationState.RESERVED:
            raise CapsuleRefused(f"capsule authority is {reservation.state.value} without a published capsule")

        staged = None
        try:
            staged = stage_publication(
                prepared,
                publication,
                manifest_row_id=self._manifest_row_id,
                receipt_ref=request.receipt_ref,
                scan_authority=self._scan_authority,
            )
            if self._hook:
                self._hook("after_stage")
            reservation = atomic_publish(
                staged,
                publication,
                validate=self._scan_authority.assert_fresh,
                admit=lambda: self._write_authority.start(reservation),
                hook=self._hook,
            )
            if self._hook:
                self._hook("before_authority_commit")
            self._write_authority.commit(
                reservation,
                {"capsule_id": publication.capsule_id, "content_ref": prepared.content_ref},
            )
            if self._hook:
                self._hook("after_authority_commit")
            read_evidence(publication.final, self._write_authority)
            return PromotedCapsule(
                publication.capsule_id,
                prepared.content_ref,
                publication.final,
                prepared.candidate_manifest,
            )
        except BaseException as error:
            self._record_interruption(reservation.key, error)
            raise
        finally:
            if staged is not None:
                discard_staging(staged)

    def _record_interruption(self, key: str, error: BaseException) -> None:
        current = self._write_authority.current(key)
        reason = type(error).__name__
        if current is not None and current.state is ReservationState.RESERVED:
            self._write_authority.abort(current, f"capsule-stage-refused:{reason}")
        elif current is not None and current.state is ReservationState.STARTED:
            self._write_authority.possibly_sent(current, f"capsule-promotion-interrupted:{reason}")


__all__ = [
    "CapsuleInvalid",
    "CapsuleRefused",
    "CapsuleRequest",
    "EvidenceCapsulePromoter",
    "PromotedCapsule",
]
