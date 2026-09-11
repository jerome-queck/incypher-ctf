"""Derive an immutable complete terminal-Run source snapshot."""

from __future__ import annotations

from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.evidence_capsule_contracts import CapsuleRefused, SourceSnapshot


def snapshot_terminal_run(store: EventStore) -> SourceSnapshot:
    first = store.events()
    if not first:
        raise CapsuleRefused("capsule source has no canonical events")
    encoded = tuple(canonical_bytes(event.envelope) for event in first)
    second = store.events()
    if encoded != tuple(canonical_bytes(event.envelope) for event in second):
        raise CapsuleRefused("canonical source changed while it was being snapshotted")
    if [event.sequence for event in first] != list(range(1, len(first) + 1)):
        raise CapsuleRefused("capsule source is not the complete genesis chain")
    records = [event.payload.get("record") for event in first]
    if first[0].previous_digest or records[0] != "run-open" or records.count("run-open") != 1:
        raise CapsuleRefused("capsule source does not begin at Run genesis")
    if records[-1] != "run-close" or records.count("run-close") != 1:
        raise CapsuleRefused("capsule source is not a terminal Run")
    for before, after in zip(first, first[1:], strict=False):
        if after.previous_digest != before.event_digest:
            raise CapsuleRefused("capsule source predecessor chain is discontinuous")
    run_ids = {str(event.envelope.get("run_id", "")) for event in first}
    if run_ids != {store.run_id}:
        raise CapsuleRefused("capsule source contains another Run")
    return SourceSnapshot(
        run_id=store.run_id,
        events=tuple(event.envelope for event in first),
        chain_head=first[-1].event_digest,
        referenced_blobs=frozenset(event.blob_digest for event in first),
    )
