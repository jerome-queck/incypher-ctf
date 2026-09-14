"""Storage admission and retirement are observable at the governor boundary."""

import errno
import json
import copy
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from solver.event_store import EventStore, InvalidEventError, ObservationRecorded
from solver.observation import digest_of
from solver.redaction import Redactor
from solver.recovery.incident import RECEIPT
from solver.recovery.storage import recover_storage_pressure
from solver.storage_governor import (
    AdmissionClass,
    PressureState,
    StorageCapacity,
    StorageGovernor,
    StorageGovernorProfile,
    ReachabilityRoots,
    RetirementCandidate,
    manifest_receipt,
    verify_receipt,
)
from solver.storage_governor_proof import load_controlled_proof
from solver.storage_governor_contracts import RetirementRecorded, StorageClassified
from solver.event_store_storage import canonical_bytes
from solver.manifest import generate_manifest
from test_manifest import release_candidate_profile
from solver.write_reservation import ReservationConflict, ReservationUnavailable


def capacity(value: int) -> StorageCapacity:
    return StorageCapacity(
        bytes=value,
        filesystem_objects=value,
        create=value,
        append=value,
        rename=value,
        unlink=value,
        durability=value,
    )


def manifest_capacity(multiplier: int) -> StorageCapacity:
    return StorageCapacity(
        bytes=1000 * multiplier,
        filesystem_objects=10 * multiplier,
        create=multiplier,
        append=multiplier,
        rename=multiplier,
        unlink=multiplier,
        durability=multiplier,
    )


PROFILE = StorageGovernorProfile(
    writable_envelope=capacity(100),
    warning_remaining=capacity(40),
    stop_admission_remaining=capacity(20),
    authority_only_remaining=capacity(18),
    shared_authority_pool=capacity(13),
    terminal_floor=capacity(2),
    recovery_floor=capacity(3),
)


def profile_with_envelope(value: int) -> StorageGovernorProfile:
    return StorageGovernorProfile(
        writable_envelope=capacity(value),
        warning_remaining=capacity(min(40, value)),
        stop_admission_remaining=capacity(min(20, value)),
        authority_only_remaining=capacity(18),
        shared_authority_pool=capacity(13),
        terminal_floor=capacity(2),
        recovery_floor=capacity(3),
    )


@pytest.mark.parametrize(
    ("envelope", "expected", "ordinary_admitted"),
    (
        (42, PressureState.NORMAL, True),
        (41, PressureState.WARNING, True),
        (20, PressureState.STOP_ADMISSION, False),
        (18, PressureState.AUTHORITY_ONLY, False),
    ),
)
def test_pressure_thresholds_and_ordinary_admission_are_deterministic(tmp_path, envelope, expected, ordinary_admitted):
    governor = StorageGovernor(tmp_path, "run-1", profile_with_envelope(envelope))

    first = governor.admit(
        request_id=f"ordinary:{expected.value}",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
    )
    repeated = governor.admit(
        request_id=f"ordinary:{expected.value}",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
    )

    assert first == repeated
    assert first.pressure is expected
    assert first.admitted is ordinary_admitted


def test_one_admission_request_id_cannot_change_its_capacity_decision(tmp_path):
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    governor.admit(
        request_id="attempt:42",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
    )

    with pytest.raises(ReservationConflict, match="different semantic effect"):
        governor.admit(
            request_id="attempt:42",
            admission_class=AdmissionClass.ORDINARY,
            need=capacity(2),
        )


def test_admission_derives_headroom_from_one_durable_authority_instead_of_the_caller(tmp_path):
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)

    decision = governor.admit(
        request_id="attempt:derived",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
    )

    assert decision.admitted is True
    assert decision.remaining == capacity(99)


def test_two_admissions_racing_the_stop_floor_cannot_reuse_headroom(tmp_path):
    profile = StorageGovernorProfile(
        writable_envelope=capacity(20),
        warning_remaining=capacity(20),
        stop_admission_remaining=capacity(18),
        authority_only_remaining=capacity(18),
        shared_authority_pool=capacity(13),
        terminal_floor=capacity(2),
        recovery_floor=capacity(3),
    )
    governor = StorageGovernor(tmp_path, "run-1", profile)

    with ThreadPoolExecutor(max_workers=2) as workers:
        decisions = list(
            workers.map(
                lambda index: governor.admit(
                    request_id=f"attempt:{index}",
                    admission_class=AdmissionClass.ORDINARY,
                    need=capacity(1),
                ),
                range(2),
            )
        )

    assert sorted(decision.admitted for decision in decisions) == [False, True]


