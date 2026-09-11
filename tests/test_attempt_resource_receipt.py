"""Attempt Resource receipts are reconstructed from canonical facts, not trusted summaries."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from solver.attempt_executor import AttemptExecutor, RuntimeBinding
from solver.attempt_executor_contracts import ResourceOutcome, RuntimeObservation
from solver.attempt_resource_receipt import manifest_receipt, verify_receipt, write_receipt
from solver.event_store import InvalidReceiptError
from solver.redaction import Redactor
from solver.work_generation import GenerationFence
from test_attempt_executor import IMAGE_ID, OutcomeRuntime, isolation_receipt, request


class QualifiedRuntime(OutcomeRuntime):
    def launch(self, envelope_id, incoming):
        observation = super().launch(envelope_id, incoming)
        return RuntimeObservation(
            outcome=observation.outcome,
            exit_code=observation.exit_code,
            output=observation.output,
            cgroup_path=observation.cgroup_path,
            executor_uid=observation.executor_uid,
            observed={
                **observation.observed,
                "processes_after_kill": 0,
                "cleanup_seconds": 0.01,
                "deny_control": True,
                "deny_secrets": True,
                "deny_sibling": True,
                "deny_outside_workspace": True,
                "deny_public_network": True,
            },
            cleanup_complete=True,
        )


def qualified_receipt(tmp_path: Path) -> Path:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-11T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    runtime = QualifiedRuntime(ResourceOutcome.CPU)
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
        generation_fence=fence,
        runtime=runtime,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )
    for outcome in (
        ResourceOutcome.CPU,
        ResourceOutcome.MEMORY,
        ResourceOutcome.PIDS,
        ResourceOutcome.FILESYSTEM,
        ResourceOutcome.NETWORK,
        ResourceOutcome.DEADLINE,
    ):
        runtime.outcome = outcome
        executor.start(request(tmp_path, generation.generation_id)).result()
    return write_receipt(state, "run-1", isolation_receipt(state))


def test_receipt_reconstructs_all_breaches_deny_probes_and_exact_candidate_binding(tmp_path: Path) -> None:
    receipt = qualified_receipt(tmp_path)

    verified = verify_receipt(receipt, require_qualified=True)
    document = json.loads(receipt.read_bytes())

    assert verified == receipt
    assert document["binding"] == {
        "image_id": IMAGE_ID,
        "image_manifest_digest": "sha256:" + "b" * 64,
        "image_config_digest": "sha256:" + "c" * 64,
        "platform": "linux/arm64",
        "profile_digest": document["profile_digest"],
    }
    assert {row["outcome"] for row in document["envelopes"]} == {
        "cpu-limit",
        "memory-limit",
        "pid-limit",
        "filesystem-limit",
        "network-limit",
        "wall-clock-limit",
    }
    assert all(row["cleanup_complete"] for row in document["envelopes"])
    assert manifest_receipt(receipt)["ref"] == "receipt:attempt-resource-envelope"


def test_receipt_tampering_and_partial_or_stale_evidence_cannot_reach_the_manifest(tmp_path: Path) -> None:
    receipt = qualified_receipt(tmp_path)
    document = json.loads(receipt.read_bytes())
    document["envelopes"][0]["cleanup_complete"] = False
    receipt.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(InvalidReceiptError):
        verify_receipt(receipt, require_qualified=True)
    with pytest.raises(InvalidReceiptError):
        manifest_receipt(receipt)
