"""Independent versioned reader for v1 Run streams and v2 evidence capsules."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from solver.evidence_capsule_contracts import (
    CAPSULE_KIND,
    CAPSULE_MANIFEST,
    CAPSULE_SCHEMA_VERSION,
    PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN,
    CapsuleInvalid,
    CapsuleRefused,
    ReadEvidence,
)
from solver.event_store_contracts import CANONICAL_SCHEMA_VERSION, event_contract
from solver.event_store_storage import canonical_bytes
from solver.manifest import canonical_manifest_bytes, manifest_digest, parse_manifest
from solver.write_reservation import ReservationState, WriteAuthority


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CapsuleInvalid(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _json(body: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapsuleInvalid(f"{label} is not valid UTF-8 JSON: {error}") from None
    if not isinstance(value, dict):
        raise CapsuleInvalid(f"{label} is not a JSON object")
    return value


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _verify_source(path: Path, source: Mapping[str, Any]) -> frozenset[str]:
    body = path.read_bytes()
    lines = body.splitlines()
    if len(lines) != source.get("event_count") or not lines:
        raise CapsuleInvalid("source event count does not match")
    previous = ""
    run_id = source.get("run_id")
    referenced_blobs: set[str] = set()
    lifecycle_records: list[str] = []
    for expected, line in enumerate(lines, start=1):
        envelope = _json(line, f"source event {expected}")
        if canonical_bytes(envelope) != line:
            raise CapsuleInvalid("source event is not canonically encoded")
        if envelope.get("schema_version") != CANONICAL_SCHEMA_VERSION or envelope.get("seq") != expected:
            raise CapsuleInvalid("source event sequence or schema is invalid")
        if envelope.get("prev_digest") != previous or envelope.get("run_id") != run_id:
            raise CapsuleInvalid("source predecessor or Run identity is invalid")
        event_digest = envelope.get("event_digest")
        unsigned = {key: value for key, value in envelope.items() if key != "event_digest"}
        if not isinstance(event_digest, str) or _digest(canonical_bytes(unsigned)) != event_digest:
            raise CapsuleInvalid("source event digest is invalid")
        contract = event_contract(str(envelope.get("event_type", "")))
        payload = envelope.get("payload")
        if contract is None or not isinstance(payload, dict):
            raise CapsuleInvalid("source event has no supported typed payload")
        try:
            contract.validate_payload(payload, sequence=expected)
        except Exception as error:
            raise CapsuleInvalid(f"source event payload is invalid: {error}") from None
        blob_digest = payload.get("blob_digest")
        if not isinstance(blob_digest, str):
            raise CapsuleInvalid("source event has no blob digest")
        referenced_blobs.add(blob_digest)
        if isinstance(payload.get("record"), str):
            lifecycle_records.append(payload["record"])
        previous = event_digest
    first = _json(lines[0], "source genesis")
    last = _json(lines[-1], "source head")
    if (
        first["payload"].get("record") != "run-open"
        or last["payload"].get("record") != "run-close"
        or lifecycle_records.count("run-open") != 1
        or lifecycle_records.count("run-close") != 1
    ):
        raise CapsuleInvalid("source is not a complete terminal Run")
    if previous != source.get("chain_head"):
        raise CapsuleInvalid("source chain head does not match")
    if source.get("first_sequence") != 1 or source.get("last_sequence") != len(lines):
        raise CapsuleInvalid("source sequence bounds do not match")
    return frozenset(referenced_blobs)


def verify_evidence_content(path: Path) -> ReadEvidence:
    """Explicitly verify capsule bytes for forensic use, without a Promotion verdict."""

    if path.is_file():
        return ReadEvidence(schema_version=1, kind="v1-run-jsonl", path=path, raw=path.read_bytes())
    manifest_path = path / CAPSULE_MANIFEST
    try:
        manifest_body = manifest_path.read_bytes()
    except OSError as error:
        raise CapsuleInvalid("capsule manifest is unavailable") from error
    document = _json(manifest_body, CAPSULE_MANIFEST)
    if canonical_bytes(document) != manifest_body:
        raise CapsuleInvalid("capsule manifest is not canonically encoded")
    if document.get("schema_version") != CAPSULE_SCHEMA_VERSION or document.get("kind") != CAPSULE_KIND:
        raise CapsuleInvalid("unsupported evidence capsule version or kind")
    files = document.get("files")
    if not isinstance(files, dict):
        raise CapsuleInvalid("capsule file inventory is absent")
    content_basis = document.get("content_basis")
    publication_basis = document.get("publication_basis")
    if (
        not isinstance(content_basis, dict)
        or not isinstance(content_basis.get("blobs"), list)
        or not isinstance(publication_basis, dict)
    ):
        raise CapsuleInvalid("capsule identity basis or blob closure is absent")
    actual = {item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file() and item != manifest_path}
    expected_files = {"receipt.json", "candidate-manifest.json", "source/events.jsonl"} | {
        f"blobs/{item.get('digest')}" for item in content_basis["blobs"] if isinstance(item, dict)
    }
    if actual != set(files) or actual != expected_files:
        raise CapsuleInvalid("capsule file inventory is incomplete or has extras")
    for relative, expected in files.items():
        if not isinstance(relative, str) or not isinstance(expected, str):
            raise CapsuleInvalid("capsule file inventory is malformed")
        if _digest((path / relative).read_bytes()) != expected:
            raise CapsuleInvalid(f"capsule file digest differs for {relative}")

    content_digest = _digest(canonical_bytes(content_basis))
    content_ref = f"capsule-content:{content_digest}"
    if document.get("content_ref") != content_ref:
        raise CapsuleInvalid("capsule content identity differs")
    capsule_id = _digest(canonical_bytes(publication_basis))
    if document.get("capsule_id") != capsule_id or path.name != capsule_id:
        raise CapsuleInvalid("capsule publication identity differs")

    receipt_body = (path / "receipt.json").read_bytes()
    receipt = _json(receipt_body, "receipt")
    if canonical_bytes(receipt) != receipt_body:
        raise CapsuleInvalid("receipt is not canonically encoded")
    receipt_basis = content_basis.get("receipt")
    if not isinstance(receipt_basis, dict) or _digest(receipt_body) != receipt_basis.get("digest"):
        raise CapsuleInvalid("receipt digest differs from capsule content")
    for field in ("kind", "schema_version", "producer"):
        if receipt.get(field) != receipt_basis.get(field):
            raise CapsuleInvalid(f"receipt {field} differs from capsule content")

    candidate_body = (path / "candidate-manifest.json").read_bytes()
    candidate = parse_manifest(_json(candidate_body, "candidate manifest"))
    if canonical_manifest_bytes(candidate) != candidate_body:
        raise CapsuleInvalid("candidate manifest is not canonically encoded")
    if manifest_digest(candidate) != document.get("candidate_manifest_digest"):
        raise CapsuleInvalid("candidate manifest digest differs")
    if candidate["candidate"]["identity"] != document.get("candidate_identity"):
        raise CapsuleInvalid("candidate identity differs")
    link = document.get("manifest_link")
    if not isinstance(link, dict):
        raise CapsuleInvalid("candidate manifest link is absent")
    rows = [row for row in candidate["requirements"] if row["row_id"] == link.get("row_id")]
    if len(rows) != 1 or rows[0]["receipt_ref"] != link.get("receipt_ref"):
        raise CapsuleInvalid("candidate row does not point back to the capsule receipt")
    capsule_refs = [ref for ref in rows[0]["evidence_refs"] if ref.startswith("capsule-content:")]
    if capsule_refs != [content_ref]:
        raise CapsuleInvalid("candidate row does not point back to exactly this capsule content")
    receipts = [item for item in candidate["receipts"] if item["ref"] == link.get("receipt_ref")]
    if (
        len(receipts) != 1
        or receipts[0].get("digest") != link.get("receipt_digest")
        or receipts[0].get("digest") != receipt_basis.get("digest")
        or link.get("receipt_ref") != receipt_basis.get("ref")
    ):
        raise CapsuleInvalid("candidate receipt descriptor differs from capsule link")
    if receipts[0].get("kind") != content_basis.get("receipt", {}).get("kind"):
        raise CapsuleInvalid("candidate receipt kind differs from capsule content")
    source = content_basis.get("source")
    if not isinstance(source, dict):
        raise CapsuleInvalid("source closure is absent")
    referenced_blobs = _verify_source(path / "source" / "events.jsonl", source)
    selected_blobs: set[str] = set()
    for blob in content_basis.get("blobs", []):
        if not isinstance(blob, dict) or not isinstance(blob.get("digest"), str):
            raise CapsuleInvalid("blob closure is malformed")
        body = (path / "blobs" / blob["digest"]).read_bytes()
        if _digest(body) != blob["digest"] or len(body) != blob.get("bytes"):
            raise CapsuleInvalid("blob closure content differs")
        selected_blobs.add(blob["digest"])
    excluded = content_basis.get("excluded_source_blobs")
    if not isinstance(excluded, list) or any(
        not isinstance(item, dict) or item.get("classification") != "canonical-private-body" for item in excluded
    ):
        raise CapsuleInvalid("excluded source blob classification is malformed")
    excluded_blobs = {str(item["digest"]) for item in excluded}
    if selected_blobs & excluded_blobs or selected_blobs | excluded_blobs != referenced_blobs:
        raise CapsuleInvalid("selected and excluded blobs do not close the canonical source")
    expected_publication = {
        "domain": "incypher.evidence-capsule.publication.v1",
        "content_ref": content_ref,
        "candidate_manifest_digest": document["candidate_manifest_digest"],
        "candidate_identity": document["candidate_identity"],
        "reservation": document.get("reservation"),
    }
    if publication_basis != expected_publication:
        raise CapsuleInvalid("publication identity basis differs")
    return ReadEvidence(
        schema_version=CAPSULE_SCHEMA_VERSION,
        kind=CAPSULE_KIND,
        path=path,
        capsule_id=capsule_id,
        manifest=cast(Mapping[str, Any], document),
    )


def read_evidence(path: Path, authority: WriteAuthority | None = None) -> ReadEvidence:
    """Read v1 evidence or an authority-committed versioned capsule."""

    evidence = verify_evidence_content(path)
    if evidence.kind == "v1-run-jsonl":
        return evidence
    if authority is None:
        raise CapsuleRefused(
            "capsule bytes verify, but authoritative capsule reading requires its WriteAuthority",
            classification=PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN,
        )
    return _require_committed_authority(evidence, authority)


def _require_committed_authority(evidence: ReadEvidence, authority: WriteAuthority) -> ReadEvidence:
    """Read promoted evidence only where its exact authority durably committed."""

    reservation = evidence.manifest.get("reservation") if evidence.manifest else None
    if not isinstance(reservation, dict) or not isinstance(reservation.get("key"), str):
        raise CapsuleInvalid("capsule has no authority reservation identity")
    current = authority.current(reservation["key"])
    current_identity = (
        {
            "key": current.key,
            "effect_fingerprint": current.effect_fingerprint,
            "boot_id": current.boot_id,
            "object_slots": list(current.object_slots),
            "pool": current.pool.value,
            "need": current.need.as_dict(),
            "retention": current.retention.value,
        }
        if current is not None
        else None
    )
    if current is None or current_identity != reservation or current.state is not ReservationState.COMMITTED:
        raise CapsuleRefused(
            "capsule bytes verify, but publication authority is not durably committed",
            classification=PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN,
        )
    return evidence


def read_promoted_evidence(path: Path, authority: WriteAuthority) -> ReadEvidence:
    """Named authoritative capsule-read alias for callers at promotion seams."""

    return read_evidence(path, authority)


__all__ = ["read_evidence", "read_promoted_evidence", "verify_evidence_content"]
