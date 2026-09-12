import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from solver.attempt_progress import ProgressController
from solver.attempt_progress_contracts import (
    EvidenceArtifact,
    EvidenceClassification,
    JudgeSource,
    ProgressRequest,
    ProgressStatus,
)
from solver.attempt_progress_receipt import verify_receipt
from solver.event_store_storage import digest_bytes
from solver.redaction import Redactor
from solver.work_generation import GenerationFence
from solver.stall import Confirmation, Deadline, Thresholds, Watch


NOW = dt.datetime(2026, 9, 22, 12, tzinfo=dt.timezone.utc)


def controller(tmp_path):
    def timestamp():
        return "2026-09-22T12:00:00Z"

    fence = GenerationFence(tmp_path, "run-1", Redactor({}), timestamp)
    generation = fence.acquire("challenge-1", "attempt-1")
    return ProgressController(tmp_path, "run-1", Redactor({}), timestamp, fence=fence), fence, generation


def request(generation, evidence, *, checkpoint="checkpoint-1", epoch=0):
    return ProgressRequest(
        generation.generation_id,
        generation.attempt_id,
        checkpoint,
        epoch,
        NOW.isoformat(),
        NOW.isoformat(),
        evidence,
    )


def artifact(identity, classification, source=JudgeSource.DETERMINISTIC):
    return EvidenceArtifact(
        identity,
        digest_bytes(identity.encode()),
        source,
        classification,
        f"{identity} changed state",
        f"verify {identity}",
    )


def test_only_confirmed_semantic_evidence_grants_one_bounded_epoch(tmp_path):
    progress, _, generation = controller(tmp_path)
    confirmed = artifact("evidence-1", EvidenceClassification.CONFIRMED)

    accepted = progress.confirm(request(generation, confirmed))
    repeated = progress.confirm(request(generation, confirmed))
    activity = progress.confirm(
        request(
            generation,
            artifact("evidence-2", EvidenceClassification.ACTIVITY),
            checkpoint="checkpoint-2",
            epoch=1,
        )
    )
    no_inference = progress.confirm(
        request(
            generation,
            artifact("evidence-3", EvidenceClassification.NO_INFERENCE, source=JudgeSource.NONE),
            checkpoint="checkpoint-3",
            epoch=1,
        )
    )

    assert (accepted.status, accepted.epoch_before, accepted.epoch_after) == (ProgressStatus.ACCEPTED, 0, 1)
    assert repeated.status is ProgressStatus.REPEATED
    assert activity.status is ProgressStatus.UNCONFIRMED
    assert no_inference.status is ProgressStatus.NO_INFERENCE


def test_closed_generation_evidence_is_late_and_never_enters_carry(tmp_path):
    progress, fence, generation = controller(tmp_path)
    from solver.event_store_contracts import GenerationDisposition

    fence.close(generation.generation_id, GenerationDisposition.INTERRUPT)
    outcome = progress.confirm(request(generation, artifact("evidence-late", EvidenceClassification.CONFIRMED)))

    assert outcome.status is ProgressStatus.LATE
    assert progress.carry(generation.generation_id).evidence_ids == ()


def test_closed_generation_drops_previously_accepted_carry(tmp_path):
    progress, fence, generation = controller(tmp_path)
    from solver.event_store_contracts import GenerationDisposition

    progress.confirm(request(generation, artifact("evidence-1", EvidenceClassification.CONFIRMED)))
    fence.close(generation.generation_id, GenerationDisposition.INTERRUPT)

    assert progress.carry(generation.generation_id).evidence_ids == ()


def test_closed_generation_rejects_replay_of_accepted_evidence(tmp_path):
    progress, fence, generation = controller(tmp_path)
    from solver.event_store_contracts import GenerationDisposition

    accepted = artifact("evidence-1", EvidenceClassification.CONFIRMED)
    progress.confirm(request(generation, accepted))
    fence.close(generation.generation_id, GenerationDisposition.INTERRUPT)

    assert progress.confirm(request(generation, accepted)).status is ProgressStatus.LATE


def test_carry_replays_current_generation_evidence_deterministically(tmp_path):
    progress, fence, generation = controller(tmp_path)
    progress.confirm(request(generation, artifact("evidence-1", EvidenceClassification.CONFIRMED)))

    restarted = ProgressController(tmp_path, "run-1", Redactor({}), lambda: "2026-09-22T12:01:00Z", fence=fence)

    assert restarted.carry(generation.generation_id) == progress.carry(generation.generation_id)
    assert restarted.carry(generation.generation_id).evidence_ids == ("evidence-1",)


def test_unconfirmed_activity_cannot_prevent_the_epoch_cliff(tmp_path):
    progress, _, generation = controller(tmp_path)
    progress.confirm(request(generation, artifact("noise", EvidenceClassification.ACTIVITY)))

    assert progress.expired(generation.generation_id, epoch=0, steps=25, cliff=25)


