"""Instance-Lease receipt I/O and verification against canonical authority history."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.instance_lease_contracts import (
    LeasePhase,
    MANIFEST_RECEIPT_REF,
    MANIFEST_ROW_ID,
    RECEIPT_FILENAME,
    RECEIPT_TYPE,
    SCHEMA_VERSION,
)
from solver.instance_lease_projection import receipt_document
from solver.event_store import (
    WORK_GENERATION_RECORDED,
    CommittedEvent,
    GenerationAuthority,
    GenerationClassification,
    GenerationRecord,
)
from solver.write_reservation import WriteAuthority


def write_receipt(
    authority: WriteAuthority,
    run_id: str,
    board_id: str,
    destination: Path,
) -> Path:
    document = receipt_document(run_id, board_id, authority.reservations(), authority.trace)
    path = Path(destination)
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path, authority: WriteAuthority, generation_events: Sequence[CommittedEvent] = ()) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Instance-Lease receipt is unreadable") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise ValueError("Instance-Lease receipt is not canonical JSON")
    if supplied.get("schema_version") != SCHEMA_VERSION or supplied.get("receipt_type") != RECEIPT_TYPE:
        raise ValueError("Instance-Lease receipt contract is unsupported")
    run_id, board_id = str(supplied.get("run_id", "")), str(supplied.get("board_id", ""))
    if supplied != receipt_document(run_id, board_id, authority.reservations(), authority.trace):
        raise ValueError("Instance-Lease receipt disagrees with canonical authority history")
    for lease in supplied["leases"]:
        if lease["phase"] == LeasePhase.ATTEMPT_BOUND.value and not lease["authenticated_row_id"]:
            raise ValueError("Attempt-bound Lease lacks authenticated row identity")
        for step in lease["effect_trace"]:
            _verify_generation_authority(step, lease, generation_events)
    return receipt_path


def _verify_generation_authority(step, lease, generation_events) -> None:
    sequence = step["generation_authority_sequence"]
    event_id = step["generation_authority_event_id"]
    matches = [event for event in generation_events if event.sequence == sequence]
    if len(matches) != 1:
        raise ValueError("Instance-Lease effect lacks canonical generation authority")
    event = matches[0]
    payload = event.payload
    if (
        event.event_type != WORK_GENERATION_RECORDED
        or payload.get("event_id") != event_id
        or payload.get("generation_id") != lease["generation_id"]
        or payload.get("attempt_id") != lease["attempt_id"]
        or payload.get("record") != GenerationRecord.AUTHORITY.value
        or payload.get("authority") != GenerationAuthority.AUTHORITY.value
        or payload.get("classification") != GenerationClassification.CURRENT.value
    ):
        raise ValueError("Instance-Lease generation authority linkage is invalid")


def manifest_receipt(
    path: Path, authority: WriteAuthority, generation_events: Sequence[CommittedEvent] = ()
) -> dict[str, str]:
    verified = verify_receipt(path, authority, generation_events)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(
    manifest: Mapping[str, object],
    path: Path,
    authority: WriteAuthority,
    generation_events: Sequence[CommittedEvent] = (),
) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path, authority, generation_events))


__all__ = [
    "RECEIPT_FILENAME",
    "link_manifest",
    "manifest_receipt",
    "verify_receipt",
    "write_receipt",
]
