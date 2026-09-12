"""Typed values at the privileged native Codex Control seam."""

from __future__ import annotations

from dataclasses import dataclass

CODEX_CONTROL_SOCKET_ENV = "INCYPHER_CODEX_CONTROL_SOCKET"


@dataclass(frozen=True)
class CodexCatalogueEntry:
    model: str
    efforts: tuple[str, ...]


@dataclass(frozen=True)
class CodexRequest:
    request_id: str
    turn_id: str
    model: str
    effort: str
    prompt: str
    workdir: str = ""
    attempt_id: str = ""
    deadline: str = ""
    first_step: int = 1


@dataclass(frozen=True)
class CodexTurn:
    request_id: str
    turn_id: str
    model: str
    text: str
    tokens_in: int
    tokens_out: int
    duration_ms: int
    stream: tuple[dict[str, object], ...] = ()


@dataclass(frozen=True)
class LimitObservation:
    limit_id: str
    used_percent: float | None
    resets_at: str | None
    source: str
    observed_at: str

    @property
    def remaining_percent(self) -> float | None:
        if self.used_percent is None:
            return None
        return max(0.0, min(100.0, 100.0 - self.used_percent))


@dataclass(frozen=True)
class CodexControlResult:
    outcome: str
    turn: CodexTurn | None = None
    limits: tuple[LimitObservation, ...] = ()


@dataclass(frozen=True)
class NativeResponse:
    turn: CodexTurn
    limits: tuple[LimitObservation, ...] = ()


__all__ = [
    "CodexCatalogueEntry",
    "CodexControlResult",
    "CodexRequest",
    "CodexTurn",
    "LimitObservation",
    "NativeResponse",
]