def test_shared_terminal_and_recovery_headroom_are_non_borrowable(tmp_path):
    profile = StorageGovernorProfile(
        writable_envelope=capacity(3),
        warning_remaining=capacity(3),
        stop_admission_remaining=capacity(3),
        authority_only_remaining=capacity(3),
        shared_authority_pool=capacity(1),
        terminal_floor=capacity(1),
        recovery_floor=capacity(1),
    )
    governor = StorageGovernor(tmp_path, "run-1", profile)

    shared = governor.admit(
        request_id="authority:one",
        admission_class=AdmissionClass.AUTHORITY,
        need=capacity(1),
    )
    with pytest.raises(ReservationUnavailable):
        governor.admit(
            request_id="authority:two",
            admission_class=AdmissionClass.AUTHORITY,
            need=capacity(1),
        )
    terminal = governor.admit(
        request_id="terminal:one",
        admission_class=AdmissionClass.TERMINAL,
        need=capacity(1),
    )
    recovery = governor.admit(
        request_id="recovery:one",
        admission_class=AdmissionClass.RECOVERY,
        need=capacity(1),
    )

    assert [shared.admitted, terminal.admitted, recovery.admitted] == [True, True, True]


def test_retired_capacity_is_reusable_only_after_the_completion_record(tmp_path):
    profile = StorageGovernorProfile(
        writable_envelope=capacity(21),
        warning_remaining=capacity(21),
        stop_admission_remaining=capacity(18),
        authority_only_remaining=capacity(18),
        shared_authority_pool=capacity(13),
        terminal_floor=capacity(2),
        recovery_floor=capacity(3),
    )
    governor = StorageGovernor(tmp_path, "run-1", profile)
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation("first"), body=b"x")
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{event.blob_digest}",
        storage_class="raw-observation",
        digest=event.blob_digest,
        length=event.blob_bytes,
        event_sequences=(event.sequence,),
    )
    governor.classify(candidate)
    first = governor.admit(
        request_id="body:first",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
        resource=candidate,
    )

    before = governor.admit(
        request_id="body:before-retirement",
        admission_class=AdmissionClass.ORDINARY,
        need=StorageCapacity(2, 2, 1, 1, 1, 1, 1),
    )
    governor.retire(
        (RetirementCandidate(**{**candidate.__dict__, "reservation_key": first.reservation_key}),),
        ReachabilityRoots(),
        reason="hard-pressure",
    )
    after = governor.admit(
        request_id="body:after-retirement",
        admission_class=AdmissionClass.ORDINARY,
        need=StorageCapacity(2, 2, 1, 1, 1, 1, 1),
    )

    assert before.admitted is False
    assert after.admitted is True


def test_retirement_uses_recovery_without_borrowing_terminal_capacity(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation("retire-with-recovery"), body=b"x")
    observed = []
    governor = None

    def observe(phase):
        if phase != "before_tombstone":
            return
        observed.append(
            governor.admit(
                request_id="recovery:competing",
                admission_class=AdmissionClass.RECOVERY,
                need=PROFILE.recovery_floor,
            ).admitted
        )
        observed.append(
            governor.admit(
                request_id="terminal:independent",
                admission_class=AdmissionClass.TERMINAL,
                need=capacity(1),
            ).admitted
        )

    governor = StorageGovernor(tmp_path, "run-1", PROFILE, retirement_hook=observe)
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{event.blob_digest}",
        storage_class="raw-observation",
        digest=event.blob_digest,
        length=event.blob_bytes,
        event_sequences=(event.sequence,),
    )
    governor.classify(candidate)
    governor.retire(
        (candidate,),
        ReachabilityRoots(),
        reason="hard-pressure",
    )

    assert observed == [False, True]


def test_full_data_budget_records_authority_only_pressure_before_refusing_work(tmp_path):
    governor = StorageGovernor(tmp_path, "run-1", profile_with_envelope(18))

    decision = governor.admit(
        request_id="attempt:42",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
    )
    assert decision.admitted is False
    canonical = EventStore(tmp_path, run_id="run-1").events()
    assert canonical[-1].payload["record"] == "pressure-decision"
    assert canonical[-1].payload["admitted"] is False
    governor.close()
    governor = StorageGovernor(tmp_path, "run-1", profile_with_envelope(18))
    receipt = json.loads(governor.write_receipt(roots=ReachabilityRoots()).read_text())
    recorded = receipt["pressure_decisions"][-1]
    assert recorded["request_id"] == "attempt:42"
    assert recorded["pressure"] == "authority-only"
    assert recorded["admitted"] is False


