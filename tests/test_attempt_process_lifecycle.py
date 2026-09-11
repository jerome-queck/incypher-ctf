"""Attempt process trees close behind their durable Work-generation fence."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from solver.attempt_executor import AttemptExecutor, LateAttemptResult, ProcessTreeResidue, RuntimeBinding
from solver.attempt_executor_contracts import ProcessLifecycle, ResourceOutcome, RuntimeObservation
from solver.attempt_process_lifecycle_receipt import link_manifest, manifest_receipt, verify_receipt
from solver.event_store import EventStore, GenerationDisposition, InvalidReceiptError
from solver.manifest import generate_manifest
from solver.redaction import Redactor
from solver.work_generation import GenerationFence
from test_attempt_executor import IMAGE_ID, ImmediateRuntime, isolation_receipt, request
from test_manifest import release_candidate_profile


class ClosingRuntime(ImmediateRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.cancelled = threading.Event()

    def launch(self, envelope_id, incoming):
        self.started.set()
        assert self.cancelled.wait(2)
        return RuntimeObservation(
            ResourceOutcome.CANCELLED,
            None,
            b"late hostile output",
            f"/run/cgroup-parent/container/executors/{envelope_id}",
            20000,
            {"cause": "cancelled"},
            True,
            ProcessLifecycle(
                descendants=(41, 42, 43),
                after_term=(43,),
                after_kill=(),
                term_sent=True,
                kill_sent=True,
                term_grace_seconds=0.5,
                cleanup_seconds=0.6,
                stream_limit_bytes=1024,
                stream_captured_bytes=19,
                stream_total_bytes=19,
                stream_truncated=False,
                control_eof=False,
            ),
        )

    def cancel(self, _envelope_id):
        self.cancelled.set()


class ResidueRuntime(ClosingRuntime):
    def launch(self, envelope_id, incoming):
        observed = super().launch(envelope_id, incoming)
        process = observed.process_lifecycle
        assert process is not None
        return RuntimeObservation(
            observed.outcome,
            observed.exit_code,
            observed.output,
            observed.cgroup_path,
            observed.executor_uid,
            observed.observed,
            False,
            ProcessLifecycle(
                descendants=process.descendants,
                after_term=process.after_term,
                after_kill=(43,),
                term_sent=True,
                kill_sent=True,
                term_grace_seconds=process.term_grace_seconds,
                cleanup_seconds=process.cleanup_seconds,
                stream_limit_bytes=process.stream_limit_bytes,
                stream_captured_bytes=process.stream_captured_bytes,
                stream_total_bytes=process.stream_total_bytes,
                stream_truncated=process.stream_truncated,
                control_eof=process.control_eof,
            ),
        )

    def reconcile(self, unresolved):
        return tuple(
            RuntimeObservation(
                ResourceOutcome.RECONCILED,
                None,
                b"",
                str(state["cgroup_path"]),
                20000,
                {"cause": "restart-reconciliation"},
                False,
                ProcessLifecycle(
                    descendants=(43,),
                    after_term=(43,),
                    after_kill=(43,),
                    term_sent=True,
                    kill_sent=True,
                    term_grace_seconds=0.5,
                    cleanup_seconds=1.0,
                    stream_limit_bytes=1024,
                    stream_captured_bytes=0,
                    stream_total_bytes=0,
                    stream_truncated=False,
                    control_eof=True,
                ),
            )
            for state in unresolved
        )


def _executor(tmp_path: Path, runtime: ClosingRuntime):
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-12T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
        generation_fence=fence,
        runtime=runtime,
        timestamp=lambda: "2026-09-12T00:00:00Z",
    )
    return state, fence, generation, executor


def test_close_generation_fences_before_teardown_and_quarantines_late_output(tmp_path: Path) -> None:
    runtime = ClosingRuntime()
    state, _fence, generation, executor = _executor(tmp_path, runtime)
    handle = executor.start(request(tmp_path, generation.generation_id))
    assert runtime.started.wait(2)

    executor.close_generation(generation.generation_id, GenerationDisposition.COMPLETE)

    with pytest.raises(LateAttemptResult):
        handle.result()
    events = EventStore(state, run_id="run-1").events()
    generation_close = next(
        event.sequence
        for event in events
        if event.event_type == "work-generation.recorded" and event.payload["record"] == "close"
    )
    process_fence = next(
        event
        for event in events
        if event.event_type == "attempt-process.recorded" and event.payload["record"] == "fenced"
    )
    rejected = next(
        event
        for event in events
        if event.event_type == "work-generation.recorded" and event.payload["record"] == "late-event"
    )
    assert generation_close < process_fence.sequence < rejected.sequence
    assert process_fence.payload["fence_sequence"] == generation_close
    assert rejected.body == b"late hostile output"


def test_process_receipt_reconstructs_inventory_stream_fence_and_escalation(tmp_path: Path) -> None:
    runtime = ClosingRuntime()
    state, _fence, generation, executor = _executor(tmp_path, runtime)
    handle = executor.start(request(tmp_path, generation.generation_id))
    assert runtime.started.wait(2)
    executor.close_generation(generation.generation_id, GenerationDisposition.COMPLETE)
    with pytest.raises(LateAttemptResult):
        handle.result()

    path = state / "runs" / "run-1" / "canonical" / "attempt-process-lifecycle.receipt.json"
    assert verify_receipt(path) == path
    document = json.loads(path.read_bytes())
    assert document["processes"][0]["descendants"] == [41, 42, 43]
    assert document["processes"][0]["after_kill"] == []
    assert document["processes"][0]["stream_total_bytes"] == 19
    assert document["processes"][0]["fence_sequence"] > 0
    assert document["manifest_link"]["row_id"] == "core.supervisor"
    assert manifest_receipt(path)["ref"] == "receipt:attempt-process-lifecycle"

    manifest = generate_manifest(
        image_digest=IMAGE_ID,
        release_candidate_profile=release_candidate_profile(),
    )
    linked = link_manifest(manifest, path)
    supervisor = next(row for row in linked["requirements"] if row["row_id"] == "core.supervisor")
    assert supervisor["receipt_ref"] == "receipt:attempt-process-lifecycle"
    manifest["candidate"]["image_digest"] = "sha256:" + "f" * 64
    with pytest.raises(InvalidReceiptError, match="different candidate image"):
        link_manifest(manifest, path)


def test_generation_close_fails_when_owned_process_residue_survives_escalation(tmp_path: Path) -> None:
    runtime = ResidueRuntime()
    _state, _fence, generation, executor = _executor(tmp_path, runtime)
    executor.start(request(tmp_path, generation.generation_id))
    assert runtime.started.wait(2)

    with pytest.raises(ProcessTreeResidue, match="retains process residue"):
        executor.close_generation(generation.generation_id, GenerationDisposition.COMPLETE)


def test_restart_reconciles_residue_before_a_replacement_can_start(tmp_path: Path) -> None:
    runtime = ResidueRuntime()
    state, fence, generation, first = _executor(tmp_path, runtime)
    first.start(request(tmp_path, generation.generation_id))
    assert runtime.started.wait(2)
    with pytest.raises(ProcessTreeResidue):
        first.close_generation(generation.generation_id, GenerationDisposition.INTERRUPT)
    first.close()

    replacement = fence.acquire("challenge-1", "attempt-2")
    with pytest.raises(ProcessTreeResidue):
        AttemptExecutor(
            state=state,
            run_id="run-1",
            isolation_receipt=isolation_receipt(state),
            binding=RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
            generation_fence=fence,
            runtime=runtime,
            timestamp=lambda: "2026-09-12T00:00:00Z",
        )
    assert not any(
        event.payload.get("generation_id") == replacement.generation_id
        and event.event_type == "attempt-envelope.recorded"
        and event.payload["record"] == "reserved"
        for event in EventStore(state, run_id="run-1").events()
    )
