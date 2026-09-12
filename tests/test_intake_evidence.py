"""Governed private Intake evidence admission, reachability, and retirement."""

import pytest
import hashlib

from solver.intake_evidence import IntakeEvidenceWriter
from solver.evidence_capsule_build import _excluded_blobs


EMPTY_PROOF = {
    "policy": {"version": 1, "forms": []},
    "policy_digest": "7b9531388bc0580239e170773eedefaa3e49eb8c9d95e59d13df240eb585697c",
    "steps": [],
}


def test_private_evidence_admission_refuses_class_budget_pressure(tmp_path):
    writer = IntakeEvidenceWriter(tmp_path, "run-1", max_response_bytes=5, max_total_bytes=4)

    with pytest.raises(OSError, match="storage pressure"):
        writer.seal(
            b"12345",
            classified_event_id="board-broker:000002",
            sanitized=b"12345",
            redaction_proof=EMPTY_PROOF,
        )

    root = tmp_path / "runs" / "run-1" / "sealed" / "private-board-response"
    assert not (root / "sha256").exists() or not tuple((root / "sha256").iterdir())


def test_startup_retires_uncommitted_private_evidence(tmp_path):
    writer = IntakeEvidenceWriter(tmp_path, "run-1")
    writer.seal(
        b"orphan",
        classified_event_id="board-broker:000002",
        sanitized=b"orphan",
        redaction_proof=EMPTY_PROOF,
    )

    IntakeEvidenceWriter(tmp_path, "run-1")

    root = tmp_path / "runs" / "run-1" / "sealed" / "private-board-response"
    assert not tuple((root / "references").iterdir())
    assert not tuple((root / "sha256").iterdir())


def test_startup_retires_blob_written_before_its_registration(tmp_path):
    body = b"crashed-before-reference"
    digest = hashlib.sha256(body).hexdigest()
    blobs = tmp_path / "runs" / "run-1" / "sealed" / "private-board-response" / "sha256"
    blobs.mkdir(parents=True)
    (blobs / digest).write_bytes(body)

    IntakeEvidenceWriter(tmp_path, "run-1")

    assert not tuple(blobs.iterdir())


def test_private_intake_digest_is_explicitly_excluded_from_capsule_promotion():
    class Source:
        referenced_blobs = frozenset({"a" * 64})
        events = (
            {
                "event_type": "board-broker.recorded",
                "payload": {"operation": "intake-read", "raw_blob_digest": "b" * 64},
            },
        )

    assert _excluded_blobs(Source(), ()) == [
        {"digest": "a" * 64, "classification": "canonical-private-body"},
        {"digest": "b" * 64, "classification": "restart-private-broker"},
    ]
