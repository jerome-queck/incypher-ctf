"""Fail-closed exact-value sanitisation for capsule publication."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from solver.evidence_capsule_contracts import CapsuleRefused, SanitizationPolicy
from solver.event_store_storage import canonical_bytes
from solver.redaction import Redactor


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
