"""Crash-replayable sixty-second fence for possibly-sent Candidate effects."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path

from solver.event_store_storage import atomic_write, canonical_bytes, digest_bytes
from solver.write_reservation import (
    Capacity,
    EffectIdentity,
    EffectIndeterminate,
    ReservedEffect,
    RetentionPolicy,
    WriteAuthority,
)
from solver.board_broker_contracts import BoardBrokerResult, BoardOutcome

SCHEMA_VERSION = 1
RECEIPT_TYPE = "ambiguous-submission"
RECEIPT_FILENAME = f"{RECEIPT_TYPE}.receipt.json"
MANIFEST_ROW_ID = "core.submission-tail"
MANIFEST_RECEIPT_REF = f"receipt:{RECEIPT_TYPE}"
FENCE_SECONDS = 60.0
PROBE_OFFSETS = (0.0, 15.0, 30.0, 60.0)
AMBIGUITY_EVENT = "submission.ambiguity-event"
EVENT_NEED = Capacity(4096, 1, 3)
AMBIGUITY_PATH_NEED = Capacity(64 * 1024, 8, 24)
AMBIGUITY_PATH_OPERATION = "submission.ambiguity-path"


class FenceClosed(RuntimeError):
    """The Candidate's at-most-once authority has already been consumed."""


@dataclass(frozen=True)
class CompleteSubmissionIdentity:
    board_identity: str
    challenge_id: int
    challenge_revision: str
    instance_provenance: str
    candidate_digest: str
    submission_epoch: int

    def __post_init__(self):
        if (
            any(
                not item
                for item in (
                    self.board_identity,
                    self.challenge_revision,
                    self.instance_provenance,
                    self.candidate_digest,
                )
            )
            or self.challenge_id < 1
            or self.submission_epoch < 1
        ):
            raise ValueError("complete submission identity is incomplete")

    @property
    def payload_identity(self):
        return digest_bytes(canonical_bytes(self.__dict__))

    @property
    def reservation_id(self):
        return f"serial-submit:{self.payload_identity}"

    @property
    def effect_id(self):
        return EffectIdentity("board.submit-candidate", str(self.challenge_id), self.payload_identity).fingerprint

    def document(self):
        return {**self.__dict__, "reservation_id": self.reservation_id, "effect_id": self.effect_id}


@dataclass(frozen=True)
class Evidence:
    kind: str
    source: str

    @classmethod
    def score_change(cls, source: str) -> Evidence:
        return cls("score-change", source)

    @classmethod
    def unsettled(cls, source: str) -> Evidence:
        return cls("unsettled", source)


@dataclass(frozen=True)
class AuthenticatedSubmissionEvidence:
    kind: str
    source: str
    verdict: str
    candidate_id: str
    request_id: str
    classified_event_id: str
    binding_digest: str
    peer_identity_digest: str
    effect_id: str
    submission_epoch: int
    supplied_value_digest: str
    board_row_id: str
    row_type: str
    submitted_at: str
    complete_identity: Mapping[str, object]

    @classmethod
    def from_broker(
        cls,
        result: BoardBrokerResult,
    ):
        provenance = result.provenance
        row = result.value
        required = {
            "board_row_id",
            "row_type",
            "submitted_at",
            "request_id",
            "verdict",
            "candidate_id",
            "supplied_value_digest",
            "effect_id",
            "submission_epoch",
            "complete_identity",
        }
        if not isinstance(row, Mapping) or set(row) != required or not isinstance(row["complete_identity"], Mapping):
            raise ValueError("submission ledger row shape is invalid")
        verdict = str(row["verdict"])
        if verdict not in {"correct", "incorrect", "refused", "paused", "rate-limited"}:
            raise ValueError("unsupported submission-ledger verdict")
        if (
            result.outcome is not BoardOutcome.ANSWERED
            or not result.request_id
            or row["request_id"] != result.request_id
            or not provenance.classified_event_id
            or not provenance.binding_digest
            or not provenance.peer_identity_digest
        ):
            raise ValueError("submission ledger evidence is not broker-authenticated")
        return cls(
            "exact-candidate-verdict",
            provenance.endpoint,
            verdict,
            str(row["candidate_id"]),
            result.request_id,
            provenance.classified_event_id,
            provenance.binding_digest,
            provenance.peer_identity_digest,
            str(row["effect_id"]),
            int(row["submission_epoch"]),
            str(row["supplied_value_digest"]),
            str(row["board_row_id"]),
            str(row["row_type"]),
            str(row["submitted_at"]),
            dict(row["complete_identity"]),
        )


