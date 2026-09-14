"""Pure qualification of two complete Board observations into one Intake snapshot."""

from __future__ import annotations

import datetime as dt
import base64
import hashlib
import json
import urllib.parse
from dataclasses import dataclass
from typing import Mapping

from solver.event_store_storage import canonical_bytes

MAX_PAGES = 1_000
MAX_CHALLENGES = 100_000


@dataclass(frozen=True)
class IntakeContract:
    profile_digest: str
    subject_digest: str
    profile_decision_event_digest: str = ""
    board_origin: str = ""
    landing_digests: tuple[str, ...] = ()
    read_control_digests: tuple[str, ...] = ()
    read_control_statuses: tuple[int, ...] = ()
    pagination_shape: str = "ctfd-pages"
    max_pages: int = MAX_PAGES
    max_challenges: int = MAX_CHALLENGES
    max_fetch_bytes: int = 256 * 1024 * 1024
    scoreboard_top: int = 0
    mana_outcome: str = ""
    schema_version: int = 1
    qualifier_version: int = 1

    def document(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "qualifier_version": self.qualifier_version,
            "profile_digest": self.profile_digest,
            "subject_digest": self.subject_digest,
            "profile_decision_event_digest": self.profile_decision_event_digest,
            "board_origin": self.board_origin,
            "landing_digests": list(self.landing_digests),
            "read_control_digests": list(self.read_control_digests),
            "read_control_statuses": list(self.read_control_statuses),
            "pagination_shape": self.pagination_shape,
            "max_pages": self.max_pages,
            "max_challenges": self.max_challenges,
            "max_fetch_bytes": self.max_fetch_bytes,
            "scoreboard_top": self.scoreboard_top,
            "mana_outcome": self.mana_outcome,
            "id_order": "integer-numeric-before-string-utf8-v1",
        }

    @property
    def digest(self) -> str:
        return _digest(canonical_bytes(self.document()))


@dataclass(frozen=True)
class PriorFence:
    profile_digest: str
    event_id: str
    event_digest: str
    snapshot_digest: str

    @classmethod
    def genesis(cls, profile_digest: str) -> PriorFence:
        return cls(profile_digest, "genesis", "", "")

    def document(self) -> dict[str, str]:
        return {
            "profile_digest": self.profile_digest,
            "event_id": self.event_id,
            "event_digest": self.event_digest,
            "snapshot_digest": self.snapshot_digest,
        }


@dataclass(frozen=True)
class IntakeDocument:
    request_id: str
    classified_event_id: str
    pass_no: int
    kind: str
    endpoint: str
    status: int
    content_type: str
    raw: bytes
    original_bytes: int
    complete: bool
    profile_digest: str
    subject_digest: str
    capability_digest: str
    peer_digest: str
    page: int = 0
    challenge_id: int | str | None = None
    outcome: str = "answered"
    location: str = ""
    raw_blob_digest: str = ""
    sanitized_blob_digest: str = ""
    request_digest: str = ""
    hop: int = 0
    auth_forwarded: bool = True
    resource_identity: str = ""

    @property
    def raw_digest(self) -> str:
        return _digest(self.raw)

    def evidence(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "classified_event_id": self.classified_event_id,
            "pass": self.pass_no,
            "kind": self.kind,
            "endpoint": self.endpoint,
            "status": self.status,
            "content_type": self.content_type,
            "raw_digest": self.raw_digest,
            "original_bytes": self.original_bytes,
            "complete": self.complete,
            "profile_digest": self.profile_digest,
            "subject_digest": self.subject_digest,
            "capability_digest": self.capability_digest,
            "peer_digest": self.peer_digest,
            "page": self.page,
            "challenge_id": self.challenge_id,
            "outcome": self.outcome,
            "location": self.location,
            "raw_blob_digest": self.raw_blob_digest,
            "sanitized_blob_digest": self.sanitized_blob_digest,
            "request_digest": self.request_digest,
            "hop": self.hop,
            "auth_forwarded": self.auth_forwarded,
            "resource_identity": self.resource_identity,
        }


@dataclass(frozen=True)
class IntakePass:
    number: int
    identity_before: IntakeDocument
    pages: tuple[IntakeDocument, ...]
    details: tuple[IntakeDocument, ...]
    attachments: tuple[IntakeDocument, ...]
    identity_after: IntakeDocument
    absence_controls: tuple[IntakeDocument, ...] = ()
    landing: IntakeDocument | None = None
    read_control: IntakeDocument | None = None
    scoreboard: IntakeDocument | None = None
    mana: IntakeDocument | None = None

    @property
    def documents(self) -> tuple[IntakeDocument, ...]:
        return (
            self.identity_before,
            *((self.landing,) if self.landing else ()),
            *((self.read_control,) if self.read_control else ()),
            *((self.scoreboard,) if self.scoreboard else ()),
            *((self.mana,) if self.mana else ()),
            *self.pages,
            *self.details,
            *self.attachments,
            *self.absence_controls,
            self.identity_after,
        )


@dataclass(frozen=True)
class IntakeAuthority:
    profile_digest: str
    profile_decision_event_digest: str
    controller_peer_digest: str
    binding_digest: str

    def document(self) -> dict[str, str]:
        return {
            "profile_digest": self.profile_digest,
            "profile_decision_event_digest": self.profile_decision_event_digest,
            "controller_peer_digest": self.controller_peer_digest,
            "binding_digest": self.binding_digest,
        }

    @property
    def digest(self) -> str:
        return _digest(canonical_bytes(self.document()))

    @classmethod
    def testing(cls, contract: IntakeContract) -> IntakeAuthority:
        fallback = "f" * 64
        return cls(
            contract.profile_digest,
            contract.profile_decision_event_digest or fallback,
            fallback,
            fallback,
        )


@dataclass(frozen=True)
class IntakeProbe:
    attempt_id: str
    contract: IntakeContract
    prior_fence: PriorFence
    passes: tuple[IntakePass, IntakePass]
    observed_at: dt.datetime
    prior_snapshot: IntakeSnapshot | None = None


@dataclass(frozen=True)
class TypedId:
    kind: str
    value: int | str

    @classmethod
    def parse(cls, value: object) -> TypedId:
        if isinstance(value, bool):
            raise ValueError("challenge ID is boolean")
        if isinstance(value, int) and value >= 0:
            return cls("integer", value)
        if isinstance(value, str) and value:
            return cls("string", value)
        raise ValueError("challenge ID is unsupported")

    def document(self) -> dict[str, str]:
        return {"type": self.kind, "value": str(self.value)}

    def sort_key(self) -> tuple[int, int | bytes]:
        if self.kind == "integer":
            return (0, int(self.value))
        return (1, str(self.value).encode("utf-8"))


