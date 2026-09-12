"""Receipt projection and manifest linkage for canonical CPA Harness facts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from solver.cpa_contracts import CPAConfig, CPA_HARNESS_RECORDED, SERVICE_VERSION
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.manifest import attach_requirement_receipt


def config_digest(config: CPAConfig) -> str:
    return hashlib.sha256(canonical_bytes(config.document())).hexdigest()


def build_cpa_receipt(store: EventStore, config: CPAConfig) -> dict[str, object]:
    events = [event for event in store.events() if event.event_type == CPA_HARNESS_RECORDED]
    digest = config_digest(config)
    if not events or any(event.payload["config_digest"] != digest for event in events):
        raise ValueError("canonical CPA facts do not match configuration")
    rows = [
        {
            "sequence": event.sequence,
            "record": event.payload["record"],
            "request_id": event.payload["request_id"],
            "status": event.payload["status"],
            "peer_digest": event.payload["peer_digest"],
            "turn_count": event.payload["turn_count"],
            "tool_count": event.payload["tool_count"],
            "measured": event.payload["measured"],
            "model": event.payload["model"],
            "duration_ms": event.payload["duration_ms"],
            "tokens_in": event.payload["tokens_in"],
            "tokens_out": event.payload["tokens_out"],
            "proposal_kind": event.payload["proposal_kind"],
            "proposal_digest": event.payload["proposal_digest"],
            "detail": event.payload["detail"],
        }
        for event in events
    ]
    return {
        "schema_version": 1,
        "receipt_type": "cpa-harness",
        "attestation": {
            "service_version": SERVICE_VERSION,
            "config_digest": digest,
            "credential_mode": config.credential_mode,
        },
        "config": config.document(),
        "bounds": {"loop": 1, "turns": config.max_turns, "tools": config.max_tools},
        "audit": rows,
        "event_chain_head": events[-1].event_digest,
    }


def _validate_structure(receipt: object) -> None:
    if (
        not isinstance(receipt, Mapping)
        or receipt.get("schema_version") != 1
        or receipt.get("receipt_type") != "cpa-harness"
    ):
        raise ValueError("CPA receipt identity is invalid")
    config_document = receipt.get("config")
    attestation = receipt.get("attestation")
    if not isinstance(config_document, Mapping) or not isinstance(attestation, Mapping):
        raise ValueError("CPA receipt attestation is incomplete")
    expected = hashlib.sha256(canonical_bytes(config_document)).hexdigest()
    if attestation.get("config_digest") != expected:
        raise ValueError("CPA receipt config digest is invalid")
    if attestation.get("service_version") != SERVICE_VERSION or attestation.get(
        "credential_mode"
    ) != config_document.get("credential_mode"):
        raise ValueError("CPA receipt service attestation is invalid")
    if receipt.get("bounds") != {
        "loop": 1,
        "turns": config_document.get("max_turns"),
        "tools": config_document.get("max_tools"),
    }:
        raise ValueError("CPA receipt bounds are invalid")
    audit = receipt.get("audit")
    if not isinstance(audit, list) or not audit or any(not isinstance(row, Mapping) for row in audit):
        raise ValueError("CPA receipt audit is invalid")


def validate_cpa_receipt(receipt: object, *, store: EventStore, config: CPAConfig) -> None:
    _validate_structure(receipt)
    if receipt != build_cpa_receipt(store, config):
        raise ValueError("CPA receipt disagrees with canonical state")


def manifest_receipt(receipt: dict[str, object], *, store: EventStore, config: CPAConfig) -> dict[str, str]:
    validate_cpa_receipt(receipt, store=store, config=config)
    return {
        "ref": "receipt:cpa-harness",
        "kind": "cpa-harness",
        "digest": hashlib.sha256(canonical_bytes(receipt)).hexdigest(),
    }


def attach_cpa_receipt(
    manifest: Mapping[str, object],
    receipt: dict[str, object],
    *,
    store: EventStore,
    config: CPAConfig,
):
    return attach_requirement_receipt(
        manifest,
        "core.inference-cpa",
        manifest_receipt(receipt, store=store, config=config),
    )


__all__ = ["attach_cpa_receipt", "build_cpa_receipt", "config_digest", "manifest_receipt", "validate_cpa_receipt"]