@pytest.mark.parametrize("fault_errno", (errno.ENOSPC, errno.EDQUOT, errno.EROFS, errno.EIO))
def test_unwritable_authority_ledger_never_turns_missing_pressure_evidence_into_admission(tmp_path, fault_errno):
    governor = StorageGovernor(tmp_path, "run-1", profile_with_envelope(18))

    with patch("solver.write_reservation_storage.os.pwrite", side_effect=OSError(fault_errno, "controlled")):
        with pytest.raises(OSError) as failed:
            governor.admit(
                request_id=f"fault:{fault_errno}",
                admission_class=AdmissionClass.ORDINARY,
                need=capacity(1),
            )

    assert failed.value.errno == fault_errno
    assert EventStore(tmp_path, run_id="run-1").events() == []
    receipt = json.loads(governor.write_receipt(roots=ReachabilityRoots()).read_text())
    assert receipt["pressure_decisions"] == []


def test_durability_failure_returns_no_admission_even_if_the_preallocated_row_is_observable(tmp_path):
    governor = StorageGovernor(tmp_path, "run-1", profile_with_envelope(18))

    with patch("solver.write_reservation_storage.os.fsync", side_effect=OSError(errno.EIO, "controlled fsync")):
        with pytest.raises(OSError, match="controlled fsync"):
            governor.admit(
                request_id="fault:durability",
                admission_class=AdmissionClass.ORDINARY,
                need=capacity(1),
            )

    assert EventStore(tmp_path, run_id="run-1").events() == []
    receipt = json.loads(governor.write_receipt(roots=ReachabilityRoots()).read_text())
    assert receipt["pressure_decisions"] == []


def observation(attempt_id: str) -> ObservationRecorded:
    return ObservationRecorded(
        attempt_id=attempt_id,
        step_index=1,
        command_raw="printf output",
        command_normalised="printf output",
        tool="bash",
    )


def classify(governor: StorageGovernor, *candidates: RetirementCandidate) -> None:
    for candidate in candidates:
        governor.classify(candidate)


def test_retirement_releases_only_an_unreachable_canonical_blob_and_replays_without_a_dangling_reference(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    reachable = store.append(observation("reachable"), body=b"keep")
    disposable = store.append(observation("disposable"), body=b"retire")
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    candidates = (
        RetirementCandidate(
            path=f"sealed/sha256/{reachable.blob_digest}",
            storage_class="raw-observation",
            digest=reachable.blob_digest,
            length=reachable.blob_bytes,
            event_sequences=(reachable.sequence,),
        ),
        RetirementCandidate(
            path=f"sealed/sha256/{disposable.blob_digest}",
            storage_class="raw-observation",
            digest=disposable.blob_digest,
            length=disposable.blob_bytes,
            event_sequences=(disposable.sequence,),
        ),
    )
    classify(governor, *candidates)

    result = governor.retire(
        candidates,
        ReachabilityRoots(
            event_sequences=frozenset({reachable.sequence}),
            blob_digests=frozenset({reachable.blob_digest}),
        ),
        reason="hard-pressure",
    )

    replayed = EventStore(tmp_path, run_id="run-1").events()
    assert result.retired_digests == (digest_of(b"retire"),)
    assert (store.sealed_dir / reachable.blob_digest).read_bytes() == b"keep"
    assert not (store.sealed_dir / disposable.blob_digest).exists()
    assert replayed[0].body == b"keep"
    assert replayed[1].body == b""
    assert [
        event.payload["record"] for event in replayed if event.payload.get("record", "").startswith("retirement-")
    ] == [
        "retirement-tombstone",
        "retirement-complete",
    ]


def test_pressure_recovery_runs_actual_governed_retirement_under_incident_authority(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    disposable = store.append(observation("disposable"), body=b"retire")
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{disposable.blob_digest}",
        storage_class="raw-observation",
        digest=disposable.blob_digest,
        length=disposable.blob_bytes,
        event_sequences=(disposable.sequence,),
    )
    recovery_profile = StorageGovernorProfile(
        writable_envelope=capacity(10_000),
        warning_remaining=capacity(9_000),
        stop_admission_remaining=capacity(8_000),
        authority_only_remaining=capacity(7_000),
        shared_authority_pool=capacity(6_000),
        terminal_floor=capacity(500),
        recovery_floor=capacity(500),
    )
    governor = StorageGovernor(tmp_path, "run-1", recovery_profile)
    governor.classify(candidate)

    result = recover_storage_pressure(
        governor,
        governor.recovery_composition(
            tmp_path,
            Redactor({}),
            now=lambda: dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
        ),
        (candidate,),
        ReachabilityRoots(),
        failed_revision="pressure-revision-1",
        reason="controlled-pressure",
        now=lambda: dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
    )

    assert result.disposition == "resolved"
    assert not (store.sealed_dir / disposable.blob_digest).exists()
    receipt = json.loads((tmp_path / "runs" / "run-1" / "canonical" / RECEIPT).read_text())
    assert receipt["changed_action"]["dimension"] == "storage-revision"
    assert receipt["changed_action"]["source"] == "canonical-storage-classification"
    assert receipt["probation_outcome"] == "passed"


@pytest.mark.parametrize(
    "crash_point",
    ("before_tombstone", "after_tombstone", "after_delete", "after_completion"),
)
def test_every_retirement_crash_boundary_replays_to_the_old_or_completed_set(tmp_path, crash_point):
    store = EventStore(tmp_path, run_id="run-1")
    disposable = store.append(observation("disposable"), body=b"retire")
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{disposable.blob_digest}",
        storage_class="raw-observation",
        digest=disposable.blob_digest,
        length=disposable.blob_bytes,
        event_sequences=(disposable.sequence,),
    )

    def crash(point: str) -> None:
        if point == crash_point:
            raise RuntimeError(f"crash:{point}")

    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    governor.classify(candidate)
    governor.close()
    governor = StorageGovernor(tmp_path, "run-1", PROFILE, retirement_hook=crash)
    with pytest.raises(RuntimeError, match=f"crash:{crash_point}"):
        governor.retire((candidate,), ReachabilityRoots(), reason="controlled-pressure")

    governor.close()
    restarted = StorageGovernor(tmp_path, "run-1", PROFILE)
    events = EventStore(tmp_path, run_id="run-1").events()
    records = [
        event.payload["record"]
        for event in events
        if event.event_type == "storage-governor.recorded" and event.payload.get("record", "").startswith("retirement-")
    ]
    if crash_point == "before_tombstone":
        assert (store.sealed_dir / disposable.blob_digest).read_bytes() == b"retire"
        assert records == []
    else:
        assert not (store.sealed_dir / disposable.blob_digest).exists()
        assert records == ["retirement-tombstone", "retirement-complete"]
        assert events[0].body == b""
    assert restarted.replay_pending_retirements() == ()


