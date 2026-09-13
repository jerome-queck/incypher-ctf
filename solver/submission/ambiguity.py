"""Crash-replayable sixty-second fence for possibly-sent Candidate effects."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes
from solver.write_reservation import (
    Capacity,
    EffectIdentity,
    RetentionPolicy,
    WriteAuthority,
)
from solver.submission.ambiguity_types import (
    AmbiguityEvent,
    AuthenticatedSubmissionEvidence,
    CompleteSubmissionIdentity,
    Evidence,
    FenceClosed,
    LedgerRowType,
    PendingSubmission,
    SubmissionDisposition,
    SubmissionVerdict,
)

SCHEMA_VERSION = 1
RECEIPT_TYPE = "ambiguous-submission"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.submission-tail"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"
FENCE_SECONDS = 60.0
PROBE_OFFSETS = (0.0, 15.0, 30.0, 60.0)
AMBIGUITY_EVENT = "submission.ambiguity-event"
AMBIGUITY_PATH_NEED = Capacity(12_800, 1, 24)
AMBIGUITY_PATH_OPERATION = "submission.ambiguity-path"


class AmbiguousSubmissionFence:
    """Persist ambiguity before reconciliation; never grants the same Candidate another POST."""

    def __init__(
        self,
        state: Path,
        authority: WriteAuthority,
        *,
        run_id: str,
        boot_id: str,
        monotonic: Callable[[], float],
        wall_time: Callable[[], float] | None = None,
        probe: Callable[[PendingSubmission], Evidence | AuthenticatedSubmissionEvidence],
    ) -> None:
        self._root = Path(state)
        self._path = self._root / "runs" / run_id / "canonical" / RECEIPT_FILENAME
        self._authority = authority
        self._run_id = run_id
        self._boot_id = boot_id
        self._clock = monotonic
        self._wall = wall_time or monotonic
        self._anchors: dict[str, tuple[float, float]] = {}
        self._probe = probe
        self._lock = threading.Lock()

    def reserve_path(
        self, candidate_id: str, identity: CompleteSubmissionIdentity, *, record_wire: bool = True
    ) -> None:
        if not isinstance(identity, CompleteSubmissionIdentity):
            raise TypeError("ambiguity path requires CompleteSubmissionIdentity")
        if not candidate_id:
            raise ValueError("ambiguity needs Candidate identity")
        self._authority.reserve(
            f"ambiguity-path:{identity.effect_id}",
            EffectIdentity(AMBIGUITY_PATH_OPERATION, identity.effect_id, identity.payload_identity),
            AMBIGUITY_PATH_NEED,
            retention=RetentionPolicy.RECORD,
        )
        starts = [
            row
            for row in self._events()
            if row.get("event") == AmbiguityEvent.WIRE_STARTED and row.get("effect_id") == identity.effect_id
        ]
        if record_wire and not starts:
            self.mark_wire(candidate_id, identity)

    def mark_wire(self, candidate_id: str, identity: CompleteSubmissionIdentity) -> None:
        reservation = self._authority.current(f"ambiguity-path:{identity.effect_id}")
        if reservation is None:
            raise ValueError("ambiguity path must be reserved before wire send")
        if reservation.state.value == "reserved":
            reservation = self._authority.start(reservation)
        starts = [
            row
            for row in self._events()
            if row.get("event") == AmbiguityEvent.WIRE_STARTED and row.get("effect_id") == identity.effect_id
        ]
        if not starts:
            wire_started_at = self._wall()
            self._append(
                {
                    "event": AmbiguityEvent.WIRE_STARTED,
                    "boot_id": self._boot_id,
                    "candidate_id": candidate_id,
                    "effect_id": identity.effect_id,
                    "wire_started_at": wire_started_at,
                    "deadline": wire_started_at + FENCE_SECONDS,
                    "reservation_id": reservation.key,
                    "complete_identity": identity.document(),
                }
            )

    def begin(self, candidate_id: str, identity: CompleteSubmissionIdentity) -> PendingSubmission:
        if not isinstance(identity, CompleteSubmissionIdentity):
            raise TypeError("ambiguity begin requires CompleteSubmissionIdentity")
        effect_id = identity.effect_id
        challenge_id = identity.challenge_id
        budget = self._authority.current(f"ambiguity-path:{effect_id}")
        if (
            budget is None
            or budget.state.value not in {"started", "possibly-sent"}
            or budget.need != AMBIGUITY_PATH_NEED
        ):
            raise ValueError("complete ambiguity path was not reserved before the Board effect")
        with self._lock:
            if candidate_id in self._states():
                raise FenceClosed(f"Candidate {candidate_id!r} is already spent")
            starts = [
                row
                for row in self._events()
                if row.get("event") == AmbiguityEvent.WIRE_STARTED and row.get("effect_id") == effect_id
            ]
            if len(starts) != 1:
                raise ValueError("submission has no unique durable wire start")
            wire_started_at = float(starts[0]["wire_started_at"])
            pending = PendingSubmission(
                candidate_id,
                effect_id,
                challenge_id,
                wire_started_at,
                wire_started_at + FENCE_SECONDS,
                complete_identity=identity.document(),
            )
            self._anchors[candidate_id] = (self._clock(), 0.0)
            self._append(
                {
                    "event": AmbiguityEvent.POSSIBLY_SENT,
                    "boot_id": self._boot_id,
                    "candidate_id": candidate_id,
                    "effect_id": effect_id,
                    "challenge_id": challenge_id,
                    "wire_started_at": pending.wire_started_at,
                    "deadline": pending.deadline,
                    "posts": 1,
                    "complete_identity": identity.document(),
                }
            )
            return pending

    def reconcile(self, pending: PendingSubmission) -> PendingSubmission:
        with self._lock:
            state = self._states().get(pending.candidate_id)
            if state is None or state.effect_id != pending.effect_id:
                raise ValueError("Candidate ambiguity does not match canonical replay")
            if state.disposition is not SubmissionDisposition.PENDING:
                return state
            events = self._events()
            boots = {row["boot_id"] for row in events if row["candidate_id"] == state.candidate_id}
            if self._boot_id not in boots:
                self._append_identity(state, "boot-replayed")
            anchor = self._anchors.get(state.candidate_id)
            if anchor is None:
                wall_at_boot = self._wall()
                prior = max(
                    (float(row.get("at", row.get("elapsed_carried", 0.0))) for row in events),
                    default=0.0,
                )
                wall_elapsed = wall_at_boot - state.wire_started_at
                carried = FENCE_SECONDS if wall_elapsed < 0 else max(prior, wall_elapsed)
                self._anchors[state.candidate_id] = (self._clock(), carried)
                self._append(
                    {
                        "event": AmbiguityEvent.BOOT_REPLAYED,
                        "boot_id": self._boot_id,
                        "candidate_id": state.candidate_id,
                        "effect_id": state.effect_id,
                        "wall_at_boot": wall_at_boot,
                        "elapsed_carried": carried,
                    }
                )
                anchor = self._anchors[state.candidate_id]
            now = max(anchor[1], anchor[1] + self._clock() - anchor[0])
            attempted = sum(
                row["event"] == AmbiguityEvent.EVIDENCE_PROBE
                for row in events
                if row["candidate_id"] == state.candidate_id
            )
            due = sum(now >= offset for offset in PROBE_OFFSETS)
            while attempted < due:
                try:
                    evidence = self._probe(state)
                except Exception:
                    evidence = Evidence.unsettled("submission-probe:failed")
                attempted += 1
                self._append(
                    {
                        "event": AmbiguityEvent.EVIDENCE_PROBE,
                        "boot_id": self._boot_id,
                        "candidate_id": state.candidate_id,
                        "effect_id": state.effect_id,
                        "at": now,
                        "scheduled_offset": PROBE_OFFSETS[attempted - 1],
                        "kind": evidence.kind,
                        "source": evidence.source,
                        "authenticated": isinstance(evidence, AuthenticatedSubmissionEvidence),
                        "candidate_match": getattr(evidence, "candidate_id", "") == state.candidate_id,
                        "verdict": getattr(evidence, "verdict", ""),
                        "request_id": getattr(evidence, "request_id", ""),
                        "classified_event_id": getattr(evidence, "classified_event_id", ""),
                        "binding_digest": getattr(evidence, "binding_digest", ""),
                        "peer_identity_digest": getattr(evidence, "peer_identity_digest", ""),
                        "submission_epoch": getattr(evidence, "submission_epoch", 0),
                        "evidence_effect_id": getattr(evidence, "effect_id", ""),
                        "supplied_value_digest": getattr(evidence, "supplied_value_digest", ""),
                        "board_row_id": getattr(evidence, "board_row_id", ""),
                        "row_type": getattr(evidence, "row_type", ""),
                        "submitted_at": getattr(evidence, "submitted_at", ""),
                        "evidence_complete_identity": getattr(evidence, "complete_identity", {}),
                    }
                )
                disposition = self._definitive(state, evidence)
                if disposition:
                    self._close(state, disposition, evidence.source, now)
                    return self._states()[state.candidate_id]
            if now >= FENCE_SECONDS and attempted == len(PROBE_OFFSETS):
                self._close(state, SubmissionDisposition.UNKNOWN_AND_SPENT, "fence-expired", state.deadline)
                return self._states()[state.candidate_id]
            return state

    def closed_effect_at_epoch(self, epoch: int) -> str | None:
        """Return the closed ambiguity which authorizes exactly one successor epoch."""
        matches = [
            state
            for state in self._states().values()
            if state.disposition is not SubmissionDisposition.PENDING
            and (state.complete_identity or {}).get("submission_epoch") == epoch
        ]
        return matches[-1].effect_id if matches else None

    def record_reconciliation_failure(self, pending: PendingSubmission, source: str) -> None:
        """Retain a sanitized unexpected-cycle fault without terminating reconciliation."""
        with self._lock:
            state = self._states().get(pending.candidate_id)
            if state is None or state.disposition is not SubmissionDisposition.PENDING:
                return
            self._append(
                {
                    "event": AmbiguityEvent.RECONCILIATION_FAILED,
                    "boot_id": self._boot_id,
                    "candidate_id": state.candidate_id,
                    "effect_id": state.effect_id,
                    "source": source,
                }
            )

    @staticmethod
    def _definitive(state: PendingSubmission, evidence: Evidence) -> SubmissionDisposition | None:
        if not isinstance(evidence, AuthenticatedSubmissionEvidence):
            return None
        expected = state.complete_identity or {}
        if (
            evidence.candidate_id != state.candidate_id
            or evidence.effect_id != state.effect_id
            or evidence.submission_epoch != expected.get("submission_epoch")
            or evidence.supplied_value_digest != expected.get("candidate_digest")
            or evidence.complete_identity != expected
            or evidence.row_type is not LedgerRowType.SUBMISSION
            or not evidence.board_row_id
            or not evidence.submitted_at
        ):
            return None
        return {
            SubmissionVerdict.CORRECT: SubmissionDisposition.ACCEPTED,
            SubmissionVerdict.INCORRECT: SubmissionDisposition.REJECTED,
            SubmissionVerdict.REFUSED: SubmissionDisposition.REFUSED_AND_SPENT,
            SubmissionVerdict.PAUSED: SubmissionDisposition.REFUSED_AND_SPENT,
            SubmissionVerdict.RATE_LIMITED: SubmissionDisposition.REFUSED_AND_SPENT,
        }[evidence.verdict]

    def _close(self, state: PendingSubmission, disposition: SubmissionDisposition, source: str, at: float) -> None:
        self._append(
            {
                "event": AmbiguityEvent.FENCE_CLOSED,
                "boot_id": self._boot_id,
                "candidate_id": state.candidate_id,
                "effect_id": state.effect_id,
                "at": at,
                "disposition": disposition.value,
                "provenance": source,
            }
        )
        budget = self._authority.current(f"ambiguity-path:{state.effect_id}")
        if budget is not None and budget.state.value in {"started", "possibly-sent"}:
            if budget.state.value == "started":
                budget = self._authority.possibly_sent(budget, "ambiguity-lifecycle-active")
            self._authority.refuse_indeterminate(budget, disposition.value)

    @property
    def barrier_open(self) -> bool:
        return any(state.disposition is SubmissionDisposition.PENDING for state in self._states().values())

    def close_boot(self) -> None:
        self._authority.close()

    def pending(self) -> tuple[PendingSubmission, ...]:
        return tuple(state for state in self._states().values() if state.disposition is SubmissionDisposition.PENDING)

    def effect_state(self, reservation_id: str):
        return self._authority.current(reservation_id)

    def release_unused_path(self, effect_id: str) -> None:
        budget = self._authority.current(f"ambiguity-path:{effect_id}")
        if budget is not None and budget.state.value == "reserved":
            self._authority.abort(budget, "definitive-submit-result")

    def can_submit(self, candidate_id: str, effect_id: str = "") -> bool:
        states = self._states()
        return (
            candidate_id not in states
            and effect_id not in {state.effect_id for state in states.values()}
            and not any(state.disposition is SubmissionDisposition.PENDING for state in states.values())
        )

    def post_trace(self, candidate_id: str) -> tuple[str, ...]:
        return tuple(
            AmbiguityEvent.POSSIBLY_SENT.value
            for row in self._events()
            if row["candidate_id"] == candidate_id and row["event"] == AmbiguityEvent.POSSIBLY_SENT
        )

    def write_receipt(self) -> Path:
        events = self._events()
        states = self._states(events)
        document = {
            "schema_version": SCHEMA_VERSION,
            "receipt_type": RECEIPT_TYPE,
            "run_id": self._run_id,
            "evidence_class": "solver-observation",
            "manifest_link": {"row_id": MANIFEST_ROW_ID, "receipt_ref": MANIFEST_RECEIPT_REF},
            "fence_seconds": FENCE_SECONDS,
            "events": events,
            "no_resend_trace": [{"candidate_id": candidate_id, "posts": 1} for candidate_id in sorted(states)],
            "final_dispositions": [
                {"candidate_id": item.candidate_id, "disposition": item.disposition, "provenance": item.provenance}
                for item in sorted(states.values(), key=lambda value: value.candidate_id)
            ],
        }
        destination = self._path.parent / RECEIPT_FILENAME
        atomic_write(destination, canonical_bytes(document) + b"\n")
        return destination

    def _append_identity(self, state: PendingSubmission, event: str) -> None:
        self._append(
            {
                "event": event,
                "boot_id": self._boot_id,
                "candidate_id": state.candidate_id,
                "effect_id": state.effect_id,
            }
        )

    def _append(self, event: Mapping[str, object]) -> None:
        reservation = self._authority.current(f"ambiguity-path:{event['effect_id']}")
        if reservation is None:
            raise ValueError("ambiguity lifecycle lacks its pre-wire authority")
        path = self._authority.object_path(reservation)
        events = json.loads(path.read_text()) if path.exists() and path.stat().st_size else []
        events.append(dict(event))
        body = canonical_bytes(events) + b"\n"
        if len(body) > AMBIGUITY_PATH_NEED.bytes:
            raise ValueError("bounded ambiguity lifecycle exceeded its reserved authority")
        self._authority.persist_reserved_record(reservation, body)

    def _events(self) -> list[dict[str, object]]:
        events = []
        for item in self._authority.reservations():
            if item.identity.operation != AMBIGUITY_PATH_OPERATION:
                continue
            path = self._authority.object_path(item)
            if path.exists() and path.stat().st_size:
                events.extend(json.loads(path.read_text()))
        return events

    def _states(self, events=None) -> dict[str, PendingSubmission]:
        states: dict[str, PendingSubmission] = {}
        for row in self._events() if events is None else events:
            candidate_id = str(row["candidate_id"])
            if row["event"] == AmbiguityEvent.POSSIBLY_SENT:
                states[candidate_id] = PendingSubmission(
                    candidate_id,
                    str(row["effect_id"]),
                    int(row["challenge_id"]),
                    float(row["wire_started_at"]),
                    float(row["deadline"]),
                    complete_identity=dict(row["complete_identity"]),
                )
            elif row["event"] == AmbiguityEvent.FENCE_CLOSED:
                current = states[candidate_id]
                states[candidate_id] = PendingSubmission(
                    current.candidate_id,
                    current.effect_id,
                    current.challenge_id,
                    current.wire_started_at,
                    current.deadline,
                    SubmissionDisposition(str(row["disposition"])),
                    str(row["provenance"]),
                    current.complete_identity,
                )
        return states


from solver.submission.ambiguity_adapter import AmbiguityAwareSerialSubmission  # noqa: E402
from solver.submission.ambiguity_receipt import (  # noqa: E402
    link_manifest,
    manifest_receipt,
    verify_receipt,
)

__all__ = [
    "AmbiguousSubmissionFence",
    "AmbiguityAwareSerialSubmission",
    "CompleteSubmissionIdentity",
    "Evidence",
    "FenceClosed",
    "PendingSubmission",
    "link_manifest",
    "manifest_receipt",
    "verify_receipt",
]
