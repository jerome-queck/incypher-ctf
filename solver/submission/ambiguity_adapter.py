"""Bind ambiguity fencing to the serial Board submission authority."""

import hashlib

from solver.submission.ambiguity_types import CompleteSubmissionIdentity
from solver.write_reservation import EffectIndeterminate


class AmbiguityAwareSerialSubmission:
    """Adapter whose SerialSubmission reservation uses the complete ADR-0052 identity."""

    def __init__(self, serial, fence, identity_for, *, epoch_authority=None, board_identity=None):
        self._serial, self._fence, self._identity_for = serial, fence, identity_for
        self._epochs, self._board_identity = epoch_authority, board_identity
        self._prepared = {}
        self._deferred = set()

    def pending_count(self):
        return self._serial.pending_count()

    def pending_candidate_ids(self):
        return tuple(item.candidate_id for item in self._fence.pending())

    def was_dispatched(self, admission):
        complete = self._identity_for(admission.candidate)
        return self._serial.was_dispatched(complete.reservation_id)

    def was_possibly_sent(self, candidate_id):
        return self._fence.was_possibly_sent(candidate_id)

    def defer(self, candidate_id):
        complete = self._prepared.get(candidate_id)
        if complete is None:
            return
        self._serial.defer(complete.reservation_id)
        self._fence.release_unused_path(complete.effect_id)
        self._deferred.add(candidate_id)

    def prepare(self, admission):
        """Reserve serial, ambiguity, reconciliation and outcome capacity before final cutoff."""
        candidate_id = admission.candidate.identity
        complete = self._prepared.get(candidate_id)
        if complete is None:
            complete = self._prepare(admission)
            self._serial.reserve(admission, complete)
            self._prepared[candidate_id] = complete
        return complete

    def dispatch(self, admission, *, binding):
        def prepare():
            complete = self.prepare(admission)
            original_id = admission.candidate.identity
            supplied_value_digest = hashlib.sha256(admission.candidate.candidate).hexdigest()
            return complete, lambda: self._fence.mark_wire(
                original_id,
                complete,
                binding,
                supplied_value_digest=supplied_value_digest,
            )

        try:
            result = self._serial.dispatch(
                admission,
                binding=binding,
                prepare=prepare,
                predecessor_dispatched=lambda candidate_id: (
                    candidate_id in self._deferred or self._fence.was_possibly_sent(candidate_id)
                ),
            )
        except BaseException:
            complete = self._prepared.get(admission.candidate.identity) or self._identity_for(admission.candidate)
            current = self._fence.effect_state(complete.reservation_id)
            if current is not None and current.state.value == "possibly-sent":
                if self._fence.can_submit(admission.candidate.identity, complete.effect_id):
                    self._fence.begin(admission.candidate.identity, complete)
            raise
        complete = self._prepared.get(admission.candidate.identity) or self._identity_for(admission.candidate)
        self._fence.release_unused_path(complete.effect_id)
        return result

    def _prepare(self, admission):
        if self._epochs is not None:
            current = self._epochs.current(self._board_identity)
            closed = self._fence.closed_effect_at_epoch(current)
            if closed is not None:
                self._epochs.advance_after(self._board_identity, closed, current)
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
        self._fence.reserve_path(original_id, complete, record_wire=False)
        return complete
