"""Canonical authority identity shared by capsule publication and replay."""

from typing import Any

from solver.write_reservation import WriteReservation


def reservation_identity(reservation: WriteReservation) -> dict[str, Any]:
    return {
        "key": reservation.key,
        "effect_fingerprint": reservation.effect_fingerprint,
        "boot_id": reservation.boot_id,
        "object_slots": list(reservation.object_slots),
        "pool": reservation.pool.value,
        "need": reservation.need.as_dict(),
        "retention": reservation.retention.value,
    }
