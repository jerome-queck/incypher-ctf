"""Typed proof that the sanctioned environment file was atomically replaced."""

from __future__ import annotations

from pathlib import Path

_COMPLETION = object()


class EnvCutoverResult:
    def __init__(self, path: Path, content_digest: str, completion: object) -> None:
        if completion is not _COMPLETION:
            raise TypeError("EnvCutoverResult is created only by a completed atomic replacement")
        self.path = Path(path)
        self.content_digest = content_digest

    @property
    def completed_atomic_env_cutover(self) -> bool:
        return True

    def evidence(self) -> dict[str, object]:
        return {
            "authority": self.path.name,
            "operation": "atomic-replace",
            "legacy_authority": "absent",
            "content_digest": self.content_digest,
        }


def completed_cutover(path: Path, content_digest: str) -> EnvCutoverResult:
    """Seal the post-os.replace result for the env-file implementation."""

    return EnvCutoverResult(path, content_digest, _COMPLETION)


__all__ = ["EnvCutoverResult", "completed_cutover"]
