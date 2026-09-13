"""Fail-closed exact-value sanitisation for capsule publication."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from solver.credentials import SCANNED_SECRETS
from solver.env_file import assignments, environment_sources
from solver.evidence_capsule_contracts import CapsuleRefused, SanitizationPolicy
from solver.evidence_capsule_vault import MacOSKeychainScannerVault, ScannerVaultReader, read_scanner_vault
from solver.event_store_storage import canonical_bytes
from solver.redaction import Redactor
from solver.strict_json import StrictJSONError, strict_json_value


class HostSanitizationAuthority:
    """Trusted host-derived credential history and path scan authority."""

    def __init__(
        self,
        root: Path,
        *,
        host_paths: tuple[Path, ...],
        vault_reader: ScannerVaultReader,
    ) -> None:
        self._root = root.resolve()
        self._host_paths = tuple(path.resolve() for path in host_paths)
        self._vault_reader = vault_reader
        self._source: tuple[str, str] | None = None
        self._fingerprint, self._policy = self._snapshot()

    @classmethod
    def from_macos_keychain(cls, root: Path, *, host_paths: tuple[Path, ...]) -> HostSanitizationAuthority:
        """Compose the production host authority with its explicit platform adapter."""

        return cls(root, host_paths=host_paths, vault_reader=MacOSKeychainScannerVault())

    def bind_source(self, run_id: str, chain_head: str) -> None:
        """Bind the vault completeness attestation to the terminal source once."""

        source = (run_id, chain_head)
        if self._source is not None and self._source != source:
            raise CapsuleRefused("scan authority is already bound to another source Run")
        fingerprint, policy = self._snapshot(source)
        if fingerprint != self._fingerprint:
            raise CapsuleRefused("scanner vault authority changed after composition")
        self._source, self._policy = source, policy

    @property
    def version(self) -> int:
        return self._bound_policy().version

    @property
    def digest(self) -> str:
        return policy_digest(self._bound_policy())

    @property
    def identity(self) -> dict[str, Any]:
        return self._bound_policy().vault.as_dict()

    def verify(self, body: bytes, *, label: str, structured: bool = False) -> None:
        verify_sanitized(body, self._bound_policy(), label=label, structured=structured)

    def assert_fresh(self) -> None:
        """Refuse if the complete authority source changed since composition."""

        source = self._source
        if source is None:
            raise CapsuleRefused("scan authority is not bound to a terminal source Run")
        fingerprint, _policy = self._snapshot(source)
        if fingerprint != self._fingerprint:
            raise CapsuleRefused("scanner vault authority changed after composition")

    def _bound_policy(self) -> SanitizationPolicy:
        if self._source is None:
            raise CapsuleRefused("scan authority is not bound to a terminal source Run")
        return self._policy

    def _snapshot(self, source: tuple[str, str] | None = None) -> tuple[str, SanitizationPolicy]:
        active = self._root / ".env"
        if not active.is_file() or active.is_symlink():
            raise CapsuleRefused("host scan authority requires one regular canonical .env source")
        candidates = list(environment_sources(self._root))
        if candidates != [active]:
            raise CapsuleRefused("legacy .env overlays are ambiguous and cannot authorize scanner history")
        try:
            active_body = active.read_bytes()
            active_values = assignments(active_body.decode("utf-8"))
        except (OSError, UnicodeDecodeError) as error:
            raise CapsuleRefused("canonical .env is not completely readable") from error
        vault = read_scanner_vault(self._vault_reader)
        current = set(vault.current)
        for name in SCANNED_SECRETS:
            value = active_values.get(name)
            if value is not None and (not value.strip() or (name, value) not in current):
                raise CapsuleRefused(f"active credential {name} is absent from the scanner vault current set")
        if (
            source is not None
            and (
                vault.identity.completeness_run_id,
                vault.identity.completeness_chain_head,
            )
            != source
        ):
            raise CapsuleRefused("scanner vault is stale for the terminal source Run")
        fingerprint = hashlib.sha256(
            canonical_bytes(
                {
                    "vault": vault.raw_digest,
                    "active_env": hashlib.sha256(active_body).hexdigest(),
                    "host_paths": [str(path) for path in self._host_paths],
                }
            )
        ).hexdigest()
        paths = tuple(sorted({str(self._root), *(str(path) for path in self._host_paths)}))
        return fingerprint, SanitizationPolicy(vault.secrets, paths, vault.identity)


def policy_digest(policy: SanitizationPolicy) -> str:
    basis = {
        "version": policy.version,
        "forbidden_path_digests": sorted(hashlib.sha256(path.encode()).hexdigest() for path in policy.forbidden_paths),
        "vault": policy.vault.as_dict(),
    }
    return hashlib.sha256(canonical_bytes(basis)).hexdigest()


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
        decoded = strict_json_value(body, label=label)
    except StrictJSONError as error:
        raise CapsuleRefused(str(error)) from None
    for value in _walk_strings(decoded):
        encoded = value.encode()
        for name, secret in policy.secrets:
            if secret.strip() and Redactor({name: secret}).redact(encoded) != encoded:
                raise CapsuleRefused(f"{label} carries declared credential {name}")
        if any(Redactor({"HOST_PATH": path}).redact(encoded) != encoded for path in paths):
            raise CapsuleRefused(f"{label} carries a forbidden host path")
