import json

import pytest

from solver.event_store import InvalidReceiptError
from solver.lead_contracts import CandidateProposal
from solver.lead_controller import LeadController
from solver.lead_receipt import link_manifest, manifest_receipt, verify_receipt, write_receipt
from solver.manifest import generate_manifest
from solver.manifest_contracts import CORE_REQUIREMENT_IDS
from solver.redaction import Redactor
from solver.work_generation import GenerationFence

from test_lead_controller import ScriptedModel, clock, initial, measured
from test_manifest import release_candidate_profile


def test_receipt_reconstructs_replay_and_never_discloses_candidate(tmp_path):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    controller = LeadController(
        tmp_path,
        "run-1",
        redactor,
        clock,
        ScriptedModel(measured("derive candidate", CandidateProposal("flag{private}", ("observation-7",)))),
        fence=fence,
    )
    controller.handle(initial(generation.generation_id, "observed candidate"))

    path = write_receipt(tmp_path, "run-1")
    document = json.loads(path.read_text())

    assert path.name == "lead-engagement.receipt.json"
    assert document["receipt_type"] == "lead-engagement"
    assert document["role"] == "solve-lead"
    engagement = document["engagements"][0]
    assert engagement["state_transition_digest"] == engagement["deterministic_replay"]["replayed_digest"]
    assert document["deterministic_replay"]["matches"] is True
    assert engagement["turn_measures"] == [
        {
            "context_bytes": 18,
            "duration_ms": 10,
            "model": "fake-model",
            "output_bytes": engagement["turn_measures"][0]["output_bytes"],
            "tokens_in": 20,
            "tokens_out": 10,
            "turn_index": 1,
            "usage_known": True,
        }
    ]
    assert engagement["proposals"][0]["kind"] == "candidate"
    assert set(engagement["proposals"][0]) == {"digest", "kind", "proposal_id", "turn_index"}
    assert "flag{private}" not in path.read_text()
    assert document["manifest_link"] == {
        "receipt_ref": "receipt:lead-engagement",
        "row_id": "core.persistent-solve-lead",
    }
    assert document["manifest_link"]["row_id"] in CORE_REQUIREMENT_IDS
    assert verify_receipt(path) == path
    assert manifest_receipt(path)["ref"] == "receipt:lead-engagement"
    manifest = generate_manifest(
        image_digest="sha256:" + "8" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    linked = link_manifest(manifest, path)
    row = next(item for item in linked["requirements"] if item["row_id"] == "core.persistent-solve-lead")
    assert row["status"] == "implemented"
    assert row["receipt_ref"] == "receipt:lead-engagement"


def test_receipt_rejects_a_document_not_rebuilt_from_canonical_state(tmp_path):
    redactor = Redactor({})
    fence = GenerationFence(tmp_path, "run-1", redactor, clock)
    generation = fence.acquire("challenge-1", "attempt-1")
    controller = LeadController(
        tmp_path,
        "run-1",
        redactor,
        clock,
        ScriptedModel(measured("derive candidate", CandidateProposal("flag{x}", ("observation-1",)))),
        fence=fence,
    )
    controller.handle(initial(generation.generation_id, "observed"))
    path = write_receipt(tmp_path, "run-1")
    document = json.loads(path.read_text())
    document["engagements"][0]["state_transition_digest"] = "0" * 64
    path.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(InvalidReceiptError):
        verify_receipt(path)
