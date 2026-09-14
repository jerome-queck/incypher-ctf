"""Public contracts for one Attempt's declared Target capability."""

from __future__ import annotations

import enum
import re
import base64
import json
from dataclasses import dataclass
from typing import Any, Mapping, TypedDict
from urllib.parse import urlencode

from solver.event_store_contracts import InvalidEventError

TARGET_EXCHANGE_RECORDED = "target-broker.recorded"
SCHEMA_VERSION = 1
TARGET_EXCHANGE_COMMAND = "exchange"
TARGET_HTTP_SESSION_COMMAND = "http-session"
TARGET_TCP_SESSION_COMMAND = "tcp-session"
TARGET_BROWSER_COMMAND = "browser"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}")


class TargetProtocol(str, enum.Enum):
    HTTP = "http"
    HTTPS = "https"
    TCP = "tcp"


class TargetOutcome(str, enum.Enum):
    ANSWERED = "answered"
    DENIED = "denied"
    TOO_LARGE = "too-large"
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    AMBIGUOUS_CLOSE = "ambiguous-close"
    REVOKED = "revoked"
    CAPABILITY_REFUSED = "capability-refused"
    BUDGET_EXHAUSTED = "budget-exhausted"


class TargetRecord(str, enum.Enum):
    RESERVED = "reserved"
    CLASSIFIED = "classified"


class HttpBodyKind(str, enum.Enum):
    RAW = "raw"
    TEXT = "text"
    JSON = "json"
    FORM = "form"


@dataclass(frozen=True)
class HttpBody:
    kind: HttpBodyKind
    content: bytes

    @classmethod
    def raw(cls, content: bytes) -> "HttpBody":
        return cls(HttpBodyKind.RAW, bytes(content))

    @classmethod
    def text(cls, content: str) -> "HttpBody":
        return cls(HttpBodyKind.TEXT, content.encode())

    @classmethod
    def json(cls, content: bytes | str | Mapping[str, object]) -> "HttpBody":
        if isinstance(content, Mapping):
            encoded = json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
        elif isinstance(content, str):
            encoded = content.encode()
        else:
            encoded = bytes(content)
        json.loads(encoded)
        return cls(HttpBodyKind.JSON, encoded)

    @classmethod
    def form(cls, fields: Mapping[str, str]) -> "HttpBody":
        return cls(HttpBodyKind.FORM, urlencode(tuple(fields.items())).encode())


@dataclass(frozen=True)
class HttpSessionRequest:
    method: str
    path: str
    query: tuple[tuple[str, str], ...] = ()
    body: HttpBody = HttpBody(HttpBodyKind.RAW, b"")
    headers: tuple[tuple[str, str], ...] = ()
    response_headers: tuple[str, ...] = ()

    def document(self) -> dict[str, object]:
        return {
            "method": self.method,
            "path": self.path,
            "query": [list(field) for field in self.query],
            "body": {
                "kind": self.body.kind.value,
                "content": base64.b64encode(self.body.content).decode(),
            },
            "headers": [list(field) for field in self.headers],
            "response_headers": list(self.response_headers),
        }


class TcpReceiveMode(str, enum.Enum):
    RAW = "raw"
    LINE = "line"
    DELIMITER = "delimiter"
    FIXED = "fixed"
    LENGTH_PREFIXED = "length-prefixed"


@dataclass(frozen=True)
class TcpReceive:
    mode: TcpReceiveMode
    maximum_bytes: int
    size: int = 0
    delimiter: bytes = b""
    length_bytes: int = 0
    byteorder: str = "big"

    @classmethod
    def raw(cls, maximum_bytes: int) -> "TcpReceive":
        return cls(TcpReceiveMode.RAW, maximum_bytes)

    @classmethod
    def line(cls, maximum_bytes: int) -> "TcpReceive":
        return cls(TcpReceiveMode.LINE, maximum_bytes, delimiter=b"\n")

    @classmethod
    def delimiter_terminated(cls, delimiter: bytes, maximum_bytes: int) -> "TcpReceive":
        return cls(TcpReceiveMode.DELIMITER, maximum_bytes, delimiter=bytes(delimiter))

    @classmethod
    def fixed(cls, size: int) -> "TcpReceive":
        return cls(TcpReceiveMode.FIXED, size, size=size)

    @classmethod
    def length_prefixed(cls, length_bytes: int, maximum_bytes: int, *, byteorder: str = "big") -> "TcpReceive":
        return cls(
            TcpReceiveMode.LENGTH_PREFIXED,
            maximum_bytes,
            length_bytes=length_bytes,
            byteorder=byteorder,
        )

    def document(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "maximum_bytes": self.maximum_bytes,
            "size": self.size,
            "delimiter": base64.b64encode(self.delimiter).decode(),
            "length_bytes": self.length_bytes,
            "byteorder": self.byteorder,
        }