def test_retirement_refuses_an_incomplete_release_of_a_shared_canonical_blob(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    first = store.append(observation("first"), body=b"shared")
    second = store.append(observation("second"), body=b"shared")
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{first.blob_digest}",
        storage_class="raw-observation",
        digest=first.blob_digest,
        length=first.blob_bytes,
        event_sequences=(first.sequence,),
    )

    with pytest.raises(ValueError, match="every canonical reference"):
        StorageGovernor(tmp_path, "run-1", PROFILE).classify(candidate)

    assert (store.sealed_dir / first.blob_digest).read_bytes() == b"shared"
    assert [event.body for event in store.events()] == [b"shared", b"shared"]
    assert second.blob_digest == first.blob_digest


def test_in_flight_canonical_reservation_is_an_implicit_reachability_root(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    body = b"staged-but-not-committed"
    digest = digest_of(body)
    store.reserve(observation("in-flight"), blob_digest=digest, blob_bytes=len(body))
    (store.sealed_dir / digest).write_bytes(body)
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{digest}",
        storage_class="raw-observation",
        digest=digest,
        length=len(body),
    )

    roots = ReachabilityRoots(
        in_flight_paths=frozenset({candidate.path}),
        in_flight_digests=frozenset({candidate.digest}),
    )
    result = StorageGovernor(tmp_path, "run-1", PROFILE).retire((candidate,), roots, reason="controlled-pressure")

    assert result.retired_digests == ()
    assert result.protected_paths == (candidate.path,)
    assert (store.sealed_dir / digest).read_bytes() == body


@pytest.mark.parametrize(
    "storage_class",
    ("canonical-authority", "solve-receipt", "selected-evidence", "promoted-evidence", "v1-evidence"),
)
def test_protected_storage_classes_never_become_retirement_candidates(tmp_path, storage_class):
    store = EventStore(tmp_path, run_id="run-1")
    protected = store.append(observation(storage_class), body=storage_class.encode())
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{protected.blob_digest}",
        storage_class=storage_class,
        digest=protected.blob_digest,
        length=protected.blob_bytes,
        event_sequences=(protected.sequence,),
    )

    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    governor.classify(candidate)
    roots = ReachabilityRoots(
        event_sequences=frozenset({protected.sequence}),
        blob_digests=frozenset({protected.blob_digest}),
    )
    result = governor.retire((candidate,), roots, reason="hard-pressure")

    assert result.retired_digests == ()
    assert result.protected_paths == (candidate.path,)
    assert (store.sealed_dir / protected.blob_digest).read_bytes() == storage_class.encode()


