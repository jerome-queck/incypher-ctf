"""Trusted qualification seed expressed only through production authorities."""

from __future__ import annotations

from solver.candidate_admission_contracts import CandidateDisposition, CandidateProposal, CandidateProvenance
from solver.record import Usage


def seed_final_interval(
    recorder,
    admission,
    context_for,
    lease_coordinator,
    *,
    selected_profile,
) -> None:
    """Create replayable ordinary inputs; never mutate a Run or its projections directly."""
    generation = seed_final_interval_lease(recorder, lease_coordinator, selected_profile=selected_profile)
    seed_final_interval_candidates(recorder, admission, context_for, generation)


def seed_final_interval_lease(recorder, lease_coordinator, *, selected_profile):
    """Establish occupied-Lane and Lease truth before Boot reconciliation."""
    generation = seed_final_interval_generation(recorder, selected_profile=selected_profile)
    lease_coordinator.acquire(1, generation=generation)
    return generation


def seed_final_interval_generation(recorder, *, selected_profile):
    """Establish occupied-Lane truth without inventing an Instance for restart proof."""
    if selected_profile is None or int(selected_profile["lanes"]) != 2:
        raise ValueError("controlled final interval requires the signed two-Lane profile")
    return recorder.generations.acquire("integer:1", "qualification-attempt-occupied")


def seed_final_interval_candidates(recorder, admission, context_for, generation) -> None:
    """Admit controlled Candidates after the production submission authority exists."""
    candidates = (b"qualification{accepted}", b"qualification{ambiguous}")
    for index, candidate in enumerate(candidates, 1):
        observation = recorder.step_begin(
            attempt_id=generation.attempt_id,
            step_index=index,
            command_raw="controlled qualification observation",
            command_normalised="controlled qualification observation",
            tool="qualification",
        ).end(exit_code=0, output=b"observed " + candidate, usage=Usage(model=""))
        event_digest = admission.observation_digest(attempt_id=generation.attempt_id, body_digest=observation.digest)
        ready = admission.admit_ready(
            CandidateProposal(
                f"qualification-candidate-{index}",
                1,
                generation.generation_id,
                candidate,
                CandidateProvenance(CandidateDisposition.OBSERVED, (event_digest,), ()),
                context_for(1),
            )
        )
        if ready is None:
            raise ValueError("controlled Candidate was not admitted by production authority")