@dataclass(frozen=True)
class TcpSessionRequest:
    body: bytes
    receive: TcpReceive
    half_close: bool = False

    def document(self) -> dict[str, object]:
        return {
            "body": base64.b64encode(self.body).decode(),
            "receive": self.receive.document(),
            "half_close": self.half_close,
        }


@dataclass(frozen=True)
class BrowserSessionRequest:
    path: str
    wait_selector: str = ""
    download_selector: str = ""
    admitted_subresources: tuple[str, ...] = ()

    def document(self) -> dict[str, object]:
        return {
            "path": self.path,
            "wait_selector": self.wait_selector,
            "download_selector": self.download_selector,
            "admitted_subresources": list(self.admitted_subresources),
        }


@dataclass(frozen=True)
class BrowserObservations:
    dom: str = ""
    network: tuple[tuple[str, str, int, int], ...] = ()
    local_storage: tuple[tuple[str, str], ...] = ()
    session_storage: tuple[tuple[str, str], ...] = ()
    downloads: tuple[tuple[str, str, int], ...] = ()


@dataclass(frozen=True)
class BrowserTransportResult:
    outcome: TargetOutcome
    observations: BrowserObservations = BrowserObservations()
    elapsed_ms: int = 0
    response_bytes: int = 0


class TcpTargetExchangeRequest(TypedDict):
    body: str


class HttpTargetExchangeRequest(TypedDict):
    method: str
    path: str
    body: str


TargetExchangeRequest = TcpTargetExchangeRequest | HttpTargetExchangeRequest


@dataclass(frozen=True)
class TargetEndpoint:
    protocol: TargetProtocol
    host: str
    port: int

    def __post_init__(self) -> None:
        if not self.host or not 1 <= self.port <= 65535:
            raise ValueError("Target endpoint must name one host and port")