def test_storage_governor_receipt_binds_budgets_roots_retirement_and_headroom(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    kept = store.append(observation("kept"), body=b"keep")
    retired = store.append(observation("retired"), body=b"retire")
    roots = ReachabilityRoots(
        event_sequences=frozenset({kept.sequence}),
        blob_digests=frozenset({kept.blob_digest}),
    )
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    governor.admit(
        request_id="attempt:refused",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
    )
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{retired.blob_digest}",
        storage_class="raw-observation",
        digest=retired.blob_digest,
        length=retired.blob_bytes,
        event_sequences=(retired.sequence,),
    )
    governor.classify(candidate)
    governor.retire(
        (candidate,),
        roots,
        reason="hard-pressure",
    )

    receipt_path = governor.write_receipt(roots=roots)
    receipt = json.loads(receipt_path.read_text())

    assert receipt["receipt_type"] == "storage-governor"
    assert receipt["profile"]["authority_only_remaining"] == capacity(18).as_dict()
    assert receipt["roots"]["event_sequences"] == [kept.sequence]
    assert receipt["retired_digests"] == [retired.blob_digest]
    assert receipt["remaining_authority_headroom"] == capacity(18).as_dict()
    assert receipt["post_crash_verification"]["pending_retirements"] == []
    assert receipt["post_crash_verification"]["verified_event_count"] > 0
    assert receipt["controlled_proof"]["retirement_crash_outcomes"]["after-delete"] == "replay-completed"
    assert verify_receipt(receipt_path) == receipt_path
    descriptor = manifest_receipt(receipt_path)
    assert "manifest_link" not in receipt
    draft = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    requirements = copy.deepcopy(draft["requirements"])
    row = next(item for item in requirements if item["row_id"] == "core.canonical-state-replay-restart")
    row.update(status="implemented", receipt_ref=descriptor["ref"])
    linked = generate_manifest(
        image_digest=draft["candidate"]["image_digest"],
        release_candidate_profile=draft["selected_profile"],
        requirements=requirements,
        receipts=[descriptor],
    )
    linked_row = next(item for item in linked["requirements"] if item["row_id"] == row["row_id"])
    assert linked_row["receipt_ref"] == "receipt:storage-governor"

    receipt["retired_digests"] = []
    receipt_path.write_bytes(canonical_bytes(receipt) + b"\n")
    with pytest.raises(ValueError, match="canonical state"):
        verify_receipt(receipt_path)


def test_receipt_verification_reconstructs_roots_and_headroom_instead_of_trusting_the_document(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    rooted = store.append(observation("rooted"), body=b"rooted")
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    receipt_path = governor.write_receipt(
        roots=ReachabilityRoots(
            event_sequences=frozenset({rooted.sequence}),
            blob_digests=frozenset({rooted.blob_digest}),
        )
    )
    receipt = json.loads(receipt_path.read_text())

    receipt["roots"] = ReachabilityRoots().as_dict()
    receipt["remaining_authority_headroom"] = capacity(0).as_dict()
    receipt_path.write_bytes(canonical_bytes(receipt) + b"\n")

    with pytest.raises(ValueError, match="canonical state"):
        verify_receipt(receipt_path)


@pytest.mark.parametrize("omitted", ("event", "blob"))
def test_receipt_source_rejects_omitted_live_canonical_roots(tmp_path, omitted):
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation("rooted"), body=b"rooted")
    roots = ReachabilityRoots(
        event_sequences=frozenset() if omitted == "event" else frozenset({event.sequence}),
        blob_digests=frozenset() if omitted == "blob" else frozenset({event.blob_digest}),
    )

    with pytest.raises(ValueError, match="omit authoritative"):
        StorageGovernor(tmp_path, "run-1", PROFILE).write_receipt(roots=roots)


def test_receipt_source_rejects_omitted_in_flight_roots(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    body = b"in-flight"
    store.reserve(observation("in-flight-root"), blob_digest=digest_of(body), blob_bytes=len(body))
    (store.sealed_dir / digest_of(body)).write_bytes(body)

    with pytest.raises(ValueError, match="omit authoritative"):
        StorageGovernor(tmp_path, "run-1", PROFILE).write_receipt(roots=ReachabilityRoots())


@pytest.mark.parametrize(
    ("directory", "field"),
    (("promoted", "promoted_paths"), ("v1", "paths")),
)
def test_first_receipt_inventories_promoted_and_v1_roots_without_a_prior_snapshot(tmp_path, directory, field):
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    relative = f"{directory}/evidence"
    path = governor.run_dir / relative
    path.parent.mkdir(parents=True)
    path.write_text("evidence")

    with pytest.raises(ValueError, match="omit authoritative"):
        governor.write_receipt(roots=ReachabilityRoots())

    receipt = json.loads(governor.write_receipt(roots=ReachabilityRoots(**{field: frozenset({relative})})).read_text())
    assert receipt["roots"][field] == [relative]


def test_unclassified_resource_cannot_be_retired(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation("unclassified"), body=b"unclassified")
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{event.blob_digest}",
        storage_class="raw-observation",
        digest=event.blob_digest,
        length=event.blob_bytes,
        event_sequences=(event.sequence,),
    )

    with pytest.raises(ValueError, match="authoritative storage classification"):
        StorageGovernor(tmp_path, "run-1", PROFILE).retire((candidate,), ReachabilityRoots(), reason="attack")
    assert (store.sealed_dir / event.blob_digest).read_bytes() == b"unclassified"


