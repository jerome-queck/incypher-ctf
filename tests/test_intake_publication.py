"""Canonical lifecycle and atomic publication of Intake attempts."""

import datetime as dt
import json
from dataclasses import replace

import pytest
from solver.event_store import EventStore
from solver.intake_journal import IntakeJournal
from solver.intake_qualification import (
    IntakeAuthority,
    IntakeContract,
    IntakeDecision,
    IntakeDocument,
    IntakePass,
    IntakeProbe,
    IntakeSnapshot,
    PriorFence,
    qualify,
)
from solver.intake_receipt import link_manifest, verify_receipt, write_interrupted_receipt, write_receipt
from solver.manifest import generate_manifest
from solver.redaction import Redactor
from test_manifest import release_candidate_profile


PROFILE = "1" * 64
SUBJECT = "db187520f6abbe69fa69847b0f7167afbb6963a34d69647774f9f3f4291beb62"
NOW = dt.datetime(2026, 9, 22, 2, 30, tzinfo=dt.timezone.utc)


def contract():
    return IntakeContract(PROFILE, SUBJECT)


def snapshot(attempt_id):
    return IntakeSnapshot(f"intake-snapshot:{attempt_id}", PROFILE, NOW, (), "authenticated-empty")


def settled(attempt_id, prior):
    return IntakeDecision(attempt_id, True, "coherent", prior, snapshot(attempt_id), ())


def journal(tmp_path):
    return IntakeJournal(
        tmp_path,
        "run-1",
        Redactor({}),
        timestamp=lambda: NOW.isoformat(),
    )


def authority():
    return IntakeAuthority(PROFILE, "f" * 64, "4" * 64, "3" * 64)


def test_start_is_durable_before_observations_and_settled_terminal_advances_authority(tmp_path):
    intake = journal(tmp_path)
    prior = PriorFence.genesis(PROFILE)

    intake.start("attempt-1", contract(), prior)
    committed = intake.publish(settled("attempt-1", prior))

    assert committed.record == "settled"
    events = EventStore(tmp_path, run_id="run-1").events()
    assert [event.payload["record"] for event in events] == ["started", "settled"]
    assert events[0].sequence < events[1].sequence
    assert events[1].payload["snapshot_digest"] == snapshot("attempt-1").digest
    assert json.loads(events[1].body)["snapshot_id"] == "intake-snapshot:attempt-1"


def test_atomic_fence_conflict_closes_old_attempt_without_rebinding_its_decision(tmp_path):
    first = journal(tmp_path)
    second = journal(tmp_path)
    genesis = PriorFence.genesis(PROFILE)
    first.start("attempt-1", contract(), genesis)
    second.start("attempt-2", contract(), genesis)

    published = first.publish(settled("attempt-1", genesis))
    conflict = second.publish(settled("attempt-2", genesis))

    assert published.record == "settled"
    assert conflict.record == "fence-conflict"
    assert conflict.expected_fence == genesis
    assert conflict.observed_fence.event_id == "intake-decision:attempt-1:settled"
    assert conflict.observed_fence.event_digest
    assert json.loads(published.event.body)["snapshot_id"] == "intake-snapshot:attempt-1"


def test_restart_refuses_a_settled_snapshot_that_cannot_be_requalified(tmp_path):
    intake = journal(tmp_path)
    prior = PriorFence.genesis(PROFILE)
    intake.start("attempt-unproved", contract(), prior)
    intake.publish(settled("attempt-unproved", prior))

    with pytest.raises(ValueError, match="no authentication bracket"):
        journal(tmp_path).latest_snapshot()


def test_genesis_publication_compares_profile_as_part_of_the_prior_fence_under_lock(tmp_path):
    wrong_fence = PriorFence("2" * 64, "genesis", "", "")
    intake = journal(tmp_path)
    intake.start("attempt-wrong-fence", contract(), wrong_fence)

    conflict = intake.publish(settled("attempt-wrong-fence", wrong_fence))

    assert conflict.record == "fence-conflict"
    assert conflict.observed_fence == PriorFence.genesis(PROFILE)


