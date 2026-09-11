"""Fail-closed exact-value sanitisation for capsule publication."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from solver.credentials import SECRETS
from solver.env_file import assignment_rows, environment_sources
from solver.evidence_capsule_contracts import CapsuleRefused, SanitizationPolicy
from solver.event_store_storage import canonical_bytes
from solver.redaction import Redactor


class HostSanitizationAuthority:
    """Trusted host-derived credential history and path scan authority."""

    def __init__(self, root: Path, *, host_paths: tuple[Path, ...]) -> None:
        self._root = root.resolve()
        self._host_paths = tuple(path.resolve() for path in host_paths)
        self._fingerprint, self._policy = self._snapshot()

    @property
    def version(self) -> int:
        return self._policy.version

    @property
    def digest(self) -> str:
        return policy_digest(self._fresh_policy())

    def verify(self, body: bytes, *, label: str, structured: bool = False) -> None:
        verify_sanitized(body, self._fresh_policy(), label=label, structured=structured)

    def assert_fresh(self) -> None:
        """Refuse if the complete authority source changed since composition."""

        self._fresh_policy()

    def _fresh_policy(self) -> SanitizationPolicy:
        fingerprint, policy = self._snapshot()
        if fingerprint != self._fingerprint:
            raise CapsuleRefused("host credential history changed after scan-authority composition")
        return policy

    def _snapshot(self) -> tuple[str, SanitizationPolicy]:
        active = self._root / ".env"
        if not active.is_file() or active.is_symlink():
            raise CapsuleRefused("host scan authority requires one regular canonical .env source")
        candidates = list(environment_sources(self._root))
        if not candidates or candidates[0] != active:
            raise CapsuleRefused("host scan authority requires one regular canonical .env source")
        rows = []
        secrets: list[tuple[str, str]] = []
        for path in candidates:
            if not path.is_file() or path.is_symlink():
                raise CapsuleRefused("host credential history contains a non-regular source")
            try:
                body = path.read_bytes()
                values = assignment_rows(body.decode("utf-8"))
            except (OSError, UnicodeDecodeError) as error:
                raise CapsuleRefused("host credential history is not completely readable") from error
            rows.append({"name": path.name, "digest": hashlib.sha256(body).hexdigest()})
            for name, value in values:
                if name in SECRETS and value.strip() and (name, value) not in secrets:
                    secrets.append((name, value))
        fingerprint = hashlib.sha256(canonical_bytes({"sources": rows})).hexdigest()
        paths = tuple(sorted({str(self._root), *(str(path) for path in self._host_paths)}))
        return fingerprint, SanitizationPolicy(tuple(secrets), paths)


def policy_digest(policy: SanitizationPolicy) -> str:
    basis = {
        "version": policy.version,
        "secrets": sorted(
            (name, hashlib.sha256(value.encode()).hexdigest()) for name, value in policy.secrets if value.strip()
        ),
        "forbidden_path_digests": sorted(hashlib.sha256(path.encode()).hexdigest() for path in policy.forbidden_paths),
    }
    return hashlib.sha256(canonical_bytes(basis)).hexdigest()


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CapsuleRefused(f"structured evidence contains duplicate JSON key {key!r}")
        result[key] = value
    return result


def _walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key)
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def verify_sanitized(
    body: bytes,
    policy: SanitizationPolicy,
    *,
    label: str,
    structured: bool = False,
) -> None:
    for name, value in policy.secrets:
        if value.strip() and Redactor({name: value}).redact(body) != body:
            raise CapsuleRefused(f"{label} carries declared credential {name}")
    paths = [path for path in policy.forbidden_paths if path]
    if any(Redactor({"HOST_PATH": path}).redact(body) != body for path in paths):
        raise CapsuleRefused(f"{label} carries a forbidden host path")
    if not structured:
        return
    try:
        decoded = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapsuleRefused(f"{label} is not lossless structured JSON: {error}") from None
    for value in _walk_strings(decoded):
        encoded = value.encode()
        for name, secret in policy.secrets:
            if secret.strip() and Redactor({name: secret}).redact(encoded) != encoded:
                raise CapsuleRefused(f"{label} carries declared credential {name}")
        if any(Redactor({"HOST_PATH": path}).redact(encoded) != encoded for path in paths):
            raise CapsuleRefused(f"{label} carries a forbidden host path")