@dataclass(frozen=True)
class PresentValue:
    state: str
    value: object = None

    @classmethod
    def from_field(cls, source: Mapping[str, object], name: str) -> PresentValue:
        if name not in source:
            return cls("missing")
        if source[name] is None:
            return cls("null")
        return cls("value", source[name])

    def document(self) -> dict[str, object]:
        answer: dict[str, object] = {"state": self.state}
        if self.state == "value":
            answer["value"] = self.value
        return answer


@dataclass(frozen=True)
class ValueFact:
    value: int | bool
    source: str
    outcome: str = "answered"
    observed_at: dt.datetime | None = None

    def document(self) -> dict[str, object]:
        return {
            "value": self.value,
            "source": self.source,
            "outcome": self.outcome,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
        }


@dataclass(frozen=True)
class AttachmentSnapshot:
    resource_identity: str
    name: str
    content_digest: str
    nbytes: int
    content_type: str
    outcome: str
    complete: bool
    reason: str
    original_bytes: int
    stored_bytes: int
    location_digest: str
    final_host: str
    over_limit: Mapping[str, int] | None = None
    hops: tuple[Mapping[str, object], ...] = ()

    def document(self) -> dict[str, object]:
        return {
            "resource_identity": self.resource_identity,
            "name": self.name,
            "content_digest": self.content_digest,
            "bytes": self.nbytes,
            "content_type": self.content_type,
            "outcome": self.outcome,
            "complete": self.complete,
            "reason": self.reason,
            "original_bytes": self.original_bytes,
            "stored_bytes": self.stored_bytes,
            "location_digest": self.location_digest,
            "final_host": self.final_host,
            "over_limit": dict(self.over_limit) if self.over_limit else None,
            "hops": [dict(item) for item in self.hops],
        }


@dataclass(frozen=True)
class ChallengeSnapshot:
    challenge_id: TypedId
    name: str
    category: str
    challenge_type: str
    statement: str
    value: ValueFact
    solves: ValueFact
    solved: ValueFact
    position: PresentValue
    attempts: int
    max_attempts: PresentValue
    shared: PresentValue
    timeout: PresentValue
    destroy_on_flag: PresentValue
    mana_cost: PresentValue
    attachments: tuple[AttachmentSnapshot, ...]
    revision_digest: str

    @property
    def files(self) -> tuple[str, ...]:
        return tuple(item.resource_identity for item in self.attachments)

    def document(self) -> dict[str, object]:
        return {
            "id": self.challenge_id.document(),
            "name": self.name,
            "category": self.category,
            "type": self.challenge_type,
            "statement": self.statement,
            "value": self.value.document(),
            "solves": self.solves.document(),
            "solved": self.solved.document(),
            "position": self.position.document(),
            "attempts": self.attempts,
            "max_attempts": self.max_attempts.document(),
            "shared": self.shared.document(),
            "timeout": self.timeout.document(),
            "destroy_on_flag": self.destroy_on_flag.document(),
            "mana_cost": self.mana_cost.document(),
            "attachments": [item.document() for item in self.attachments],
            "revision_digest": self.revision_digest,
        }


@dataclass(frozen=True)
class TombstoneSnapshot:
    challenge_id: TypedId
    prior_revision_digest: str
    prior_snapshot_digest: str
    absence_evidence: tuple[Mapping[str, object], Mapping[str, object]]
    observed_at: dt.datetime
    reason: str = "authenticated-not-found"

    def document(self) -> dict[str, object]:
        return {
            "id": self.challenge_id.document(),
            "prior_revision_digest": self.prior_revision_digest,
            "prior_snapshot_digest": self.prior_snapshot_digest,
            "absence_evidence": [dict(item) for item in self.absence_evidence],
            "observed_at": self.observed_at.isoformat(),
            "reason": self.reason,
        }


