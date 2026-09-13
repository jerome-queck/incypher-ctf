"""One crash-durable authority for admitted Candidate Board effects."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from solver.board import Verdict
from solver.board_broker import BoardBrokerClient
from solver.board_broker_contracts import BoardOutcome
from solver.capability import CapabilityBinding
from solver.candidate_admission_contracts import ReadyAdmission, ReadyCandidate
from solver.write_reservation import Capacity, EffectIdentity, ReservedEffect, RetentionPolicy, WriteAuthority
from solver.submission import SUBMIT_CANDIDATE_OPERATION


SUBMISSION_NEED = Capacity(bytes=32 * 1024, objects=1, operations=3)


@dataclass(frozen=True)
class SubmissionResult:
    candidate_id: str
    effect_id: str
    reservation_id: str
    verdict: Verdict


class SerialSubmission:
    """Dispatch the first ready Candidate immediately, with one account-wide POST in flight."""

    def __init__(
        self,
        state: Path,
        authority: WriteAuthority,
        timestamp: Callable[[], str],
        board_broker_path: Path,
        open_client: Callable[[Path, CapabilityBinding], BoardBrokerClient] | None = None,
    ) -> None:
        self._authority = authority
        self._timestamp = timestamp
        self._board_broker_path = Path(board_broker_path)
        self._open_client = open_client or (
            lambda path, binding: BoardBrokerClient.open(path, binding, scope="board.submit")
        )
        self._queue = threading.Condition()
        self._pending: dict[str, ReadyAdmission] = {}
        self._busy = False

    def pending_count(self) -> int:
        """Return the number of ready Candidates waiting behind the active POST."""

        with self._queue:
            return len(self._pending)

    def dispatch(
        self, admission: ReadyAdmission, *, binding: CapabilityBinding, pre_wire: Callable[[], None] | None = None
    ) -> SubmissionResult | None:
        candidate = admission.candidate
        if candidate.generation_id != binding.generation_id:
            raise ValueError("Candidate generation does not match submission capability")
        with self._queue:
            self._pending.setdefault(candidate.identity, admission)
            while (
                self._busy
                or admission.ready_order
                != min((item.ready_order for item in self._pending.values()), default=admission.ready_order)
                or not all(
                    self._authority.current(f"serial-submit:{identity}") is not None
                    for identity in admission.predecessor_ids
                )
            ):
                self._queue.wait()
            self._busy = True
            self._pending.pop(candidate.identity, None)
        try:
            return self._dispatch(candidate, binding, admission.ready_at, pre_wire)
        finally:
            with self._queue:
                self._busy = False
                self._queue.notify_all()

    def _dispatch(
        self, candidate: ReadyCandidate, binding: CapabilityBinding, ready_at: str, pre_wire=None
    ) -> SubmissionResult:
        if not ready_at:
            raise ValueError("Candidate readiness needs its canonical timestamp")
        try:
            text = candidate.candidate.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("ready Candidate is not UTF-8") from error
        effect = EffectIdentity(SUBMIT_CANDIDATE_OPERATION, str(candidate.challenge_id), candidate.identity)
        effect_id = effect.fingerprint
        key = f"serial-submit:{candidate.identity}"
        self._authority.reserve(key, effect, SUBMISSION_NEED, retention=RetentionPolicy.RECEIPT)
        reserved_at = self._timestamp()
        requested_at = self._timestamp()
        client = self._open_client(self._board_broker_path, binding)

        def submit() -> Verdict:
            if pre_wire is not None:
                pre_wire()
            result = client.submit(candidate.challenge_id, text)
            if result.outcome is not BoardOutcome.ANSWERED or not isinstance(result.value, Verdict):
                raise RuntimeError(f"Board broker classified submit as {result.outcome.value}")
            return result.value

        try:
            verdict = ReservedEffect(self._authority).execute(
                key,
                effect,
                SUBMISSION_NEED,
                submit,
                encode=lambda answer: {
                    "candidate_id": candidate.identity,
                    "effect_id": effect_id,
                    "ready_at": ready_at,
                    "reserved_at": reserved_at,
                    "requested_at": requested_at,
                    "result_at": self._timestamp(),
                    "outcome": answer.outcome,
                    "http_status": answer.http_status,
                    "message_digest": hashlib.sha256(answer.message.encode()).hexdigest(),
                },
                decode=lambda row: Verdict(str(row["outcome"]), "", int(row["http_status"])),
                retention=RetentionPolicy.RECEIPT,
            )
        finally:
            client.close()
        return SubmissionResult(candidate.identity, effect_id, key, verdict)
