"""One deep seam for generation-fenced hostile command execution."""

from __future__ import annotations

import fcntl
import json
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from solver.attempt_executor_contracts import (
    AttemptEnvelopeRecorded,
    AttemptRequest,
    AttemptResult,
    EnvelopeRecord,
    EnvelopeSpec,
    NetworkPolicy,
    ResourceOutcome,
    RuntimeBinding,
    RuntimeObservation,
    RuntimeReservation,
)
from solver.attempt_process_contracts import AttemptProcessRecorded, ProcessRecord
from solver.event_store import EventStore
from solver.event_store_contracts import ATTEMPT_ENVELOPE_RECORDED, EMPTY_BLOB_DIGEST
from solver.event_store_storage import canonical_bytes
from solver.isolation import STRICT_PROFILE_DIGEST
from solver.isolation_receipt import verify_receipt as verify_isolation_receipt
from solver.work_generation import GenerationAuthority, GenerationFence


@dataclass(frozen=True)
class RuntimeInput:
    state: Path
    run_id: str
    request: AttemptRequest
    binding: RuntimeBinding


class RuntimeAdapter(Protocol):
    def reconcile(self, unresolved: tuple[dict[str, object], ...]) -> tuple[RuntimeObservation, ...]: ...

    def prepare(self, envelope_id: str, incoming: RuntimeInput) -> RuntimeReservation: ...

    def launch(self, envelope_id: str, incoming: RuntimeInput) -> RuntimeObservation: ...


class ExecutorBusy(RuntimeError):
    """Another process owns reconciliation and launch for this Run."""


class LateAttemptResult(RuntimeError):
    """A cleaned child result arrived after its Work generation closed."""


class ProcessTreeResidue(RuntimeError):
    """An owned process tree could not be completely reconciled."""


class AttemptHandle:
    """One in-flight envelope, with idempotent cancellation and one typed result."""

    def __init__(self, executor: AttemptExecutor, envelope_id: str, request: AttemptRequest) -> None:
        self._executor = executor
        self.envelope_id = envelope_id
        self._request = request
        self._done = threading.Event()
        self._cancelled = threading.Event()
        self._result: AttemptResult | None = None
        self._error: BaseException | None = None

    def cancel(self) -> None:
        if self._cancelled.is_set():
            return
        self._cancelled.set()
        self._executor._revoke_generation(self._request.generation_id)
        cancel = getattr(self._executor._runtime, "cancel", None)
        if cancel is not None:
            cancel(self.envelope_id)

    def result(self) -> AttemptResult:
        self._done.wait()
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result

    def _finish(self, result: AttemptResult) -> None:
        self._result = result
        self._done.set()

    def _fail(self, error: BaseException) -> None:
        self._error = error
        self._done.set()


