"""Durable submission-ledger replay and exact-correlation seam."""

from types import SimpleNamespace

import pytest

from solver.submission.ambiguity import CompleteSubmissionIdentity
from solver.submission.ledger import SubmissionLedgerPhase, SubmissionLedgerRecorded, project_submission_row
from solver.event_store import EventStore


IDENTITY = CompleteSubmissionIdentity("board", 7, "revision", "instance", "candidate-digest", 3)


def event(phase, *, candidate="candidate-1", verdict=""):
    recorded = SubmissionLedgerRecorded(
        f"request-1:{phase.value}", phase, "request-1", candidate, "flag{x}", IDENTITY, "2026-09-13T00:00:00Z", verdict
    )
    return SimpleNamespace(event_type=recorded.event_type, payload=recorded.payload(blob_digest="blob", blob_bytes=7))


def test_intent_without_result_replays_as_unsettled():
    row = project_submission_row([event(SubmissionLedgerPhase.INTENDED)], IDENTITY.effect_id)
    assert row["status"] == "unsettled"


def test_submission_ledger_is_a_registered_canonical_event(tmp_path):
    recorded = SubmissionLedgerRecorded(
        "request-1:intended",
        SubmissionLedgerPhase.INTENDED,
        "request-1",
        "candidate-1",
        "flag{x}",
        IDENTITY,
        "2026-09-13T00:00:00Z",
    )

    committed = EventStore(tmp_path, "run-1").append(recorded, body=b"flag{x}")

    assert project_submission_row([committed], IDENTITY.effect_id)["status"] == "unsettled"


def test_exact_result_replays_as_classified_authoritative_row():
    row = project_submission_row(
        [event(SubmissionLedgerPhase.INTENDED), event(SubmissionLedgerPhase.CLASSIFIED, verdict="correct")],
        IDENTITY.effect_id,
    )
    assert (row["status"], row["verdict"], row["submission_epoch"], row["effect_id"]) == (
        "classified",
        "correct",
        3,
        IDENTITY.effect_id,
    )


def test_mismatched_result_is_rejected():
    with pytest.raises(ValueError, match="does not match intent"):
        project_submission_row(
            [event(SubmissionLedgerPhase.INTENDED), event(SubmissionLedgerPhase.CLASSIFIED, candidate="other")],
            IDENTITY.effect_id,
        )


def test_duplicate_result_is_rejected_on_replay():
    with pytest.raises(ValueError, match="replay is ambiguous"):
        project_submission_row(
            [
                event(SubmissionLedgerPhase.INTENDED),
                event(SubmissionLedgerPhase.CLASSIFIED),
                event(SubmissionLedgerPhase.CLASSIFIED),
            ],
            IDENTITY.effect_id,
        )
