"""Primitive JSON value checks shared by manifest validators."""

import hashlib
from collections.abc import Mapping, Sequence
from typing import cast

from solver.event_store_storage import canonical_bytes
from solver.manifest_contracts import ManifestValidationError


def fail(message: str) -> None:
    raise ManifestValidationError(message)


def copy_json(value: object) -> object:
    """Copy JSON-shaped input without retaining caller-owned mutable containers."""

    if isinstance(value, Mapping):
        copied: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                fail("JSON object keys must be strings")
            copied[key] = copy_json(item)
        return copied
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [copy_json(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    fail(f"value of type {type(value).__name__} is not JSON-shaped")


def mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        fail(f"{name} must be an object")
    return cast(Mapping[str, object], value)


def string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        fail(f"{name} must be a non-empty string")
    return value


def string_list(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        fail(f"{name} must be a list of non-empty strings")
    if len(value) != len(set(value)):
        fail(f"{name} contains duplicate values")
    return cast(list[str], value)


def positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        fail(f"{name} must be a positive integer")
    return value


def sha256(value: object, name: str, *, prefixed: bool = False) -> str:
    digest = string(value, name)
    expected_length = 71 if prefixed else 64
    start = 7 if prefixed else 0
    if prefixed and not digest.startswith("sha256:"):
        fail(f"{name} must be a SHA-256 digest")
    if len(digest) != expected_length or any(character not in "0123456789abcdef" for character in digest[start:]):
        fail(f"{name} must be a lowercase SHA-256 digest")
    return digest


def digest(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def provisional_identity_basis(manifest: Mapping[str, object]) -> dict[str, object]:
    basis = copy_json(manifest)
    if not isinstance(basis, dict):
        fail("manifest must be an object")
    candidate = basis.get("candidate")
    if isinstance(candidate, dict):
        candidate.pop("identity", None)
    return basis
