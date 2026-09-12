"""Qualify one Board profile from complete public-contract observations."""

from __future__ import annotations

import hashlib
import json
import re
import datetime as dt
from collections.abc import Mapping
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from solver.board import ANSWERED, Mana, instance_ledger_rows
from solver.profile import (
    ABSENT,
    ANONYMOUS_ANSWERED,
    ANONYMOUS_REFUSED,
    ANONYMOUS_UNREADABLE,
    ASSUMED,
    CTFD_DEFAULT_INCORRECT_PER_MIN,
    INSTALLED,
    STATED,
    UNREADABLE,
    Profile,
    Rules,
)

SCHEMA_VERSION = 1
_WINDOW_INIT = re.compile(rb"window\.init\s*=\s*(.*?)</script", re.DOTALL | re.IGNORECASE)
PROFILE_ENDPOINTS = {
    "identity": "/api/v1/users/me",
    "landing": "/",
    "read_contract": "/api/v1/challenges?field=intake-is-not-a-field&q=a",
    "challenges": "/api/v1/challenges",
    "ledger": "/plugins/ctfd-chall-manager/instances",
    "mana": "/api/v1/plugins/ctfd-chall-manager/mana",
    "configs": "/api/v1/configs",
    "anonymous_challenges": "/api/v1/challenges",
}


@dataclass(frozen=True)
class ProfileDocument:
    """One complete, sanitized response used by profile qualification."""

    request_id: str
    endpoint: str
    status: int
    content_type: str
    body: bytes
    original_bytes: int = 0
    complete: bool = True

    def __post_init__(self) -> None:
        if self.original_bytes == 0 and self.body:
            object.__setattr__(self, "original_bytes", len(self.body))

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def media_type(self) -> str:
        return self.content_type.partition(";")[0].strip().lower()


@dataclass(frozen=True)
class ProfileCycle:
    """One complete pass over the remote profile controls and facts."""

    identity: ProfileDocument
    landing: ProfileDocument
    read_contract: ProfileDocument
    challenges: ProfileDocument
    ledger: ProfileDocument
    mana: ProfileDocument
    configs: ProfileDocument
    anonymous_challenges: ProfileDocument


@dataclass(frozen=True)
class ProfileProbe:
    """The two passes whose semantic agreement qualifies one remote snapshot."""

    probe_id: str
    rules_source: str
    cycles: tuple[ProfileCycle, ...]
    schema_version: int = SCHEMA_VERSION


@dataclass(frozen=True)
class ProfileDecision:
    """A profile may leave evidence without acquiring downstream authority."""

    authoritative: bool
    reason: str
    profile: Profile | None
    unsettled_fields: tuple[str, ...] = ()


PROFILE_FIELDS = (
    "authenticated_identity",
    "read_contract",
    "instanced_challenges",
    "chall_manager",
    "mana",
    "submissions_per_minute",
    "configs_outcome",
    "board_window",
    "unauthenticated_read",
)
AUTHENTICATED_FIELDS = PROFILE_FIELDS[:-1]

_DOCUMENT_FIELDS = {
    "identity": AUTHENTICATED_FIELDS,
    "landing": PROFILE_FIELDS,
    "read_contract": ("read_contract",),
    "challenges": ("instanced_challenges",),
    "ledger": ("chall_manager",),
    "mana": ("mana",),
    "configs": ("submissions_per_minute", "configs_outcome", "board_window"),
    "anonymous_challenges": ("unauthenticated_read",),
}

_PROJECTION_FIELDS = {
    "identity": ("authenticated_identity",),
    "landing_window": ("board_window",),
    "challenges": ("instanced_challenges",),
    "instanced_challenges": ("instanced_challenges",),
    "chall_manager": ("chall_manager",),
    "mana": ("mana",),
    "configs": ("submissions_per_minute", "configs_outcome", "board_window"),
    "unauthenticated_read": ("unauthenticated_read",),
}


class _Unsettled(ValueError):
    def __init__(self, reason: str, fields: tuple[str, ...]) -> None:
        super().__init__(reason)
        self.fields = fields


