"""Public contracts for bounded public Research observations."""

from __future__ import annotations

import enum
import base64
from dataclasses import dataclass
from typing import Any, Mapping

from solver.event_store_contracts import InvalidEventError

RESEARCH_BROKER_RECORDED = "research-broker.recorded"


class ResearchOutcome(str, enum.Enum):
    ANSWERED = "answered"
    DENIED = "denied"
    TOO_LARGE = "too-large"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    REVOKED = "revoked"
    CAPABILITY_REFUSED = "capability-refused"
    STALE_CACHE = "stale-cache"
    BUDGET_EXHAUSTED = "budget-exhausted"


class ResearchKind(str, enum.Enum):
    DNS = "dns"
    IDENTITY = "identity"
    EMAIL = "email"
    DOMAIN = "domain"
    GEO = "geo"


class ResearchPolicyDecision(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    NOT_APPLICABLE = "not-applicable"


def research_policy_decision(terms_decision: str, robots_decision: str) -> str:
    decisions = (terms_decision, robots_decision)
    if any(decision in {"", ResearchPolicyDecision.DENY.value} for decision in decisions):
        return ResearchPolicyDecision.DENY.value
    if all(decision == ResearchPolicyDecision.NOT_APPLICABLE.value for decision in decisions):
        return ResearchPolicyDecision.NOT_APPLICABLE.value
    return ResearchPolicyDecision.ALLOW.value


@dataclass(frozen=True)
class ResearchSource:
    kind: ResearchKind
    url_template: str
    terms: str
    robots: str
    terms_decision: str = ""
    robots_decision: str = ""

    def __post_init__(self) -> None:
        allowed = {"", *(item.value for item in ResearchPolicyDecision)}
        if self.terms_decision not in allowed or self.robots_decision not in allowed:
            raise ValueError("Research source policy decision is invalid")

    @property
    def policy_decision(self) -> str:
        return research_policy_decision(self.terms_decision, self.robots_decision)


@dataclass(frozen=True)
class ResearchQuery:
    kind: ResearchKind
    source_id: str
    subject: str
    body: bytes = b""
    content_type: str = ""

    @classmethod
    def live(cls, kind: ResearchKind, source_id: str, subject: str) -> "ResearchQuery":
        return cls(kind, source_id, subject)

    @classmethod
    def recorded(
        cls,
        kind: ResearchKind,
        source_id: str,
        subject: str,
        body: bytes,
        *,
        content_type: str,
    ) -> "ResearchQuery":
        return cls(kind, source_id, subject, bytes(body), content_type)

    def document(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "source_id": self.source_id,
            "subject": self.subject,
            "body": base64.b64encode(self.body).decode(),
            "content_type": self.content_type,
        }


@dataclass(frozen=True)
class ResearchLimits:
    max_body_bytes: int
    timeout_seconds: float
    max_redirects: int
    cache_seconds: int
    max_requests: int = 32
    max_total_bytes: int = 1024 * 1024
    max_total_seconds: float = 60
    min_interval_ms: int = 0

    def __post_init__(self) -> None:
        if (
            self.max_body_bytes <= 0
            or self.timeout_seconds <= 0
            or self.max_redirects < 0
            or self.cache_seconds < 0
            or self.max_requests <= 0
            or self.max_total_bytes <= 0
            or self.max_total_seconds <= 0
            or self.min_interval_ms < 0
        ):
            raise ValueError("Research limits are invalid")


@dataclass(frozen=True)
class ResearchTransportResult:
    status: int = 0
    headers: Mapping[str, str] = None  # type: ignore[assignment]
    body: bytes = b""
    elapsed_ms: int = 0
    redirect_url: str = ""
    outcome: ResearchOutcome = ResearchOutcome.ANSWERED

    def __post_init__(self) -> None:
        if self.headers is None:
            object.__setattr__(self, "headers", {})


@dataclass(frozen=True)
class ResearchProvenance:
    dns_chain: tuple[tuple[str, tuple[str, ...]], ...] = ()
    redirect_chain: tuple[str, ...] = ()
    body_digest: str = ""
    observed_at: str = ""
    expires_at: str = ""
    elapsed_ms: int = 0
    kind: ResearchKind | None = None
    source_id: str = ""
    terms: str = ""
    robots: str = ""
    origin: str = ""
    query_digest: str = ""
    terms_decision: str = ""
    robots_decision: str = ""
    policy_decision: str = ""


@dataclass(frozen=True)
class ResearchResult:
    outcome: ResearchOutcome
    body: bytes = b""
    status: int = 0
    content_type: str = ""
    provenance: ResearchProvenance = ResearchProvenance()
    request_id: str = ""
    cached: bool = False


@dataclass(frozen=True)
class ResearchBrokerRecorded:
    event_id: str
    request_id: str
    run_id: str
    boot_id: str
    generation_id: str
    attempt_id: str
    url_digest: str
    outcome: ResearchOutcome
    content_type: str
    dns_chain: tuple[tuple[str, tuple[str, ...]], ...]
    redirect_chain: tuple[str, ...]
    observed_at: str
    expires_at: str
    elapsed_ms: int
    cached: bool
    max_body_bytes: int
    timeout_ms: int
    max_redirects: int
    cache_seconds: int
    kind: ResearchKind | None
    source_id: str
    terms: str
    robots: str
    origin: str
    query_digest: str
    terms_decision: str
    robots_decision: str
    policy_decision: str
    max_requests: int
    max_total_bytes: int
    max_total_seconds_ms: int
    min_interval_ms: int

    @property
    def event_type(self) -> str:
        return RESEARCH_BROKER_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "request_id": self.request_id,
            "run_id": self.run_id,
            "boot_id": self.boot_id,
            "generation_id": self.generation_id,
            "attempt_id": self.attempt_id,
            "url_digest": self.url_digest,
            "outcome": self.outcome.value,
            "content_type": self.content_type,
            "dns_chain": [[host, list(addresses)] for host, addresses in self.dns_chain],
            "redirect_chain": list(self.redirect_chain),
            "observed_at": self.observed_at,
            "expires_at": self.expires_at,
            "elapsed_ms": self.elapsed_ms,
            "cached": self.cached,
            "max_body_bytes": self.max_body_bytes,
            "timeout_ms": self.timeout_ms,
            "max_redirects": self.max_redirects,
            "cache_seconds": self.cache_seconds,
            "kind": self.kind.value if self.kind else "",
            "source_id": self.source_id,
            "terms": self.terms,
            "robots": self.robots,
            "origin": self.origin,
            "query_digest": self.query_digest,
            "terms_decision": self.terms_decision,
            "robots_decision": self.robots_decision,
            "policy_decision": self.policy_decision,
            "max_requests": self.max_requests,
            "max_total_bytes": self.max_total_bytes,
            "max_total_seconds_ms": self.max_total_seconds_ms,
            "min_interval_ms": self.min_interval_ms,
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        expected = {
            "event_id",
            "request_id",
            "run_id",
            "boot_id",
            "generation_id",
            "attempt_id",
            "url_digest",
            "outcome",
            "content_type",
            "dns_chain",
            "redirect_chain",
            "observed_at",
            "expires_at",
            "elapsed_ms",
            "cached",
            "blob_digest",
            "blob_bytes",
            "max_body_bytes",
            "timeout_ms",
            "max_redirects",
            "cache_seconds",
            "kind",
            "source_id",
            "terms",
            "robots",
            "origin",
            "query_digest",
            "terms_decision",
            "robots_decision",
            "policy_decision",
            "max_requests",
            "max_total_bytes",
            "max_total_seconds_ms",
            "min_interval_ms",
        }
        if set(payload) != expected:
            raise InvalidEventError("Research-broker event fields are invalid", sequence=sequence)
        try:
            ResearchOutcome(str(payload["outcome"]))
            if payload["kind"]:
                ResearchKind(str(payload["kind"]))
            for field in ("terms_decision", "robots_decision", "policy_decision"):
                if payload[field] not in {"", *(item.value for item in ResearchPolicyDecision)}:
                    raise ValueError(field)
            if any(payload[field] for field in ("terms_decision", "robots_decision", "policy_decision")) and (
                payload["policy_decision"]
                != research_policy_decision(payload["terms_decision"], payload["robots_decision"])
            ):
                raise ValueError("policy_decision")
        except (TypeError, ValueError) as error:
            raise InvalidEventError("Research-broker outcome is invalid", sequence=sequence) from error


__all__ = [
    "RESEARCH_BROKER_RECORDED",
    "ResearchBrokerRecorded",
    "ResearchLimits",
    "ResearchKind",
    "ResearchOutcome",
    "ResearchPolicyDecision",
    "ResearchProvenance",
    "ResearchQuery",
    "ResearchResult",
    "ResearchTransportResult",
    "ResearchSource",
    "research_policy_decision",
]
