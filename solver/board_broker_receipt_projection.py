"""Pure projection from canonical Board-broker lifecycle to its receipt."""

from __future__ import annotations

from collections.abc import Sequence

from solver.board_broker_contracts import BOARD_BROKER_RECORDED, BoardRecord, SCHEMA_VERSION
from solver.event_store import InvalidReceiptError
from solver.event_store_contracts import CAPABILITY_CUSTODY_RECORDED, CapabilityRecord


def receipt_document(run_id: str, events: Sequence[object]) -> dict[str, object]:
    broker_events = [event for event in events if event.event_type == BOARD_BROKER_RECORDED]
    if not broker_events:
        raise InvalidReceiptError("Board-broker receipt has no canonical requests")
    lifecycles: dict[str, dict[str, list[object]]] = {}
    for event in broker_events:
        request_id = event.payload["request_id"]
        lifecycle = lifecycles.setdefault(
            request_id,
            {BoardRecord.RESERVED.value: [], BoardRecord.CLASSIFIED.value: []},
        )
        lifecycle[event.payload["record"]].append(event)
    if any(any(len(records) != 1 for records in lifecycle.values()) for lifecycle in lifecycles.values()):
        raise InvalidReceiptError("Board-broker receipt has an incomplete or duplicate request lifecycle")
    requests = []
    paired = sorted(
        (
            (lifecycle[BoardRecord.RESERVED.value][0], lifecycle[BoardRecord.CLASSIFIED.value][0])
            for lifecycle in lifecycles.values()
        ),
        key=lambda pair: pair[0].sequence,
    )
    for before, after in paired:
        identity = (
            "request_id",
            "operation",
            "binding_digest",
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "scope",
            "peer_identity_digest",
            "request_digest",
        )
        if any(before.payload[name] != after.payload[name] for name in identity) or before.sequence >= after.sequence:
            raise InvalidReceiptError("Board-broker request lifecycle changes identity")
        requests.append(
            {
                "reservation_sequence": before.sequence,
                "classification_sequence": after.sequence,
                "request_id": after.payload["request_id"],
                "operation": after.payload["operation"],
                "scope": after.payload["scope"],
                "binding_digest": after.payload["binding_digest"],
                "binding": {
                    name: after.payload[name]
                    for name in ("run_id", "boot_id", "generation_id", "lane_id", "attempt_id", "step_id")
                },
                "caller_identity_digest": after.payload["peer_identity_digest"],
                "request_digest": after.payload["request_digest"],
                "classification": after.payload["outcome"],
                "endpoint": after.payload["endpoint"],
                "http_status": after.payload["http_status"],
                "response": {
                    "digest": after.payload["response_digest"],
                    "sealed_digest": after.blob_digest,
                    "original_bytes": after.payload["response_original_bytes"],
                    "sealed_bytes": after.blob_bytes,
                    "truncated": after.payload["response_truncated"],
                    "lost_bytes": after.payload["response_lost_bytes"],
                },
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": "board-broker",
        "run_id": run_id,
        "requests": requests,
        "secret_leak_probes": _secret_leak_probes(events),
        "chain_head": events[-1].event_digest,
        "manifest_link": {
            "row_id": "core.brokered-credentials-egress",
            "receipt_ref": "receipt:board-broker",
        },
    }


def _secret_leak_probes(events: Sequence[object]) -> dict[str, str]:
    capability = [event for event in events if event.event_type == CAPABILITY_CUSTODY_RECORDED]
    probes = {
        event.payload["probe_kind"]: event.payload["probe_result"]
        for event in capability
        if event.payload["record"] == CapabilityRecord.PROBE_RECORDED.value
    }
    expected = {"memory", "environment", "argv", "file", "event", "socket"}
    if set(probes) != expected or any(
        result != ("refused" if kind == "socket" else "clear") for kind, result in probes.items()
    ):
        raise InvalidReceiptError("Board-broker receipt needs every executor secret probe clear")
    return probes


__all__ = ["receipt_document"]
