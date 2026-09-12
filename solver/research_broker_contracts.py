"""Public contracts for bounded public Research observations."""

from __future__ import annotations

import enum
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


@dataclass(frozen=True)
class ResearchLimits:
    max_body_bytes: int
    timeout_seconds: float
    max_redirects: int
    cache_seconds: int

    def __post_init__(self) -> None:
        if self.max_body_bytes <= 0 or self.timeout_seconds <= 0 or self.max_redirects < 0 or self.cache_seconds < 0:
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
        }
        if set(payload) != expected:
            raise InvalidEventError("Research-broker event fields are invalid", sequence=sequence)
        try:
            ResearchOutcome(str(payload["outcome"]))
        except ValueError as error:
            raise InvalidEventError("Research-broker outcome is invalid", sequence=sequence) from error


__all__ = [
    "RESEARCH_BROKER_RECORDED",
    "ResearchBrokerRecorded",
    "ResearchLimits",
    "ResearchOutcome",
    "ResearchProvenance",
    "ResearchResult",
    "ResearchTransportResult",
]
