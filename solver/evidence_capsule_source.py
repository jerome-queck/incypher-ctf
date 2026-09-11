"""Derive an immutable complete terminal-Run source snapshot."""

from __future__ import annotations

from solver.event_store import EventStore
from solver.evidence_capsule_contracts import CapsuleRefused, SourceSnapshot


def snapshot_terminal_run(store: EventStore) -> SourceSnapshot:
    try:
        stable = store.terminal_snapshot()
    except RuntimeError as error:
        raise CapsuleRefused(str(error)) from error
    return SourceSnapshot(
        run_id=stable.run_id,
        events=tuple(event.envelope for event in stable.events),
        chain_head=stable.chain_head,
        referenced_blobs=frozenset(event.blob_digest for event in stable.events),
    )