@pytest.mark.parametrize("wrong_field", ["profile_digest", "event_id", "snapshot_digest"])
def test_publication_compares_every_non_genesis_fence_field_under_lock(tmp_path, wrong_field):
    intake = journal(tmp_path)
    genesis = PriorFence.genesis(PROFILE)
    intake.start("attempt-first", contract(), genesis)
    first = intake.publish(settled("attempt-first", genesis)).observed_fence
    values = first.document()
    values[wrong_field] = "2" * 64
    wrong = PriorFence(**values)
    intake.start("attempt-wrong-fence", contract(), wrong)

    conflict = intake.publish(settled("attempt-wrong-fence", wrong))

    assert conflict.record == "fence-conflict"
    assert conflict.observed_fence == first


def test_restart_terminalizes_started_attempt_before_a_new_attempt_can_start(tmp_path):
    before = journal(tmp_path)
    prior = PriorFence.genesis(PROFILE)
    before.start("attempt-orphan", contract(), prior)

    restarted = journal(tmp_path)
    closed = restarted.close_orphans()
    restarted.start("attempt-next", contract(), prior)

    assert [item.record for item in closed] == ["unsettled"]
    assert closed[0].reason == "interrupted-before-read"
    events = EventStore(tmp_path, run_id="run-1").events()
    assert [event.payload["record"] for event in events] == ["started", "unsettled", "started"]

    receipt = write_interrupted_receipt(tmp_path, "run-1", "attempt-orphan")
    assert verify_receipt(receipt) == receipt


def test_fence_conflict_receipt_binds_both_fences_and_the_qualified_proposal(tmp_path):
    intake = journal(tmp_path)
    genesis = PriorFence.genesis(PROFILE)
    probe = replace(_empty_probe(), attempt_id="attempt-conflict", prior_fence=genesis)
    decision = qualify(probe)
    intake.start(probe.attempt_id, probe.contract, genesis, authority())
    for observed in probe.passes:
        for document in observed.documents:
            intake.observe(probe.attempt_id, document)

    winner = journal(tmp_path)
    winner.start("attempt-winner", contract(), genesis)
    winner.publish(settled("attempt-winner", genesis))
    conflict = intake.publish(decision)

    path = write_receipt(
        tmp_path,
        "run-1",
        probe,
        decision,
        publication_record=conflict.record,
        observed_fence=conflict.observed_fence,
    )
    assert verify_receipt(path) == path
    receipt = json.loads(path.read_text())
    assert receipt["publication"] == {
        "record": "fence-conflict",
        "reason": "prior-fence-changed",
        "expected_fence": genesis.document(),
        "observed_fence": conflict.observed_fence.document(),
        "proposed_snapshot_digest": decision.snapshot.digest,
    }


def _empty_document(pass_no, edge):
    body = (
        {"success": True, "data": {"id": 17, "team_id": None}}
        if edge.startswith("identity")
        else {
            "success": True,
            "data": [],
            "meta": {"pagination": {"page": 1, "pages": 1, "total": 0, "next": None, "prev": None}},
        }
    )
    raw = json.dumps(body, separators=(",", ":")).encode()
    return IntakeDocument(
        f"request:{pass_no}:{edge}",
        f"broker:{pass_no}:{edge}",
        pass_no,
        "identity" if edge.startswith("identity") else "list",
        "/api/v1/users/me" if edge.startswith("identity") else "/api/v1/challenges?page=1",
        200,
        "application/json",
        raw,
        len(raw),
        True,
        PROFILE,
        SUBJECT,
        "3" * 64,
        "4" * 64,
        page=0 if edge.startswith("identity") else 1,
    )


def _empty_probe():
    passes = []
    for pass_no in (1, 2):
        before = _empty_document(pass_no, "identity-before")
        page = _empty_document(pass_no, "list")
        after = _empty_document(pass_no, "identity-after")
        passes.append(IntakePass(pass_no, before, (page,), (), (), after))
    return IntakeProbe("attempt-receipt", contract(), PriorFence.genesis(PROFILE), tuple(passes), NOW)


