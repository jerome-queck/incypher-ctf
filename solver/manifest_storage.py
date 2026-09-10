"""ADR-0054 storage-profile validation."""

from collections.abc import Mapping, Sequence

from solver.manifest_contracts import (
    AUTHORITY_EFFECT_ENVELOPE_KEYS,
    CONCURRENT_AUTHORITY_KEYS,
    INCIDENT_EVIDENCE_LIMIT_KEYS,
    OPERATION_KEYS,
    PRESSURE_THRESHOLD_KEYS,
    PRODUCER_ENVELOPE_KEYS,
    RESERVATION_KEYS,
    STORAGE_KEYS,
)
from solver.manifest_values import fail, mapping, positive_int, string


def _reservation(value: object, name: str) -> dict[str, int]:
    reservation = mapping(value, name)
    if set(reservation) != RESERVATION_KEYS:
        fail(f"{name} must bind bytes, filesystem_objects and operations")
    dimensions = {
        "bytes": positive_int(reservation["bytes"], f"{name}.bytes"),
        "filesystem_objects": positive_int(reservation["filesystem_objects"], f"{name}.filesystem_objects"),
    }
    operations = mapping(reservation["operations"], f"{name}.operations")
    if set(operations) != OPERATION_KEYS:
        fail(f"{name}.operations must bind create, append, rename, unlink and durability")
    for operation in sorted(OPERATION_KEYS):
        dimensions[f"operations.{operation}"] = positive_int(operations[operation], f"{name}.operations.{operation}")
    return dimensions


def _reservation_fits_within(required: Mapping[str, int], available: Mapping[str, int]) -> bool:
    return all(required[dimension] <= available[dimension] for dimension in required)


def _sum_weighted_reservations(reservations_and_counts: Sequence[tuple[Mapping[str, int], int]]) -> dict[str, int]:
    return {
        dimension: sum(reservation[dimension] * count for reservation, count in reservations_and_counts)
        for dimension in reservations_and_counts[0][0]
    }


def _named_envelopes(
    value: object, *, collection: str, identity: str, expected_keys: frozenset[str]
) -> dict[str, dict[str, int]]:
    if not isinstance(value, list) or not value:
        fail(f"selected_profile.storage.{collection} must be a non-empty list")
    envelopes: dict[str, dict[str, int]] = {}
    for index, raw_envelope in enumerate(value):
        envelope = mapping(raw_envelope, f"selected_profile.storage.{collection}[{index}]")
        if set(envelope) != expected_keys:
            fail(f"{collection} entries must bind {identity} and reservation")
        name = string(envelope[identity], f"{collection}[{index}].{identity}")
        if name in envelopes:
            fail(f"{collection} contains duplicate {identity} values")
        envelopes[name] = _reservation(envelope["reservation"], f"{collection} {name}")
    if list(envelopes) != sorted(envelopes):
        fail(f"{collection} are not in stable order")
    return envelopes


def validate_storage_profile(value: object) -> None:
    storage = mapping(value, "selected_profile.storage")
    if set(storage) != STORAGE_KEYS:
        fail("selected_profile.storage must bind the complete storage reservation contract")

    envelope = _reservation(storage["writable_envelope"], "selected_profile.storage.writable_envelope")
    safety = _reservation(
        storage["host_filesystem_safety_floor"], "selected_profile.storage.host_filesystem_safety_floor"
    )
    thresholds = mapping(storage["pressure_thresholds"], "selected_profile.storage.pressure_thresholds")
    if set(thresholds) != PRESSURE_THRESHOLD_KEYS:
        fail("selected_profile.storage.pressure_thresholds must bind soft_remaining and hard_remaining")
    soft = _reservation(thresholds["soft_remaining"], "selected_profile.storage.pressure_thresholds.soft_remaining")
    hard = _reservation(thresholds["hard_remaining"], "selected_profile.storage.pressure_thresholds.hard_remaining")
    if not (
        _reservation_fits_within(safety, hard)
        and _reservation_fits_within(hard, soft)
        and _reservation_fits_within(soft, envelope)
    ):
        fail("selected_profile.storage requires safety floor <= hard <= soft <= writable envelope")

    _named_envelopes(
        storage["ordinary_producer_envelopes"],
        collection="ordinary_producer_envelopes",
        identity="producer",
        expected_keys=PRODUCER_ENVELOPE_KEYS,
    )
    authorities = _named_envelopes(
        storage["authority_effect_envelopes"],
        collection="authority_effect_envelopes",
        identity="transaction",
        expected_keys=AUTHORITY_EFFECT_ENVELOPE_KEYS,
    )
    maximum = _reservation(
        storage["maximum_authority_effect_reservation"],
        "selected_profile.storage.maximum_authority_effect_reservation",
    )
    expected_maximum = {
        dimension: max(reservation[dimension] for reservation in authorities.values()) for dimension in maximum
    }
    if maximum != expected_maximum:
        fail("maximum_authority_effect_reservation must equal the dimensional maximum envelope")

    concurrent = storage["maximum_concurrent_authority_set"]
    if not isinstance(concurrent, list) or not concurrent:
        fail("selected_profile.storage.maximum_concurrent_authority_set must be a non-empty list")
    concurrency: dict[str, int] = {}
    for index, raw_member in enumerate(concurrent):
        member = mapping(raw_member, f"selected_profile.storage.maximum_concurrent_authority_set[{index}]")
        if set(member) != CONCURRENT_AUTHORITY_KEYS:
            fail("concurrent authority members must bind transaction and maximum_concurrent")
        transaction = string(member["transaction"], f"concurrent authority member {index}.transaction")
        if transaction in concurrency:
            fail("maximum concurrent authority set contains duplicate transactions")
        concurrency[transaction] = positive_int(
            member["maximum_concurrent"], f"concurrent authority transaction {transaction}.maximum_concurrent"
        )
    if list(concurrency) != sorted(concurrency) or set(concurrency) != set(authorities):
        fail("maximum concurrent authority set must name every authority/effect envelope in stable order")

    required_pool = _sum_weighted_reservations(
        [(authorities[transaction], count) for transaction, count in concurrency.items()]
    )
    shared_pool = _reservation(storage["shared_authority_pool"], "selected_profile.storage.shared_authority_pool")
    if not _reservation_fits_within(required_pool, shared_pool):
        fail("shared_authority_pool cannot cover the maximum concurrent authority set")
    terminal = _reservation(storage["terminal_floor"], "selected_profile.storage.terminal_floor")
    recovery = _reservation(storage["recovery_floor"], "selected_profile.storage.recovery_floor")
    reserved_hard_pressure_headroom = _sum_weighted_reservations([(shared_pool, 1), (terminal, 1), (recovery, 1)])
    if not _reservation_fits_within(reserved_hard_pressure_headroom, hard):
        fail("hard pressure headroom must preserve shared authority, terminal and Recovery floors")

    limits = mapping(storage["incident_evidence_limits"], "selected_profile.storage.incident_evidence_limits")
    if set(limits) != INCIDENT_EVIDENCE_LIMIT_KEYS:
        fail("incident evidence limits must bind per_incident and aggregate")
    per_incident = _reservation(limits["per_incident"], "incident_evidence_limits.per_incident")
    aggregate = _reservation(limits["aggregate"], "incident_evidence_limits.aggregate")
    if not _reservation_fits_within(per_incident, aggregate):
        fail("aggregate Incident evidence limit must cover one per-Incident limit")
