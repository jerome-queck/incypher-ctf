"""Deterministic provisional manifest schema receipt and default ledger."""

import hashlib

from solver.event_store_storage import canonical_bytes
from solver.manifest_contracts import (
    CONTRACT_FIELDS,
    CORE_REQUIREMENT_IDS,
    CORE_TITLES,
    DRAFT_MANIFEST_KIND,
    MANIFEST_SCHEMA_RECEIPT_KIND,
    MANIFEST_SCHEMA_RECEIPT_REF,
    MANIFEST_SCHEMA_ROW_ID,
    PACK_REQUIREMENT_IDS,
    PACK_TITLES,
    SCHEMA_VERSION,
    ManifestReceipt,
)

_DESCRIPTOR = {
    "schema_version": SCHEMA_VERSION,
    "draft_kind": DRAFT_MANIFEST_KIND,
    "contract_fields": {name: sorted(fields) for name, fields in sorted(CONTRACT_FIELDS.items())},
    "core_row_ids": list(CORE_REQUIREMENT_IDS),
    "pack_row_ids": list(PACK_REQUIREMENT_IDS),
}
MANIFEST_SCHEMA_RECEIPT_DIGEST = hashlib.sha256(canonical_bytes(_DESCRIPTOR)).hexdigest()


def schema_receipt() -> ManifestReceipt:
    return {
        "ref": MANIFEST_SCHEMA_RECEIPT_REF,
        "kind": MANIFEST_SCHEMA_RECEIPT_KIND,
        "digest": MANIFEST_SCHEMA_RECEIPT_DIGEST,
    }


def default_requirements() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row_id in CORE_REQUIREMENT_IDS:
        is_schema_row = row_id == MANIFEST_SCHEMA_ROW_ID
        rows.append(
            {
                "row_id": row_id,
                "class": "core",
                "title": CORE_TITLES[row_id],
                "status": "implemented" if is_schema_row else "planned",
                "receipt_ref": MANIFEST_SCHEMA_RECEIPT_REF if is_schema_row else None,
                "evidence_refs": [],
                "source_issue": 257 if is_schema_row else 256,
                "reason": "",
            }
        )
    for row_id in PACK_REQUIREMENT_IDS:
        rows.append(
            {
                "row_id": row_id,
                "class": "pack",
                "title": PACK_TITLES[row_id],
                "status": "planned",
                "supported": False,
                "pack_state": "disabled",
                "receipt_ref": None,
                "evidence_refs": [],
                "source_issue": 256,
                "reason": "not proved in this draft",
            }
        )
    return rows