def test_real_stall_path_extends_only_after_canonical_confirmation(tmp_path):
    progress, _, generation = controller(tmp_path)

    def unconfirmed(checkpoint):
        outcome = progress.confirm(
            request(
                generation,
                artifact("activity", EvidenceClassification.ACTIVITY),
            )
        )
        return Confirmation(outcome.status is ProgressStatus.ACCEPTED, False)

    watch = Watch(
        Deadline(NOW + dt.timedelta(minutes=10)),
        Thresholds(repeats=99, novelty=99, cliff=2),
        _confirms=unconfirmed,
    )
    watch.observed("curl target", exit_code=1, digest=digest_bytes(b"closed"))
    watch.observed("curl target", exit_code=0, digest=digest_bytes(b"open"))
    watch.observed("curl target", exit_code=0, digest=digest_bytes(b"open"))

    assert watch.deadline.granted == 0
    assert watch.cause(NOW) == "cut:step-cliff"


def test_confirmed_checkpoint_resets_the_step_epoch_and_replays_extension(tmp_path):
    progress, _, generation = controller(tmp_path)

    def confirmed(checkpoint):
        outcome = progress.confirm(
            request(
                generation,
                EvidenceArtifact(
                    "stable-transition",
                    checkpoint.evidence_digest,
                    JudgeSource.DETERMINISTIC,
                    EvidenceClassification.CONFIRMED,
                    checkpoint.moved,
                    checkpoint.replay,
                ),
            )
        )
        accepted = outcome.status in {ProgressStatus.ACCEPTED, ProgressStatus.REPEATED}
        if accepted:
            deadline.apply_epochs(outcome.epoch_after, 120.0, cap=3)
        return Confirmation(accepted, outcome.status is ProgressStatus.ACCEPTED)

    deadline = Deadline(NOW + dt.timedelta(minutes=10))
    watch = Watch(deadline, Thresholds(repeats=99, novelty=99, cliff=3), _confirms=confirmed)
    watch.observed("curl target", exit_code=1, digest=digest_bytes(b"closed"))
    watch.observed("curl target", exit_code=0, digest=digest_bytes(b"open"))
    watch.observed("curl target", exit_code=0, digest=digest_bytes(b"open"))

    assert watch.steps == 0
    assert deadline.granted == 1
    assert watch.cause(NOW) == ""


def test_restart_reapplies_recorded_epoch_extension_once(tmp_path):
    progress, _, generation = controller(tmp_path)
    sealed = digest_bytes(b"open")
    progress.confirm(
        request(
            generation,
            EvidenceArtifact(
                "stable-transition",
                sealed,
                JudgeSource.DETERMINISTIC,
                EvidenceClassification.CONFIRMED,
                "curl target answers differently",
                "curl target",
            ),
        )
    )

    def replay(checkpoint):
        outcome = progress.confirm(
            request(
                generation,
                EvidenceArtifact(
                    "stable-transition",
                    checkpoint.evidence_digest,
                    JudgeSource.DETERMINISTIC,
                    EvidenceClassification.CONFIRMED,
                    checkpoint.moved,
                    checkpoint.replay,
                ),
            )
        )
        if outcome.status is ProgressStatus.REPEATED:
            deadline.apply_epochs(outcome.epoch_after, 120.0, cap=3)
        return Confirmation(False, False)

    deadline = Deadline(NOW + dt.timedelta(minutes=10))
    watch = Watch(deadline, Thresholds(repeats=99, novelty=99), _confirms=replay)
    watch.observed("curl target", exit_code=1, digest=digest_bytes(b"closed"))
    watch.observed("curl target", exit_code=0, digest=sealed)
    watch.observed("curl target", exit_code=0, digest=sealed)

    assert deadline.granted == 1
    assert watch.steps == 3
    restored_budget = deadline.budget
    deadline.apply_epochs(1, 120.0, cap=3)
    assert (deadline.granted, deadline.budget) == (1, restored_budget)


def test_unknown_judge_cannot_confirm_progress(tmp_path):
    progress, _, generation = controller(tmp_path)

    outcome = progress.confirm(
        request(generation, artifact("evidence-1", EvidenceClassification.CONFIRMED, source="unknown-judge"))
    )

    assert outcome.status is ProgressStatus.UNCONFIRMED


def test_generation_rejects_evidence_owned_by_another_attempt(tmp_path):
    progress, _, generation = controller(tmp_path)
    mismatched = replace(
        request(generation, artifact("evidence-1", EvidenceClassification.CONFIRMED)),
        attempt_id="attempt-elsewhere",
    )

    assert progress.confirm(mismatched).status is ProgressStatus.OWNER_MISMATCH
    assert progress.carry(generation.generation_id).evidence_ids == ()


def test_concurrent_confirmations_serialize_one_epoch_transition(tmp_path):
    progress, _, generation = controller(tmp_path)
    requests = tuple(
        request(
            generation,
            artifact(f"evidence-{index}", EvidenceClassification.CONFIRMED),
            checkpoint=f"checkpoint-{index}",
        )
        for index in (1, 2)
    )

    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = tuple(workers.map(progress.confirm, requests))

    assert {outcome.status for outcome in outcomes} == {ProgressStatus.ACCEPTED, ProgressStatus.STALE_EPOCH}
    assert progress.carry(generation.generation_id).evidence_ids in {("evidence-1",), ("evidence-2",)}


def test_receipt_is_rebuilt_from_canonical_progress_facts(tmp_path):
    progress, _, generation = controller(tmp_path)
    progress.confirm(request(generation, artifact("evidence-1", EvidenceClassification.CONFIRMED)))

    receipt = verify_receipt(progress.store.canonical_dir / "attempt-progress.receipt.json")

    assert receipt.name == "attempt-progress.receipt.json"