DocumentReader = Callable[[str, str, bool], ProfileDocument]


def collect_cycle(
    probe_id: str,
    cycle: int,
    authenticated: DocumentReader,
    anonymous: DocumentReader,
) -> ProfileCycle:
    """Collect the fixed public-contract questions; policy remains in ``qualify``."""

    prefix = f"{probe_id}:{cycle}"
    return ProfileCycle(
        identity=authenticated(f"{prefix}:identity", PROFILE_ENDPOINTS["identity"], True),
        landing=authenticated(f"{prefix}:landing", PROFILE_ENDPOINTS["landing"], False),
        read_contract=authenticated(
            f"{prefix}:read-contract",
            PROFILE_ENDPOINTS["read_contract"],
            True,
        ),
        challenges=authenticated(f"{prefix}:challenges", PROFILE_ENDPOINTS["challenges"], True),
        ledger=authenticated(f"{prefix}:ledger", PROFILE_ENDPOINTS["ledger"], False),
        mana=authenticated(f"{prefix}:mana", PROFILE_ENDPOINTS["mana"], True),
        configs=authenticated(f"{prefix}:configs", PROFILE_ENDPOINTS["configs"], True),
        anonymous_challenges=anonymous(
            f"{prefix}:anonymous-challenges", PROFILE_ENDPOINTS["anonymous_challenges"], True
        ),
    )


def qualify_direct(board, anyone, rules: Rules) -> ProfileDecision:
    """Compatibility adapter for tests and unsupervised v1; production uses the broker."""

    def reader(source):
        def read(request_id: str, endpoint: str, json_content_type: bool) -> ProfileDocument:
            try:
                answer = source.inspect("GET", endpoint, json_content_type=json_content_type)
            except OSError:
                return ProfileDocument(request_id, endpoint, 0, "", b"")
            return ProfileDocument(
                request_id,
                endpoint,
                answer.status,
                answer.content_type,
                answer.body,
                len(answer.body),
                True,
            )

        return read

    probe_id = "profile-probe-direct"
    observed = ProfileProbe(
        probe_id,
        "compatibility-direct",
        tuple(collect_cycle(probe_id, cycle, reader(board), reader(anyone)) for cycle in (1, 2)),
    )
    return qualify(observed, rules)


def qualify(probe: ProfileProbe, rules: Rules) -> ProfileDecision:
    """Return the deterministic profile decision for a complete two-pass probe."""

    if probe.schema_version != SCHEMA_VERSION or len(probe.cycles) != 2:
        return _refused("profile probe schema or cycle count is unsupported", PROFILE_FIELDS)
    for wrapper in rules.flag_wrappers:
        try:
            re.compile(wrapper)
        except re.error as error:
            return _refused(f"the Flag wrapper {wrapper!r} does not compile — {error}", PROFILE_FIELDS)
    try:
        projections = tuple(_cycle_projection(cycle) for cycle in probe.cycles)
    except _Unsettled as error:
        return _refused(str(error), error.fields)
    if projections[0] != projections[1]:
        changed = tuple(key for key in projections[0] if projections[0][key] != projections[1][key])
        fields = _ordered_fields(field for key in changed for field in _PROJECTION_FIELDS[key])
        return _refused("profile-relevant facts changed between probe cycles", fields)
    projection = projections[0]
    configs = projection["configs"]
    config_values = configs["values"]
    stated_limit = config_values.get("incorrect_submissions_per_min")
    try:
        limit = int(stated_limit) if stated_limit not in (None, "") else CTFD_DEFAULT_INCORRECT_PER_MIN
        source = STATED if stated_limit not in (None, "") else ASSUMED
    except (TypeError, ValueError):
        limit, source = CTFD_DEFAULT_INCORRECT_PER_MIN, ASSUMED
    mana_observation = projection["mana"]
    window_sources = {"configs": _config_window(configs), "landing": projection["landing_window"]}
    config_window = window_sources["configs"]["value"]
    landing_window = window_sources["landing"]["value"]
    partial = any(window_sources[source]["outcome"] == "partial" for source in ("configs", "landing"))
    disagrees = bool(
        window_sources["configs"]["outcome"] == "published"
        and window_sources["landing"]["outcome"] == "published"
        and config_window != landing_window
    )
    window_sources["disagrees"] = disagrees
    if partial:
        return _refused("Board window observation is partial", ("board_window",))
    if disagrees:
        return _refused("Board window observations disagree", ("board_window",))
    profile = Profile(
        rules=rules,
        chall_manager=projection["chall_manager"],
        instanced_challenges=projection["instanced_challenges"],
        unauthenticated_read=projection["unauthenticated_read"],
        mana=None
        if mana_observation["outcome"] != ANSWERED
        else Mana(ANSWERED, mana_observation["used"], mana_observation["total"]),
        mana_outcome=mana_observation["outcome"],
        submissions_per_minute=limit,
        submissions_per_minute_source=source,
        configs_outcome=configs["outcome"],
        board_window=dict(config_window or landing_window),
        board_window_observations=window_sources,
    )
    return ProfileDecision(True, "compatible", profile)


