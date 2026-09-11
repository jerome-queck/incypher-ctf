"""Independent reservation receipt verification and manifest adaptation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.write_reservation_contracts import (
    MANIFEST_RECEIPT_REF,
    MANIFEST_ROW_ID,
    RECEIPT_TYPE,
    SCHEMA_VERSION,
    WriteReservation,
    digest,
    profile_from,
)
from solver.write_reservation_proof import load_controlled_proof

if TYPE_CHECKING:
    from solver.write_reservation import WriteAuthority


def receipt_document(authority: WriteAuthority, reservation: WriteReservation) -> dict[str, Any]:
    proof = load_controlled_proof()
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "profile_digest": authority.profile_digest,
        "key": reservation.key,
        "effect_fingerprint": reservation.effect_fingerprint,
        "pool": reservation.pool.value,
        "need": reservation.need.as_dict(),
        "object_slots": list(reservation.object_slots),
        "state": reservation.state.value,
        "trace": authority.trace(reservation.key),
        "extent_remaining": authority.extent_path(reservation.pool).stat().st_size,
        "controlled_proof_digest": digest_bytes(canonical_bytes(proof)),
        "crash_point_outcomes": proof["crash_point_outcomes"],
        "physical_exhaustion_result": proof["physical_exhaustion_result"],
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def write_receipt(authority: WriteAuthority, key: str, destination: Path | None = None) -> Path:
    reservation = authority.current(key)
    if reservation is None:
        raise ValueError(f"unknown reservation {key!r}")
    document = receipt_document(authority, reservation)
    document["receipt_digest"] = digest(document)
    destination = destination or authority.receipts_dir / f"{hashlib.sha256(key.encode()).hexdigest()}.json"
    atomic_write(destination, canonical_bytes(document) + b"\n")
    return destination


def verify_receipt(authority: WriteAuthority, path: Path) -> dict[str, Any]:
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    if raw != canonical_bytes(document) + b"\n":
        raise ValueError("write-reservation receipt is not canonical JSON")
    supplied = document.pop("receipt_digest", None)
    if supplied != digest(document):
        raise ValueError("write-reservation receipt digest mismatch")
    current = authority.current(str(document.get("key", "")))
    if current is None or document != receipt_document(authority, current):
        raise ValueError("write-reservation receipt differs from durable authority")
    return document


def manifest_receipt(path: Path) -> dict[str, str]:
    receipt_path = Path(path)
    try:
        profile = profile_from(json.loads((receipt_path.parents[1] / "profile.json").read_text()))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise ValueError("write-reservation receipt does not identify a sealed profile") from error
    from solver.write_reservation import WriteAuthority

    authority = WriteAuthority(receipt_path.parents[2], profile)
    verify_receipt(authority, receipt_path)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": RECEIPT_TYPE,
        "digest": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
    }
