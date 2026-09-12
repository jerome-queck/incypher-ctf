"""Deterministic projection of sealed Candidate admission state."""

from __future__ import annotations

from solver.candidate_admission_contracts import (
    CandidateDerivation,
    CandidateDisposition,
    CandidateProvenance,
    DerivationKind,
    ReadyCandidate,
)
from solver.candidate_vault import CandidateVaultCipher
from solver.event_store_storage import canonical_bytes, digest_bytes


def project_candidate(event, cipher: CandidateVaultCipher) -> ReadyCandidate:
    """Verify and project one admitted canonical Candidate event."""
    document = cipher.open(event.body)
    expected_fields = {
        "candidate_hex",
        "challenge_id",
        "candidate_digest",
        "generation_id",
        "disposition",
        "observation_digests",
        "tool_receipt_digests",
        "model_digest",
        "derivation",
        "admission_rule",
    }
    if not isinstance(document, dict) or set(document) != expected_fields:
        raise ValueError("Candidate vault has an unsupported schema")
    derivation_document = document["derivation"]
    derivation = (
        CandidateDerivation(
            DerivationKind(derivation_document["kind"]),
            tuple(derivation_document["source_digests"]),
            derivation_document["relationship"],
            derivation_document["claim_ref"],
        )
        if isinstance(derivation_document, dict)
        else None
    )
    provenance = CandidateProvenance(
        CandidateDisposition(document["disposition"]),
        tuple(document["observation_digests"]),
        tuple(document["tool_receipt_digests"]),
        document["model_digest"],
        derivation,
    )
    candidate = bytes.fromhex(document["candidate_hex"])
    if cipher.digest(candidate) != event.payload["candidate_digest"]:
        raise ValueError("Candidate vault digest disagrees with canonical admission")
    identity_document = {name: value for name, value in document.items() if name != "candidate_hex"}
    if (
        digest_bytes(canonical_bytes(identity_document)) != event.payload["candidate_id"]
        or digest_bytes(canonical_bytes(provenance.document())) != event.payload["provenance_digest"]
        or document["challenge_id"] != event.payload["challenge_id"]
        or document["generation_id"] != event.payload["generation_id"]
        or document["admission_rule"] != event.payload["admission_rule"]
        or document["candidate_digest"] != event.payload["candidate_digest"]
    ):
        raise ValueError("Candidate vault identity disagrees with canonical admission")
    return ReadyCandidate(
        event.payload["candidate_id"],
        event.payload["challenge_id"],
        event.payload["generation_id"],
        candidate,
        event.payload["candidate_digest"],
        provenance,
        event.payload["admission_rule"],
    )