def rules_document(rules: Rules) -> dict[str, object]:
    """Canonical JSON shape for the tracked half of a Board profile."""

    return {
        "event": rules.event,
        "url": rules.url,
        "flag_wrappers": list(rules.flag_wrappers),
        "window_seconds": rules.window_seconds,
        "prohibitions": list(rules.prohibitions),
        "closes_at": rules.closes_at.isoformat() if rules.closes_at else None,
        "requires": list(rules.requires),
        "web_search": rules.web_search,
    }


def rules_from_document(document: Mapping[str, object]) -> Rules:
    expected = {
        "event",
        "url",
        "flag_wrappers",
        "window_seconds",
        "prohibitions",
        "closes_at",
        "requires",
        "web_search",
    }
    if set(document) != expected:
        raise ValueError("Board-profile rules document is unsupported")
    closes_at = dt.datetime.fromisoformat(str(document["closes_at"])) if document["closes_at"] else None
    return Rules(
        event=str(document["event"]),
        url=str(document["url"]),
        flag_wrappers=tuple(str(item) for item in document["flag_wrappers"]),
        window_seconds=float(document["window_seconds"]),
        prohibitions=tuple(str(item) for item in document["prohibitions"]),
        closes_at=closes_at,
        requires=tuple(str(item) for item in document["requires"]),
        web_search=bool(document["web_search"]),
    )


def decision_document(decision: ProfileDecision) -> dict[str, object]:
    return {
        "authoritative": decision.authoritative,
        "reason": decision.reason,
        "profile": decision.profile.recorded() if decision.profile else None,
        "unsettled_fields": list(decision.unsettled_fields),
    }


def decision_from_document(document: Mapping[str, object], rules: Rules) -> ProfileDecision:
    if set(document) != {"authoritative", "reason", "profile", "unsettled_fields"}:
        raise ValueError("Board-profile decision shape is unsupported")
    raw_unsettled = document["unsettled_fields"]
    if not isinstance(raw_unsettled, list) or any(not isinstance(field, str) for field in raw_unsettled):
        raise ValueError("Board-profile unsettled fields are invalid")
    unsettled = tuple(raw_unsettled)
    if unsettled != _ordered_fields(unsettled):
        raise ValueError("Board-profile unsettled fields are invalid")
    authoritative = document["authoritative"] is True
    if not authoritative:
        if document["profile"] is not None:
            raise ValueError("non-authoritative Board profile carries a value")
        return ProfileDecision(False, str(document["reason"]), None, unsettled)
    recorded = document["profile"]
    if not isinstance(recorded, Mapping):
        raise ValueError("authoritative Board profile is absent")
    mana = recorded.get("mana")
    if mana is not None and not isinstance(mana, Mapping):
        raise ValueError("Board-profile Mana is invalid")
    profile = Profile(
        rules=rules,
        chall_manager=str(recorded["chall_manager"]),
        instanced_challenges=int(recorded["instanced_challenges"]),
        unauthenticated_read=str(recorded["unauthenticated_read"]),
        mana=None
        if mana is None
        else Mana(str(mana["outcome"]), int(mana["used"]), int(mana["total"]), str(mana.get("detail", ""))),
        mana_outcome=str(recorded["mana_outcome"]),
        submissions_per_minute=int(recorded["submissions_per_minute"]),
        submissions_per_minute_source=str(recorded["submissions_per_minute_source"]),
        configs_outcome=str(recorded["configs_outcome"]),
        board_window=dict(recorded.get("board_window", {})),
        board_window_observations=dict(recorded.get("board_window_observations", {})),
    )
    if profile.recorded() != dict(recorded):
        raise ValueError("Board-profile decision contains inconsistent tracked Rules")
    return ProfileDecision(True, str(document["reason"]), profile, unsettled)


