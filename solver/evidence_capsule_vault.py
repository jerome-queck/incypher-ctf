"""Versioned scanner-vault authority loaded from a distinct macOS Keychain item."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from solver.credentials import SCANNED_SECRETS
from solver.evidence_capsule_contracts import CapsuleRefused, ScannerVaultIdentity
from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.strict_json import StrictJSONError, strict_json_object


VAULT_KIND = "evidence-scanner-vault"
VAULT_SCHEMA_VERSION = 1
KEYCHAIN_SERVICE = "incypher-ctf.evidence-scanner-vault"
KEYCHAIN_ACCOUNT = "solver"


class ScannerVaultReader(Protocol):
    """Narrow trusted boundary; producer requests never receive it."""

    def read(self) -> bytes: ...


class MacOSKeychainScannerVault:
    """Read only the password body of the dedicated scanner-vault item."""

    def read(self) -> bytes:
        try:
            completed = subprocess.run(
                [
                    "/usr/bin/security",
                    "find-generic-password",
                    "-s",
                    KEYCHAIN_SERVICE,
                    "-a",
                    KEYCHAIN_ACCOUNT,
                    "-w",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise CapsuleRefused("scanner vault is unavailable from macOS Keychain") from error
        if completed.returncode != 0 or not completed.stdout.strip():
            raise CapsuleRefused("scanner vault item is missing or empty")
        return completed.stdout


@dataclass(frozen=True)
class ScannerVault:
    identity: ScannerVaultIdentity
    secrets: tuple[tuple[str, str], ...]
    current: tuple[tuple[str, str], ...]
    raw_digest: str


def vault_receipt(
    *,
    version: int,
    attestation_id: str,
    completeness_through: Mapping[str, str],
) -> str:
    basis = {
        "kind": VAULT_KIND,
        "schema_version": VAULT_SCHEMA_VERSION,
        "version": version,
        "attestation_id": attestation_id,
        "completeness_through": dict(completeness_through),
    }
    return f"sha256:{digest_bytes(canonical_bytes(basis))}"


def _strict_document(body: bytes) -> dict[str, Any]:
    try:
        return strict_json_object(body, label="scanner vault")
    except StrictJSONError as error:
        raise CapsuleRefused(str(error)) from None


def read_scanner_vault(reader: ScannerVaultReader) -> ScannerVault:
    try:
        body = reader.read()
    except CapsuleRefused:
        raise
    except Exception:
        raise CapsuleRefused("scanner vault reader failed") from None
    if not isinstance(body, bytes) or not body.strip():
        raise CapsuleRefused("scanner vault item is missing or empty")
    document = _strict_document(body)
    required = {
        "schema_version",
        "kind",
        "version",
        "attestation_id",
        "completeness_through",
        "values",
    }
    if set(document) != required or document.get("schema_version") != 1 or document.get("kind") != VAULT_KIND:
        raise CapsuleRefused("scanner vault schema is invalid or incomplete")
    version = document.get("version")
    attestation_id = document.get("attestation_id")
    through, values = document.get("completeness_through"), document.get("values")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise CapsuleRefused("scanner vault version is invalid")
    if (
        not isinstance(attestation_id, str)
        or len(attestation_id) != 64
        or any(character not in "0123456789abcdef" for character in attestation_id)
    ):
        raise CapsuleRefused("scanner vault attestation ID is invalid")
    if (
        not isinstance(through, dict)
        or set(through) != {"run_id", "chain_head"}
        or not all(isinstance(through.get(key), str) and through[key] for key in ("run_id", "chain_head"))
    ):
        raise CapsuleRefused("scanner vault completeness attestation is invalid")
    chain_head = through["chain_head"]
    if len(chain_head) != 64 or any(character not in "0123456789abcdef" for character in chain_head):
        raise CapsuleRefused("scanner vault completeness chain head is invalid")
    if not isinstance(values, dict) or set(values) != set(SCANNED_SECRETS):
        raise CapsuleRefused("scanner vault does not attest every declared credential")

    secrets: list[tuple[str, str]] = []
    current_secrets: list[tuple[str, str]] = []
    for name in SCANNED_SECRETS:
        row = values[name]
        if not isinstance(row, dict) or set(row) != {"current", "historical"}:
            raise CapsuleRefused("scanner vault credential history is incomplete")
        current, historical = row.get("current"), row.get("historical")
        if not isinstance(current, list) or not isinstance(historical, list):
            raise CapsuleRefused("scanner vault credential history is invalid")
        combined = current + historical
        if any(not isinstance(value, str) or not value.strip() for value in combined):
            raise CapsuleRefused("scanner vault contains an empty credential value")
        if len(set(combined)) != len(combined):
            raise CapsuleRefused("scanner vault repeats a credential value")
        current_secrets.extend((name, value) for value in current)
        secrets.extend((name, value) for value in combined)
    if not secrets:
        raise CapsuleRefused("scanner vault contains no exact credential values")
    receipt = vault_receipt(
        version=version,
        attestation_id=attestation_id,
        completeness_through=through,
    )
    identity = ScannerVaultIdentity(
        receipt=receipt,
        schema_version=VAULT_SCHEMA_VERSION,
        version=version,
        attestation_id=attestation_id,
        completeness_run_id=through["run_id"],
        completeness_chain_head=through["chain_head"],
    )
    return ScannerVault(identity, tuple(secrets), tuple(current_secrets), digest_bytes(canonical_bytes(document)))
