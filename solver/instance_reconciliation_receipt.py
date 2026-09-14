"""Sanitized reconciliation receipt rebuilt from canonical reservation history."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.instance_reconciliation_contracts import AdmissionVerdict, BootOwnership, ReconciliationResult
from solver.write_reservation import WriteAuthority
from solver.write_reservation_contracts import ReservationState

SCHEMA_VERSION = 1
RECEIPT_TYPE = "instance-reconciliation"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"
MANIFEST_ROW_ID = "core.board-target-lease"


def receipt_document(result: ReconciliationResult, authority: WriteAuthority) -> dict[str, object]:
    traces = []
    for reservation in authority.reservations():
        if not reservation.identity.operation.startswith("instance-reconciliation."):
            continue
        try:
            subject = json.loads(reservation.identity.subject)
        except json.JSONDecodeError:
            subject = {"snapshot_id": reservation.identity.subject}
        if subject.get("snapshot_id") != result.snapshot_id:
            continue
        traces.append(
            {
                "key": reservation.key,
                "operation": reservation.identity.operation,
                "state": reservation.state.value,
                "effect_fingerprint": reservation.identity.fingerprint,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        **result.document(),
        "authority_trace": sorted(traces, key=lambda row: str(row["key"])),
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF},
    }


def write_receipt(result: ReconciliationResult, authority: WriteAuthority, destination: Path) -> Path:
    path = Path(destination)
    atomic_write(path, canonical_bytes(receipt_document(result, authority)) + b"\n")
    return path


def verify_receipt(path: Path, authority: WriteAuthority, canonical_events, ledger_receipt: Path) -> Path:
    raw = Path(path).read_bytes()
    supplied = json.loads(raw)
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise ValueError("Instance-reconciliation receipt is not canonical JSON")
    result = ReconciliationResult(
        str(supplied["boot_id"]),
        str(supplied["snapshot_id"]),
        str(supplied["join_digest"]),
        tuple(supplied["actions"]),
        tuple(supplied["unsettled"]),
        AdmissionVerdict(supplied["admission"]),
        str(supplied["completion_key"]),
        BootOwnership(
            tuple(supplied["ownership"]["predecessor_close_event_ids"]),
            tuple(supplied["ownership"]["interrupted_attempt_close_event_ids"]),
            bool(supplied["ownership"]["interrupted_attempts_closed"]),
            bool(supplied["ownership"]["predecessors_closed"]),
        ),
        str(supplied.get("cycle_id", "")),
    )
    expected = receipt_document(result, authority)
    if supplied != expected:
        raise ValueError("Instance-reconciliation receipt disagrees with canonical authority")
    payloads = [event.payload for event in canonical_events]
    boot_opens = [row for row in payloads if row.get("record") == "boot-open"]
    if sum(row.get("boot_id") == result.boot_id for row in boot_opens) != 1:
        raise ValueError("Reconciliation Boot identity is not canonical")
    predecessor_ids = {str(row["boot_id"]) for row in boot_opens if row.get("boot_id") != result.boot_id}
    boot_closes = {
        str(row.get("boot_id")): str(row.get("event_id"))
        for row in payloads
        if row.get("record") == "boot-close"
        and row.get("disposition") in {"normal", "refused", "interrupted", "crashed"}
    }
    if predecessor_ids - boot_closes.keys() or set(result.ownership.predecessor_close_event_ids) != {
        boot_closes[item] for item in predecessor_ids
    }:
        raise ValueError("Reconciliation predecessor closure set is incomplete")
    interrupted_attempts = {
        str(row.get("attempt_id"))
        for row in payloads
        if row.get("record") == "close" and row.get("disposition") == "interrupt"
    }
    opened_attempts = {str(row.get("attempt_id")) for row in payloads if row.get("record") == "attempt-open"}
    required_attempts = interrupted_attempts & opened_attempts
    attempt_closes = {
        str(row.get("attempt_id")): str(row.get("event_id"))
        for row in payloads
        if row.get("record") == "attempt-close" and row.get("attempt_id")
    }
    if required_attempts - attempt_closes.keys() or set(result.ownership.interrupted_attempt_close_event_ids) != {
        attempt_closes[item] for item in required_attempts
    }:
        raise ValueError("Reconciliation interrupted Attempt closure set is incomplete")
    if not result.ownership.predecessors_closed or not result.ownership.interrupted_attempts_closed:
        raise ValueError("Reconciliation ownership booleans disagree with canonical closure")
    actual_decision = [
        reservation
        for reservation in authority.reservations()
        if reservation.key.endswith(f":{result.reconciliation_id}:decision")
        and reservation.state is ReservationState.COMMITTED
        and reservation.observation == result.document()
    ]
    if len(actual_decision) != 1:
        raise ValueError("Instance-reconciliation decision is not canonical")
    snapshots = [
        reservation
        for reservation in authority.reservations()
        if reservation.identity.operation == "instance-reconciliation.snapshot"
        and reservation.identity.subject == result.snapshot_id
        and reservation.state is ReservationState.COMMITTED
    ]
    snapshot_material = {"boot_id": result.boot_id, "ledger": snapshots[0].observation} if snapshots else {}
    if result.cycle_id:
        snapshot_material["cycle_id"] = result.cycle_id
    if len(snapshots) != 1 or digest_bytes(canonical_bytes(snapshot_material)) != result.snapshot_id:
        raise ValueError("Instance-reconciliation snapshot lacks canonical authenticated-ledger evidence")
    from solver.instance_ledger import verify_receipt as verify_ledger_receipt

    verified_ledger = verify_ledger_receipt(ledger_receipt)
    if json.loads(verified_ledger.read_bytes()) != snapshots[0].observation:
        raise ValueError("Reconciliation snapshot disagrees with independently verified ledger authority")
    if result.verdict.value == "open":
        complete = [
            reservation
            for reservation in authority.reservations()
            if reservation.key == result.completion_key and reservation.state is ReservationState.COMMITTED
        ]
        if len(complete) != 1:
            raise ValueError("Instance-reconciliation admission lacks completion authority")
    return Path(path)


def manifest_receipt(path: Path, authority: WriteAuthority, canonical_events, ledger_receipt: Path) -> dict[str, str]:
    verified = verify_receipt(path, authority, canonical_events, ledger_receipt)
    return {"ref": RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest, path, authority, canonical_events, ledger_receipt):
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(
        manifest, MANIFEST_ROW_ID, manifest_receipt(path, authority, canonical_events, ledger_receipt)
    )


__all__ = ["RECEIPT_FILENAME", "link_manifest", "manifest_receipt", "verify_receipt", "write_receipt"]
