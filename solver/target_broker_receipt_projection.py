"""Pure Target-broker receipt projection."""

from __future__ import annotations

from collections.abc import Sequence

from solver.attempt_executor_contracts import NETWORK_PROBE_KINDS
from solver.event_store import InvalidReceiptError
from solver.event_store_contracts import CAPABILITY_CUSTODY_RECORDED
from solver.target_broker_contracts import SCHEMA_VERSION, TARGET_EXCHANGE_RECORDED, TargetRecord

_REQUEST_IDENTITY = (
    "request_id",
    "binding_digest",
    "run_id",
    "boot_id",
    "generation_id",
    "lane_id",
    "attempt_id",
    "step_id",
    "challenge_id",
    "image_id",
    "image_manifest_digest",
    "image_config_digest",
    "platform",
    "profile_digest",
    "endpoint",
    "resolved_address",
    "protocol",
    "max_connections",
    "max_request_bytes",
    "max_response_bytes",
    "timeout_ms",
    "request_digest",
    "observation_sequence",
    "observation_digest",
    "probe_kind",
    "attempted_endpoint_digest",
)


def receipt_document(run_id: str, events: Sequence[object]) -> dict[str, object]:
    target = [event for event in events if event.event_type == TARGET_EXCHANGE_RECORDED]
    candidate = _candidate_binding(target)
    requests = _requests(target)
    probes = _verify_deny_probes(requests, events)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "target-broker",
        "producer": "target-broker",
        "receipt_type": "target-broker",
        "run_id": run_id,
        "candidate": candidate,
        "profile_digest": target[0].payload["profile_digest"],
        "requests": requests,
        "deny_probes": probes,
        "generation_accounting": _accounting(requests),
        "revocation_trace": _revocations(events),
        "chain_head": events[-1].event_digest,
        "manifest_link": {"row_id": "core.board-target-lease", "receipt_ref": "receipt:target-broker"},
    }


def _candidate_binding(target: list[object]) -> dict[str, object]:
    if not target:
        raise InvalidReceiptError("Target-broker receipt has no requests")
    fields = ("image_id", "image_manifest_digest", "image_config_digest", "platform", "profile_digest")
    if len({tuple(event.payload[name] for name in fields) for event in target}) != 1:
        raise InvalidReceiptError("Target-broker receipt spans candidate bindings")
    payload = target[0].payload
    return {
        "image_id": payload["image_id"],
        "manifest_digest": payload["image_manifest_digest"],
        "config_digest": payload["image_config_digest"],
        "platform": payload["platform"],
    }


def _requests(target: list[object]) -> list[dict[str, object]]:
    lifecycles: dict[str, list[object]] = {}
    for event in target:
        lifecycles.setdefault(event.payload["request_id"], []).append(event)
    return [
        _request_document(request_id, lifecycle)
        for request_id, lifecycle in sorted(lifecycles.items(), key=lambda item: item[1][0].sequence)
    ]


def _request_document(request_id: str, lifecycle: list[object]) -> dict[str, object]:
    if len(lifecycle) != 2:
        raise InvalidReceiptError("Target-broker request lifecycle is incomplete")
    reserved, classified = lifecycle
    if (
        reserved.payload["record"] != TargetRecord.RESERVED.value
        or classified.payload["record"] != TargetRecord.CLASSIFIED.value
    ):
        raise InvalidReceiptError("Target-broker request was not reserved before classification")
    if any(reserved.payload[name] != classified.payload[name] for name in _REQUEST_IDENTITY):
        raise InvalidReceiptError("Target-broker request identity changed")
    if classified.payload["transcript_digest"] != classified.blob_digest:
        raise InvalidReceiptError("Target-broker transcript digest does not match its sealed transcript")
    payload = classified.payload
    return {
        "request_id": request_id,
        "reservation_sequence": reserved.sequence,
        "classification_sequence": classified.sequence,
        "binding": {
            name: payload[name] for name in ("run_id", "boot_id", "generation_id", "lane_id", "attempt_id", "step_id")
        },
        "challenge_id": payload["challenge_id"],
        "declared_endpoint": payload["endpoint"],
        "resolved_address": payload["resolved_address"],
        "protocol": payload["protocol"],
        "bounds": {
            "connections": payload["max_connections"],
            "request_bytes": payload["max_request_bytes"],
            "response_bytes": payload["max_response_bytes"],
            "timeout_ms": payload["timeout_ms"],
        },
        "classification": payload["outcome"],
        "request_bytes": payload["request_bytes"],
        "response_bytes": payload["response_bytes"],
        "elapsed_ms": payload["elapsed_ms"],
        "transcript_digest": payload["transcript_digest"],
        "sealed_transcript_digest": classified.blob_digest,
        "probe_kind": payload["probe_kind"],
        "attempted_endpoint_digest": payload["attempted_endpoint_digest"],
        "observation_sequence": payload["observation_sequence"],
        "observation_digest": payload["observation_digest"],
    }