@dataclass(frozen=True)
class PendingSubmission:
    candidate_id: str
    effect_id: str
    challenge_id: int
    wire_started_at: float
    deadline: float
    disposition: str = "pending"
    provenance: str = ""
    complete_identity: Mapping[str, object] | None = None


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

    def reserve_path(self, candidate_id: str, identity: CompleteSubmissionIdentity) -> None:
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

    def begin(self, candidate_id: str, identity: CompleteSubmissionIdentity) -> PendingSubmission:
        if not isinstance(identity, CompleteSubmissionIdentity):
            raise TypeError("ambiguity begin requires CompleteSubmissionIdentity")
        effect_id = identity.effect_id
        challenge_id = identity.challenge_id
        budget = self._authority.current(f"ambiguity-path:{effect_id}")
        if budget is None or budget.state.value != "reserved" or budget.need != AMBIGUITY_PATH_NEED:
            raise ValueError("complete ambiguity path was not reserved before the Board effect")
        with self._lock:
            if candidate_id in self._states():
                raise FenceClosed(f"Candidate {candidate_id!r} is already spent")
            wire_started_at = self._wall()
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
                    "event": "possibly-sent",
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
            if state.disposition != "pending":
                return state
            events = self._events()
            boots = {row["boot_id"] for row in events if row["candidate_id"] == state.candidate_id}
            if self._boot_id not in boots:
                self._append_identity(state, "boot-replayed")
            anchor = self._anchors.get(state.candidate_id)
            if anchor is None:
                carried = max(0.0, self._wall() - state.wire_started_at)
                self._anchors[state.candidate_id] = (self._clock(), carried)
                self._append(
                    {
                        "event": "boot-replayed",
                        "boot_id": self._boot_id,
                        "candidate_id": state.candidate_id,
                        "effect_id": state.effect_id,
                        "wall_at_boot": self._wall(),
                        "elapsed_carried": carried,
                    }
                )
                anchor = self._anchors[state.candidate_id]
            now = max(anchor[1], anchor[1] + self._clock() - anchor[0])
            attempted = sum(
                row["event"] == "evidence-probe" for row in events if row["candidate_id"] == state.candidate_id
            )
            due = sum(now >= offset for offset in PROBE_OFFSETS)
            while attempted < due:
                evidence = self._probe(state)
                attempted += 1
                self._append(
                    {
                        "event": "evidence-probe",
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
                self._close(state, "unknown-and-spent", "fence-expired", state.deadline)
                return self._states()[state.candidate_id]
            return state

    @staticmethod
    def _definitive(state: PendingSubmission, evidence: Evidence) -> str:
        if not isinstance(evidence, AuthenticatedSubmissionEvidence):
            return ""
        expected = state.complete_identity or {}
        if (
            evidence.candidate_id != state.candidate_id
            or evidence.effect_id != state.effect_id
            or evidence.submission_epoch != expected.get("submission_epoch")
            or evidence.supplied_value_digest != expected.get("candidate_digest")
            or evidence.complete_identity != expected
            or evidence.row_type != "submission"
            or not evidence.board_row_id
            or not evidence.submitted_at
        ):
            return ""
        return {
            "correct": "accepted",
            "incorrect": "rejected",
            "refused": "refused-and-spent",
            "paused": "refused-and-spent",
            "rate-limited": "refused-and-spent",
        }[evidence.verdict]

    def _close(self, state: PendingSubmission, disposition: str, source: str, at: float) -> None:
        self._append(
            {
                "event": "fence-closed",
                "boot_id": self._boot_id,
                "candidate_id": state.candidate_id,
                "effect_id": state.effect_id,
                "at": at,
                "disposition": disposition,
                "provenance": source,
            }
        )
        budget = self._authority.current(f"ambiguity-path:{state.effect_id}")
        if budget is not None and budget.state.value == "reserved":
            self._authority.abort(budget, "ambiguity-path-closed")

    @property
    def barrier_open(self) -> bool:
        return any(state.disposition == "pending" for state in self._states().values())

    def close_boot(self) -> None:
        self._authority.close()

    def pending(self) -> tuple[PendingSubmission, ...]:
        return tuple(state for state in self._states().values() if state.disposition == "pending")

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
            and not any(state.disposition == "pending" for state in states.values())
        )

    def post_trace(self, candidate_id: str) -> tuple[str, ...]:
        return tuple(
            "possibly-sent"
            for row in self._events()
            if row["candidate_id"] == candidate_id and row["event"] == "possibly-sent"
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
        ordinal = len(self._events()) + 1
        key = f"ambiguity:{event['effect_id']}:{ordinal}"
        identity = EffectIdentity(AMBIGUITY_EVENT, str(event["effect_id"]), digest_bytes(canonical_bytes(event)))
        ReservedEffect(self._authority).execute(
            key,
            identity,
            EVENT_NEED,
            lambda: dict(event),
            encode=lambda value: value,
            decode=lambda value: dict(value),
            retention=RetentionPolicy.RECORD,
        )

    def _events(self) -> list[dict[str, object]]:
        return [
            dict(item.observation or {})
            for item in self._authority.reservations()
            if item.identity.operation == AMBIGUITY_EVENT
        ]

    def _states(self, events=None) -> dict[str, PendingSubmission]:
        states: dict[str, PendingSubmission] = {}
        for row in self._events() if events is None else events:
            candidate_id = str(row["candidate_id"])
            if row["event"] == "possibly-sent":
                states[candidate_id] = PendingSubmission(
                    candidate_id,
                    str(row["effect_id"]),
                    int(row["challenge_id"]),
                    float(row["wire_started_at"]),
                    float(row["deadline"]),
                    complete_identity=dict(row["complete_identity"]),
                )
            elif row["event"] == "fence-closed":
                current = states[candidate_id]
                states[candidate_id] = PendingSubmission(
                    current.candidate_id,
                    current.effect_id,
                    current.challenge_id,
                    current.wire_started_at,
                    current.deadline,
                    str(row["disposition"]),
                    str(row["provenance"]),
                    current.complete_identity,
                )
        return states


class AmbiguityAwareSerialSubmission:
    """Adapter whose SerialSubmission reservation uses the complete ADR-0052 identity."""

    def __init__(self, serial, fence: AmbiguousSubmissionFence, identity_for):
        self._serial, self._fence, self._identity_for = serial, fence, identity_for

    def pending_count(self):
        return self._serial.pending_count()

    def dispatch(self, admission, *, binding):
        complete = self._identity_for(admission.candidate)
        if not isinstance(complete, CompleteSubmissionIdentity):
            raise TypeError("production submission requires CompleteSubmissionIdentity")
        original_id = admission.candidate.identity
        if not self._fence.can_submit(original_id, complete.effect_id):
            raise EffectIndeterminate("account submission barrier is active or Candidate is spent")
        if complete.challenge_id != admission.candidate.challenge_id:
            raise ValueError("complete submission identity names another Challenge")
        if complete.candidate_digest != admission.candidate.candidate_digest:
            raise ValueError("complete submission identity names another Candidate value")
        self._fence.reserve_path(original_id, complete)
        canonical = replace(admission, candidate=replace(admission.candidate, identity=complete.payload_identity))
        try:
            result = self._serial.dispatch(canonical, binding=binding)
        except BaseException:
            current = self._fence.effect_state(complete.reservation_id)
            if current is not None and current.state.value == "possibly-sent":
                if self._fence.can_submit(original_id, complete.effect_id):
                    self._fence.begin(original_id, complete)
            raise
        self._fence.release_unused_path(complete.effect_id)
        return replace(result, candidate_id=original_id)


def verify_receipt(path: Path) -> Path:
    receipt = Path(path)
    try:
        raw = receipt.read_bytes()
        document = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("ambiguous-submission receipt cannot be read") from error
    if raw != canonical_bytes(document) + b"\n":
        raise ValueError("ambiguous-submission receipt is not canonical")
    if document.get("schema_version") != SCHEMA_VERSION or document.get("receipt_type") != RECEIPT_TYPE:
        raise ValueError("ambiguous-submission receipt contract is unsupported")
    if document.get("fence_seconds") != FENCE_SECONDS:
        raise ValueError("ambiguous-submission fence dial is invalid")
    if document.get("producer") == "external-evaluator":
        supplied = document.get("evaluator_seal")
        unsigned = dict(document)
        unsigned.pop("evaluator_seal", None)
        if supplied != digest_bytes(canonical_bytes(unsigned)):
            raise ValueError("ambiguous-submission Evaluator seal is invalid")
        observed = document.get("observed_effect_trace")
        if not isinstance(observed, list) or any(item.get("posts") != 1 for item in observed):
            raise ValueError("ambiguous-submission observed POST trace is invalid")
    starts: dict[str, Mapping[str, object]] = {}
    closed: dict[str, Mapping[str, object]] = {}
    probes: dict[str, list[Mapping[str, object]]] = {}
    for event in document.get("events", []):
        candidate = str(event.get("candidate_id", ""))
        if event.get("event") == "possibly-sent":
            if candidate in starts or event.get("posts") != 1:
                raise ValueError("ambiguous-submission no-resend trace is invalid")
            if float(event["deadline"]) != float(event["wire_started_at"]) + FENCE_SECONDS:
                raise ValueError("ambiguous-submission deadline is invalid")
            complete = event.get("complete_identity")
            if complete:
                basis = {
                    key: complete[key]
                    for key in (
                        "board_identity",
                        "challenge_id",
                        "challenge_revision",
                        "instance_provenance",
                        "candidate_digest",
                        "submission_epoch",
                    )
                }
                payload = digest_bytes(canonical_bytes(basis))
                expected_effect = EffectIdentity(
                    "board.submit-candidate", str(basis["challenge_id"]), payload
                ).fingerprint
                if (
                    complete.get("reservation_id") != f"serial-submit:{payload}"
                    or complete.get("effect_id") != expected_effect
                ):
                    raise ValueError("ambiguous-submission complete effect identity is inconsistent")
            starts[candidate] = event
        elif event.get("event") == "fence-closed":
            closed[candidate] = event
        elif event.get("event") == "evidence-probe":
            probes.setdefault(candidate, []).append(event)
    expected = [{"candidate_id": item, "posts": 1} for item in sorted(starts)]
    if document.get("no_resend_trace") != expected:
        raise ValueError("ambiguous-submission no-resend trace is invalid")
    if document.get("producer") == "external-evaluator":
        observed_ids = sorted(item["effect_id"] for item in document["observed_effect_trace"])
        started_ids = sorted(str(item["effect_id"]) for item in starts.values())
        if observed_ids != started_ids:
            raise ValueError("Evaluator POST trace does not match ambiguous effects")
    if any(item not in starts for item in closed):
        raise ValueError("ambiguous-submission close lacks a possibly-sent effect")
    exact_dispositions = {"accepted": "correct", "rejected": "incorrect"}
    for candidate, event in closed.items():
        disposition = str(event.get("disposition", ""))
        if disposition in exact_dispositions and not any(
            probe.get("authenticated") is True
            and probe.get("candidate_match") is True
            and probe.get("kind") == "exact-candidate-verdict"
            and probe.get("verdict") == exact_dispositions[disposition]
            and probe.get("source") == event.get("provenance")
            and all(
                probe.get(field)
                for field in ("request_id", "classified_event_id", "binding_digest", "peer_identity_digest")
            )
            and probe.get("evidence_effect_id") == starts[candidate].get("effect_id")
            and probe.get("submission_epoch") == starts[candidate].get("complete_identity", {}).get("submission_epoch")
            and probe.get("supplied_value_digest")
            == starts[candidate].get("complete_identity", {}).get("candidate_digest")
            and probe.get("evidence_complete_identity") == starts[candidate].get("complete_identity")
            and probe.get("row_type") == "submission"
            and probe.get("board_row_id")
            and probe.get("submitted_at")
            for probe in probes.get(candidate, [])
        ):
            raise ValueError("definitive ambiguity lacks exact authenticated provenance")
        if disposition == "unknown-and-spent" and event.get("provenance") == "fence-expired":
            if float(event.get("at", -1)) != float(starts[candidate]["deadline"]):
                raise ValueError("ambiguity expiry does not match its original deadline")
            offsets = [probe.get("scheduled_offset") for probe in probes.get(candidate, [])]
            if offsets != list(PROBE_OFFSETS):
                raise ValueError("ambiguity expiry lacks the complete reconciliation schedule")
    return receipt


def manifest_receipt(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": MANIFEST_RECEIPT_REF, "kind": RECEIPT_TYPE, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path) -> Mapping[str, object]:
    from solver.manifest import attach_requirement_receipt

    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(path))


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
