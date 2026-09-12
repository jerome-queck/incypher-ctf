"""Pure replay and receipt projection over canonical Lease reservations."""

from __future__ import annotations

import json
from collections.abc import Sequence

from solver.instance_lease_contracts import (
    LeaseGrant,
    LeasePhase,
    LeaseVerdict,
    MANIFEST_RECEIPT_REF,
    MANIFEST_ROW_ID,
    RECEIPT_TYPE,
    SCHEMA_VERSION,
    grant_from_document,
)
from solver.write_reservation_contracts import ReservationState, WriteReservation


def replay_leases(reservations: Sequence[WriteReservation], run_id: str, board_id: str) -> tuple[LeaseGrant, ...]:
    current = {}
    for reservation in reservations:
        if (
            not reservation.identity.operation.startswith("lease.")
            or reservation.identity.operation == "lease.contender"
        ):
            continue
        try:
            semantic = json.loads(reservation.identity.subject)
            if semantic.get("run_id") != run_id or semantic.get("board_id") != board_id:
                continue
            if reservation.state is ReservationState.COMMITTED and reservation.observation:
                grant = grant_from_document(reservation.observation)
            else:
                operation = reservation.identity.operation.removeprefix("lease.")
                verdict = {
                    "create": LeaseVerdict.CREATE_AMBIGUOUS,
                    "renew": LeaseVerdict.RENEW_AMBIGUOUS,
                    "release": LeaseVerdict.RELEASE_AMBIGUOUS,
                    "expire": LeaseVerdict.EXPIRY_AMBIGUOUS,
                }.get(operation, LeaseVerdict.CREATE_AMBIGUOUS)
                grant = grant_from_document(
                    {**semantic, "phase": LeasePhase.RECOVERABLE.value, "verdict": verdict.value, "close_cause": ""}
                )
            previous = current.get(grant.identity)
            if previous is None or grant.epoch >= previous.epoch:
                current[grant.identity] = grant
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return tuple(sorted(current.values(), key=lambda item: item.identity.lease_seq))


def receipt_document(run_id: str, board_id: str, reservations: Sequence[WriteReservation], trace) -> dict[str, object]:
    leases = replay_leases(reservations, run_id, board_id)
    contenders = []
    for reservation in reservations:
        if reservation.identity.operation != "lease.contender" or not reservation.observation:
            continue
        subject = json.loads(reservation.identity.subject)
        contenders.append({"attempt_id": subject["contender_attempt_id"], "result": reservation.observation["result"]})
    histories = {}
    for reservation in reservations:
        if (
            not reservation.identity.operation.startswith("lease.")
            or reservation.identity.operation == "lease.contender"
        ):
            continue
        try:
            subject = json.loads(reservation.identity.subject)
            lease_key = (str(subject["run_id"]), int(subject["lease_seq"]))
            histories.setdefault(lease_key, []).extend(
                _public_trace(
                    trace(reservation.key),
                    int(subject.get("generation_authority_sequence", 0)),
                    str(subject.get("generation_authority_event_id", "")),
                )
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "board_id": board_id,
        "leases": [
            {
                "lease_id": {"run_id": grant.identity.run_id, "lease_seq": grant.identity.lease_seq},
                "owner_id": grant.owner_id,
                "challenge_id": grant.challenge_id,
                "authenticated_row_id": grant.row_id,
                "lease_epoch": grant.epoch,
                "generation_id": grant.generation_id,
                "attempt_id": grant.attempt_id,
                "phase": grant.phase.value,
                "verdict": grant.verdict.value,
                "close_cause": grant.close_cause.value,
                "effect_trace": histories[(grant.identity.run_id, grant.identity.lease_seq)],
            }
            for grant in leases
        ],
        "contender_results": sorted(contenders, key=lambda row: row["attempt_id"]),
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
    }


def _public_trace(rows, authority_sequence: int, authority_event_id: str) -> list[dict[str, object]]:
    """Expose ordering and identity fingerprints, never semantic subjects or observations."""
    return [
        {
            "key": row["key"],
            "state": row["state"],
            "effect_fingerprint": row["effect_fingerprint"],
            "generation_authority_sequence": authority_sequence,
            "generation_authority_event_id": authority_event_id,
        }
        for row in rows
    ]


__all__ = ["receipt_document", "replay_leases"]