def test_protected_classification_cannot_be_mislabelled_for_deletion(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation("selected"), body=b"selected")
    protected = RetirementCandidate(
        path=f"sealed/sha256/{event.blob_digest}",
        storage_class="selected-evidence",
        digest=event.blob_digest,
        length=event.blob_bytes,
        event_sequences=(event.sequence,),
    )
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    governor.classify(protected)
    attack = RetirementCandidate(**{**protected.__dict__, "storage_class": "raw-observation"})
    roots = ReachabilityRoots(
        event_sequences=frozenset({event.sequence}),
        blob_digests=frozenset({event.blob_digest}),
    )

    with pytest.raises(ValueError, match="differs from authoritative storage classification"):
        governor.retire((attack,), roots, reason="attack")
    assert (store.sealed_dir / event.blob_digest).read_bytes() == b"selected"


def test_forged_retirable_classification_without_authority_cannot_enable_deletion(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation("forged-classification"), body=b"protected")
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{event.blob_digest}",
        storage_class="raw-observation",
        digest=event.blob_digest,
        length=event.blob_bytes,
        event_sequences=(event.sequence,),
    )
    store.append(
        StorageClassified(
            event_id="classification:forged",
            path=candidate.path,
            storage_class=candidate.storage_class,
            digest=candidate.digest,
            length=candidate.length,
            event_sequences=candidate.event_sequences,
            reservation_key="forged-authority",
        ),
        body=b"",
    )

    with pytest.raises(ValueError, match="matching authority reservation"):
        StorageGovernor(tmp_path, "run-1", PROFILE).retire((candidate,), ReachabilityRoots(), reason="attack")
    assert (store.sealed_dir / event.blob_digest).read_bytes() == b"protected"


@pytest.mark.parametrize("storage_class", ("canonical-authority", "unknown-retirement-class"))
def test_canonical_retirement_contract_rejects_protected_or_unknown_classes_before_replay(tmp_path, storage_class):
    store = EventStore(tmp_path, run_id="run-1")
    target = store.append(observation("protected"), body=b"protected")
    record = RetirementRecorded(
        event_id=f"forged:{storage_class}",
        record="retirement-tombstone",
        path=f"sealed/sha256/{target.blob_digest}",
        storage_class=storage_class,
        target_digest=target.blob_digest,
        target_length=target.blob_bytes,
        event_sequences=(target.sequence,),
        reason="forged",
        retirement_reservation_key="forged-reservation",
    )

    with pytest.raises(InvalidEventError, match="retirement payload"):
        store.append(record, body=b"")
    StorageGovernor(tmp_path, "run-1", PROFILE).replay_pending_retirements()
    assert (store.sealed_dir / target.blob_digest).read_bytes() == b"protected"


