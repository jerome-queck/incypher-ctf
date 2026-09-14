"""Canonical Research-broker receipt and independent verification."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path

from solver.event_store import EventStore, InvalidReceiptError
from solver.event_store_contracts import CommittedEvent
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.research_broker_contracts import RESEARCH_BROKER_RECORDED

RECEIPT_FILENAME = "research-broker.receipt.json"
RECEIPT_TYPE = "research-broker"
RECEIPT_REF = "receipt:research-broker"
MANIFEST_ROW_ID = "core.brokered-credentials-egress"


def receipt_document(run_id: str, events: Sequence[CommittedEvent]) -> dict[str, object]:
    requests = []
    for event in events:
        if event.event_type != RESEARCH_BROKER_RECORDED:
            continue
        row = event.payload
        requests.append(
            {
                "sequence": event.sequence,
                "request_id": row["request_id"],
                "generation_id": row["generation_id"],
                "attempt_id": row["attempt_id"],
                "url_digest": row["url_digest"],
                "policy_decision": row["outcome"],
                "dns_chain": row["dns_chain"],
                "redirect_chain": row["redirect_chain"],
                "sealed_body_digest": "sha256:" + event.blob_digest,
                "sealed_body_bytes": event.blob_bytes,
                "content_type": row["content_type"],
                "observed_at": row["observed_at"],
                "expires_at": row["expires_at"],
                "cached": row["cached"],
                "query": {
                    "kind": row["kind"],
                    "source_id": row["source_id"],
                    "terms": row["terms"],
                    "robots": row["robots"],
                    "origin": row["origin"],
                    "subject_digest": row["query_digest"],
                },
                "limits": {
                    "max_body_bytes": row["max_body_bytes"],
                    "timeout_ms": row["timeout_ms"],
                    "max_redirects": row["max_redirects"],
                    "cache_seconds": row["cache_seconds"],
                    "requests": row["max_requests"],
                    "total_bytes": row["max_total_bytes"],
                    "total_seconds_ms": row["max_total_seconds_ms"],
                    "min_interval_ms": row["min_interval_ms"],
                },
            }
        )
    if not requests:
        raise InvalidReceiptError("Research-broker receipt has no canonical requests")
    if not any(request["policy_decision"] == "denied" for request in requests):
        raise InvalidReceiptError("Research-broker receipt has no denied-destination probe")
    return {
        "schema_version": 1,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "requests": requests,
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF},
    }


def write_receipt(state: Path, run_id: str) -> Path:
    document = receipt_document(run_id, EventStore(state, run_id=run_id).events())
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    path = Path(path)
    try:
        raw = path.read_bytes()
        supplied = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidReceiptError("Research-broker receipt cannot be read") from error
    if not isinstance(supplied, Mapping) or raw != canonical_bytes(supplied) + b"\n":
        raise InvalidReceiptError("Research-broker receipt is not canonical JSON")
    if path.name != RECEIPT_FILENAME or path.parent.name != "canonical":
        raise InvalidReceiptError("Research-broker receipt path is invalid")
    run_dir = path.parent.parent
    expected = receipt_document(run_dir.name, EventStore(run_dir.parent.parent, run_id=run_dir.name).events())
    if dict(supplied) != expected:
        raise InvalidReceiptError("Research-broker receipt does not match canonical state")
    return path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


__all__ = ["link_manifest", "manifest_receipt", "receipt_document", "verify_receipt", "write_receipt"]
