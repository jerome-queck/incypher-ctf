"""Bind ambiguity fencing to the serial Board submission authority."""

from dataclasses import replace

from solver.submission.ambiguity_types import CompleteSubmissionIdentity
from solver.write_reservation import EffectIndeterminate


class AmbiguityAwareSerialSubmission:
    """Adapter whose SerialSubmission reservation uses the complete ADR-0052 identity."""

    def __init__(self, serial, fence, identity_for):
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