def test_retirement_reservation_key_cannot_release_a_different_blob(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    first = store.append(observation("first-bound"), body=b"first")
    second = store.append(observation("second-bound"), body=b"second")
    first_candidate = RetirementCandidate(
        path=f"sealed/sha256/{first.blob_digest}",
        storage_class="raw-observation",
        digest=first.blob_digest,
        length=first.blob_bytes,
        event_sequences=(first.sequence,),
    )
    second_candidate = RetirementCandidate(
        path=f"sealed/sha256/{second.blob_digest}",
        storage_class="raw-observation",
        digest=second.blob_digest,
        length=second.blob_bytes,
        event_sequences=(second.sequence,),
    )
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    classify(governor, first_candidate, second_candidate)
    first_grant = governor.admit(
        request_id="body:first-bound",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
        resource=first_candidate,
    )
    attack = RetirementCandidate(**{**second_candidate.__dict__, "reservation_key": first_grant.reservation_key})

    with pytest.raises(ValueError, match="different storage resource"):
        governor.retire((attack,), ReachabilityRoots(), reason="attack")
    assert (store.sealed_dir / second.blob_digest).read_bytes() == b"second"


def test_replay_rejects_cross_object_capacity_release_before_deletion(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    first = store.append(observation("first-replay-bound"), body=b"first")
    second = store.append(observation("second-replay-bound"), body=b"second")
    first_candidate = RetirementCandidate(
        path=f"sealed/sha256/{first.blob_digest}",
        storage_class="raw-observation",
        digest=first.blob_digest,
        length=first.blob_bytes,
        event_sequences=(first.sequence,),
    )
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    classify(governor, first_candidate)
    grant = governor.admit(
        request_id="body:first-replay-bound",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
        resource=first_candidate,
    )
    second_candidate = RetirementCandidate(
        path=f"sealed/sha256/{second.blob_digest}",
        storage_class="raw-observation",
        digest=second.blob_digest,
        length=second.blob_bytes,
        event_sequences=(second.sequence,),
    )
    governor.classify(second_candidate)
    store.append(
        RetirementRecorded(
            event_id="forged:cross-object",
            record="retirement-tombstone",
            path=f"sealed/sha256/{second.blob_digest}",
            storage_class="raw-observation",
            target_digest=second.blob_digest,
            target_length=second.blob_bytes,
            event_sequences=(second.sequence,),
            reason="forged",
            reservation_key=grant.reservation_key,
            retirement_reservation_key="forged-retirement",
        ),
        body=b"",
    )
    governor.close()

    with pytest.raises(ValueError, match="different storage resource"):
        StorageGovernor(tmp_path, "run-1", PROFILE)
    assert (store.sealed_dir / second.blob_digest).read_bytes() == b"second"


def test_retirement_refuses_paths_outside_the_exact_sealed_digest_identity(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation("kept"), body=b"keep")
    link = store.sealed_dir / "alias"
    link.symlink_to(store.sealed_dir / event.blob_digest)
    candidate = RetirementCandidate(
        path="sealed/sha256/alias",
        storage_class="raw-observation",
        digest=event.blob_digest,
        length=event.blob_bytes,
        event_sequences=(event.sequence,),
    )

    with pytest.raises(ValueError, match="exact sealed digest path"):
        StorageGovernor(tmp_path, "run-1", PROFILE).retire((candidate,), ReachabilityRoots(), reason="hard-pressure")

    assert (store.sealed_dir / event.blob_digest).read_bytes() == b"keep"
    assert link.is_symlink()


def test_governor_profile_is_derived_from_the_sealed_release_candidate_storage_contract():
    storage = release_candidate_profile()["storage"]

    profile = StorageGovernorProfile.from_release_candidate(storage)

    assert profile.writable_envelope == manifest_capacity(100)
    assert profile.warning_remaining == manifest_capacity(40)
    assert profile.stop_admission_remaining == manifest_capacity(20)
    assert profile.authority_only_remaining == manifest_capacity(18)


def test_pressure_evidence_is_sanitized_at_the_canonical_boundary(tmp_path):
    secret = "storage-secret"
    governor = StorageGovernor(
        tmp_path,
        "run-1",
        PROFILE,
        redactor=Redactor({"TEAM_KEY": secret}),
    )

    governor.admit(
        request_id=f"attempt:{secret}",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
    )

    assert secret not in EventStore(tmp_path, run_id="run-1").events_path.read_text()


@pytest.mark.parametrize(
    "dimension",
    ("bytes", "filesystem_objects", "create", "append", "rename", "unlink", "durability"),
)
def test_each_storage_dimension_independently_forces_authority_only_pressure(tmp_path, dimension):
    envelope = capacity(100).as_dict()
    warning = capacity(40).as_dict()
    stop = capacity(20).as_dict()
    for values in (envelope, warning, stop):
        values[dimension] = 18
    profile = StorageGovernorProfile(
        writable_envelope=StorageCapacity(**envelope),
        warning_remaining=StorageCapacity(**warning),
        stop_admission_remaining=StorageCapacity(**stop),
        authority_only_remaining=capacity(18),
        shared_authority_pool=capacity(13),
        terminal_floor=capacity(2),
        recovery_floor=capacity(3),
    )

    decision = StorageGovernor(tmp_path, "run-1", profile).admit(
        request_id=f"dimension:{dimension}",
        admission_class=AdmissionClass.ORDINARY,
        need=capacity(1),
    )

    assert decision.pressure is PressureState.AUTHORITY_ONLY
    assert decision.admitted is False


@pytest.mark.parametrize("fault", ("unlink", "durability"))
def test_metadata_or_durability_failure_after_tombstone_is_replayed_without_losing_authority(tmp_path, fault):
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation(fault), body=fault.encode())
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{event.blob_digest}",
        storage_class="raw-observation",
        digest=event.blob_digest,
        length=event.blob_bytes,
        event_sequences=(event.sequence,),
    )
    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    governor.classify(candidate)
    target = "pathlib.Path.unlink" if fault == "unlink" else "solver.storage_governor.fsync_directory"

    with patch(target, side_effect=OSError(errno.EIO, f"controlled {fault} failure")):
        with pytest.raises(OSError, match=f"controlled {fault} failure"):
            governor.retire((candidate,), ReachabilityRoots(), reason="fault-injection")

    governor.close()
    StorageGovernor(tmp_path, "run-1", PROFILE)
    events = EventStore(tmp_path, run_id="run-1").events()
    assert not (store.sealed_dir / event.blob_digest).exists()
    assert [one.payload["record"] for one in events if one.payload.get("record", "").startswith("retirement-")] == [
        "retirement-tombstone",
        "retirement-complete",
    ]
    assert events[0].body == b""