def _cycle_projection(cycle: ProfileCycle) -> dict[str, Any]:
    incomplete = tuple(name for name in ProfileCycle.__dataclass_fields__ if not getattr(cycle, name).complete)
    if incomplete:
        raise _Unsettled(
            "profile response was truncated",
            _ordered_fields(field for name in incomplete for field in _DOCUMENT_FIELDS[name]),
        )
    try:
        return _project_complete_cycle(cycle)
    except _Unsettled:
        raise
    except ValueError as error:
        raise _Unsettled(str(error), PROFILE_FIELDS) from error


def _project_complete_cycle(cycle: ProfileCycle) -> dict[str, Any]:
    landing = cycle.landing
    if landing.status != 200 or landing.media_type != "text/html":
        raise _Unsettled("authenticated landing page is unreadable", PROFILE_FIELDS)
    identity = _settle(AUTHENTICATED_FIELDS, _json_data, cycle.identity, "authenticated identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("id"), int):
        raise _Unsettled("authenticated identity is malformed", AUTHENTICATED_FIELDS)
    marker = _settle(AUTHENTICATED_FIELDS, _window_init, landing)
    if marker.get("userId") != identity["id"]:
        raise _Unsettled("authenticated landing identity disagrees with users/me", AUTHENTICATED_FIELDS)
    if marker.get("userMode") == "teams" and marker.get("teamId") != identity.get("team_id"):
        raise _Unsettled("authenticated landing team disagrees with users/me", AUTHENTICATED_FIELDS)
    _settle(("read_contract",), _read_contract, cycle.read_contract, landing)
    challenges = _settle(("instanced_challenges",), _json_contract, cycle.challenges, landing, "Challenge list")
    if (
        not isinstance(challenges, list)
        or any(not isinstance(entry, dict) for entry in challenges)
        or any(
            not isinstance(entry.get("id"), int)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("type"), str)
            for entry in challenges
        )
    ):
        raise _Unsettled("Challenge list is malformed", ("instanced_challenges",))
    instanced = sum(entry.get("type") == "dynamic_iac" for entry in challenges)
    chall_manager = _settle(("chall_manager",), _ledger_contract, cycle.ledger, landing, identity)
    if instanced and chall_manager != INSTALLED:
        raise _Unsettled(
            "instanced Challenges have no compatible Instance ledger",
            ("instanced_challenges", "chall_manager"),
        )
    mana = _settle(("mana",), _optional_mana, cycle.mana, landing, chall_manager)
    configs = _settle(
        ("submissions_per_minute", "configs_outcome", "board_window"),
        _optional_configs,
        cycle.configs,
        landing,
    )
    anonymous = _settle(("unauthenticated_read",), _anonymous_outcome, cycle.anonymous_challenges, landing)
    return {
        "identity": {"id": identity["id"], "team_id": identity.get("team_id"), "user_mode": marker.get("userMode")},
        "landing_window": _landing_window(marker),
        "challenges": sorted((entry["id"], entry["name"], entry["type"]) for entry in challenges),
        "instanced_challenges": instanced,
        "chall_manager": chall_manager,
        "mana": mana,
        "configs": configs,
        "unauthenticated_read": anonymous,
    }