def test_receipt_rebuilds_probe_from_canonical_events_reruns_qualification_and_links_core_intake(tmp_path):
    probe = _empty_probe()
    decision = qualify(probe)
    intake = journal(tmp_path)
    intake.start(probe.attempt_id, probe.contract, probe.prior_fence, authority())
    for observed in probe.passes:
        for document in observed.documents:
            intake.observe(probe.attempt_id, document)
    intake.publish(decision)

    path = write_receipt(tmp_path, "run-1", probe, decision)
    verified = verify_receipt(path)
    manifest = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    linked = link_manifest(manifest, path)

    assert verified == path
    receipt = json.loads(path.read_text())
    assert receipt["receipt_type"] == "intake-snapshot"
    assert receipt["manifest_link"] == {"row_id": "core.intake", "receipt_ref": "receipt:intake-snapshot"}
    row = next(item for item in linked["requirements"] if item["row_id"] == "core.intake")
    assert row["receipt_ref"] == "receipt:intake-snapshot"


def test_receipt_tampering_cannot_change_a_page_or_settlement_verdict(tmp_path):
    probe = _empty_probe()
    decision = qualify(probe)
    intake = journal(tmp_path)
    intake.start(probe.attempt_id, probe.contract, probe.prior_fence, authority())
    for observed in probe.passes:
        for document in observed.documents:
            intake.observe(probe.attempt_id, document)
    intake.publish(decision)
    path = write_receipt(tmp_path, "run-1", probe, decision)
    supplied = json.loads(path.read_text())
    supplied["decision"]["reason"] = "invented"
    path.write_text(json.dumps(supplied, sort_keys=True, separators=(",", ":")) + "\n")

    try:
        verify_receipt(path)
    except ValueError as error:
        assert "decision" in str(error).lower() or "canonical" in str(error).lower()
    else:
        raise AssertionError("tampered receipt verified")


def test_unsettled_receipt_cannot_satisfy_the_candidate_manifest_row(tmp_path):
    coherent = _empty_probe()
    second = coherent.passes[1]
    failed_identity = replace(second.identity_after, status=401, outcome="auth-failure")
    probe = replace(coherent, passes=(coherent.passes[0], replace(second, identity_after=failed_identity)))
    decision = qualify(probe)
    intake = journal(tmp_path)
    intake.start(probe.attempt_id, probe.contract, probe.prior_fence, authority())
    for observed in probe.passes:
        for document in observed.documents:
            intake.observe(probe.attempt_id, document)
    intake.publish(decision)
    path = write_receipt(tmp_path, "run-1", probe, decision)
    manifest = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )

    with pytest.raises(ValueError, match="settled"):
        link_manifest(manifest, path)


def test_superseded_settled_receipt_cannot_satisfy_core_intake(tmp_path):
    first_probe = _empty_probe()
    first_decision = qualify(first_probe)
    intake = journal(tmp_path)
    intake.start(first_probe.attempt_id, first_probe.contract, first_probe.prior_fence, authority())
    for observed in first_probe.passes:
        for document in observed.documents:
            intake.observe(first_probe.attempt_id, document)
    first_publication = intake.publish(first_decision)
    first_receipt = write_receipt(tmp_path, "run-1", first_probe, first_decision)

    second_probe = replace(
        _empty_probe(),
        attempt_id="attempt-receipt-2",
        prior_fence=first_publication.observed_fence,
        prior_snapshot=first_decision.snapshot,
    )
    second_decision = qualify(second_probe)
    intake.start(second_probe.attempt_id, second_probe.contract, second_probe.prior_fence, authority())
    for observed in second_probe.passes:
        for document in observed.documents:
            intake.observe(second_probe.attempt_id, document)
    intake.publish(second_decision)
    write_receipt(tmp_path, "run-1", second_probe, second_decision)
    manifest = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )

    with pytest.raises(ValueError, match="latest canonical settled"):
        link_manifest(manifest, first_receipt)