@pytest.mark.parametrize(
    ("append_number", "append_phase"),
    ((1, "before_append"), (1, "after_append"), (2, "before_append"), (2, "after_append")),
)
def test_canonical_retirement_append_crashes_preserve_or_finish_a_verifiable_set(tmp_path, append_number, append_phase):
    store = EventStore(tmp_path, run_id="run-1")
    event = store.append(observation("retire"), body=b"retire")
    candidate = RetirementCandidate(
        path=f"sealed/sha256/{event.blob_digest}",
        storage_class="raw-observation",
        digest=event.blob_digest,
        length=event.blob_bytes,
        event_sequences=(event.sequence,),
    )
    appends = 0

    def crash(phase: str) -> None:
        nonlocal appends
        if phase == "before_append":
            appends += 1
        if appends == append_number and phase == append_phase:
            raise RuntimeError(f"canonical-crash:{append_number}:{append_phase}")

    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    governor.classify(candidate)
    governor.close()
    governor = StorageGovernor(tmp_path, "run-1", PROFILE, event_store_hook=crash)
    with pytest.raises(RuntimeError, match="canonical-crash"):
        governor.retire((candidate,), ReachabilityRoots(), reason="fault-injection")

    governor.close()
    restarted = StorageGovernor(tmp_path, "run-1", PROFILE)
    if append_number == 1 and append_phase == "before_append":
        assert (store.sealed_dir / event.blob_digest).read_bytes() == b"retire"
        restarted.retire((candidate,), ReachabilityRoots(), reason="fault-injection")
    assert not (store.sealed_dir / event.blob_digest).exists()
    replayed = EventStore(tmp_path, run_id="run-1").events()
    assert [one.payload["record"] for one in replayed if one.payload.get("record", "").startswith("retirement-")] == [
        "retirement-tombstone",
        "retirement-complete",
    ]
    assert replayed[0].body == b""


def test_retirement_plan_uses_policy_class_before_path_order(tmp_path):
    store = EventStore(tmp_path, run_id="run-1")
    events = [store.append(observation(str(index)), body=f"body-{index}".encode()) for index in range(2)]
    path_first, path_second = sorted(events, key=lambda event: event.blob_digest)
    candidates = (
        RetirementCandidate(
            path=f"sealed/sha256/{path_first.blob_digest}",
            storage_class="closed-incident-detail",
            digest=path_first.blob_digest,
            length=path_first.blob_bytes,
            event_sequences=(path_first.sequence,),
        ),
        RetirementCandidate(
            path=f"sealed/sha256/{path_second.blob_digest}",
            storage_class="ephemeral",
            digest=path_second.blob_digest,
            length=path_second.blob_bytes,
            event_sequences=(path_second.sequence,),
        ),
    )

    governor = StorageGovernor(tmp_path, "run-1", PROFILE)
    classify(governor, *candidates)
    governor.retire(
        candidates,
        ReachabilityRoots(),
        reason="soft-pressure",
    )

    tombstones = [
        event.payload
        for event in EventStore(tmp_path, run_id="run-1").events()
        if event.event_type == "storage-governor.recorded" and event.payload["record"] == "retirement-tombstone"
    ]
    assert [event["storage_class"] for event in tombstones] == ["ephemeral", "closed-incident-detail"]


def test_controlled_storage_proof_is_current_and_covers_all_fault_families():
    proof = load_controlled_proof()

    assert set(proof["retirement_crash_outcomes"]) == {
        "before-tombstone",
        "after-tombstone",
        "after-delete",
        "after-completion",
        "tombstone-before-append",
        "tombstone-after-append",
        "completion-before-append",
        "completion-after-append",
    }
    assert proof["pressure_dimensions"] == [
        "append",
        "bytes",
        "create",
        "durability",
        "filesystem_objects",
        "rename",
        "unlink",
    ]
    assert proof["fault_outcomes"] == {
        "alias-path": "refused-before-deletion",
        "cross-object-reservation": "refused-before-deletion",
        "durability": "replay-completed",
        "forged-classification": "refused-before-deletion",
        "metadata-unlink": "replay-completed",
        "protected-class-mislabel": "refused-before-deletion",
        "protected-or-unknown-class": "refused-at-canonical-boundary",
        "shared-reference": "refused-before-deletion",
    }
    assert proof["admission_observations"] == {
        "authority-ledger-pwrite-errno": [5, 28, 30, 69],
        "durability-failure": "no-admission-no-canonical-claim",
        "full-data-envelope": "canonical-authority-only-refusal",
        "nonborrowable-pools": ["shared", "terminal", "recovery"],
        "stop-floor-race": ["admitted", "refused"],
    }
    assert proof["retirement_capacity_observation"] == "released-after-completion"