def _json_contract(document: ProfileDocument, landing: ProfileDocument, name: str) -> Any:
    if document.status == 200 and document.digest == landing.digest:
        raise ValueError(f"{name} was a landing-page catch-all")
    if document.status != 200 or document.media_type != "application/json":
        raise ValueError(f"{name} is not a genuine JSON contract")
    return _json_data(document, name)


def _json_data(document: ProfileDocument, name: str) -> Any:
    if document.status != 200 or document.media_type != "application/json":
        raise ValueError(f"{name} is not a genuine JSON contract")
    try:
        payload = json.loads(document.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} is malformed") from error
    if not isinstance(payload, dict) or payload.get("success") is not True or "data" not in payload:
        raise ValueError(f"{name} is malformed")
    return payload["data"]


def _window_init(document: ProfileDocument) -> dict[str, Any]:
    match = _WINDOW_INIT.search(document.body)
    if match is None:
        raise ValueError("authenticated landing identity marker is absent")
    block = match.group(1)
    marker = {name: _javascript_scalar(block, name) for name in ("userId", "teamId", "userMode", "start", "end")}
    marker = {name: value for name, value in marker.items() if value is not _MISSING}
    if not isinstance(marker.get("userId"), int) or marker.get("userMode") not in {"users", "teams"}:
        raise ValueError("authenticated landing identity marker is malformed")
    return marker


_MISSING = object()


def _javascript_scalar(block: bytes, name: str):
    key = re.escape(name.encode())
    found = re.search(rb"(?:['\"]?" + key + rb"['\"]?)\s*:\s*(null|-?\d+|'[^']*'|\"[^\"]*\")", block)
    if found is None:
        return _MISSING
    raw = found.group(1)
    if raw == b"null":
        return None
    if raw[:1] in (b"'", b'"'):
        return raw[1:-1].decode("utf-8", "strict")
    return int(raw)


def _landing_window(marker: Mapping[str, Any]) -> dict[str, Any]:
    return _window_observation(marker)


def _config_window(configs: Mapping[str, Any]) -> dict[str, Any]:
    if configs["outcome"] != "answered":
        return {"outcome": configs["outcome"], "value": {}}
    return _window_observation(configs["values"])


def _window_observation(values: Mapping[str, Any]) -> dict[str, Any]:
    present = {key: values[key] for key in ("start", "end") if key in values}
    if not present:
        return {"outcome": "not-published", "value": {}}
    value = {key: item for key, item in present.items() if item not in (None, "")}
    outcome = "published" if len(value) == 2 else "partial"
    return {"outcome": outcome, "value": value}


def _read_contract(document: ProfileDocument, landing: ProfileDocument) -> None:
    if document.status not in (400, 403):
        raise ValueError("read-contract control was not a CTFd field refusal")
    payload = _negative_contract(document, landing, "read-contract control", {"application/json"})
    if not isinstance(payload, dict) or payload.get("success") is not False:
        raise ValueError("read-contract control was not a CTFd field refusal")


def _ledger_contract(document: ProfileDocument, landing: ProfileDocument, identity: dict[str, Any]) -> str:
    if document.status == 404:
        _negative_contract(document, landing, "Instance ledger absence", {"application/json", "text/html"})
        return ABSENT
    if document.status != 200:
        if document.digest == landing.digest:
            raise ValueError("Instance ledger was a landing-page catch-all")
        return UNREADABLE
    if document.media_type != "text/html" or document.digest == landing.digest:
        raise ValueError("Instance ledger is not a genuine authenticated HTML contract")
    marker = _window_init(document)
    if marker.get("userId") != identity["id"]:
        raise ValueError("Instance ledger identity disagrees with users/me")
    if marker.get("userMode") == "teams" and marker.get("teamId") != identity.get("team_id"):
        raise ValueError("Instance ledger team disagrees with users/me")
    if instance_ledger_rows(document.body) is None:
        return UNREADABLE
    return INSTALLED


