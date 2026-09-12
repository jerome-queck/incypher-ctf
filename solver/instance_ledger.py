"""Mode-aware, authenticated projection of the Board's Instance ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes

ABSENT = "absent"
UNSETTLED = "unsettled"
NOT_OURS = "not-ours"
EMPTY = "empty"
POPULATED = "populated"
OUTCOMES = (ABSENT, UNSETTLED, NOT_OURS, EMPTY, POPULATED)
RECEIPT_TYPE = "instance-ledger-identity"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"
MANIFEST_ROW_ID = "core.board-target-lease"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class LedgerRow:
    """One normalized row, still carrying the ownership evidence the Board supplied."""

    row_id: str
    challenge_id: int | str
    user_id: int | None = None
    team_id: int | None = None
    source_page: int = 0
    response_digest: str = ""

    def document(self) -> dict[str, object]:
        return {
            "row_id": self.row_id,
            "challenge_id": self.challenge_id,
            "user_id": self.user_id,
            "team_id": self.team_id,
            "source_page": self.source_page,
            "response_digest": self.response_digest,
        }


@dataclass(frozen=True)
class LedgerPage:
    """One complete, sealed broker response after its wire format is normalized."""

    status: int
    body: bytes = b""
    rows: tuple[LedgerRow, ...] = ()
    page: int = 1
    total_pages: int = 1
    total_rows: int = 0
    complete: bool = True
    response_digest: str = ""

    @property
    def digest(self) -> str:
        return self.response_digest or hashlib.sha256(self.body).hexdigest()


@dataclass(frozen=True)
class LedgerResult:
    outcome: str
    mode: str
    user_id: int
    team_id: int | None
    identity_digest: str
    owned: tuple[LedgerRow, ...] = ()
    foreign: tuple[LedgerRow, ...] = ()
    pages: tuple[LedgerPage, ...] = ()
    reason: str = ""


@dataclass(frozen=True)
class AuthenticatedIdentity:
    """The identity already settled by the authoritative Board profile."""

    mode: str
    user_id: int
    team_id: int | None


def read_profiled_instance_ledger(identity: AuthenticatedIdentity, broker) -> LedgerResult:
    """Read through profiled, generation-scoped Board authority after identity settles."""

    if (
        identity.mode not in {"teams", "users"}
        or identity.user_id <= 0
        or (identity.mode == "teams" and not identity.team_id)
    ):
        return identify_ledger(
            mode=identity.mode,
            user_id=identity.user_id,
            team_id=identity.team_id,
            pages=(),
        )
    first = _broker_page(broker, 1)
    pages = [first]
    if first.status == 200 and first.complete and first.total_pages > 1:
        pages.extend(_broker_page(broker, number) for number in range(2, first.total_pages + 1))
    return identify_ledger(
        mode=identity.mode,
        user_id=identity.user_id,
        team_id=identity.team_id,
        pages=tuple(pages),
    )


def _broker_page(broker, page: int) -> LedgerPage:
    from solver.board_broker_contracts import BoardOperation, BoardOutcome

    result = broker.instance_ledger_page(page)
    if result.operation is not BoardOperation.INSTANCE_LEDGER_PAGE or result.outcome is not BoardOutcome.ANSWERED:
        return LedgerPage(
            status=result.provenance.http_status,
            page=page,
            complete=False,
            response_digest=result.provenance.response_digest,
        )
    if not isinstance(result.value, LedgerPage):
        return LedgerPage(status=200, page=page, complete=False, response_digest=result.provenance.response_digest)
    if result.provenance.truncated or result.value.digest != result.provenance.response_digest:
        return replace(result.value, complete=False)
    return result.value


def identify_ledger(*, mode: str, user_id: int, team_id: int | None, pages: tuple[LedgerPage, ...]) -> LedgerResult:
    """Settle ledger ownership only from a complete authenticated page sequence."""

    if mode not in {"teams", "users"} or user_id <= 0 or (mode == "teams" and not team_id):
        return _result(UNSETTLED, mode, user_id, team_id, pages, reason="authenticated identity is unsettled")
    if not pages:
        return _result(UNSETTLED, mode, user_id, team_id, pages, reason="ledger returned no page")
    if pages[0].status == 404:
        return _result(ABSENT, mode, user_id, team_id, pages)
    if any(page.status != 200 or not page.complete for page in pages):
        return _result(UNSETTLED, mode, user_id, team_id, pages, reason="ledger page is unreadable or truncated")
    expected = tuple(range(1, pages[0].total_pages + 1))
    numbered = tuple(page.page for page in pages)
    if (
        not expected
        or numbered != expected
        or any(page.total_pages != pages[0].total_pages for page in pages)
        or len({page.digest for page in pages}) != len(pages)
    ):
        return _result(UNSETTLED, mode, user_id, team_id, pages, reason="ledger pagination is incomplete or repeated")
    rows = tuple(
        replace(row, source_page=page.page, response_digest=page.digest) for page in pages for row in page.rows
    )
    if any(page.total_rows != pages[0].total_rows for page in pages) or len(rows) != pages[0].total_rows:
        return _result(UNSETTLED, mode, user_id, team_id, pages, reason="ledger empty or row count is uncorroborated")
    owner = (lambda row: row.team_id == team_id) if mode == "teams" else (lambda row: row.user_id == user_id)
    owned = tuple(row for row in rows if owner(row))
    foreign = tuple(row for row in rows if not owner(row))
    outcome = POPULATED if owned else NOT_OURS if foreign else EMPTY
    return _result(outcome, mode, user_id, team_id, pages, owned, foreign)


def receipt_document(run_id: str, result: LedgerResult) -> dict[str, object]:
    if not run_id or result.outcome not in OUTCOMES:
        raise ValueError("Instance-ledger receipt input is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "receipt_type": RECEIPT_TYPE,
        "run_id": run_id,
        "mode": result.mode,
        "authenticated_identity": _identity_document(result),
        "authenticated_identity_digest": result.identity_digest,
        "outcome": result.outcome,
        "reason": result.reason,
        "pagination_trace": [
            {
                "page": page.page,
                "total_pages": page.total_pages,
                "total_rows": page.total_rows,
                "status": page.status,
                "complete": page.complete,
                "response_digest": page.digest,
            }
            for page in result.pages
        ],
        "row_ownership_counts": {"owned": len(result.owned), "foreign": len(result.foreign)},
        "owned_rows": [row.document() for row in result.owned],
        "foreign_rows": [row.document() for row in result.foreign],
        "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF},
    }


def write_receipt(state: Path, run_id: str, result: LedgerResult) -> Path:
    path = Path(state) / "runs" / run_id / "canonical" / RECEIPT_FILENAME
    atomic_write(path, canonical_bytes(receipt_document(run_id, result)) + b"\n")
    return path


def verify_receipt(path: Path) -> Path:
    receipt_path = Path(path)
    try:
        raw = receipt_path.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Instance-ledger receipt cannot be read as JSON") from error
    if raw != canonical_bytes(document) + b"\n" or not isinstance(document, Mapping):
        raise ValueError("Instance-ledger receipt is not canonical JSON")
    expected = {
        "schema_version",
        "receipt_type",
        "run_id",
        "mode",
        "authenticated_identity",
        "authenticated_identity_digest",
        "outcome",
        "reason",
        "pagination_trace",
        "row_ownership_counts",
        "owned_rows",
        "foreign_rows",
        "manifest_link",
    }
    if set(document) != expected or document["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Instance-ledger receipt shape is unsupported")
    if document["receipt_type"] != RECEIPT_TYPE or document["outcome"] not in OUTCOMES:
        raise ValueError("Instance-ledger receipt vocabulary is unsupported")
    run_id = str(document["run_id"])
    if not run_id or receipt_path != receipt_path.parents[2] / run_id / "canonical" / RECEIPT_FILENAME:
        raise ValueError("Instance-ledger receipt path does not match its Run identity")
    counts = document["row_ownership_counts"]
    if not isinstance(counts, Mapping) or counts != {
        "owned": len(document["owned_rows"]),
        "foreign": len(document["foreign_rows"]),
    }:
        raise ValueError("Instance-ledger receipt ownership counts disagree")
    identity = document["authenticated_identity"]
    if not isinstance(identity, Mapping) or set(identity) != {"user_id", "team_id"}:
        raise ValueError("Instance-ledger receipt authenticated identity is invalid")
    if document["mode"] not in {"teams", "users"} or type(identity["user_id"]) is not int or identity["user_id"] <= 0:
        raise ValueError("Instance-ledger receipt authenticated identity is invalid")
    if document["mode"] == "teams" and (type(identity["team_id"]) is not int or identity["team_id"] <= 0):
        raise ValueError("Instance-ledger receipt authenticated identity is invalid")
    identity_digest = str(document["authenticated_identity_digest"])
    if len(identity_digest) != 64 or any(character not in "0123456789abcdef" for character in identity_digest):
        raise ValueError("Instance-ledger receipt identity digest is invalid")
    expected_digest = digest_bytes(canonical_bytes({"mode": document["mode"], **identity}))
    if identity_digest != expected_digest:
        raise ValueError("Instance-ledger receipt identity digest disagrees")
    trace = document["pagination_trace"]
    if not isinstance(trace, list) or any(not isinstance(item, Mapping) for item in trace):
        raise ValueError("Instance-ledger receipt pagination trace is invalid")
    outcome = document["outcome"]
    statuses = [item.get("status") for item in trace]
    pages = [item.get("page") for item in trace]
    total_pages = [item.get("total_pages") for item in trace]
    total_rows = [item.get("total_rows") for item in trace]
    digests = [item.get("response_digest") for item in trace]
    settled_trace = (
        bool(trace)
        and all(status == 200 for status in statuses)
        and all(item.get("complete") is True for item in trace)
    )
    if settled_trace and (
        len(set(total_pages)) != 1
        or pages != list(range(1, total_pages[0] + 1))
        or len(set(total_rows)) != 1
        or len(set(digests)) != len(digests)
        or any(not isinstance(digest, str) or len(digest) != 64 for digest in digests)
    ):
        raise ValueError("Instance-ledger receipt pagination trace is unsettled")
    if settled_trace and total_rows[0] != counts["owned"] + counts["foreign"]:
        raise ValueError("Instance-ledger receipt total rows disagree")
    if document["reason"]:
        expected_outcome = UNSETTLED
    elif statuses[:1] == [404]:
        expected_outcome = ABSENT
    elif not settled_trace:
        expected_outcome = UNSETTLED
    elif any(status != 200 for status in statuses) or any(item.get("complete") is not True for item in trace):
        expected_outcome = UNSETTLED
    elif counts["owned"]:
        expected_outcome = POPULATED
    elif counts["foreign"]:
        expected_outcome = NOT_OURS
    else:
        expected_outcome = EMPTY
    if outcome != expected_outcome:
        raise ValueError("Instance-ledger receipt outcome disagrees with its evidence")
    provenance = {(item.get("page"), item.get("response_digest")) for item in trace}
    rows = [*document["owned_rows"], *document["foreign_rows"]]
    if any(
        not isinstance(row, Mapping) or (row.get("source_page"), row.get("response_digest")) not in provenance
        for row in rows
    ):
        raise ValueError("Instance-ledger receipt row provenance is invalid")
    owner_key = "team_id" if document["mode"] == "teams" else "user_id"
    owner_id = identity["team_id"] if document["mode"] == "teams" else identity["user_id"]
    if any(row.get(owner_key) != owner_id for row in document["owned_rows"]) or any(
        row.get(owner_key) == owner_id for row in document["foreign_rows"]
    ):
        raise ValueError("Instance-ledger receipt ownership classification is invalid")
    if document["manifest_link"] != {"row_id": MANIFEST_ROW_ID, "receipt_ref": RECEIPT_REF}:
        raise ValueError("Instance-ledger receipt manifest link is invalid")
    return receipt_path


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


def _result(outcome, mode, user_id, team_id, pages, owned=(), foreign=(), reason="") -> LedgerResult:
    identity = canonical_bytes({"mode": mode, "user_id": user_id, "team_id": team_id})
    return LedgerResult(outcome, mode, user_id, team_id, digest_bytes(identity), owned, foreign, pages, reason)


def _identity_document(result: LedgerResult) -> dict[str, object]:
    # Board IDs are not credentials; their digest is the stable binding used by manifests.
    return {"user_id": result.user_id, "team_id": result.team_id}


__all__ = [
    "AuthenticatedIdentity",
    "LedgerPage",
    "LedgerResult",
    "LedgerRow",
    "identify_ledger",
    "link_manifest",
    "manifest_receipt",
    "read_profiled_instance_ledger",
    "receipt_document",
    "verify_receipt",
    "write_receipt",
]
