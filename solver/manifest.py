"""Typed boundary for release-candidate manifest drafts.

The glossary's **Release-candidate manifest** is the sealed declaration selected for
one exact image.  This module owns its earlier ``provisional`` draft form only.  A
draft is explicitly typed as ``release-candidate-manifest-draft`` and cannot validate
as the sealed artifact; later release work owns sealing and Gate claims.

Standard library only: this module is part of the Solver image.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import cast

from solver.event_store_storage import canonical_bytes
from solver.manifest_contracts import (
    CORE_REQUIREMENT_IDS,
    DRAFT_MANIFEST_KIND,
    MANIFEST_SCHEMA_RECEIPT_REF,
    MANIFEST_SCHEMA_ROW_ID,
    PACK_REQUIREMENT_IDS,
    PROVISIONAL,
    SCHEMA_VERSION,
    ManifestReceipt,
    ManifestValidationError,
    ReleaseCandidateManifestDraft,
    ReleaseCandidateProfile,
    RequirementRow,
)

from solver.manifest_values import (
    copy_json as _copy_json,
    fail as _fail,
)
from solver.manifest_schema import MANIFEST_SCHEMA_RECEIPT_DIGEST, default_requirements, schema_receipt
from solver.manifest_validation import validate_manifest
from solver.manifest_values import digest as _digest
from solver.manifest_values import provisional_identity_basis as _identity_basis


def _ensure_schema_receipt(manifest: dict[str, object]) -> None:
    receipts = manifest.get("receipts")
    if not isinstance(receipts, list):
        _fail("receipts must be a list")
    expected_schema_receipt = schema_receipt()
    matching = [
        item for item in receipts if isinstance(item, Mapping) and item.get("ref") == MANIFEST_SCHEMA_RECEIPT_REF
    ]
    if not matching:
        receipts.append(expected_schema_receipt)
    elif matching != [expected_schema_receipt]:
        _fail("manifest-schema receipt is not the deterministic schema receipt")
    receipts.sort(key=lambda item: item.get("ref", "") if isinstance(item, Mapping) else "")

    requirements = manifest.get("requirements")
    if not isinstance(requirements, list):
        _fail("requirements must be a list")
    schema_rows = [
        item for item in requirements if isinstance(item, Mapping) and item.get("row_id") == MANIFEST_SCHEMA_ROW_ID
    ]
    if len(schema_rows) == 1 and schema_rows[0].get("receipt_ref") != MANIFEST_SCHEMA_RECEIPT_REF:
        _fail("core.manifest-schema must link the manifest-schema receipt")


def generate_manifest(
    *,
    image_digest: str,
    release_candidate_profile: ReleaseCandidateProfile,
    requirements: Sequence[RequirementRow] | None = None,
    receipts: Sequence[ManifestReceipt] | None = None,
) -> ReleaseCandidateManifestDraft:
    """Generate one deterministic provisional release-candidate manifest draft.

    Inputs are copied, not mutated.  The returned identity is derived from every other
    draft field, so changing a profile or evidence reference creates a new identity.
    This function never emits the sealed glossary artifact or a qualification claim.
    """

    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "kind": DRAFT_MANIFEST_KIND,
        "lifecycle": PROVISIONAL,
        "candidate": {"image_digest": image_digest},
        "selected_profile": _copy_json(release_candidate_profile),
        "requirements": _copy_json(list(requirements)) if requirements is not None else default_requirements(),
        "receipts": _copy_json(list(receipts)) if receipts is not None else [],
    }
    _ensure_schema_receipt(manifest)
    identity = _digest(_identity_basis(manifest))
    candidate = cast(dict[str, object], manifest["candidate"])
    candidate["identity"] = f"{PROVISIONAL}:{identity}"
    validate_manifest(manifest)
    return cast(ReleaseCandidateManifestDraft, manifest)


def attach_requirement_receipt(
    manifest: Mapping[str, object],
    row_id: str,
    receipt: ManifestReceipt,
) -> ReleaseCandidateManifestDraft:
    """Return a new draft whose named implemented row links one verified receipt."""

    current = parse_manifest(manifest)
    requirements = _copy_json(current["requirements"])
    receipts = _copy_json(current["receipts"])
    rows = [row for row in requirements if row["row_id"] == row_id]
    if len(rows) != 1:
        _fail(f"manifest has no unique requirement row {row_id!r}")
    row = rows[0]
    previous_ref = row["receipt_ref"]
    if row["status"] in {"planned", "missing"}:
        row["status"] = "implemented"
    row["receipt_ref"] = receipt["ref"]
    row["reason"] = ""
    receipts = [item for item in receipts if item["ref"] != receipt["ref"]]
    receipts.append(_copy_json(receipt))
    referenced = {item["receipt_ref"] for item in requirements if item["receipt_ref"] is not None}
    if previous_ref not in referenced:
        receipts = [item for item in receipts if item["ref"] != previous_ref]
    receipts.sort(key=lambda item: item["ref"])
    if previous_ref == MANIFEST_SCHEMA_RECEIPT_REF and row_id != MANIFEST_SCHEMA_ROW_ID:
        _fail("only the manifest-schema row may replace the schema receipt")
    return generate_manifest(
        image_digest=current["candidate"]["image_digest"],
        release_candidate_profile=current["selected_profile"],
        requirements=requirements,
        receipts=receipts,
    )


def canonical_manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    """Encode a JSON manifest with one deterministic representation."""

    copied = _copy_json(manifest)
    if not isinstance(copied, Mapping):
        _fail("manifest must be an object")
    return canonical_bytes(cast(Mapping[str, object], copied))


def parse_manifest(payload: bytes | str | Mapping[str, object]) -> ReleaseCandidateManifestDraft:
    """Decode and validate canonical JSON or an already decoded draft mapping."""

    if isinstance(payload, Mapping):
        document = _copy_json(payload)
    else:
        try:
            document = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ManifestValidationError(f"manifest is not valid UTF-8 JSON: {error}") from None
    if not isinstance(document, dict):
        _fail("manifest JSON must be an object")
    validate_manifest(cast(Mapping[str, object], document))
    return cast(ReleaseCandidateManifestDraft, document)


def manifest_digest(manifest: Mapping[str, object]) -> str:
    """Return the SHA-256 digest of the canonical manifest bytes."""

    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


__all__ = [
    "CORE_REQUIREMENT_IDS",
    "DRAFT_MANIFEST_KIND",
    "MANIFEST_SCHEMA_RECEIPT_DIGEST",
    "ManifestReceipt",
    "ManifestValidationError",
    "PACK_REQUIREMENT_IDS",
    "ReleaseCandidateManifestDraft",
    "ReleaseCandidateProfile",
    "RequirementRow",
    "SCHEMA_VERSION",
    "attach_requirement_receipt",
    "canonical_manifest_bytes",
    "generate_manifest",
    "manifest_digest",
    "parse_manifest",
    "validate_manifest",
]
