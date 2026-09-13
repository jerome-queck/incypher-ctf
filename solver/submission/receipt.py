"""Sanitized, replay-verifiable serial-submission receipt."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from solver.event_store import InvalidReceiptError
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.write_reservation import WriteAuthority
from solver.submission import SUBMIT_CANDIDATE_OPERATION
from solver.write_reservation import EffectIdentity

SCHEMA_VERSION = 2
RECEIPT_TYPE = "serial-submission"
RECEIPT_FILENAME = "serial-submission.receipt.json"
MANIFEST_ROW_ID = "core.submission-tail"
MANIFEST_RECEIPT_REF = "receipt:serial-submission"


def receipt_document(run_id: str, authority: WriteAuthority) -> dict[str, object]:
    submissions = []
    timeline = []
    for reservation in sorted(authority.reservations(), key=lambda item: item.key):
        if reservation.identity.operation != SUBMIT_CANDIDATE_OPERATION:
            continue
        trace = authority.trace(reservation.key)
        timeline.extend(trace)
        submissions.append(
            {
                "reservation_id": reservation.key,
                "candidate_id": reservation.identity.payload_digest,
                "effect_id": reservation.effect_fingerprint,
                "operation": reservation.identity.operation,
                "challenge_id": reservation.identity.subject,
                "states": [row["state"] for row in trace],
                "ordinals": [row["ordinal"] for row in trace],
                "result": dict(reservation.observation or {}),
            }
        )
    timeline.sort(key=lambda row: row["ordinal"])
    active = maximum = 0
    for row in timeline:
        if row["state"] == "started":
            active += 1
            maximum = max(maximum, active)
        elif row["state"] in {"committed", "possibly-sent"}:
            active -= 1
    order = [str(row["identity"]["payload_digest"]) for row in timeline if row["state"] == "started"]
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
        "evidence_class": "controlled-runtime-trace",
        "dispatch_order": order,
        "in_flight_maximum": maximum,
        "submissions": submissions,
    }


def write_receipt(path: Path, run_id: str, authority: WriteAuthority) -> Path:
    destination = Path(path) / RECEIPT_FILENAME
    atomic_write(destination, canonical_bytes(receipt_document(run_id, authority)) + b"\n")
    return destination


def verify_receipt(path: Path, authority: WriteAuthority | None = None) -> Path:
    receipt = Path(path)
    try:
        raw = receipt.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("serial-submission receipt cannot be read") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("serial-submission receipt is not canonical JSON")
    schema_version = supplied.get("schema_version")
    if schema_version not in {1, SCHEMA_VERSION} or supplied.get("receipt_type") != RECEIPT_TYPE:
        raise InvalidReceiptError("serial-submission receipt has an unsupported contract")
    submissions = supplied.get("submissions")
    if (
        not isinstance(submissions, list)
        or supplied.get("in_flight_maximum") not in {0, 1}
        or supplied.get("evidence_class") != "controlled-runtime-trace"
    ):
        raise InvalidReceiptError("serial-submission receipt has an invalid trace")
    for row in submissions:
        if not isinstance(row, Mapping) or row.get("operation") != SUBMIT_CANDIDATE_OPERATION:
            raise InvalidReceiptError("serial-submission receipt has an invalid operation")
        identity = EffectIdentity(str(row["operation"]), str(row["challenge_id"]), str(row["candidate_id"]))
        if row.get("effect_id") != identity.fingerprint:
            raise InvalidReceiptError("serial-submission effect identity does not verify")
        states = row.get("states")
        ordinals = row.get("ordinals")
        allowed = (
            ["reserved", "started", "committed"],
            ["reserved", "started", "possibly-sent"],
            ["reserved", "aborted"],
            ["reserved", "aborted", "reserved", "started", "committed"],
            ["reserved", "aborted", "reserved", "started", "possibly-sent"],
        )
        if states not in allowed:
            raise InvalidReceiptError("serial-submission state trace is not exactly once")
        if schema_version == 2 and (
            not isinstance(ordinals, list) or len(ordinals) != len(states) or ordinals != sorted(set(ordinals))
        ):
            raise InvalidReceiptError("serial-submission trace ordinals are incomplete")
        result = row.get("result")
        if states[-1] == "committed":
            if not isinstance(result, Mapping) or not (
                result.get("ready_at")
                < result.get("reserved_at")
                < result.get("requested_at")
                < result.get("result_at")
            ):
                raise InvalidReceiptError("serial-submission timestamps are not ordered")
    if schema_version == 1:
        return receipt
    transitions = sorted(
        (
            (ordinal, state, str(row["candidate_id"]))
            for row in submissions
            for ordinal, state in zip(row["ordinals"], row["states"], strict=True)
        ),
        key=lambda item: item[0],
    )
    active = maximum = 0
    order = []
    for _ordinal, state, candidate_id in transitions:
        if state == "started":
            active += 1
            maximum = max(maximum, active)
            order.append(candidate_id)
        elif state in {"committed", "possibly-sent"}:
            active -= 1
        if active < 0:
            raise InvalidReceiptError("serial-submission lifecycle order is impossible")
    if supplied.get("in_flight_maximum") != maximum or supplied.get("dispatch_order") != order:
        raise InvalidReceiptError("serial-submission order or concurrency claim does not verify")
    if authority is not None and dict(supplied) != receipt_document(str(supplied.get("run_id", "")), authority):
        raise InvalidReceiptError("serial-submission receipt does not match authority state")
    return receipt


def manifest_receipt(path: Path, authority: WriteAuthority | None = None) -> dict[str, str]:
    verified = verify_receipt(path, authority)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path, authority: WriteAuthority) -> Mapping[str, object]:
    from solver.manifest import attach_partial_requirement_receipt

    return attach_partial_requirement_receipt(
        manifest,
        MANIFEST_ROW_ID,
        manifest_receipt(path, authority),
        reason="Serial Candidate dispatch is qualified; final-window lifecycle remains planned.",
    )