class AttemptExecutor:
    """Reserve, launch, measure, terminate and record every hostile envelope."""

    def __init__(
        self,
        *,
        state: Path,
        run_id: str,
        isolation_receipt: Path,
        binding: RuntimeBinding,
        generation_fence: GenerationFence,
        runtime: RuntimeAdapter,
        timestamp,
    ) -> None:
        verified = verify_isolation_receipt(isolation_receipt)
        receipt = json.loads(verified.read_bytes())
        if receipt["run_id"] != run_id or receipt["image_id"] != binding.image_id:
            raise ValueError("Attempt executor binding differs from strict-Isolation admission")
        self._state = Path(state)
        self._run_id = run_id
        self._binding = binding
        self._isolation_receipt = verified
        self._generations = generation_fence
        self._runtime = runtime
        self._timestamp = timestamp
        self._store = EventStore(self._state, run_id=run_id)
        self._canonical_lock = threading.RLock()
        self._handles: set[AttemptHandle] = set()
        self._handles_lock = threading.Lock()
        self._generation_revocations: list[Callable[[str], None]] = []
        self._lock_file = self._open_lock()
        self._owner_epoch = self._next_owner_epoch()
        try:
            self._append(EnvelopeRecord.OWNER_OPENED, event_id=f"{self._owner_epoch}:open")
            self._reconcile()
        except BaseException:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None
            raise

    def _open_lock(self):
        path = self._state / "runs" / self._run_id / "attempt-executor.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        opened = path.open("a+b")
        try:
            fcntl.flock(opened.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            opened.close()
            raise ExecutorBusy(f"Run {self._run_id!r} already has an Attempt executor") from error
        return opened

    def _next_owner_epoch(self) -> str:
        count = sum(
            event.event_type == ATTEMPT_ENVELOPE_RECORDED and event.payload["record"] == "owner-opened"
            for event in self._store.events()
        )
        return f"executor-owner-{count + 1:06d}"

    def _reconcile(self) -> None:
        states = _envelope_states(self._store.events())
        unresolved = tuple(state for state in states.values() if state["record"] not in {"result", "discarded"})
        residue = _residue_states(self._store.events(), states, unresolved)
        observations = self._runtime.reconcile((*unresolved, *residue))
        for state, observation in zip((*unresolved, *residue), observations, strict=True):
            if state in residue:
                if observation.process_lifecycle is None:
                    raise ProcessTreeResidue(f"envelope {state['envelope_id']!r} lacks reconciliation evidence")
                self._append_process(
                    ProcessRecord.OBSERVED,
                    state,
                    event_id=f"{state['envelope_id']}:process-observed:{self._owner_epoch}",
                    lifecycle=observation.process_lifecycle,
                )
                if not observation.cleanup_complete:
                    raise ProcessTreeResidue(f"envelope {state['envelope_id']!r} retains process residue")
                continue
            try:
                self._record_result(state, observation)
            except LateAttemptResult:
                pass
            if not observation.cleanup_complete:
                raise ProcessTreeResidue(f"envelope {state['envelope_id']!r} retains process residue")

    def start(self, request: AttemptRequest) -> AttemptHandle:
        with self._canonical_lock:
            state = next(
                (
                    item
                    for item in self._generations.projection().generations
                    if item.generation_id == request.generation_id
                ),
                None,
            )
            if state is None or not state.active or state.attempt_id != request.attempt_id:
                raise ValueError("Attempt request does not own one active Work generation")
            if not request.argv or any(not argument or "\x00" in argument for argument in request.argv):
                raise ValueError("Attempt command must be a non-empty NUL-free argv")
            if not request.workspace.is_dir():
                raise ValueError("Attempt generation workspace must already exist")
            self._generations.authorize(request.generation_id, GenerationAuthority.TOOL)
            envelope_id = self._next_envelope_id()
            self._append(
                EnvelopeRecord.RESERVED,
                event_id=f"{envelope_id}:reserved",
                envelope_id=envelope_id,
                request=request,
                declared=request.envelope.document(),
            )
        handle = AttemptHandle(self, envelope_id, request)
        with self._handles_lock:
            self._handles.add(handle)
        threading.Thread(target=self._run, args=(handle,), daemon=True).start()
        return handle

    def close(self) -> None:
        """Release process ownership after every started handle has reached a result."""

        with self._handles_lock:
            handles = tuple(self._handles)
        for handle in handles:
            handle.cancel()
        for handle in handles:
            try:
                handle.result()
            except BaseException:
                pass
        if self._lock_file is None:
            return
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        self._lock_file.close()
        self._lock_file = None

    def close_generation(self, generation_id: str, disposition) -> None:
        """Fence one Work generation, then terminate and drain all of its envelopes."""

        self._revoke_generation(generation_id)
        with self._canonical_lock:
            self._generations.close(generation_id, disposition)
            close = next(
                event
                for event in reversed(self._store.events())
                if event.event_type == "work-generation.recorded"
                and event.payload["generation_id"] == generation_id
                and event.payload["record"] == "close"
            )
            fenced = {
                event.payload["envelope_id"]
                for event in self._store.events()
                if event.event_type == "attempt-process.recorded"
                and event.payload["record"] == ProcessRecord.FENCED.value
            }
            for state in _envelope_states(self._store.events()).values():
                envelope_id = str(state["envelope_id"])
                if state["generation_id"] == generation_id and envelope_id not in fenced:
                    self._append_process(
                        ProcessRecord.FENCED,
                        state,
                        event_id=f"{envelope_id}:fenced",
                        fence_sequence=close.sequence,
                        fence_ts=str(close.payload["ts"]),
                    )
        with self._handles_lock:
            handles = tuple(handle for handle in self._handles if handle._request.generation_id == generation_id)
        for handle in handles:
            handle.cancel()
        for handle in handles:
            try:
                handle.result()
            except LateAttemptResult:
                pass
        self._write_process_receipt()

    def add_generation_revocation(self, revoke: Callable[[str], None]) -> None:
        """Register authority removed before any owned process cancellation or teardown."""

        if revoke not in self._generation_revocations:
            self._generation_revocations.append(revoke)

    def _revoke_generation(self, generation_id: str) -> None:
        for revoke in tuple(self._generation_revocations):
            revoke(generation_id)

    def _run(self, handle: AttemptHandle) -> None:
        try:
            incoming = RuntimeInput(self._state, self._run_id, handle._request, self._binding)
            try:
                reservation = self._runtime.prepare(handle.envelope_id, incoming)
            except BaseException as error:
                observation = RuntimeObservation(
                    ResourceOutcome.LAUNCH_FAILED,
                    None,
                    b"",
                    "",
                    -1,
                    {"launch_failure": type(error).__name__, "detail": str(error)[:512]},
                    True,
                )
                with self._canonical_lock:
                    state = _envelope_states(self._store.events())[handle.envelope_id]
                    handle._finish(self._record_result(state, observation))
                return
            with self._canonical_lock:
                self._append(
                    EnvelopeRecord.LAUNCHED,
                    event_id=f"{handle.envelope_id}:launched",
                    envelope_id=handle.envelope_id,
                    request=handle._request,
                    cgroup_path=reservation.cgroup_path,
                    executor_uid=reservation.executor_uid,
                    declared=handle._request.envelope.document(),
                    observed={"control_nonce": reservation.control_nonce},
                )
            observation = self._runtime.launch(
                handle.envelope_id,
                incoming,
            )
            if handle._cancelled.is_set() and observation.outcome is ResourceOutcome.EXITED:
                observation = RuntimeObservation(
                    ResourceOutcome.CANCELLED,
                    observation.exit_code,
                    observation.output,
                    observation.cgroup_path,
                    observation.executor_uid,
                    observation.observed,
                    observation.cleanup_complete,
                    observation.process_lifecycle,
                )
            with self._canonical_lock:
                state = _envelope_states(self._store.events())[handle.envelope_id]
                handle._finish(self._record_result(state, observation))
        except BaseException as error:
            handle._fail(error)
        finally:
            with self._handles_lock:
                self._handles.discard(handle)

    def _record_result(self, state: dict[str, object], observation: RuntimeObservation) -> AttemptResult:
        envelope_id = str(state["envelope_id"])
        request = AttemptRequest(
            generation_id=str(state["generation_id"]),
            attempt_id=str(state["attempt_id"]),
            step_id=str(state["step_id"]),
            argv=("reconciled",),
            workspace=self._state,
            envelope=_spec_from_document(state["declared"]),
        )
        if observation.process_lifecycle is not None:
            self._append_process(
                ProcessRecord.OBSERVED,
                state,
                event_id=f"{envelope_id}:process-observed:{self._owner_epoch}",
                lifecycle=observation.process_lifecycle,
            )
        evidence = observation.output or canonical_bytes(
            {"envelope_id": envelope_id, "outcome": observation.outcome.value}
        )

        def commit_result(_grant) -> None:
            self._append(
                EnvelopeRecord.RESULT,
                event_id=f"{envelope_id}:result",
                envelope_id=envelope_id,
                request=request,
                cgroup_path=observation.cgroup_path,
                executor_uid=observation.executor_uid,
                outcome=observation.outcome,
                exit_code=observation.exit_code,
                cleanup_complete=observation.cleanup_complete,
                declared=state["declared"],
                observed={**dict(state.get("observed", {})), **observation.observed},
            )

        decision, _ = self._generations.authorize_and_commit(
            request.generation_id,
            GenerationAuthority.TOOL,
            commit_result,
            evidence,
        )
        if not decision.accepted:
            self._append(
                EnvelopeRecord.DISCARDED,
                event_id=f"{envelope_id}:discarded",
                envelope_id=envelope_id,
                request=request,
                cgroup_path=observation.cgroup_path,
                executor_uid=observation.executor_uid,
                cleanup_complete=observation.cleanup_complete,
                declared=state["declared"],
                observed={**dict(state.get("observed", {})), **observation.observed},
            )
            if not observation.cleanup_complete:
                raise ProcessTreeResidue(f"envelope {envelope_id!r} retains process residue")
            raise LateAttemptResult(
                f"Attempt result belongs to {decision.classification.value} {request.generation_id!r}"
            )
        from solver.attempt_resource_receipt import write_receipt

        write_receipt(self._state, self._run_id, self._isolation_receipt)
        if observation.process_lifecycle is not None:
            self._write_process_receipt()
        if not observation.cleanup_complete:
            raise ProcessTreeResidue(f"envelope {envelope_id!r} retains process residue")
        return AttemptResult(
            envelope_id=envelope_id,
            generation_id=request.generation_id,
            outcome=observation.outcome,
            exit_code=observation.exit_code,
            output=observation.output,
            observed=observation.observed,
            cleanup_complete=observation.cleanup_complete,
        )

    def _append_process(
        self,
        record: ProcessRecord,
        state: dict[str, object],
        *,
        event_id: str,
        fence_sequence: int = 0,
        fence_ts: str = "",
        lifecycle=None,
    ) -> None:
        event = AttemptProcessRecorded(
            event_id=event_id,
            record=record,
            owner_epoch=self._owner_epoch,
            envelope_id=str(state["envelope_id"]),
            generation_id=str(state["generation_id"]),
            attempt_id=str(state["attempt_id"]),
            step_id=str(state["step_id"]),
            cgroup_path=str(state.get("cgroup_path", "")),
            fence_sequence=fence_sequence,
            fence_ts=fence_ts,
            lifecycle=lifecycle,
            ts=self._timestamp(),
        )
        self._store.append(event, body=b"")
        self._write_process_receipt()

    def _write_process_receipt(self) -> None:
        from solver.attempt_process_lifecycle_receipt import write_receipt

        write_receipt(self._state, self._run_id, self._isolation_receipt)

    def _next_envelope_id(self) -> str:
        serial = 1 + sum(
            event.event_type == ATTEMPT_ENVELOPE_RECORDED and event.payload["record"] == "reserved"
            for event in self._store.events()
        )
        return f"envelope-{serial:06d}"

    def _append(
        self,
        record: EnvelopeRecord,
        *,
        event_id: str,
        envelope_id: str = "",
        request: AttemptRequest | None = None,
        cgroup_path: str = "",
        executor_uid: int = -1,
        outcome: ResourceOutcome | None = None,
        exit_code: int | None = None,
        cleanup_complete: bool = False,
        declared=None,
        observed=None,
    ) -> None:
        event = AttemptEnvelopeRecorded(
            event_id=event_id,
            record=record,
            owner_epoch=self._owner_epoch,
            envelope_id=envelope_id,
            generation_id=request.generation_id if request else "",
            attempt_id=request.attempt_id if request else "",
            step_id=request.step_id if request else "",
            profile_digest=STRICT_PROFILE_DIGEST if request else "",
            binding=self._binding if request else None,
            cgroup_path=cgroup_path,
            executor_uid=executor_uid,
            outcome=outcome,
            exit_code=exit_code,
            cleanup_complete=cleanup_complete,
            declared=declared,
            observed=observed,
            ts=self._timestamp(),
        )
        reservation = self._store.reserve(event, blob_digest=EMPTY_BLOB_DIGEST, blob_bytes=0)
        self._store.commit(reservation, event, body=b"")


def _envelope_states(events) -> dict[str, dict[str, object]]:
    states: dict[str, dict[str, object]] = {}
    for event in events:
        if event.event_type == ATTEMPT_ENVELOPE_RECORDED and event.payload["envelope_id"]:
            states[event.payload["envelope_id"]] = dict(event.payload)
    return states


def _residue_states(events, states, unresolved) -> tuple[dict[str, object], ...]:
    unresolved_ids = {state["envelope_id"] for state in unresolved}
    latest: dict[str, dict[str, object]] = {}
    for event in events:
        if event.event_type == "attempt-process.recorded" and event.payload["record"] == ProcessRecord.OBSERVED.value:
            latest[event.payload["envelope_id"]] = event.payload
    return tuple(
        states[envelope_id]
        for envelope_id, process in latest.items()
        if envelope_id not in unresolved_ids and process["lifecycle"]["after_kill"]
    )


def _spec_from_document(document) -> EnvelopeSpec:
    return EnvelopeSpec(
        cpu_seconds=float(document["cpu_seconds"]),
        cpu_quota_us=int(document["cpu_quota_us"]),
        memory_bytes=int(document["memory_bytes"]),
        pids=int(document["pids"]),
        filesystem_bytes=int(document["filesystem_bytes"]),
        network=NetworkPolicy(document["network"]),
        wall_seconds=float(document["wall_seconds"]),
        cleanup_seconds=float(document["cleanup_seconds"]),
    )


__all__ = [
    "AttemptExecutor",
    "AttemptHandle",
    "AttemptRequest",
    "AttemptResult",
    "EnvelopeSpec",
    "ExecutorBusy",
    "LateAttemptResult",
    "ProcessTreeResidue",
    "NetworkPolicy",
    "ResourceOutcome",
    "RuntimeBinding",
    "RuntimeInput",
    "RuntimeObservation",
]