def _optional_mana(document: ProfileDocument, landing: ProfileDocument, chall_manager: str):
    if document.status == 404:
        _negative_contract(document, landing, "Mana absence", {"application/json"})
        return {"outcome": ABSENT}
    if chall_manager == ABSENT and document.status == 200:
        raise ValueError("Mana contract contradicts an absent Instance ledger")
    if chall_manager == UNREADABLE and document.status != 200:
        if document.digest == landing.digest:
            raise ValueError("Mana was a landing-page catch-all")
        return {"outcome": UNREADABLE}
    if document.status != 200:
        raise ValueError(f"/mana answered HTTP {document.status}, neither a total nor an absence")
    data = _json_contract(document, landing, "Mana")
    if not isinstance(data, dict) or not all(isinstance(data.get(key), int) for key in ("used", "total")):
        raise ValueError("Mana is malformed")
    return {"outcome": ANSWERED, "used": data["used"], "total": data["total"]}


def _optional_configs(document: ProfileDocument, landing: ProfileDocument) -> dict[str, Any]:
    if document.status in (401, 403):
        _negative_contract(document, landing, "Board configs refusal", {"application/json"})
        return {"outcome": "refused", "values": {}}
    if document.status == 404:
        _negative_contract(document, landing, "Board configs absence", {"application/json"})
        return {"outcome": "absent", "values": {}}
    if document.status == 0 or document.status >= 500:
        if document.digest == landing.digest:
            raise ValueError("Board configs were a landing-page catch-all")
        return {"outcome": "unreadable", "values": {}}
    data = _json_contract(document, landing, "Board configs")
    if not isinstance(data, list) or any(not isinstance(entry, dict) for entry in data):
        raise ValueError("Board configs are malformed")
    return {"outcome": "answered", "values": {str(entry.get("key")): entry.get("value") for entry in data}}


def _anonymous_outcome(document: ProfileDocument, landing: ProfileDocument) -> str:
    if document.status in (401, 403, 404) or 300 <= document.status < 400:
        _negative_contract(document, landing, "anonymous Challenge refusal", {"application/json", "text/html"})
        return ANONYMOUS_REFUSED
    if document.status == 0:
        return ANONYMOUS_UNREADABLE
    if document.status >= 500:
        if document.digest == landing.digest:
            raise ValueError("anonymous Challenge list was a landing-page catch-all")
        return ANONYMOUS_UNREADABLE
    data = _json_contract(document, landing, "anonymous Challenge list")
    if not isinstance(data, list):
        return ANONYMOUS_UNREADABLE
    return ANONYMOUS_ANSWERED


def _negative_contract(
    document: ProfileDocument,
    landing: ProfileDocument,
    name: str,
    media_types: set[str],
) -> Any:
    if document.digest == landing.digest:
        raise ValueError(f"{name} was a landing-page catch-all")
    if document.media_type not in media_types:
        raise ValueError(f"{name} has an unrecognised content type")
    if document.media_type != "application/json":
        return None
    try:
        payload = json.loads(document.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} is malformed") from error
    if not isinstance(payload, dict) or payload.get("success") is not False:
        raise ValueError(f"{name} is malformed")
    return payload


def _settle(fields: tuple[str, ...], operation, *args):
    try:
        return operation(*args)
    except _Unsettled:
        raise
    except ValueError as error:
        raise _Unsettled(str(error), fields) from error


def _ordered_fields(fields) -> tuple[str, ...]:
    supplied = set(fields)
    return tuple(field for field in PROFILE_FIELDS if field in supplied)


def _refused(reason: str, fields: tuple[str, ...]) -> ProfileDecision:
    return ProfileDecision(False, reason, None, _ordered_fields(fields))


__all__ = [
    "ProfileCycle",
    "ProfileDecision",
    "ProfileDocument",
    "ProfileProbe",
    "PROFILE_FIELDS",
    "PROFILE_ENDPOINTS",
    "decision_document",
    "decision_from_document",
    "collect_cycle",
    "qualify",
    "qualify_direct",
    "rules_document",
    "rules_from_document",
]
