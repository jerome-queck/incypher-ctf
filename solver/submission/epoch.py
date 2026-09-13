"""Single-writer durable Submission epoch allocation."""

from __future__ import annotations

from dataclasses import dataclass
import threading

from solver.event_store_contracts import InvalidEventError

SUBMISSION_EPOCH_ADVANCED = "submission-epoch.advanced"


@dataclass(frozen=True)
class SubmissionEpochAdvanced:
    event_id: str
    board_identity: str
    epoch: int
    ts: str

    @property
    def event_type(self):
        return SUBMISSION_EPOCH_ADVANCED

    @property
    def identity(self):
        return self.event_id

    def payload(self, *, blob_digest: str, blob_bytes: int):
        return {**vars(self), "blob_digest": blob_digest, "blob_bytes": blob_bytes}

    @classmethod
    def identity_from_payload(cls, payload):
        return str(payload.get("event_id", ""))

    @classmethod
    def validate_payload(cls, payload, *, sequence: int):
        if not payload.get("board_identity") or type(payload.get("epoch")) is not int or payload["epoch"] < 1:
            raise InvalidEventError("Submission epoch is invalid")


class SubmissionEpochAuthority:
    def __init__(self, store, timestamp):
        self._store, self._timestamp = store, timestamp
        self._lock = threading.Lock()

    def current(self, board_identity: str) -> int:
        rows = [
            e
            for e in self._store.events()
            if e.event_type == SUBMISSION_EPOCH_ADVANCED and e.payload["board_identity"] == board_identity
        ]
        epochs = [int(e.payload["epoch"]) for e in rows]
        if epochs and epochs != list(range(1, len(epochs) + 1)):
            raise InvalidEventError("Submission epoch replay is not contiguous")
        return epochs[-1] if epochs else 0

    def advance(self, board_identity: str) -> int:
        with self._lock:
            return self._append_next(board_identity)

    def ensure(self, board_identity: str) -> int:
        """Create the first epoch once; Boots only replay it."""
        with self._lock:
            return self.current(board_identity) or self._append_next(board_identity)

    def advance_after(self, board_identity: str, closed_effect_id: str, closed_epoch: int) -> int:
        """Durably and idempotently open the successor epoch after one closed ambiguity."""
        with self._lock:
            current = self.current(board_identity)
            if current != closed_epoch:
                return current
            return self._append_next(board_identity, cause=closed_effect_id)

    def _append_next(self, board_identity: str, *, cause: str = "initial") -> int:
        epoch = self.current(board_identity) + 1
        self._store.append(
            SubmissionEpochAdvanced(
                f"submission-epoch:{board_identity}:{epoch}:{cause}", board_identity, epoch, self._timestamp()
            ),
            body=b"",
        )
        return epoch
