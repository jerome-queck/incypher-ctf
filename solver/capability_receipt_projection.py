"""Pure projection and transition validation for capability-custody receipts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from solver.capability_evidence import PROBE_KINDS
from solver.event_store import CAPABILITY_CUSTODY_RECORDED, CapabilityRecord, InvalidReceiptError

BINDING_FIELDS = ("run_id", "boot_id", "generation_id", "lane_id", "attempt_id", "step_id")


def receipt_document(run_id: str, events: list[Any], *, strict_probes: bool = False) -> dict[str, Any]:
    custody_events = [event for event in events if event.event_type == CAPABILITY_CUSTODY_RECORDED]
    if not custody_events:
        raise InvalidReceiptError("capability-custody receipt has no custody evidence")
    boot_id = _boot_id(custody_events)
    env_cutover = _env_cutover(custody_events, run_id, boot_id)
    broker_transfers = _broker_transfers(custody_events, env_cutover["sequence"], run_id, boot_id)
    handle_scopes, issued = _handle_scopes(custody_events, run_id)
    peer_auth_failures, revocation_trace, denial_trace = _handle_traces(custody_events, issued, run_id)
    probes = _probe_document(custody_events, run_id, boot_id, strict=strict_probes)
    if strict_probes:
        if {row["broker"] for row in broker_transfers} != {"board", "codex", "cpa"}:
            raise InvalidReceiptError("capability receipt does not prove all three broker owners")
        if not handle_scopes:
            raise InvalidReceiptError("capability receipt has no scoped handle")
        if not peer_auth_failures:
            raise InvalidReceiptError("capability receipt has no peer-authentication failure trace")
        if not revocation_trace:
            raise InvalidReceiptError("capability receipt has no revocation trace")
    return {
        "schema_version": 1,
        "receipt_type": "capability-custody",
        "run_id": run_id,
        "boot_id": boot_id,
        "env_cutover": env_cutover,
        "broker_transfers": broker_transfers,
        "handle_scopes": handle_scopes,
        "peer_auth_failures": peer_auth_failures,
        "revocation_trace": revocation_trace,
        "denial_trace": denial_trace,
        "executor_secret_probes": probes,
        "chain_head": events[-1].event_digest,
        "manifest_link": {
            "row_id": "core.brokered-credentials-egress",
            "receipt_ref": "receipt:capability-custody",
        },
    }


def _boot_id(events: list[Any]) -> str:
    boot_ids = {
        event.payload["boot_id"]
        for event in events
        if isinstance(event.payload.get("boot_id"), str) and event.payload["boot_id"]
    }
    if len(boot_ids) != 1:
        raise InvalidReceiptError("capability-custody evidence has no single Boot identity")
    return next(iter(boot_ids))


def _env_cutover(events: list[Any], run_id: str, boot_id: str) -> dict[str, Any]:
    matches = [event for event in events if event.payload["record"] == CapabilityRecord.ENV_CUTOVER.value]
    if len(matches) != 1:
        raise InvalidReceiptError("capability-custody evidence needs exactly one env cutover")
    event = matches[0]
    payload = event.payload
    if payload["run_id"] != run_id or payload["boot_id"] != boot_id:
        raise InvalidReceiptError("capability env cutover belongs to another Run or Boot")
    return {
        "sequence": event.sequence,
        "scope": payload["scope"],
        "decision": payload["decision"],
        "evidence_digest": payload["evidence_digest"],
    }


def _broker_transfers(events: list[Any], env_sequence: int, run_id: str, boot_id: str) -> list[dict[str, Any]]:
    transfers = [event for event in events if event.payload["record"] == CapabilityRecord.TRANSFER_ACCEPTED.value]
    clears = [event for event in events if event.payload["record"] == CapabilityRecord.SOURCE_CLEARED.value]
    if not transfers:
        raise InvalidReceiptError("capability-custody evidence has no broker transfer")
    grouped: dict[tuple[str, str], list[str]] = {}
    transfer_sequences: dict[tuple[str, str, str], int] = {}
    for event in transfers:
        payload = event.payload
        _require_transfer_identity(payload, run_id, boot_id, event.sequence, env_sequence)
        broker, evidence_digest, names = payload["broker"], payload["evidence_digest"], payload["secret_names"]
        if len(set(names)) != len(names):
            raise InvalidReceiptError("capability transfer repeats a secret name", sequence=event.sequence)
        grouped.setdefault((broker, evidence_digest), []).extend(names)
        for name in names:
            key = (broker, name, evidence_digest)
            if key in transfer_sequences:
                raise InvalidReceiptError("capability transfer is duplicated", sequence=event.sequence)
            transfer_sequences[key] = event.sequence
    cleared: set[tuple[str, str, str]] = set()
    for event in clears:
        payload = event.payload
        _require_transfer_identity(payload, run_id, boot_id, event.sequence, env_sequence)
        broker, evidence_digest, names = payload["broker"], payload["evidence_digest"], payload["secret_names"]
        if len(set(names)) != len(names):
            raise InvalidReceiptError("capability source clear repeats a secret name", sequence=event.sequence)
        for name in names:
            key = (broker, name, evidence_digest)
            transfer_sequence = transfer_sequences.get(key)
            if transfer_sequence is None:
                raise InvalidReceiptError("capability source clear has no matching transfer", sequence=event.sequence)
            if event.sequence <= transfer_sequence:
                raise InvalidReceiptError(
                    "capability source clear does not follow its transfer", sequence=event.sequence
                )
            if key in cleared:
                raise InvalidReceiptError("capability source is cleared more than once", sequence=event.sequence)
            cleared.add(key)
    if cleared != set(transfer_sequences):
        raise InvalidReceiptError("capability transfer/source-clear evidence is incomplete")
    return [
        {"broker": broker, "secret_names": sorted(names), "evidence_digest": evidence_digest}
        for (broker, evidence_digest), names in grouped.items()
    ]


def _require_transfer_identity(
    payload: Mapping[str, Any], run_id: str, boot_id: str, sequence: int, env_sequence: int
) -> None:
    if sequence <= env_sequence:
        raise InvalidReceiptError("capability transfer precedes env cutover", sequence=sequence)
    if payload["run_id"] != run_id or payload["boot_id"] != boot_id:
        raise InvalidReceiptError("capability transfer belongs to another Run or Boot", sequence=sequence)


def _handle_scopes(events: list[Any], run_id: str) -> tuple[list[dict[str, Any]], dict[str, Mapping[str, Any]]]:
    issued_events = [event for event in events if event.payload["record"] == CapabilityRecord.ISSUED.value]
    issued: dict[str, Mapping[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for event in issued_events:
        payload = event.payload
        handle = payload["handle_digest"]
        if handle in issued:
            raise InvalidReceiptError("capability handle was issued more than once", sequence=event.sequence)
        if payload["run_id"] != run_id:
            raise InvalidReceiptError("capability handle belongs to another Run", sequence=event.sequence)
        if any(not payload[field] for field in ("boot_id", "generation_id", "scope")):
            raise InvalidReceiptError("capability handle binding is incomplete", sequence=event.sequence)
        issued[handle] = payload
        rows.append(
            {"handle_digest": handle, "scope": payload["scope"], **{field: payload[field] for field in BINDING_FIELDS}}
        )
    return rows, issued


def _handle_traces(
    events: list[Any], issued: Mapping[str, Mapping[str, Any]], run_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    peer_failures: list[dict[str, Any]] = []
    revocations: list[dict[str, Any]] = []
    denials: list[dict[str, Any]] = []
    revoked: set[str] = set()
    for event in events:
        payload = event.payload
        record = payload["record"]
        if record not in {
            CapabilityRecord.AUTHORIZED.value,
            CapabilityRecord.DENIED.value,
            CapabilityRecord.REVOKED.value,
        }:
            continue
        handle = payload["handle_digest"]
        known = issued.get(handle)
        if known is None:
            if record != CapabilityRecord.DENIED.value or payload["reason"] not in {
                "unknown-handle",
                "peer-authentication-unavailable",
            }:
                raise InvalidReceiptError("capability handle decision has no issued handle", sequence=event.sequence)
            if any(payload[field] for field in (*BINDING_FIELDS, "scope")):
                raise InvalidReceiptError("unknown capability denial carries a binding", sequence=event.sequence)
        else:
            if payload["run_id"] != run_id:
                raise InvalidReceiptError("capability handle decision belongs to another Run", sequence=event.sequence)
            if any(payload[field] != known[field] for field in (*BINDING_FIELDS, "scope")):
                raise InvalidReceiptError("capability handle decision changed its binding", sequence=event.sequence)
            issue_sequence = _sequence_for_handle(events, handle, CapabilityRecord.ISSUED.value)
            if event.sequence <= issue_sequence:
                raise InvalidReceiptError("capability handle decision precedes issue", sequence=event.sequence)
            if record == CapabilityRecord.AUTHORIZED.value and handle in revoked:
                raise InvalidReceiptError("capability authorization follows revocation", sequence=event.sequence)
            if record == CapabilityRecord.REVOKED.value:
                if handle in revoked:
                    raise InvalidReceiptError("capability handle was revoked more than once", sequence=event.sequence)
                revoked.add(handle)
        if record == CapabilityRecord.DENIED.value:
            row = {
                "sequence": event.sequence,
                "handle_digest": handle,
                "peer_uid": payload["peer_uid"],
                "peer_identity_digest": payload["peer_identity_digest"],
                "reason": payload["reason"],
            }
            denials.append(row)
            if payload["reason"] in {"peer-mismatch", "peer-authentication-unavailable"}:
                peer_failures.append(row.copy())
        elif record == CapabilityRecord.REVOKED.value:
            revocations.append({"sequence": event.sequence, "handle_digest": handle, "reason": payload["reason"]})
    return peer_failures, revocations, denials


def _sequence_for_handle(events: list[Any], handle: str, record: str) -> int:
    matches = [
        event.sequence
        for event in events
        if event.payload["record"] == record and event.payload["handle_digest"] == handle
    ]
    if len(matches) != 1:
        raise InvalidReceiptError("capability handle has an ambiguous issue")
    return matches[0]


def _probe_document(events: list[Any], run_id: str, boot_id: str, *, strict: bool) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    custody_complete = max(
        (event.sequence for event in events if event.payload["record"] == CapabilityRecord.SOURCE_CLEARED.value),
        default=0,
    )
    for event in events:
        payload = event.payload
        if payload["record"] != CapabilityRecord.PROBE_RECORDED.value:
            continue
        if payload["run_id"] != run_id or payload["boot_id"] != boot_id:
            raise InvalidReceiptError("capability probe belongs to another Run or Boot", sequence=event.sequence)
        latest[payload["probe_kind"]] = event
    checks = {kind: latest[kind].payload["probe_result"] for kind in PROBE_KINDS if kind in latest}
    digests = {latest[kind].payload["evidence_digest"] for kind in PROBE_KINDS if kind in latest}
    common_digest = next(iter(digests), "") if len(digests) == 1 else ""
    if strict:
        if set(checks) != set(PROBE_KINDS):
            raise InvalidReceiptError("capability receipt does not contain all five latest probes")
        if any(checks[kind] != "clear" for kind in PROBE_KINDS):
            raise InvalidReceiptError("capability receipt contains a non-clear executor probe")
        if len(digests) != 1:
            raise InvalidReceiptError("capability probes do not share one evidence digest")
        if any(latest[kind].sequence <= custody_complete for kind in PROBE_KINDS):
            raise InvalidReceiptError("capability probes do not follow completed bootstrap custody")
    return {"checks": checks, "evidence_digest": common_digest}


__all__ = ["receipt_document"]
