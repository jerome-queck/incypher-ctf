"""Admission-first, generation-fenced Specialist dispatch."""

from __future__ import annotations

import datetime as dt
import fcntl
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from threading import RLock

from solver.event_store_contracts import GenerationDisposition
from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.specialist_contracts import SpecialistBatch, SpecialistEvidence, SpecialistInvocation, SpecialistProfile
from solver.specialist_contracts import SpecialistInvoke, SpecialistProposal, SpecialistResult, SpecialistTask
from solver.specialist_contracts import EvidenceResolver, SpecialistTermination, SpecialistTerminator
from solver.specialist_contracts import SpecialistToolsFactory
from solver.specialist_contracts import SpecialistVerdict, SpecialistView
from solver.work_generation import GenerationFence
from solver.specialist_pool_proof import PROOF_DIGEST


class SpecialistPool:
    _owners: set[tuple[str, str, str]] = set()
    _owners_lock = RLock()

    def __init__(
        self,
        state: Path,
        run_id: str,
        profile: SpecialistProfile,
        generations: GenerationFence,
        *,
        now: Callable[[], dt.datetime],
        resolve_evidence: EvidenceResolver,
        tools: SpecialistToolsFactory,
        terminate: SpecialistTerminator,
        hook: Callable[[str], None] = lambda _point: None,
    ):
        expected_root = generations.store.run_dir.parent.parent.resolve()
        if generations.run_id != run_id or Path(state).resolve() != expected_root:
            raise ValueError("Specialist pool and generation authority name different canonical state or Run")
        self.state, self.run_id, self.profile, self.generations = Path(state), run_id, profile, generations
        self._now, self._resolve, self._tools, self._terminate, self._hook = (
            now,
            resolve_evidence,
            tools,
            terminate,
            hook,
        )
        self._path = self.state / run_id / "specialist-pool.control.json"
        self._receipt = self.state / run_id / "specialist-pool.receipt.json"
        self._file_lock = self.state / run_id / "specialist-pool.lock"

    def _owner_key(self, private_generation_id):
        return (str(self.state.resolve()), self.run_id, private_generation_id)

    def dispatch(
        self, tasks: tuple[SpecialistTask, ...], invoke: SpecialistInvoke, *, cancelled: Callable[[], bool]
    ) -> SpecialistBatch:
        self._file_lock.parent.mkdir(parents=True, exist_ok=True)
        with self._file_lock.open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            journal = self._load()
            known = {row["task_id"] for row in journal["admissions"]} | {row["task_id"] for row in journal["results"]}
            ordered = sorted(tasks, key=lambda item: (-item.expected_critical_path_benefit, item.task_id))
            fresh = [task for task in ordered if task.task_id not in known]
            terminal = {row["task_id"] for row in journal["results"] if row["verdict"] != "unsettled"}
            active_count = sum(row["task_id"] not in terminal for row in journal["admissions"])
            capacity = max(0, self.profile.maximum - active_count)
            quota_remaining = self.profile.shared_quota_turns - len(journal["quota_trace"])
            admitted = []
            for task in fresh:
                if len(admitted) >= capacity or task.turn_limit > quota_remaining:
                    self._terminal(journal, task.task_id, SpecialistVerdict.REJECTED, "pool-or-quota-bound")
                    continue
                candidate = self._admit(journal, task)
                if candidate is not None:
                    admitted.append(candidate)
                    quota_remaining -= task.turn_limit
                    with self._owners_lock:
                        self._owners.add(self._owner_key(candidate[1]["private_generation_id"]))
            self._write(journal)
        active = [item for item in admitted if item is not None]
        completed = {}
        workers = []
        for task, _admission, evidence in active:
            worker = threading.Thread(
                target=lambda one=task, facts=evidence: completed.setdefault(
                    one.task_id, self._invoke(one, facts, invoke, cancelled)
                ),
                daemon=True,
            )
            worker.start()
            workers.append(worker)
        for worker in workers:
            worker.join(self.profile.max_wall_seconds + 1)
        results = []
        try:
            for task, admission, _evidence in active:
                result = completed.get(
                    task.task_id,
                    SpecialistResult(task.task_id, SpecialistVerdict.CANCELLED, reason="join-bound"),
                )
                self._hook("after_invoke")
                if result.verdict is SpecialistVerdict.UNSETTLED:
                    disposition = None
                else:
                    disposition = (
                        GenerationDisposition.COMPLETE
                        if result.verdict is SpecialistVerdict.ACCEPTED
                        else GenerationDisposition.INTERRUPT
                    )
                    self.generations.close(admission["private_generation_id"], disposition)
                with self._file_lock.open("a+b") as lock:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                    journal = self._load()
                    self._result(journal, result, admission, "" if disposition is None else disposition.value)
                    self._write(journal)
                self._hook("after_close")
                results.append(result)
        finally:
            with self._owners_lock:
                for _task, admission, _evidence in active:
                    self._owners.discard(self._owner_key(admission["private_generation_id"]))
        for task in ordered:
            if task.task_id in known:
                row = next(row for row in journal["results"] if row["task_id"] == task.task_id)
                proposal = self._proposal(row)
                if row["verdict"] == SpecialistVerdict.UNSETTLED.value:
                    results.append(
                        SpecialistResult(task.task_id, SpecialistVerdict.UNSETTLED, proposal, "owner-recovery-required")
                    )
                    continue
                results.append(
                    SpecialistResult(
                        task.task_id,
                        SpecialistVerdict(row["verdict"]),
                        proposal,
                        row["reason"],
                        replayed=True,
                    )
                )
            elif task.task_id not in {item[0].task_id for item in admitted if item is not None}:
                row = next(row for row in journal["results"] if row["task_id"] == task.task_id)
                results.append(SpecialistResult(task.task_id, SpecialistVerdict(row["verdict"]), reason=row["reason"]))
        accepted_refs = {
            ref
            for result in results
            if result.verdict is SpecialistVerdict.ACCEPTED and result.proposal is not None
            for ref in result.proposal.evidence_refs
        }
        evidence = tuple(
            SpecialistEvidence(**item)
            for admission in journal["admissions"]
            for item in admission["evidence"]
            if item["ref"] in accepted_refs
        )
        return SpecialistBatch(tuple(sorted(results, key=lambda item: item.task_id)), str(self._receipt), evidence)

    def _admit(self, journal, task):
        reason, evidence = self._validate(task)
        if reason:
            self._terminal(journal, task.task_id, SpecialistVerdict.REJECTED, reason)
            return None
        segment = len(self.generations.projection().generations) + 1
        private = self.generations.acquire(
            f"specialist:{task.task_id}", f"{task.attempt_id}:{task.task_id}:segment-{segment}"
        )
        self._hook("after_private_generation")
        admission = {
            "task_id": task.task_id,
            "engagement_id": task.engagement_id,
            "parent_engagement_id": task.parent_engagement_id,
            "parent_generation_id": task.generation_id,
            "parent_attempt_id": task.attempt_id,
            "private_generation_id": private.generation_id,
            "private_attempt_id": private.attempt_id,
            "evidence": [item.__dict__ for item in evidence],
            "tool_profile": task.requested_tool_profile,
            "deadline": task.deadline,
            "benefit": task.expected_critical_path_benefit,
        }
        journal["admissions"].append(admission)
        for turn_index in range(1, task.turn_limit + 1):
            journal["quota_trace"].append(
                {"sequence": len(journal["quota_trace"]) + 1, "task_id": task.task_id, "turn_index": turn_index}
            )
        self._write(journal)
        self._hook("after_admission")
        return task, admission, evidence

    def _invoke(self, task, evidence, invoke, cancelled):
        if cancelled():
            return SpecialistResult(task.task_id, SpecialistVerdict.CANCELLED, reason="parent-cancelled")
        tools = self._tools(task.requested_tool_profile)
        view = SpecialistView(task.task_id, task.goal, task.question_or_hypothesis, evidence, tools, task.deadline)
        context_bytes = len(
            canonical_bytes(
                {
                    "task": task.task_id,
                    "goal": task.goal,
                    "question": task.question_or_hypothesis,
                    "evidence": [item.__dict__ for item in evidence],
                    "tool_profile": tools.profile,
                    "deadline": task.deadline,
                }
            )
        )
        if context_bytes > self.profile.max_context_bytes:
            return SpecialistResult(task.task_id, SpecialistVerdict.REJECTED, reason="context-bound")
        answer, failure = [], []

        def call():
            try:
                answer.append(invoke(view))
            except BaseException as error:
                failure.append(type(error).__name__)

        worker = threading.Thread(target=call, name=f"specialist-{task.task_id}", daemon=True)
        worker.start()
        remaining = min(
            (dt.datetime.fromisoformat(task.deadline) - self._now()).total_seconds(),
            self.profile.max_wall_seconds,
        )
        deadline = time.monotonic() + max(0.0, remaining)
        while worker.is_alive() and not cancelled() and time.monotonic() < deadline:
            worker.join(min(0.01, max(0.0, deadline - time.monotonic())))
        if worker.is_alive() or cancelled():
            tools.revoke()
            termination = self._terminate(task.engagement_id)
            worker.join(min(1.0, self.profile.max_wall_seconds))
            if not isinstance(termination, SpecialistTermination) or not termination.ended or termination.survivors:
                return SpecialistResult(
                    task.task_id,
                    SpecialistVerdict.UNSETTLED,
                    reason="termination-owner-survived",
                    termination=termination if isinstance(termination, SpecialistTermination) else None,
                )
            if len(termination.evidence_digest) != 64:
                return SpecialistResult(
                    task.task_id, SpecialistVerdict.UNSETTLED, reason="termination-evidence-invalid"
                )
            return SpecialistResult(
                task.task_id,
                SpecialistVerdict.CANCELLED,
                reason="bounded-termination",
                termination=termination,
            )
        if failure or not answer or not isinstance(answer[0], SpecialistInvocation):
            return SpecialistResult(
                task.task_id, SpecialistVerdict.REJECTED, reason=(failure or ["malformed-result"])[0]
            )
        invocation = answer[0]
        proposal = invocation.proposal
        if not 1 <= invocation.turns <= task.turn_limit:
            return SpecialistResult(task.task_id, SpecialistVerdict.REJECTED, reason="turn-bound")
        if any(ref not in {item.ref for item in evidence} for ref in proposal.evidence_refs):
            return SpecialistResult(task.task_id, SpecialistVerdict.REJECTED, reason="unauthorized-evidence")
        encoded = canonical_bytes(
            {"summary": proposal.summary, "evidence_refs": proposal.evidence_refs, "candidate": proposal.candidate}
        )
        if len(encoded) > self.profile.max_result_bytes:
            return SpecialistResult(task.task_id, SpecialistVerdict.REJECTED, reason="result-bound")
        return SpecialistResult(
            task.task_id,
            SpecialistVerdict.ACCEPTED,
            SpecialistProposal(
                proposal.summary, proposal.evidence_refs, proposal.candidate, invocation.turns, context_bytes
            ),
        )

    def _validate(self, task):
        if task.requested_tool_profile not in self.profile.tool_profiles or not self._current(
            task.generation_id, task.attempt_id
        ):
            return "authority-denied", ()
        deadline = dt.datetime.fromisoformat(task.deadline)
        if deadline <= self._now() or (deadline - self._now()).total_seconds() > self.profile.max_wall_seconds:
            return "deadline-bound", ()
        try:
            evidence = tuple(self._resolve(ref, task.generation_id) for ref in task.evidence_refs)
        except Exception:
            return "evidence-unsettled", ()
        if any(item.generation_id != task.generation_id for item in evidence):
            return "cross-generation-evidence", ()
        if digest_bytes(canonical_bytes([item.__dict__ for item in evidence])) != task.evidence_digest:
            return "evidence-digest-mismatch", ()
        return "", evidence

    def _load(self):
        journal = (
            json.loads(self._path.read_bytes())
            if self._path.exists()
            else {
                "schema_version": 1,
                "run_id": self.run_id,
                "profile": self.profile.document(),
                "admissions": [],
                "quota_trace": [],
                "results": [],
            }
        )
        terminal = {row["task_id"] for row in journal["results"]}
        admitted_generations = {row["private_generation_id"] for row in journal["admissions"]}
        for state in self.generations.projection().generations:
            if (
                state.active
                and state.work_id.startswith("specialist:")
                and state.generation_id not in admitted_generations
            ):
                self.generations.close(state.generation_id, GenerationDisposition.INTERRUPT)
        for admission in journal["admissions"]:
            if admission["task_id"] in terminal:
                continue
            state = next(
                (
                    item
                    for item in self.generations.projection().generations
                    if item.generation_id == admission["private_generation_id"]
                ),
                None,
            )
            with self._owners_lock:
                locally_owned = self._owner_key(admission["private_generation_id"]) in self._owners
            if locally_owned:
                continue
            if state and state.active:
                self.generations.close(state.generation_id, GenerationDisposition.INTERRUPT)
            self._result(
                journal,
                SpecialistResult(admission["task_id"], SpecialistVerdict.CANCELLED, reason="replayed-interruption"),
                admission,
                "interrupt",
            )
        if journal["admissions"]:
            self._write(journal)
        return journal

    def _current(self, generation, attempt):
        return any(
            item.generation_id == generation and item.attempt_id == attempt and item.active
            for item in self.generations.projection().generations
        )

    def _terminal(self, journal, task_id, verdict, reason):
        journal["results"].append(
            {
                "task_id": task_id,
                "verdict": verdict.value,
                "reason": reason,
                "private_generation_id": "",
                "disposition": "",
                "proposal_digest": "",
                "accepted_evidence_refs": [],
                "turns": 0,
                "context_bytes": 0,
                "proposal": None,
                "termination": None,
            }
        )

    def _result(self, journal, result, admission, disposition):
        proposal = result.proposal
        journal["results"].append(
            {
                "task_id": result.task_id,
                "verdict": result.verdict.value,
                "reason": result.reason,
                "private_generation_id": admission["private_generation_id"],
                "disposition": disposition,
                "proposal_digest": ""
                if proposal is None
                else digest_bytes(
                    canonical_bytes(
                        {
                            "summary": proposal.summary,
                            "evidence_refs": proposal.evidence_refs,
                            "candidate": proposal.candidate,
                        }
                    )
                ),
                "accepted_evidence_refs": [] if proposal is None else list(proposal.evidence_refs),
                "turns": 0 if proposal is None else proposal.turns,
                "context_bytes": 0 if proposal is None else proposal.context_bytes,
                "proposal": None
                if proposal is None
                else {
                    "summary": proposal.summary,
                    "evidence_refs": list(proposal.evidence_refs),
                    "candidate": proposal.candidate,
                    "turns": proposal.turns,
                    "context_bytes": proposal.context_bytes,
                },
                "termination": None if result.termination is None else result.termination.__dict__,
            }
        )

    @staticmethod
    def _proposal(row):
        proposal = row.get("proposal")
        if proposal is None:
            return None
        return SpecialistProposal(
            proposal["summary"],
            tuple(proposal["evidence_refs"]),
            proposal["candidate"],
            proposal["turns"],
            proposal["context_bytes"],
        )

    def _write(self, journal):
        journal["admissions"].sort(key=lambda row: row["task_id"])
        journal["results"].sort(key=lambda row: row["task_id"])
        atomic_write(self._path, canonical_bytes(journal) + b"\n")
        receipt = {
            **journal,
            "receipt_type": "specialist-pool",
            "controlled_proof_digest": PROOF_DIGEST,
            "generation_digest": self.generations.projection().digest,
            "control_digest": digest_bytes(canonical_bytes(journal)),
            "manifest_link": {"row_id": "core.lane-specialist-topology", "receipt_ref": "receipt:specialist-pool"},
        }
        atomic_write(self._receipt, canonical_bytes(receipt) + b"\n")


__all__ = ["SpecialistPool"]
