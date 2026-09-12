"""Independent forensic verifier and authority-gated versioned evidence reader."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from solver.evidence_capsule_authority import reservation_identity
from solver.evidence_capsule_contracts import (
    CAPSULE_KIND,
    CAPSULE_MANIFEST,
    CAPSULE_SCHEMA_VERSION,
    PUBLICATION_DOMAIN,
    PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN,
    CapsuleInvalid,
    CapsuleRefused,
    ReadEvidence,
)
from solver.event_store_contracts import CANONICAL_SCHEMA_VERSION, LifecycleRecord, event_contract
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.manifest import canonical_manifest_bytes, manifest_digest, parse_manifest
from solver.strict_json import StrictJSONError, strict_json_object
from solver.write_reservation import ReservationState, WriteAuthority


def _json(body: bytes, label: str) -> dict[str, Any]:
    try:
        return strict_json_object(body, label=label)
    except StrictJSONError as error:
        raise CapsuleInvalid(str(error)) from None


def _load_capsule(path: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest_path = path / CAPSULE_MANIFEST
    try:
        body = manifest_path.read_bytes()
    except OSError as error:
        raise CapsuleInvalid("capsule manifest is unavailable") from error
    document = _json(body, CAPSULE_MANIFEST)
    if canonical_bytes(document) != body:
        raise CapsuleInvalid("capsule manifest is not canonically encoded")
    if document.get("schema_version") != CAPSULE_SCHEMA_VERSION or document.get("kind") != CAPSULE_KIND:
        raise CapsuleInvalid("unsupported evidence capsule version or kind")
    content, publication = document.get("content_basis"), document.get("publication_basis")
    if not isinstance(content, dict) or not isinstance(content.get("blobs"), list) or not isinstance(publication, dict):
        raise CapsuleInvalid("capsule identity basis or blob closure is absent")
    return document, content, publication


def _verify_file_inventory(path: Path, document: Mapping[str, Any], content: Mapping[str, Any]) -> None:
    files = document.get("files")
    if not isinstance(files, dict):
        raise CapsuleInvalid("capsule file inventory is absent")
    manifest_path = path / CAPSULE_MANIFEST
    actual = {item.relative_to(path).as_posix() for item in path.rglob("*") if item.is_file() and item != manifest_path}
    expected = {"receipt.json", "candidate-manifest.json", "source/events.jsonl"} | {
        f"blobs/{item.get('digest')}" for item in content["blobs"] if isinstance(item, dict)
    }
    if actual != set(files) or actual != expected:
        raise CapsuleInvalid("capsule file inventory is incomplete or has extras")
    for relative, expected_digest in files.items():
        if not isinstance(relative, str) or not isinstance(expected_digest, str):
            raise CapsuleInvalid("capsule file inventory is malformed")
        if digest_bytes((path / relative).read_bytes()) != expected_digest:
            raise CapsuleInvalid(f"capsule file digest differs for {relative}")


def _verify_identities(
    path: Path,
    document: Mapping[str, Any],
    content: Mapping[str, Any],
    publication: Mapping[str, Any],
) -> tuple[str, str]:
    content_ref = f"capsule-content:{digest_bytes(canonical_bytes(content))}"
    if document.get("content_ref") != content_ref:
        raise CapsuleInvalid("capsule content identity differs")
    capsule_id = digest_bytes(canonical_bytes(publication))
    if document.get("capsule_id") != capsule_id or path.name != capsule_id:
        raise CapsuleInvalid("capsule publication identity differs")
    return content_ref, capsule_id


def _verify_receipt(path: Path, content: Mapping[str, Any]) -> dict[str, Any]:
    body = (path / "receipt.json").read_bytes()
    receipt = _json(body, "receipt")
    if canonical_bytes(receipt) != body:
        raise CapsuleInvalid("receipt is not canonically encoded")
    basis = content.get("receipt")
    if not isinstance(basis, dict) or digest_bytes(body) != basis.get("digest"):
        raise CapsuleInvalid("receipt digest differs from capsule content")
    for field in ("kind", "schema_version", "producer"):
        if receipt.get(field) != basis.get(field):
            raise CapsuleInvalid(f"receipt {field} differs from capsule content")
    return basis


def _verify_candidate_link(
    path: Path,
    document: Mapping[str, Any],
    content_ref: str,
    receipt_basis: Mapping[str, Any],
) -> None:
    body = (path / "candidate-manifest.json").read_bytes()
    candidate = parse_manifest(_json(body, "candidate manifest"))
    if canonical_manifest_bytes(candidate) != body:
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
        or receipts[0].get("kind") != receipt_basis.get("kind")
    ):
        raise CapsuleInvalid("candidate receipt descriptor differs from capsule link")


def _verify_source(path: Path, source: Mapping[str, Any]) -> frozenset[str]:
    lines = path.read_bytes().splitlines()
    if len(lines) != source.get("event_count") or not lines:
        raise CapsuleInvalid("source event count does not match")
    previous, run_id = "", source.get("run_id")
    blobs: set[str] = set()
    records: list[str] = []
    for expected, line in enumerate(lines, start=1):
        envelope = _json(line, f"source event {expected}")
        unsigned = {key: value for key, value in envelope.items() if key != "event_digest"}
        event_digest, payload = envelope.get("event_digest"), envelope.get("payload")
        if canonical_bytes(envelope) != line or envelope.get("schema_version") != CANONICAL_SCHEMA_VERSION:
            raise CapsuleInvalid("source event encoding or schema is invalid")
        if (
            envelope.get("seq") != expected
            or envelope.get("prev_digest") != previous
            or envelope.get("run_id") != run_id
        ):
            raise CapsuleInvalid("source sequence, predecessor, or Run identity is invalid")
        if not isinstance(event_digest, str) or digest_bytes(canonical_bytes(unsigned)) != event_digest:
            raise CapsuleInvalid("source event digest is invalid")
        contract = event_contract(str(envelope.get("event_type", "")))
        if contract is None or not isinstance(payload, dict):
            raise CapsuleInvalid("source event has no supported typed payload")
        try:
            contract.validate_payload(payload, sequence=expected)
        except Exception as error:
            raise CapsuleInvalid(f"source event payload is invalid: {error}") from None
        if not isinstance(payload.get("blob_digest"), str):
            raise CapsuleInvalid("source event has no blob digest")
        blobs.add(payload["blob_digest"])
        if isinstance(payload.get("record"), str):
            records.append(payload["record"])
        previous = event_digest
    if (
        records.count(LifecycleRecord.RUN_OPEN.value) != 1
        or records.count(LifecycleRecord.RUN_CLOSE.value) != 1
        or records[0] != LifecycleRecord.RUN_OPEN.value
        or records[-1] != LifecycleRecord.RUN_CLOSE.value
    ):
        raise CapsuleInvalid("source is not a complete terminal Run")
    if (
        previous != source.get("chain_head")
        or source.get("first_sequence") != 1
        or source.get("last_sequence") != len(lines)
    ):
        raise CapsuleInvalid("source chain head or sequence bounds do not match")
    return frozenset(blobs)


def _verify_blob_closure(path: Path, content: Mapping[str, Any]) -> None:
    source = content.get("source")
    if not isinstance(source, dict):
        raise CapsuleInvalid("source closure is absent")
    referenced = _verify_source(path / "source" / "events.jsonl", source)
    selected: set[str] = set()
    for blob in content["blobs"]:
        if not isinstance(blob, dict) or not isinstance(blob.get("digest"), str):
            raise CapsuleInvalid("blob closure is malformed")
        body = (path / "blobs" / blob["digest"]).read_bytes()
        if digest_bytes(body) != blob["digest"] or len(body) != blob.get("bytes"):
            raise CapsuleInvalid("blob closure content differs")
        selected.add(blob["digest"])
    excluded = content.get("excluded_source_blobs")
    allowed_exclusions = {"canonical-private-body", "restart-private-broker"}
    if not isinstance(excluded, list) or any(
        not isinstance(item, dict)
        or set(item) != {"digest", "classification"}
        or item.get("classification") not in allowed_exclusions
        or not isinstance(item.get("digest"), str)
        for item in excluded
    ):
        raise CapsuleInvalid("excluded source blob classification is malformed")
    canonical_excluded = {
        str(item["digest"]) for item in excluded if item["classification"] == "canonical-private-body"
    }
    private_excluded = {str(item["digest"]) for item in excluded if item["classification"] == "restart-private-broker"}
    source_events = [json.loads(line) for line in (path / "source" / "events.jsonl").read_bytes().splitlines()]
    private_referenced = {
        str(event["payload"]["raw_blob_digest"])
        for event in source_events
        if event.get("event_type") == "board-broker.recorded"
        and event.get("payload", {}).get("operation") == "intake-read"
        and event.get("payload", {}).get("raw_blob_digest")
    }
    if (
        selected & (canonical_excluded | private_excluded)
        or selected | canonical_excluded != referenced
        or private_excluded != private_referenced - selected - canonical_excluded
    ):
        raise CapsuleInvalid("selected and excluded blobs do not close the canonical source")


def _verify_publication(document: Mapping[str, Any], publication: Mapping[str, Any], content_ref: str) -> None:
    expected = {
        "domain": PUBLICATION_DOMAIN,
        "content_ref": content_ref,
        "candidate_manifest_digest": document["candidate_manifest_digest"],
        "candidate_identity": document["candidate_identity"],
        "reservation": document.get("reservation"),
    }
    if publication != expected:
        raise CapsuleInvalid("publication identity basis differs")


def verify_evidence_content(path: Path) -> ReadEvidence:
    """Explicitly verify capsule bytes for forensic use, without a Promotion verdict."""

    if path.is_file():
        return ReadEvidence(schema_version=1, kind="v1-run-jsonl", path=path, raw=path.read_bytes())
    document, content, publication = _load_capsule(path)
    _verify_file_inventory(path, document, content)
    content_ref, capsule_id = _verify_identities(path, document, content, publication)
    receipt_basis = _verify_receipt(path, content)
    _verify_candidate_link(path, document, content_ref, receipt_basis)
    _verify_blob_closure(path, content)
    _verify_publication(document, publication, content_ref)
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
    reservation = evidence.manifest.get("reservation") if evidence.manifest else None
    if not isinstance(reservation, dict) or not isinstance(reservation.get("key"), str):
        raise CapsuleInvalid("capsule has no authority reservation identity")
    current = authority.current(reservation["key"])
    if (
        current is None
        or reservation_identity(current) != reservation
        or current.state is not ReservationState.COMMITTED
    ):
        raise CapsuleRefused(
            "capsule bytes verify, but publication authority is not durably committed",
            classification=PUBLICATION_PRESENT_AUTHORITY_UNCERTAIN,
        )
    return evidence


__all__ = ["read_evidence", "verify_evidence_content"]
