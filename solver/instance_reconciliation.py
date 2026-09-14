"""Deterministic Boot barrier joining Board rows, Leases, and Work generations."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.instance_lease_contracts import LeaseGrant, LeasePhase
from solver.instance_ledger import (
    ABSENT,
    EMPTY,
    NOT_OURS,
    UNSETTLED,
    LedgerResult,
    receipt_document as ledger_receipt_document,
)
from solver.instance_reconciliation_contracts import AdmissionVerdict, BootOwnership, ReconciliationResult
from solver.work_generation import GenerationState
from solver.write_reservation import (
    Capacity,
    EffectIdentity,
    ReservedEffect,
    RetentionPolicy,
    WriteAuthority,
)

RECONCILIATION_CAPACITY = Capacity(4096, 1, 8)


class InstanceReconciler:
    """Open admission only after one authenticated snapshot settles every local claim."""

    def __init__(
        self,
        authority: WriteAuthority,
        *,
        run_id: str,
        board_id: str,
        cleanup: Callable[[LeaseGrant, str], object],
        finalize: Callable[[LeaseGrant, str], object] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._authority = authority
        self._effects = ReservedEffect(authority)
        self._run_id, self._board_id, self._cleanup = run_id, board_id, cleanup
        self._finalize = finalize
        self._now = now

    def reconcile(
        self,
        *,
        boot_id: str,
        ledger: LedgerResult,
        leases: Sequence[LeaseGrant],
        generations: Sequence[GenerationState],
        ownership: BootOwnership,
    ) -> ReconciliationResult:
        ledger_document = ledger_receipt_document(self._run_id, ledger)
        snapshot_id = self._record_snapshot(boot_id, ledger_document)
        join = self._join_document(ledger, leases, generations, ownership)
        join_digest = digest_bytes(canonical_bytes(join))
        unsettled: list[str] = []
        actions: list[str] = []
        if ledger.outcome in {UNSETTLED, ABSENT, NOT_OURS}:
            unsettled.append(f"ledger:{ledger.reason or ledger.outcome}")
        elif not ownership.proved:
            unsettled.append("predecessor-or-attempt-ownership-unproved")
        else:
            owned = {row.row_id: row for row in ledger.owned}
            active = {generation.generation_id for generation in generations if generation.active}
            if active:
                unsettled.append("live-predecessor-generation")
            for lease in leases:
                if lease.phase is LeasePhase.CLOSED:
                    continue
                row = owned.get(lease.row_id)
                if row is None:
                    action = f"finalize:{lease.row_id}"
                    if ledger.outcome != EMPTY or self._finalize is None:
                        unsettled.append(f"lease:{lease.identity.lease_seq}:row-unsettled")
                    elif not self._quarantine_elapsed(lease.row_id, snapshot_id):
                        unsettled.append(f"{action}:quarantine")
                    elif not self._execute_finalize(snapshot_id, join_digest, action, lease):
                        unsettled.append(f"{action}:outcome-ambiguous")
                    else:
                        actions.append(action)
                elif lease.phase is LeasePhase.RECOVERABLE and lease.generation_id in active:
                    unsettled.append(f"lease:{lease.identity.lease_seq}:effect-ambiguous")
                elif lease.generation_id not in active:
                    action = f"terminate:{row.row_id}"
                    if not self._quarantine_elapsed(row.row_id, snapshot_id):
                        unsettled.append(f"{action}:quarantine")
                    elif not self._execute_cleanup(snapshot_id, join_digest, action, lease):
                        unsettled.append(f"{action}:outcome-ambiguous")
                    else:
                        actions.append(action)
            leased_rows = {lease.row_id for lease in leases if lease.phase is not LeasePhase.CLOSED}
            for row in ledger.owned:
                if row.row_id not in leased_rows:
                    unsettled.append(f"row:{row.row_id}:unattributed")
        if unsettled:
            result = ReconciliationResult(
                boot_id,
                snapshot_id,
                join_digest,
                tuple(actions),
                tuple(sorted(unsettled)),
                AdmissionVerdict.CLOSED,
                ownership=ownership,
            )
            self._commit_decision(result)
            return result
        completion_key = f"instance-reconciliation:{self._run_id}:{boot_id}:complete"
        result = ReconciliationResult(
            boot_id,
            snapshot_id,
            join_digest,
            tuple(actions),
            (),
            AdmissionVerdict.OPEN,
            completion_key,
            ownership,
        )
        try:
            self._commit_completion(completion_key, snapshot_id, join_digest, actions, ownership)
        except Exception:
            result = ReconciliationResult(
                boot_id,
                snapshot_id,
                join_digest,
                tuple(actions),
                ("reconciliation-complete:outcome-ambiguous",),
                AdmissionVerdict.CLOSED,
                ownership=ownership,
            )
        self._commit_decision(result)
        return result

    def project_join(self, ledger, leases, generations, ownership) -> str:
        """Digest the authoritative inputs without admitting a reconciliation effect."""
        return digest_bytes(canonical_bytes(self._join_document(ledger, leases, generations, ownership)))

    def _record_snapshot(self, boot_id, ledger_document) -> str:
        snapshot_id = digest_bytes(canonical_bytes({"boot_id": boot_id, "ledger": ledger_document}))
        key = f"instance-reconciliation:{self._run_id}:snapshot:{snapshot_id}"
        self._effects.execute(
            key,
            EffectIdentity("instance-reconciliation.snapshot", snapshot_id),
            RECONCILIATION_CAPACITY,
            lambda: None,
            encode=lambda _result: ledger_document,
            decode=lambda document: document,
            retention=RetentionPolicy.RECORD,
        )
        return snapshot_id

    def _quarantine_elapsed(self, row_id: str, snapshot_id: str) -> bool:
        key = f"instance-reconciliation:{self._run_id}:quarantine:{row_id}"
        existing = next((item for item in self._authority.reservations() if item.key == key), None)
        now = self._now()
        if existing is not None and existing.observation:
            return (
                snapshot_id != existing.observation["snapshot_id"]
                and now - float(existing.observation["observed_at"]) >= 15.0
            )
        reservation = self._authority.reserve(
            key,
            EffectIdentity(
                "instance-reconciliation.quarantine",
                json.dumps({"row_id": row_id, "snapshot_id": snapshot_id}, sort_keys=True, separators=(",", ":")),
            ),
            RECONCILIATION_CAPACITY,
            retention=RetentionPolicy.RECORD,
        )
        self._authority.commit(self._authority.start(reservation), {"snapshot_id": snapshot_id, "observed_at": now})
        return False

    def _execute_cleanup(self, snapshot_id, join_digest, action, lease) -> bool:
        key = f"instance-reconciliation:{self._run_id}:cleanup:{action}"
        subject = json.dumps(
            {"snapshot_id": snapshot_id, "join_digest": join_digest, "action": action},
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self._effects.execute(
                key,
                EffectIdentity("instance-reconciliation.cleanup", subject),
                RECONCILIATION_CAPACITY,
                lambda: self._settled_cleanup(lease, snapshot_id),
                encode=lambda _result: {"action": action, "result": "settled"},
                decode=lambda result: result,
                retention=RetentionPolicy.RECORD,
            )
            return True
        except Exception:
            return False

    def _settled_cleanup(self, lease, snapshot_id):
        result = self._cleanup(lease, snapshot_id)
        if result is False:
            raise RuntimeError("fixed cleanup did not settle")
        return result

    def _execute_finalize(self, snapshot_id, join_digest, action, lease) -> bool:
        key = f"instance-reconciliation:{self._run_id}:archive:{lease.identity.run_id}:{lease.identity.lease_seq}:{snapshot_id}"
        subject = json.dumps(
            {"snapshot_id": snapshot_id, "join_digest": join_digest, "action": action},
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            self._effects.execute(
                key,
                EffectIdentity("instance-reconciliation.archive", subject),
                RECONCILIATION_CAPACITY,
                lambda: self._finalize(lease, snapshot_id),
                encode=lambda _result: {"action": action, "result": "closed"},
                decode=lambda result: result,
                retention=RetentionPolicy.RECORD,
            )
            return True
        except Exception:
            return False

    def _commit_completion(self, key, snapshot_id, join_digest, actions, ownership) -> None:
        subject = json.dumps(
            {"snapshot_id": snapshot_id, "join_digest": join_digest, "ownership": ownership.document()},
            sort_keys=True,
            separators=(",", ":"),
        )
        self._effects.execute(
            key,
            EffectIdentity("instance-reconciliation.complete", subject),
            RECONCILIATION_CAPACITY,
            lambda: None,
            encode=lambda _result: {"admission": AdmissionVerdict.OPEN.value, "actions": actions},
            decode=lambda result: result,
            retention=RetentionPolicy.RECORD,
        )

    def _commit_decision(self, result: ReconciliationResult) -> None:
        key = f"instance-reconciliation:{self._run_id}:{result.boot_id}:decision"
        subject = json.dumps(
            {"snapshot_id": result.snapshot_id, "join_digest": result.join_digest},
            sort_keys=True,
            separators=(",", ":"),
        )
        self._effects.execute(
            key,
            EffectIdentity("instance-reconciliation.decision", subject),
            RECONCILIATION_CAPACITY,
            lambda: None,
            encode=lambda _result: result.document(),
            decode=lambda document: document,
            retention=RetentionPolicy.RECORD,
        )

    def _join_document(self, ledger, leases, generations, ownership):
        return {
            "run_id": self._run_id,
            "board_id": self._board_id,
            "ledger_identity_digest": ledger.identity_digest,
            "ledger_outcome": ledger.outcome,
            "owned_rows": sorted(row.row_id for row in ledger.owned),
            "foreign_rows": sorted(row.row_id for row in ledger.foreign),
            "leases": sorted(
                (
                    lease.identity.lease_seq,
                    lease.row_id,
                    lease.generation_id,
                    lease.phase.value,
                    lease.verdict.value,
                )
                for lease in leases
            ),
            "generations": [
                generation.document() for generation in sorted(generations, key=lambda item: item.generation_id)
            ],
            "ownership": ownership.document(),
        }


__all__ = ["InstanceReconciler"]