@dataclass(frozen=True)
class TargetLimits:
    max_connections: int
    max_request_bytes: int
    max_response_bytes: int
    timeout_seconds: float
    max_exchanges: int = 1
    max_total_request_bytes: int = 0
    max_total_response_bytes: int = 0
    max_redirects: int = 0
    max_cookies: int = 32
    max_cookie_bytes: int = 4096
    max_total_seconds: float = 0

    def __post_init__(self) -> None:
        if min(self.max_connections, self.max_request_bytes, self.max_response_bytes) <= 0:
            raise ValueError("Target limits must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("Target timeout must be positive")
        if self.max_exchanges <= 0 or self.max_redirects < 0 or min(self.max_cookies, self.max_cookie_bytes) <= 0:
            raise ValueError("Target session limits are invalid")
        if self.max_total_request_bytes <= 0:
            object.__setattr__(self, "max_total_request_bytes", self.max_request_bytes * self.max_connections)
        if self.max_total_response_bytes <= 0:
            object.__setattr__(self, "max_total_response_bytes", self.max_response_bytes * self.max_connections)
        if self.max_total_seconds <= 0:
            object.__setattr__(self, "max_total_seconds", self.timeout_seconds * self.max_exchanges)


@dataclass(frozen=True)
class TargetCandidateBinding:
    image_id: str
    manifest_digest: str
    config_digest: str
    platform: str
    profile_digest: str

    def __post_init__(self) -> None:
        if any(_DIGEST.fullmatch(value) is None for value in (self.image_id, self.manifest_digest, self.config_digest)):
            raise ValueError("Target candidate image binding is invalid")
        if self.platform not in {"linux/amd64", "linux/arm64"} or _HEX_DIGEST.fullmatch(self.profile_digest) is None:
            raise ValueError("Target candidate profile binding is invalid")


@dataclass(frozen=True)
class TargetProvenance:
    challenge_id: str = ""
    generation_id: str = ""
    endpoint: str = ""
    protocol: TargetProtocol | None = None
    request_bytes: int = 0
    response_bytes: int = 0
    transcript_digest: str = ""
    elapsed_ms: int = 0
    resolved_address: str = ""
    server_name: str = ""
    certificate_sha256: str = ""


@dataclass(frozen=True)
class TargetResult:
    outcome: TargetOutcome
    body: bytes = b""
    status: int = 0
    provenance: TargetProvenance = TargetProvenance()
    request_id: str = ""
    headers: tuple[tuple[str, str], ...] = ()
    redirect_chain: tuple[str, ...] = ()
    cookies: tuple[tuple[str, str], ...] = ()
    browser: BrowserObservations = BrowserObservations()


@dataclass(frozen=True)
class TargetBrokerRecorded:
    event_id: str
    request_id: str
    record: TargetRecord
    binding_digest: str
    run_id: str
    boot_id: str
    generation_id: str
    lane_id: str
    attempt_id: str
    step_id: str
    challenge_id: str
    image_id: str
    image_manifest_digest: str
    image_config_digest: str
    platform: str
    profile_digest: str
    endpoint: str
    resolved_address: str
    protocol: TargetProtocol
    max_connections: int
    max_request_bytes: int
    max_response_bytes: int
    timeout_ms: int
    operation: str
    max_exchanges: int
    max_total_request_bytes: int
    max_total_response_bytes: int
    max_total_seconds_ms: int
    max_redirects: int
    max_cookies: int
    request_digest: str
    observation_sequence: int = 0
    observation_digest: str = ""
    outcome: TargetOutcome | None = None
    request_bytes: int = 0
    response_bytes: int = 0
    status: int = 0
    elapsed_ms: int = 0
    transcript_digest: str = ""
    probe_kind: str = ""
    attempted_endpoint_digest: str = ""
    server_name: str = ""
    certificate_sha256: str = ""
    response_headers_digest: str = ""
    redirect_chain: tuple[str, ...] = ()
    cookies_digest: str = ""
    browser_observation_digest: str = ""
    ts: str = ""

    @property
    def event_type(self) -> str:
        return TARGET_EXCHANGE_RECORDED

    @property
    def identity(self) -> str:
        return self.event_id

    @classmethod
    def identity_from_payload(cls, payload: Mapping[str, Any]) -> str:
        return str(payload.get("event_id", ""))

    def payload(self, *, blob_digest: str, blob_bytes: int) -> dict[str, object]:
        return {
            **{
                name: getattr(self, name)
                for name in (
                    "event_id",
                    "request_id",
                    "binding_digest",
                    "run_id",
                    "boot_id",
                    "generation_id",
                    "lane_id",
                    "attempt_id",
                    "step_id",
                    "challenge_id",
                    "image_id",
                    "image_manifest_digest",
                    "image_config_digest",
                    "platform",
                    "profile_digest",
                    "endpoint",
                    "resolved_address",
                    "max_connections",
                    "max_request_bytes",
                    "max_response_bytes",
                    "timeout_ms",
                    "operation",
                    "max_exchanges",
                    "max_total_request_bytes",
                    "max_total_response_bytes",
                    "max_total_seconds_ms",
                    "max_redirects",
                    "max_cookies",
                    "request_digest",
                    "observation_sequence",
                    "observation_digest",
                    "request_bytes",
                    "response_bytes",
                    "status",
                    "elapsed_ms",
                    "transcript_digest",
                    "probe_kind",
                    "attempted_endpoint_digest",
                    "server_name",
                    "certificate_sha256",
                    "response_headers_digest",
                    "cookies_digest",
                    "browser_observation_digest",
                    "ts",
                )
            },
            "record": self.record.value,
            "protocol": self.protocol.value,
            "outcome": self.outcome.value if self.outcome else "",
            "redirect_chain": list(self.redirect_chain),
            "blob_digest": blob_digest,
            "blob_bytes": blob_bytes,
        }

    @classmethod
    def validate_payload(cls, payload: Mapping[str, Any], *, sequence: int) -> None:
        required = {
            "event_id",
            "request_id",
            "record",
            "binding_digest",
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "challenge_id",
            "image_id",
            "image_manifest_digest",
            "image_config_digest",
            "platform",
            "profile_digest",
            "endpoint",
            "resolved_address",
            "protocol",
            "max_connections",
            "max_request_bytes",
            "max_response_bytes",
            "timeout_ms",
            "operation",
            "max_exchanges",
            "max_total_request_bytes",
            "max_total_response_bytes",
            "max_total_seconds_ms",
            "max_redirects",
            "max_cookies",
            "request_digest",
            "observation_sequence",
            "observation_digest",
            "outcome",
            "request_bytes",
            "response_bytes",
            "status",
            "elapsed_ms",
            "transcript_digest",
            "probe_kind",
            "attempted_endpoint_digest",
            "server_name",
            "certificate_sha256",
            "response_headers_digest",
            "redirect_chain",
            "cookies_digest",
            "browser_observation_digest",
            "ts",
            "blob_digest",
            "blob_bytes",
            "observation_sequence",
        }
        if set(payload) != required:
            raise InvalidEventError("Target-broker event fields are invalid", sequence=sequence)
        try:
            record = TargetRecord(str(payload["record"]))
            TargetProtocol(str(payload["protocol"]))
            if payload["outcome"]:
                TargetOutcome(str(payload["outcome"]))
        except ValueError as error:
            raise InvalidEventError("Target-broker event enum is invalid", sequence=sequence) from error
        identities = (
            "event_id",
            "request_id",
            "binding_digest",
            "run_id",
            "boot_id",
            "generation_id",
            "lane_id",
            "attempt_id",
            "step_id",
            "challenge_id",
            "image_id",
            "image_manifest_digest",
            "image_config_digest",
            "platform",
            "profile_digest",
            "endpoint",
            "resolved_address",
            "request_digest",
            "ts",
        )
        if any(not isinstance(payload[name], str) or not payload[name] for name in identities):
            raise InvalidEventError("Target-broker identity is incomplete", sequence=sequence)
        if (
            any(
                _DIGEST.fullmatch(str(payload[name])) is None
                for name in ("image_id", "image_manifest_digest", "image_config_digest")
            )
            or payload["platform"] not in {"linux/amd64", "linux/arm64"}
            or _HEX_DIGEST.fullmatch(str(payload["profile_digest"])) is None
        ):
            raise InvalidEventError("Target-broker candidate binding is invalid", sequence=sequence)
        numbers = (
            "max_connections",
            "max_request_bytes",
            "max_response_bytes",
            "timeout_ms",
            "max_exchanges",
            "max_total_request_bytes",
            "max_total_response_bytes",
            "max_total_seconds_ms",
            "max_redirects",
            "max_cookies",
            "request_bytes",
            "response_bytes",
            "status",
            "elapsed_ms",
            "blob_bytes",
        )
        if any(
            not isinstance(payload[name], int) or isinstance(payload[name], bool) or payload[name] < 0
            for name in numbers
        ):
            raise InvalidEventError("Target-broker accounting is invalid", sequence=sequence)
        if any(
            payload[name] <= 0 for name in ("max_connections", "max_request_bytes", "max_response_bytes", "timeout_ms")
        ):
            raise InvalidEventError("Target-broker bounds are invalid", sequence=sequence)
        if payload["operation"] not in {"exchange", "http-session", "tcp-session", "browser", "denial"}:
            raise InvalidEventError("Target-broker operation is invalid", sequence=sequence)
        if record is TargetRecord.RESERVED and (payload["outcome"] or payload["transcript_digest"]):
            raise InvalidEventError("Target-broker reservation carries a classification", sequence=sequence)
        if record is TargetRecord.CLASSIFIED and not payload["outcome"]:
            raise InvalidEventError("Target-broker classification has no outcome", sequence=sequence)


__all__ = [
    "BrowserObservations",
    "BrowserSessionRequest",
    "BrowserTransportResult",
    "HttpTargetExchangeRequest",
    "HttpBody",
    "HttpBodyKind",
    "HttpSessionRequest",
    "TcpTargetExchangeRequest",
    "TargetEndpoint",
    "TargetCandidateBinding",
    "TargetLimits",
    "TargetOutcome",
    "TargetProtocol",
    "TargetProvenance",
    "TargetRecord",
    "TargetResult",
    "TcpReceive",
    "TcpReceiveMode",
    "TcpSessionRequest",
    "TargetExchangeRequest",
    "TARGET_EXCHANGE_COMMAND",
    "TARGET_BROWSER_COMMAND",
    "TARGET_HTTP_SESSION_COMMAND",
    "TARGET_TCP_SESSION_COMMAND",
    "TARGET_EXCHANGE_RECORDED",
]