@dataclass(frozen=True)
class IntakeSnapshot:
    snapshot_id: str
    profile_digest: str
    observed_at: dt.datetime
    challenges: tuple[ChallengeSnapshot, ...]
    empty_diagnosis: str
    tombstones: tuple[TombstoneSnapshot, ...] = ()
    scoreboard: tuple[Mapping[str, object], ...] = ()
    mana: Mapping[str, object] | None = None

    def document(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "snapshot_id": self.snapshot_id,
            "profile_digest": self.profile_digest,
            "observed_at": self.observed_at.isoformat(),
            "empty_diagnosis": self.empty_diagnosis,
            "challenges": [challenge.document() for challenge in self.challenges],
            "tombstones": [item.document() for item in self.tombstones],
            "scoreboard": [dict(item) for item in self.scoreboard],
            "mana": dict(self.mana) if self.mana else None,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_bytes(self.document())

    @property
    def digest(self) -> str:
        return _digest(self.canonical_bytes())


@dataclass(frozen=True)
class IntakeDecision:
    attempt_id: str
    settled: bool
    reason: str
    prior_fence: PriorFence
    snapshot: IntakeSnapshot | None = None
    observation_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotAvailable:
    snapshot: IntakeSnapshot
    refresh: IntakeDecision


@dataclass(frozen=True)
class NoCoherentSnapshot:
    decision: IntakeDecision
    retry_at: dt.datetime


def availability(
    decision: IntakeDecision,
    *,
    prior: IntakeSnapshot | None,
    retry_at: dt.datetime,
) -> SnapshotAvailable | NoCoherentSnapshot:
    if decision.settled:
        assert decision.snapshot is not None
        return SnapshotAvailable(decision.snapshot, decision)
    if prior is not None:
        return SnapshotAvailable(prior, decision)
    return NoCoherentSnapshot(decision, retry_at)


def probe_document(probe: IntakeProbe) -> dict[str, object]:
    return {
        "attempt_id": probe.attempt_id,
        "contract": probe.contract.document(),
        "prior_fence": probe.prior_fence.document(),
        "observed_at": probe.observed_at.isoformat(),
        "prior_snapshot": probe.prior_snapshot.document() if probe.prior_snapshot else None,
        "passes": [_pass_document(observed) for observed in probe.passes],
    }


def probe_from_document(value: object) -> IntakeProbe:
    if not isinstance(value, Mapping):
        raise ValueError("Intake probe is not an object")
    contract_value = value.get("contract")
    fence_value = value.get("prior_fence")
    passes_value = value.get("passes")
    if not isinstance(contract_value, Mapping) or not isinstance(fence_value, Mapping):
        raise ValueError("Intake probe contract or fence is absent")
    if not isinstance(passes_value, list) or len(passes_value) != 2:
        raise ValueError("Intake probe does not contain two passes")
    contract = contract_from_document(contract_value)
    prior = _fence_from_document(fence_value)
    prior_snapshot = value.get("prior_snapshot")
    return IntakeProbe(
        str(value["attempt_id"]),
        contract,
        prior,
        tuple(_pass_from_document(item) for item in passes_value),  # type: ignore[arg-type]
        dt.datetime.fromisoformat(str(value["observed_at"])),
        _snapshot_from_document(prior_snapshot) if prior_snapshot is not None else None,
    )


def contract_from_document(value: object) -> IntakeContract:
    if not isinstance(value, Mapping):
        raise ValueError("Intake contract is not an object")
    contract = IntakeContract(
        profile_digest=str(value["profile_digest"]),
        subject_digest=str(value["subject_digest"]),
        profile_decision_event_digest=str(value["profile_decision_event_digest"]),
        board_origin=str(value["board_origin"]),
        landing_digests=tuple(str(item) for item in _list(value["landing_digests"])),
        read_control_digests=tuple(str(item) for item in _list(value["read_control_digests"])),
        read_control_statuses=tuple(int(item) for item in _list(value["read_control_statuses"])),
        pagination_shape=str(value["pagination_shape"]),
        max_pages=int(value["max_pages"]),
        max_challenges=int(value["max_challenges"]),
        max_fetch_bytes=int(value["max_fetch_bytes"]),
        scoreboard_top=int(value["scoreboard_top"]),
        mana_outcome=str(value["mana_outcome"]),
        schema_version=int(value["schema_version"]),
        qualifier_version=int(value["qualifier_version"]),
    )
    if contract.document() != dict(value):
        raise ValueError("Intake contract is unsupported")
    return contract


def decision_document(decision: IntakeDecision) -> dict[str, object]:
    return {
        "attempt_id": decision.attempt_id,
        "settled": decision.settled,
        "reason": decision.reason,
        "prior_fence": decision.prior_fence.document(),
        "observation_ids": list(decision.observation_ids),
        "snapshot_digest": decision.snapshot.digest if decision.snapshot else "",
        "snapshot": decision.snapshot.document() if decision.snapshot else None,
    }


def snapshot_from_document(value: object) -> IntakeSnapshot:
    return _snapshot_from_document(value)


def qualify(probe: IntakeProbe) -> IntakeDecision:
    """Qualify two observations without performing I/O or consulting ambient state."""

    observations = tuple(document.classified_event_id for one in probe.passes for document in one.documents)
    try:
        if len(probe.passes) != 2 or tuple(one.number for one in probe.passes) != (1, 2):
            raise _Unsettled("pass-set-invalid")
        projections = tuple(_pass_projection(one, probe.contract) for one in probe.passes)
        if _comparison_projection(projections[0]) != _comparison_projection(projections[1]):
            raise _Unsettled("passes-disagree")
        current_ids = {_id_key(item["id"]) for item in projections[0]["challenges"]}
        prior_ids = (
            {(_typed.kind, _typed.value) for _typed in (item.challenge_id for item in probe.prior_snapshot.challenges)}
            if probe.prior_snapshot
            else set()
        )
        removed = prior_ids - current_ids
        tombstones = {_id_key(item["id"]) for item in projections[0]["tombstones"]}
        if removed - tombstones:
            raise _Unsettled("tombstone-unproved")
    except _Unsettled as unsettled:
        return IntakeDecision(
            probe.attempt_id,
            False,
            unsettled.reason,
            probe.prior_fence,
            observation_ids=observations,
        )
    challenges = tuple(_snapshot_challenge(item, probe.observed_at) for item in projections[1]["challenges"])
    prior_challenges = {
        (item.challenge_id.kind, item.challenge_id.value): item
        for item in (probe.prior_snapshot.challenges if probe.prior_snapshot else ())
    }
    by_pass = [{_id_key(item["id"]): item for item in projection["tombstones"]} for projection in projections]
    new_tombstones = tuple(
        TombstoneSnapshot(
            prior_challenges[key].challenge_id,
            prior_challenges[key].revision_digest,
            probe.prior_snapshot.digest,
            (by_pass[0][key]["evidence"], by_pass[1][key]["evidence"]),
            probe.observed_at,
        )
        for key in sorted(removed, key=lambda item: TypedId(item[0], item[1]).sort_key())
    )
    active_ids = {(item.challenge_id.kind, item.challenge_id.value) for item in challenges}
    carried_tombstones = tuple(
        item
        for item in (probe.prior_snapshot.tombstones if probe.prior_snapshot else ())
        if (item.challenge_id.kind, item.challenge_id.value) not in active_ids
    )
    snapshot = IntakeSnapshot(
        snapshot_id=f"intake-snapshot:{probe.attempt_id}",
        profile_digest=probe.contract.profile_digest,
        observed_at=probe.observed_at,
        challenges=challenges,
        empty_diagnosis=(
            "authenticated-tombstones"
            if not challenges and probe.prior_snapshot and probe.prior_snapshot.challenges
            else "authenticated-empty"
            if not challenges
            else "not-empty"
        ),
        tombstones=carried_tombstones + new_tombstones,
        scoreboard=tuple(
            {
                **row,
                "observed_at": probe.observed_at.isoformat(),
                "source": "scoreboard",
            }
            for row in projections[1]["scoreboard"]
        ),
        mana=(
            {
                **projections[1]["mana"],
                "observed_at": probe.observed_at.isoformat(),
                "source": "mana",
            }
            if projections[1]["mana"] is not None
            else None
        ),
    )
    return IntakeDecision(
        probe.attempt_id,
        True,
        "coherent",
        probe.prior_fence,
        snapshot,
        observations,
    )


class _Unsettled(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason


def _pass_projection(observed: IntakePass, contract: IntakeContract) -> dict[str, object]:
    if observed.number not in (1, 2):
        raise _Unsettled("pass-set-invalid")
    for document in observed.documents:
        _valid_document(document, observed.number, contract)
    subject_before = _identity(observed.identity_before)
    subject_after = _identity(observed.identity_after)
    if subject_before != subject_after or _digest(canonical_bytes(subject_before)) != contract.subject_digest:
        raise _Unsettled("authentication-changed")
    if contract.landing_digests or contract.read_control_digests:
        if observed.landing is None or observed.read_control is None:
            raise _Unsettled("empty-control-unproved")
        _live_controls(observed.landing, observed.read_control, contract)
    if contract.scoreboard_top:
        if observed.scoreboard is None or observed.mana is None:
            raise _Unsettled("live-facts-incomplete")
        if observed.scoreboard.endpoint != f"/api/v1/scoreboard/top/{contract.scoreboard_top}":
            raise _Unsettled("endpoint-binding-invalid")
        scoreboard = _scoreboard(observed.scoreboard)
        mana = _mana(observed.mana, contract)
    else:
        scoreboard = []
        mana = None
    entries = _pages(observed.pages, contract)
    listed: dict[tuple[str, object], Mapping[str, object]] = {}
    for entry in entries:
        challenge_id = _required_id(entry, "id")
        key = (challenge_id.kind, challenge_id.value)
        if key in listed:
            raise _Unsettled("duplicate-challenge-id")
        _validate_list(entry)
        listed[key] = entry
    details: dict[tuple[str, object], Mapping[str, object]] = {}
    for document in observed.details:
        detail = _success_data(document)
        if not isinstance(detail, Mapping):
            raise _Unsettled("detail-malformed")
        challenge_id = _required_id(detail, "id")
        key = (challenge_id.kind, challenge_id.value)
        if key in details:
            raise _Unsettled("duplicate-detail-id")
        _validate_detail(detail)
        details[key] = detail
    if set(details) != set(listed):
        raise _Unsettled("detail-set-incomplete")
    attachment_chains: dict[tuple[tuple[str, object], str], list[IntakeDocument]] = {}
    for document in observed.attachments:
        if document.challenge_id is None:
            raise _Unsettled("attachment-unsettled")
        try:
            identifier = TypedId.parse(document.challenge_id)
        except ValueError:
            raise _Unsettled("attachment-unsettled") from None
        resource = _resource_identity(document.resource_identity or document.endpoint)
        key = ((identifier.kind, identifier.value), resource)
        attachment_chains.setdefault(key, []).append(document)
    attachments = {
        key: _attachment_projection(tuple(chain), max_fetch_bytes=contract.max_fetch_bytes)
        for key, chain in attachment_chains.items()
    }
    challenges = []
    for key, list_entry in listed.items():
        detail = details[key]
        _validate_cross_source(list_entry, detail)
        expected_resources = tuple(_resource_identity(str(item)) for item in detail["files"])
        if len(expected_resources) != len(set(expected_resources)):
            raise _Unsettled("duplicate-file")
        held = tuple(attachments.pop((key, resource), None) for resource in expected_resources)
        if any(item is None for item in held):
            raise _Unsettled("attachment-set-incomplete")
        challenges.append(_semantic_challenge(list_entry, detail, tuple(item for item in held if item is not None)))
    if attachments:
        raise _Unsettled("attachment-set-invalid")
    challenges.sort(key=lambda item: TypedId(item["id"]["type"], _id_value(item["id"])).sort_key())
    tombstones = sorted(
        (_absence(document) for document in observed.absence_controls),
        key=lambda item: TypedId(str(item["id"]["type"]), _id_value(item["id"])).sort_key(),
    )
    return {
        "contract_digest": contract.digest,
        "profile_digest": contract.profile_digest,
        "subject": subject_before,
        "pagination": {
            "pages": len(observed.pages),
            "total": len(entries),
        },
        "challenges": challenges,
        "tombstones": tombstones,
        "scoreboard": scoreboard,
        "mana": mana,
    }


def _valid_document(document: IntakeDocument, pass_no: int, contract: IntakeContract) -> None:
    expected_status = (
        404 if document.kind == "absence" or (document.kind == "mana" and contract.mana_outcome == "absent") else 200
    )
    common_invalid = (
        document.pass_no != pass_no
        or document.profile_digest != contract.profile_digest
        or document.subject_digest != contract.subject_digest
        or not document.capability_digest
        or not document.peer_digest
        or document.original_bytes != len(document.raw)
    )
    if common_invalid:
        raise _Unsettled("document-unsettled")
    _valid_endpoint(document, contract)
    expected_request = _digest(canonical_bytes({"operation": "intake-read", "arguments": {"path": document.endpoint}}))
    if document.request_digest and document.request_digest != expected_request:
        raise _Unsettled("request-binding-invalid")
    if document.kind == "landing":
        if (
            document.status != 200
            or not document.complete
            or not document.content_type.split(";", 1)[0].strip().lower().startswith("text/html")
        ):
            raise _Unsettled("empty-control-unproved")
        return
    if document.kind == "read-control":
        if not document.complete or document.content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise _Unsettled("empty-control-unproved")
        return
    if document.kind == "attachment":
        if (
            document.outcome == "too-large"
            and document.status == 200
            and not document.complete
            and len(document.raw) > contract.max_fetch_bytes
        ):
            return
        redirect = document.status in {301, 302, 303, 307, 308}
        if document.outcome != "answered" or not document.complete:
            raise _Unsettled("attachment-unsettled")
        if redirect and document.location:
            return
        if document.status != 200 or not document.content_type.strip():
            raise _Unsettled("attachment-unsettled")
        return
    if (
        document.outcome != "answered"
        or document.status != expected_status
        or document.content_type.split(";", 1)[0].strip().lower() != "application/json"
        or not document.complete
    ):
        raise _Unsettled("document-unsettled")


def _absence(document: IntakeDocument) -> dict[str, object]:
    if document.kind != "absence" or document.challenge_id is None:
        raise _Unsettled("tombstone-malformed")
    wrapper = _json(document)
    if not isinstance(wrapper, Mapping) or wrapper.get("success") is not False or "data" in wrapper:
        raise _Unsettled("tombstone-malformed")
    try:
        return {
            "id": TypedId.parse(document.challenge_id).document(),
            "evidence": {
                "request_id": document.request_id,
                "classified_event_id": document.classified_event_id,
                "raw_digest": document.raw_digest,
                "raw_blob_digest": document.raw_blob_digest,
                "sanitized_blob_digest": document.sanitized_blob_digest,
                "request_digest": document.request_digest,
                "status": document.status,
                "content_type": document.content_type,
                "endpoint": document.endpoint,
            },
        }
    except ValueError:
        raise _Unsettled("tombstone-malformed") from None


def _live_controls(landing: IntakeDocument, read_control: IntakeDocument, contract: IntakeContract) -> None:
    if (
        landing.kind != "landing"
        or landing.endpoint != "/"
        or landing.raw_digest not in contract.landing_digests
        or read_control.kind != "read-control"
        or read_control.endpoint != "/api/v1/challenges/0"
        or read_control.status != 404
        or read_control.raw_digest not in contract.read_control_digests
        or read_control.status not in contract.read_control_statuses
        or read_control.raw_digest == landing.raw_digest
    ):
        raise _Unsettled("empty-control-unproved")
    wrapper = _json(read_control)
    if not isinstance(wrapper, Mapping) or wrapper.get("success") is True:
        raise _Unsettled("empty-control-unproved")


def _scoreboard(document: IntakeDocument) -> list[dict[str, object]]:
    data = _success_data(document)
    rows = data.items() if isinstance(data, Mapping) else enumerate(data, start=1) if isinstance(data, list) else ()
    standings = []
    for rank, row in rows:
        if not isinstance(row, Mapping):
            raise _Unsettled("scoreboard-malformed")
        try:
            place = int(rank)
        except (TypeError, ValueError):
            raise _Unsettled("scoreboard-malformed") from None
        score = row.get("score")
        if not isinstance(score, int) or isinstance(score, bool):
            solves = row.get("solves")
            if not isinstance(solves, list) or any(not isinstance(item, Mapping) for item in solves):
                raise _Unsettled("scoreboard-malformed")
            values = [item.get("value") for item in solves]
            if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
                raise _Unsettled("scoreboard-malformed")
            score = sum(values)
        if place < 1 or not isinstance(row.get("name"), str):
            raise _Unsettled("scoreboard-malformed")
        standings.append({"rank": place, "name": row["name"], "score": score})
    standings.sort(key=lambda row: int(row["rank"]))
    if len(standings) != len({row["rank"] for row in standings}):
        raise _Unsettled("scoreboard-malformed")
    return standings


def _mana(document: IntakeDocument, contract: IntakeContract) -> dict[str, object]:
    if document.status == 404 and contract.mana_outcome == "absent":
        if document.raw_digest in contract.landing_digests:
            raise _Unsettled("mana-malformed")
        return {"outcome": "absent", "used": 0, "total": 0}
    if contract.mana_outcome != "answered":
        raise _Unsettled("mana-malformed")
    data = _success_data(document)
    if not isinstance(data, Mapping):
        raise _Unsettled("mana-malformed")
    used, total = data.get("used"), data.get("total")
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (used, total)):
        raise _Unsettled("mana-malformed")
    return {"outcome": "answered", "used": used, "total": total}


def _valid_endpoint(document: IntakeDocument, contract: IntakeContract) -> None:
    fixed = {
        "identity": "/api/v1/users/me",
        "landing": "/",
        "read-control": "/api/v1/challenges/0",
        "mana": "/api/v1/plugins/ctfd-chall-manager/mana",
    }
    if document.kind in fixed:
        valid = document.endpoint == fixed[document.kind]
    elif document.kind == "list":
        valid = (
            document.page == 1 and document.endpoint == "/api/v1/challenges"
            if contract.pagination_shape == "single"
            else document.page >= 1 and document.endpoint == f"/api/v1/challenges?page={document.page}"
        )
    elif document.kind in {"detail", "absence"}:
        valid = document.challenge_id is not None and document.endpoint == (
            "/api/v1/challenges/" + urllib.parse.quote(str(document.challenge_id), safe="")
        )
    elif document.kind == "attachment":
        valid = bool(document.endpoint) and not document.endpoint.startswith("//")
    else:
        valid = document.kind == "scoreboard" and document.endpoint.startswith("/api/v1/scoreboard/top/")
    if not valid:
        raise _Unsettled("endpoint-binding-invalid")


def _identity(document: IntakeDocument) -> dict[str, object]:
    data = _success_data(document)
    if not isinstance(data, Mapping) or "id" not in data:
        raise _Unsettled("authentication-unproved")
    subject_id = _required_id(data, "id")
    team = PresentValue.from_field(data, "team_id")
    if team.state == "value":
        try:
            team_id = TypedId.parse(team.value)
        except ValueError:
            raise _Unsettled("authentication-unproved") from None
        team_document = {"state": "value", "value": team_id.document()}
    else:
        team_document = team.document()
    return {"id": subject_id.document(), "team_id": team_document}


def _pages(documents: tuple[IntakeDocument, ...], contract: IntakeContract) -> list[Mapping[str, object]]:
    if not documents:
        raise _Unsettled("pagination-incomplete")
    entries: list[Mapping[str, object]] = []
    expected_pages: int | None = None
    expected_total: int | None = None
    for index, document in enumerate(documents, start=1):
        if document.kind != "list" or document.page != index:
            raise _Unsettled("pagination-incomplete")
        wrapper = _json(document)
        if not isinstance(wrapper, Mapping) or wrapper.get("success") is not True:
            raise _Unsettled("list-malformed")
        data = wrapper.get("data")
        meta = wrapper.get("meta")
        if not isinstance(data, list):
            raise _Unsettled("pagination-malformed")
        if contract.pagination_shape == "single":
            if index != 1 or len(documents) != 1 or meta is not None or len(data) > contract.max_challenges:
                raise _Unsettled("pagination-malformed")
            if any(not isinstance(entry, Mapping) for entry in data):
                raise _Unsettled("list-malformed")
            return list(data)
        if contract.pagination_shape != "ctfd-pages" or meta is None:
            raise _Unsettled("pagination-malformed")
        if not isinstance(meta, Mapping) or not isinstance(meta.get("pagination"), Mapping):
            raise _Unsettled("pagination-malformed")
        pagination = meta["pagination"]
        page = _integer(pagination.get("page"), minimum=1)
        pages = _integer(pagination.get("pages"), minimum=1)
        total = _integer(pagination.get("total"), minimum=0)
        if page != index or pages > contract.max_pages or total > contract.max_challenges:
            raise _Unsettled("pagination-invalid")
        if expected_pages is None:
            expected_pages, expected_total = pages, total
        if pages != expected_pages or total != expected_total:
            raise _Unsettled("pagination-changed")
        expected_prev = None if index == 1 else index - 1
        expected_next = None if index == pages else index + 1
        if pagination.get("prev") != expected_prev or pagination.get("next") != expected_next:
            raise _Unsettled("pagination-link-invalid")
        if any(not isinstance(entry, Mapping) for entry in data):
            raise _Unsettled("list-malformed")
        entries.extend(data)
    assert expected_pages is not None and expected_total is not None
    if len(documents) != expected_pages:
        raise _Unsettled("pagination-incomplete")
    if len(entries) != expected_total:
        raise _Unsettled("pagination-total-mismatch")
    if expected_total == 0 and (expected_pages != 1 or entries):
        raise _Unsettled("pagination-empty-invalid")
    return entries


def _validate_list(entry: Mapping[str, object]) -> None:
    _required_id(entry, "id")
    for name in ("name", "category", "type"):
        if not isinstance(entry.get(name), str) or (name == "name" and not entry[name]):
            raise _Unsettled("list-field-malformed")
    for name in ("value", "solves"):
        _integer(entry.get(name), minimum=0)
    if not isinstance(entry.get("solved_by_me"), bool):
        raise _Unsettled("list-field-malformed")
    if "position" in entry:
        _integer(entry["position"], minimum=0)


def _validate_detail(detail: Mapping[str, object]) -> None:
    _required_id(detail, "id")
    if not isinstance(detail.get("description"), str) or not isinstance(detail.get("files"), list):
        raise _Unsettled("detail-field-malformed")
    if any(not isinstance(item, str) or not item for item in detail["files"]):
        raise _Unsettled("detail-field-malformed")
    if len(detail["files"]) != len(set(detail["files"])):
        raise _Unsettled("duplicate-file")
    _integer(detail.get("attempts"), minimum=0)
    _optional_integer(detail, "max_attempts", minimum=0, nullable=True)
    _optional_bool(detail, "shared")
    _optional_integer(detail, "timeout", minimum=1, nullable=True)
    _optional_bool(detail, "destroy_on_flag")
    _optional_integer(detail, "mana_cost", minimum=0)


def _validate_cross_source(listed: Mapping[str, object], detail: Mapping[str, object]) -> None:
    for name in ("id", "name", "category", "type", "value", "solves", "solved_by_me", "position"):
        if name in detail and (
            name not in listed or detail[name] != listed[name] or type(detail[name]) is not type(listed[name])
        ):
            raise _Unsettled("list-detail-disagree")


def _semantic_challenge(
    listed: Mapping[str, object],
    detail: Mapping[str, object],
    attachments: tuple[dict[str, object], ...],
) -> dict[str, object]:
    challenge_id = _required_id(listed, "id")
    terms = {
        name: PresentValue.from_field(detail, name).document()
        for name in ("max_attempts", "shared", "timeout", "destroy_on_flag", "mana_cost")
    }
    revision = {
        "id": challenge_id.document(),
        "name": listed["name"],
        "category": listed["category"],
        "type": listed["type"],
        "statement": detail["description"],
        "attachments": [
            {
                "resource_identity": item["resource_identity"],
                **({"content_digest": item["content_digest"]} if item["content_digest"] else {}),
            }
            for item in attachments
        ],
        "terms": terms,
    }
    return {
        "id": challenge_id.document(),
        "name": listed["name"],
        "category": listed["category"],
        "type": listed["type"],
        "statement": detail["description"],
        "value": listed["value"],
        "solves": listed["solves"],
        "solved": listed["solved_by_me"],
        "position": PresentValue.from_field(listed, "position").document(),
        "attempts": detail["attempts"],
        "terms": terms,
        "attachments": list(attachments),
        "revision_digest": _digest(canonical_bytes(revision)),
    }


def _snapshot_challenge(document: Mapping[str, object], observed_at: dt.datetime) -> ChallengeSnapshot:
    identifier = document["id"]
    assert isinstance(identifier, Mapping)
    terms = document.get("terms", document)
    assert isinstance(terms, Mapping)
    return ChallengeSnapshot(
        TypedId(str(identifier["type"]), _id_value(identifier)),
        str(document["name"]),
        str(document["category"]),
        str(document["type"]),
        str(document["statement"]),
        _value_fact(document["value"], boolean=False, observed_at=observed_at),
        _value_fact(document["solves"], boolean=False, observed_at=observed_at),
        _value_fact(document["solved"], boolean=True, observed_at=observed_at),
        _present(document["position"]),
        int(document["attempts"]),
        _present(terms["max_attempts"]),
        _present(terms["shared"]),
        _present(terms["timeout"]),
        _present(terms["destroy_on_flag"]),
        _present(terms["mana_cost"]),
        tuple(
            AttachmentSnapshot(
                str(item["resource_identity"]),
                str(item["name"]),
                str(item["content_digest"]),
                int(item["bytes"]),
                str(item["content_type"]),
                str(item["outcome"]),
                bool(item["complete"]),
                str(item["reason"]),
                int(item["original_bytes"]),
                int(item["stored_bytes"]),
                str(item["location_digest"]),
                str(item["final_host"]),
                dict(item["over_limit"]) if isinstance(item.get("over_limit"), Mapping) else None,
                tuple(dict(hop) for hop in item["hops"]),
            )
            for item in document["attachments"]
        ),
        str(document["revision_digest"]),
    )


def _attachment_projection(
    documents: tuple[IntakeDocument, ...],
    *,
    max_fetch_bytes: int,
) -> dict[str, object]:
    ordered = tuple(sorted(documents, key=lambda item: item.hop))
    if not ordered or tuple(item.hop for item in ordered) != tuple(range(len(ordered))) or len(ordered) > 5:
        raise _Unsettled("attachment-chain-invalid")
    resource = _resource_identity(ordered[0].resource_identity or ordered[0].endpoint)
    for index, document in enumerate(ordered[:-1]):
        if document.status not in {301, 302, 303, 307, 308} or not document.location:
            raise _Unsettled("attachment-chain-invalid")
        if _lineage_identity(document.location) != _lineage_identity(ordered[index + 1].endpoint):
            location_path = urllib.parse.urlsplit(document.location).path
            next_path = urllib.parse.urlsplit(ordered[index + 1].endpoint).path
            if not location_path or location_path != next_path:
                raise _Unsettled("attachment-chain-invalid")
    document = ordered[-1]
    final_host = urllib.parse.urlsplit(document.endpoint).hostname or ""
    location_digest = _digest(document.location.encode()) if document.location else ""

    def hop_document(item: IntakeDocument) -> dict[str, object]:
        return {
            "hop": item.hop,
            "endpoint": _lineage_identity(item.endpoint),
            "status": item.status,
            "location": _lineage_identity(item.location) if item.location else "",
            "location_digest": _digest(item.location.encode()) if item.location else "",
            "final_host": urllib.parse.urlsplit(item.endpoint).hostname or "",
            "content_type": item.content_type.split(";", 1)[0].strip().lower(),
            "original_bytes": item.original_bytes,
            "stored_bytes": len(item.raw),
            "complete": item.complete,
            "outcome": item.outcome,
            "reason": "policy-fetch-limit" if item.outcome == "too-large" else "answered",
            "raw_digest": item.raw_digest,
            "auth_forwarded": item.auth_forwarded,
            "request_id": item.request_id,
            "classified_event_id": item.classified_event_id,
            "raw_blob_digest": item.raw_blob_digest,
            "sanitized_blob_digest": item.sanitized_blob_digest,
            "request_digest": item.request_digest,
            "profile_digest": item.profile_digest,
            "capability_digest": item.capability_digest,
            "peer_digest": item.peer_digest,
        }

    if document.outcome == "too-large":
        return {
            "resource_identity": resource,
            "name": urllib.parse.unquote(urllib.parse.urlsplit(resource).path.rsplit("/", 1)[-1]) or "attachment",
            "content_digest": "",
            "bytes": 0,
            "content_type": document.content_type.split(";", 1)[0].strip().lower(),
            "status": document.status,
            "outcome": "over-limit",
            "complete": False,
            "reason": "policy-fetch-limit",
            "original_bytes": document.original_bytes,
            "stored_bytes": len(document.raw),
            "location_digest": location_digest,
            "final_host": final_host,
            "over_limit": {"cap": max_fetch_bytes, "lower_bound": document.original_bytes},
            "hops": [hop_document(item) for item in ordered],
        }
    if document.status != 200:
        raise _Unsettled("attachment-unsettled")
    name = urllib.parse.unquote(urllib.parse.urlsplit(resource).path.rsplit("/", 1)[-1]) or "attachment"
    return {
        "resource_identity": resource,
        "name": name,
        "content_digest": document.raw_digest,
        "bytes": len(document.raw),
        "content_type": document.content_type.split(";", 1)[0].strip().lower(),
        "status": document.status,
        "outcome": "held",
        "complete": True,
        "reason": "answered",
        "original_bytes": document.original_bytes,
        "stored_bytes": len(document.raw),
        "location_digest": location_digest,
        "final_host": final_host,
        "over_limit": None,
        "hops": [hop_document(item) for item in ordered],
    }


def _resource_identity(listing: str) -> str:
    parsed = urllib.parse.urlsplit(listing)
    path = parsed.path
    segments = tuple(part for part in path.split("/") if part)
    qualified_ctfd = len(segments) >= 3 and segments[-3] == "files" and bool(segments[-2]) and bool(segments[-1])
    if not qualified_ctfd:
        return listing
    if parsed.scheme or parsed.netloc:
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").encode("idna").decode().lower()
        port = parsed.port
        netloc = host if port is None or (scheme, port) in {("https", 443), ("http", 80)} else f"{host}:{port}"
        return urllib.parse.urlunsplit((scheme, netloc, path, "", ""))
    return path.lstrip("/")


def _lineage_identity(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if not parsed.scheme and not parsed.netloc:
        return parsed.path
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").encode("idna").decode().lower()
    port = parsed.port
    netloc = host if port is None or (scheme, port) in {("https", 443), ("http", 80)} else f"{host}:{port}"
    return urllib.parse.urlunsplit((scheme, netloc, parsed.path, "", ""))


def _comparison_projection(projection: dict[str, object]) -> dict[str, object]:
    compared = {name: value for name, value in projection.items() if name != "challenges"}
    challenges = []
    for source in projection["challenges"]:
        challenge = dict(source)
        normalized = []
        for attachment in challenge["attachments"]:
            item = dict(attachment)
            item["hops"] = [
                {
                    "hop": hop["hop"],
                    "endpoint": hop["endpoint"],
                    "status": hop["status"],
                    "location": hop["location"],
                    "content_type": hop["content_type"],
                    "auth_forwarded": hop["auth_forwarded"],
                }
                for hop in item["hops"]
            ]
            normalized.append(item)
        challenge["attachments"] = normalized
        challenges.append(challenge)
    compared["challenges"] = challenges
    compared["tombstones"] = [item["id"] for item in projection["tombstones"]]
    return compared


def _present(value: object) -> PresentValue:
    assert isinstance(value, Mapping)
    return PresentValue(str(value["state"]), value.get("value"))


def _value_fact(value: object, *, boolean: bool, observed_at: dt.datetime) -> ValueFact:
    if isinstance(value, Mapping):
        parsed = bool(value["value"]) if boolean else int(value["value"])
        stamped = value.get("observed_at")
        return ValueFact(
            parsed,
            str(value["source"]),
            str(value["outcome"]),
            dt.datetime.fromisoformat(str(stamped)) if stamped else observed_at,
        )
    return ValueFact(bool(value) if boolean else int(value), "list", observed_at=observed_at)


def _success_data(document: IntakeDocument) -> object:
    wrapper = _json(document)
    if not isinstance(wrapper, Mapping) or wrapper.get("success") is not True or "data" not in wrapper:
        raise _Unsettled(f"{document.kind}-malformed")
    return wrapper["data"]


def _json(document: IntakeDocument) -> object:
    try:
        return json.loads(document.raw)
    except (UnicodeDecodeError, ValueError):
        raise _Unsettled(f"{document.kind}-malformed") from None


def _required_id(source: Mapping[str, object], name: str) -> TypedId:
    if name not in source:
        raise _Unsettled("challenge-id-missing")
    try:
        return TypedId.parse(source[name])
    except ValueError:
        raise _Unsettled("challenge-id-malformed") from None


def _integer(value: object, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _Unsettled("numeric-field-malformed")
    return value


def _optional_integer(
    source: Mapping[str, object],
    name: str,
    *,
    minimum: int,
    nullable: bool = False,
) -> None:
    if name not in source or (source[name] is None and nullable):
        return
    _integer(source[name], minimum=minimum)


def _optional_bool(source: Mapping[str, object], name: str) -> None:
    if name in source and not isinstance(source[name], bool):
        raise _Unsettled("boolean-field-malformed")


def _id_value(document: Mapping[str, object]) -> int | str:
    return int(str(document["value"])) if document["type"] == "integer" else str(document["value"])


def _id_key(document: Mapping[str, object]) -> tuple[str, int | str]:
    return str(document["type"]), _id_value(document)


def _pass_document(observed: IntakePass) -> dict[str, object]:
    return {
        "number": observed.number,
        "identity_before": _document_document(observed.identity_before),
        "landing": _document_document(observed.landing) if observed.landing else None,
        "read_control": _document_document(observed.read_control) if observed.read_control else None,
        "scoreboard": _document_document(observed.scoreboard) if observed.scoreboard else None,
        "mana": _document_document(observed.mana) if observed.mana else None,
        "pages": [_document_document(item) for item in observed.pages],
        "details": [_document_document(item) for item in observed.details],
        "attachments": [_document_document(item) for item in observed.attachments],
        "absence_controls": [_document_document(item) for item in observed.absence_controls],
        "identity_after": _document_document(observed.identity_after),
    }


def _pass_from_document(value: object) -> IntakePass:
    if not isinstance(value, Mapping):
        raise ValueError("Intake pass is not an object")
    landing = value.get("landing")
    read_control = value.get("read_control")
    scoreboard = value.get("scoreboard")
    mana = value.get("mana")
    return IntakePass(
        int(value["number"]),
        _document_from_document(value["identity_before"]),
        tuple(_document_from_document(item) for item in _list(value["pages"])),
        tuple(_document_from_document(item) for item in _list(value["details"])),
        tuple(_document_from_document(item) for item in _list(value["attachments"])),
        _document_from_document(value["identity_after"]),
        tuple(_document_from_document(item) for item in _list(value["absence_controls"])),
        _document_from_document(landing) if landing is not None else None,
        _document_from_document(read_control) if read_control is not None else None,
        _document_from_document(scoreboard) if scoreboard is not None else None,
        _document_from_document(mana) if mana is not None else None,
    )


def _document_document(document: IntakeDocument) -> dict[str, object]:
    return {
        **document.evidence(),
        "raw": base64.b64encode(document.raw).decode("ascii"),
    }


def _document_from_document(value: object) -> IntakeDocument:
    if not isinstance(value, Mapping):
        raise ValueError("Intake document is not an object")
    try:
        raw = base64.b64decode(str(value["raw"]), validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("Intake document raw body is not base64") from error
    document = IntakeDocument(
        request_id=str(value["request_id"]),
        classified_event_id=str(value["classified_event_id"]),
        pass_no=int(value["pass"]),
        kind=str(value["kind"]),
        endpoint=str(value["endpoint"]),
        status=int(value["status"]),
        content_type=str(value["content_type"]),
        raw=raw,
        original_bytes=int(value["original_bytes"]),
        complete=bool(value["complete"]),
        profile_digest=str(value["profile_digest"]),
        subject_digest=str(value["subject_digest"]),
        capability_digest=str(value["capability_digest"]),
        peer_digest=str(value["peer_digest"]),
        page=int(value["page"]),
        challenge_id=value.get("challenge_id"),
        outcome=str(value["outcome"]),
        location=str(value["location"]),
        raw_blob_digest=str(value["raw_blob_digest"]),
        sanitized_blob_digest=str(value["sanitized_blob_digest"]),
        request_digest=str(value["request_digest"]),
        hop=int(value["hop"]),
        auth_forwarded=bool(value["auth_forwarded"]),
        resource_identity=str(value["resource_identity"]),
    )
    if _document_document(document) != dict(value):
        raise ValueError("Intake document evidence disagrees")
    return document


def _snapshot_from_document(value: object) -> IntakeSnapshot:
    if (
        not isinstance(value, Mapping)
        or not isinstance(value.get("challenges"), list)
        or not isinstance(value.get("tombstones"), list)
    ):
        raise ValueError("Intake snapshot is not an object")
    snapshot = IntakeSnapshot(
        str(value["snapshot_id"]),
        str(value["profile_digest"]),
        dt.datetime.fromisoformat(str(value["observed_at"])),
        tuple(
            _snapshot_challenge(item, dt.datetime.fromisoformat(str(value["observed_at"])))
            for item in value["challenges"]
        ),
        str(value["empty_diagnosis"]),
        tuple(_tombstone_from_document(item) for item in value["tombstones"]),
        tuple(dict(item) for item in _list(value.get("scoreboard"))),
        dict(value["mana"]) if isinstance(value.get("mana"), Mapping) else None,
    )
    if snapshot.document() != dict(value):
        raise ValueError("Intake snapshot shape is unsupported")
    return snapshot


def _tombstone_from_document(value: object) -> TombstoneSnapshot:
    if not isinstance(value, Mapping) or not isinstance(value.get("id"), Mapping):
        raise ValueError("Intake tombstone is not an object")
    identifier = value["id"]
    evidence = value.get("absence_evidence")
    if not isinstance(evidence, list) or len(evidence) != 2 or any(not isinstance(item, Mapping) for item in evidence):
        raise ValueError("Intake tombstone evidence is invalid")
    return TombstoneSnapshot(
        TypedId(str(identifier["type"]), _id_value(identifier)),
        str(value["prior_revision_digest"]),
        str(value["prior_snapshot_digest"]),
        (dict(evidence[0]), dict(evidence[1])),
        dt.datetime.fromisoformat(str(value["observed_at"])),
        str(value["reason"]),
    )


def _fence_from_document(value: Mapping[str, object]) -> PriorFence:
    if set(value) != {"profile_digest", "event_id", "event_digest", "snapshot_digest"}:
        raise ValueError("Intake prior fence shape is unsupported")
    return PriorFence(
        str(value["profile_digest"]),
        str(value["event_id"]),
        str(value["event_digest"]),
        str(value["snapshot_digest"]),
    )


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("Intake document collection is not a list")
    return value


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


__all__ = [
    "contract_from_document",
    "ChallengeSnapshot",
    "IntakeContract",
    "IntakeAuthority",
    "IntakeDecision",
    "IntakeDocument",
    "IntakePass",
    "IntakeProbe",
    "IntakeSnapshot",
    "NoCoherentSnapshot",
    "PriorFence",
    "SnapshotAvailable",
    "TombstoneSnapshot",
    "TypedId",
    "availability",
    "decision_document",
    "probe_document",
    "probe_from_document",
    "qualify",
    "snapshot_from_document",
]
