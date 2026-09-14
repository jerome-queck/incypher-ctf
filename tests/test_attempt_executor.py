"""A generation-scoped Attempt executes only through one measured Resource envelope."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from solver.attempt_executor import (
    AttemptExecutor,
    AttemptRequest,
    EnvelopeSpec,
    NetworkPolicy,
    RuntimeBinding,
    RuntimeObservation,
    RuntimeReservation,
    ResourceOutcome,
    LateAttemptResult,
)
from solver.event_store import GenerationDisposition
from solver.event_store import EventStore
from solver.isolation import (
    STRICT_CONTROLS,
    STRICT_PROFILE_DIGEST,
    STRICT_PROFILE_ID,
    STRICT_RUNTIME_PIN,
    IsolationReceipt,
)
from solver.isolation_receipt import write_receipt as write_isolation_receipt
from solver.redaction import Redactor
from solver.work_generation import GenerationFence


IMAGE_ID = "sha256:" + "a" * 64


def isolation_receipt(state: Path, run_id: str = "run-1") -> Path:
    return write_isolation_receipt(
        state,
        run_id,
        IsolationReceipt(
            profile_id=STRICT_PROFILE_ID,
            profile_digest=STRICT_PROFILE_DIGEST,
            image_id=IMAGE_ID,
            runtime_pin=tuple(STRICT_RUNTIME_PIN.items()),
            outer_capabilities="empty",
            checks=tuple((control, "pass") for control in STRICT_CONTROLS),
            broker_peer_uid=20000,
            processes_before_kill=4,
            processes_after_kill=0,
            owned_residue=(),
        ),
    )


def request(tmp_path: Path, generation_id: str) -> AttemptRequest:
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    return AttemptRequest(
        generation_id=generation_id,
        attempt_id="attempt-1",
        step_id="step-1",
        argv=("file", "-b", "sample"),
        workspace=work,
        envelope=EnvelopeSpec(
            cpu_seconds=1.0,
            cpu_quota_us=20_000,
            memory_bytes=32 * 1024 * 1024,
            pids=8,
            filesystem_bytes=8 * 1024 * 1024,
            network=NetworkPolicy.DENY,
            wall_seconds=5.0,
            cleanup_seconds=1.0,
        ),
    )


class ImmediateRuntime:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def reconcile(self, unresolved):
        self.events.append(("reconcile", tuple(unresolved)))
        return ()

    def prepare(self, envelope_id, incoming):
        canonical = EventStore(incoming.state, run_id=incoming.run_id).events()
        assert canonical[-1].payload["record"] == "reserved"
        self.events.append(("prepare", envelope_id))
        return RuntimeReservation(f"/run/cgroup-parent/container/executors/{envelope_id}", 20000)

    def launch(self, envelope_id, incoming):
        canonical = EventStore(incoming.state, run_id=incoming.run_id).events()
        assert canonical[-1].payload["record"] == "launched"
        self.events.append(("launch", envelope_id))
        return RuntimeObservation.exited(
            exit_code=0,
            output=b"application/octet-stream\n",
            cgroup_path=f"/run/cgroup-parent/container/executors/{envelope_id}",
            executor_uid=20000,
        )


class OutcomeRuntime(ImmediateRuntime):
    def __init__(self, outcome: ResourceOutcome) -> None:
        super().__init__()
        self.outcome = outcome

    def launch(self, envelope_id, incoming):
        super().launch(envelope_id, incoming)
        return RuntimeObservation(
            outcome=self.outcome,
            exit_code=None,
            output=b"",
            cgroup_path=f"/run/cgroup-parent/container/executors/{envelope_id}",
            executor_uid=20000,
            observed={"cause": self.outcome.value, "processes_after_kill": 0},
            cleanup_complete=True,
        )


class CancellableRuntime(ImmediateRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.cancel_count = 0

    def launch(self, envelope_id, incoming):
        self.started.set()
        assert self.stopped.wait(2)
        return RuntimeObservation.exited(
            exit_code=-9,
            output=b"",
            cgroup_path=f"/run/cgroup-parent/container/executors/{envelope_id}",
            executor_uid=20000,
        )

    def cancel(self, _envelope_id):
        self.cancel_count += 1
        self.stopped.set()


class DeferredRuntime(ImmediateRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def launch(self, envelope_id, incoming):
        self.started.set()
        assert self.release.wait(2)
        return RuntimeObservation.exited(
            exit_code=0,
            output=b"late hostile output",
            cgroup_path=f"/run/cgroup-parent/container/executors/{envelope_id}",
            executor_uid=20000,
        )


class CrashRuntime(ImmediateRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.live: set[str] = set()
        self.maximum_live = 0
        self.crash_once = True

    def launch(self, envelope_id, incoming):
        assert not self.live
        self.live.add(envelope_id)
        self.maximum_live = max(self.maximum_live, len(self.live))
        if self.crash_once:
            self.crash_once = False
            raise RuntimeError("controller crashed after kernel launch")
        self.live.remove(envelope_id)
        return RuntimeObservation.exited(
            exit_code=0,
            output=b"replacement\n",
            cgroup_path=f"/run/cgroup-parent/container/executors/{envelope_id}",
            executor_uid=20000,
        )

    def reconcile(self, unresolved):
        observations = []
        for state in unresolved:
            envelope_id = state["envelope_id"]
            assert envelope_id in self.live
            self.live.remove(envelope_id)
            observations.append(
                RuntimeObservation(
                    outcome=ResourceOutcome.RECONCILED,
                    exit_code=None,
                    output=b"",
                    cgroup_path=f"/run/cgroup-parent/container/executors/{envelope_id}",
                    executor_uid=20000,
                    observed={"processes_after_kill": 0},
                    cleanup_complete=True,
                )
            )
        return tuple(observations)


def test_active_generation_and_verified_isolation_are_reserved_before_launch(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-11T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    runtime = ImmediateRuntime()
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(
            image_id=IMAGE_ID,
            image_manifest_digest="sha256:" + "b" * 64,
            image_config_digest="sha256:" + "c" * 64,
            platform="linux/arm64",
        ),
        generation_fence=fence,
        runtime=runtime,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )

    result = executor.start(request(tmp_path, generation.generation_id)).result()

    assert result.outcome.value == "exited"
    assert result.exit_code == 0
    assert result.output == b"application/octet-stream\n"
    records = [
        event.payload["record"]
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "attempt-envelope.recorded"
    ]
    assert records == ["owner-opened", "reserved", "launched", "result"]
    assert (state / "runs" / "run-1" / "canonical" / "attempt-resource-envelope.receipt.json").is_file()
    authority = [
        event.payload
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "work-generation.recorded" and event.payload["record"] == "authority"
    ]
    assert authority[-1]["authority"] == "tool"
    result_event = next(
        event
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "attempt-envelope.recorded" and event.payload["record"] == "result"
    )
    reservation_rows = [
        json.loads(line) for line in EventStore(state, run_id="run-1").reservations_path.read_text().splitlines()
    ]
    assert [row["status"] for row in reservation_rows if row["sequence"] == result_event.sequence] == [
        "reserved",
        "committed",
    ]
    assert runtime.events == [
        ("reconcile", ()),
        ("prepare", "envelope-000001"),
        ("launch", "envelope-000001"),
    ]


@pytest.mark.parametrize(
    "outcome",
    [
        ResourceOutcome.CPU,
        ResourceOutcome.MEMORY,
        ResourceOutcome.PIDS,
        ResourceOutcome.FILESYSTEM,
        ResourceOutcome.NETWORK,
        ResourceOutcome.DEADLINE,
    ],
)
def test_each_trusted_resource_breach_is_distinct_and_cleans_the_whole_envelope(
    tmp_path: Path, outcome: ResourceOutcome
) -> None:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-11T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
        generation_fence=fence,
        runtime=OutcomeRuntime(outcome),
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )

    result = executor.start(request(tmp_path, generation.generation_id)).result()

    assert result.outcome is outcome
    assert result.cleanup_complete is True
    assert result.observed["processes_after_kill"] == 0


def test_cancellation_is_idempotent_and_cannot_be_reported_as_a_normal_exit(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-11T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    runtime = CancellableRuntime()
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
        generation_fence=fence,
        runtime=runtime,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )
    handle = executor.start(request(tmp_path, generation.generation_id))
    assert runtime.started.wait(2)

    handle.cancel()
    handle.cancel()
    result = handle.result()

    assert result.outcome is ResourceOutcome.CANCELLED
    assert runtime.cancel_count == 1


def test_generation_cancellation_revokes_drains_and_blocks_late_starts_before_fencing(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-11T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    runtime = CancellableRuntime()
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
        generation_fence=fence,
        runtime=runtime,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )
    revoked = []
    executor.add_generation_revocation(revoked.append)
    handle = executor.start(request(tmp_path, generation.generation_id))
    assert runtime.started.wait(2)

    executor.cancel_generation(generation.generation_id)
    executor.cancel_generation(generation.generation_id)

    assert handle.result().outcome is ResourceOutcome.CANCELLED
    assert revoked == [generation.generation_id]
    assert runtime.cancel_count == 1
    assert fence.projection().active_by_work["challenge-1"].generation_id == generation.generation_id
    with pytest.raises(ValueError, match="closing"):
        executor.start(request(tmp_path, generation.generation_id))

    executor.close_generation(generation.generation_id, GenerationDisposition.COMPLETE)
    assert not fence.projection().generations[-1].active


def test_result_is_rejected_when_its_generation_closed_during_execution(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-11T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    runtime = DeferredRuntime()
    executor = AttemptExecutor(
        state=state,
        run_id="run-1",
        isolation_receipt=isolation_receipt(state),
        binding=RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
        generation_fence=fence,
        runtime=runtime,
        timestamp=lambda: "2026-09-11T00:00:00Z",
    )
    handle = executor.start(request(tmp_path, generation.generation_id))
    assert runtime.started.wait(2)
    fence.close(generation.generation_id, GenerationDisposition.COMPLETE)
    runtime.release.set()

    with pytest.raises(LateAttemptResult):
        handle.result()

    records = [
        event.payload["record"]
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "attempt-envelope.recorded"
    ]
    assert records[-1] == "discarded"
    assert "result" not in records


def test_successor_reconciles_a_launched_unrecorded_envelope_before_replacement(tmp_path: Path) -> None:
    state = tmp_path / "state"
    fence = GenerationFence(state, "run-1", Redactor({}), timestamp=lambda: "2026-09-11T00:00:00Z")
    generation = fence.acquire("challenge-1", "attempt-1")
    runtime = CrashRuntime()
    options = {
        "state": state,
        "run_id": "run-1",
        "isolation_receipt": isolation_receipt(state),
        "binding": RuntimeBinding(IMAGE_ID, "sha256:" + "b" * 64, "sha256:" + "c" * 64, "linux/arm64"),
        "generation_fence": fence,
        "runtime": runtime,
        "timestamp": lambda: "2026-09-11T00:00:00Z",
    }
    first = AttemptExecutor(**options)

    with pytest.raises(RuntimeError, match="controller crashed"):
        first.start(request(tmp_path, generation.generation_id)).result()
    assert runtime.live == {"envelope-000001"}
    first.close()

    successor = AttemptExecutor(**options)
    assert runtime.live == set()
    result = successor.start(request(tmp_path, generation.generation_id)).result()

    assert result.envelope_id == "envelope-000002"
    assert result.outcome is ResourceOutcome.EXITED
    assert runtime.maximum_live == 1
    outcomes = [
        event.payload["outcome"]
        for event in EventStore(state, run_id="run-1").events()
        if event.event_type == "attempt-envelope.recorded" and event.payload["record"] == "result"
    ]
    assert outcomes == ["reconciled-after-crash", "exited"]