def _verify_deny_probes(requests: list[dict[str, object]], events: Sequence[object]) -> dict[str, str]:
    probes = {str(item["probe_kind"]): str(item["classification"]) for item in requests if item["probe_kind"]}
    if set(probes) != NETWORK_PROBE_KINDS or set(probes.values()) != {"denied"}:
        raise InvalidReceiptError("Target-broker receipt lacks deny probes")
    by_sequence = {event.sequence: event for event in events}
    used = set()
    for request in (item for item in requests if item["probe_kind"]):
        observed = by_sequence.get(request["observation_sequence"])
        declaration = {"kind": request["probe_kind"], "attempted_endpoint_digest": request["attempted_endpoint_digest"]}
        binding = request["binding"]
        if (
            observed is None
            or observed.event_digest != request["observation_digest"]
            or request["observation_digest"] in used
            or observed.event_type != "attempt-envelope.recorded"
            or observed.payload["record"] != "result"
            or observed.payload["outcome"] != "network-limit"
            or observed.payload["network_probe"] != declaration
            or not any(
                event.event_type == "attempt-envelope.recorded"
                and event.payload["record"] == "reserved"
                and event.payload["envelope_id"] == observed.payload["envelope_id"]
                and event.payload["network_probe"] == declaration
                for event in events
            )
            or any(observed.payload[name] != binding[name] for name in ("generation_id", "attempt_id", "step_id"))
        ):
            raise InvalidReceiptError("Target-broker denial lacks its observed hostile network breach")
        used.add(request["observation_digest"])
    return probes


def _accounting(requests: list[dict[str, object]]) -> dict[str, dict[str, int]]:
    accounting: dict[str, dict[str, int]] = {}
    for request in (item for item in requests if not item["probe_kind"]):
        generation = str(request["binding"]["generation_id"])
        row = accounting.setdefault(
            generation, {"connections": 0, "request_bytes": 0, "response_bytes": 0, "elapsed_ms": 0}
        )
        if request["classification"] != "budget-exhausted" and not (
            request["classification"] in {"too-large", "denied"} and request["request_bytes"] == 0
        ):
            row["connections"] += 1
        for field in ("request_bytes", "response_bytes", "elapsed_ms"):
            row[field] += int(request[field])
        bounds = request["bounds"]
        if (
            row["connections"] > bounds["connections"]
            or any(request[field] > bounds[field] for field in ("request_bytes", "response_bytes"))
            or request["elapsed_ms"] > bounds["timeout_ms"]
        ):
            raise InvalidReceiptError("Target-broker accounting exceeds its sealed bounds")
    return accounting


def _revocations(events: Sequence[object]) -> list[dict[str, object]]:
    return [
        {
            "sequence": event.sequence,
            "record": event.payload["record"],
            "generation_id": event.payload["generation_id"],
            "reason": event.payload["reason"],
        }
        for event in events
        if event.event_type == CAPABILITY_CUSTODY_RECORDED
        and event.payload["scope"] == "target.exchange"
        and event.payload["record"] in {"issued", "revoked", "denied"}
    ]


__all__ = ["receipt_document"]
