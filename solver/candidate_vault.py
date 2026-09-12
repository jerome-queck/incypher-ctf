"""Authenticated encryption for exact Candidate vault records."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from solver.event_store_storage import canonical_bytes

VAULT_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class CandidateVaultCipher:
    """Encrypt exact Candidates under caller-brokered Run custody."""

    key: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.key, bytes) or len(self.key) < 32:
            raise ValueError("Candidate vault key must contain at least 256 bits")

    def seal(self, document: object) -> bytes:
        plaintext = canonical_bytes(document)
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._encryption_key()).encrypt(nonce, plaintext, b"candidate-vault-v1")
        return canonical_bytes(
            {
                "schema_version": VAULT_SCHEMA_VERSION,
                "nonce": nonce.hex(),
                "ciphertext": ciphertext.hex(),
            }
        )

    def digest(self, candidate: bytes) -> str:
        """Return the keyed stream identity for exact Candidate bytes."""
        return hmac.new(self.key, b"candidate-digest-v1" + candidate, hashlib.sha256).hexdigest()

    def open(self, sealed: bytes) -> dict[str, object]:
        try:
            envelope = json.loads(sealed)
            if set(envelope) != {"schema_version", "nonce", "ciphertext"}:
                raise ValueError
            if envelope["schema_version"] != VAULT_SCHEMA_VERSION:
                raise ValueError
            nonce = bytes.fromhex(envelope["nonce"])
            ciphertext = bytes.fromhex(envelope["ciphertext"])
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("Candidate vault envelope is invalid") from error
        try:
            if len(nonce) != 12:
                raise ValueError
            plaintext = AESGCM(self._encryption_key()).decrypt(nonce, ciphertext, b"candidate-vault-v1")
            document = json.loads(plaintext)
        except (InvalidTag, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("Candidate vault plaintext is invalid") from error
        if not isinstance(document, dict):
            raise ValueError("Candidate vault plaintext is not an object")
        return document

    def _encryption_key(self) -> bytes:
        return hashlib.sha256(b"candidate-vault-aes256-gcm-v1" + self.key).digest()
