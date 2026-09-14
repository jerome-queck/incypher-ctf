"""Fixed Recovery changes its action before retry and proves it under probation."""

import json

import pytest

from solver.recovery.contracts import (
    ChangedAction,
    FaultKind,
    ProbeObservation,
    ProbationOutcome,
    RecoveryContext,
)
from solver.recovery.incident import Fault, IncidentEngine, verify_receipt
from solver.redaction import Redactor


class RecoveryPorts:
    def __init__(
        self,
        observation,
        probation=ProbationOutcome.PASSED,
        crash_probe=False,
        crash_remedy=False,
        crash_probation=False,
    ):
        self.observation = observation
        self.probation_outcome = probation
        self.crash_probe = crash_probe
        self.crash_remedy = crash_remedy
        self.crash_probation = crash_probation
        self.effects = []
        self.containment = []

    def fence(self, _fault):
        self.containment.append("fence")

    def evidence(self, _fault):
        self.containment.append("evidence")
        return b"bounded incident evidence"

    def teardown(self, _fault):
        self.containment.append("teardown")

    def replace(self, _fault):
        raise AssertionError("legacy replacement is not a deterministic Remedy")

    def probe(self, _fault, probe_id):
        if self.crash_probe:
            raise RuntimeError("injected crash during fixed probe")
        return self.observation

    def apply_remedy(self, _fault, remedy_id, changed_action):
        self.effects.append((remedy_id, changed_action))
        if self.crash_remedy:
            raise RuntimeError("injected crash after Remedy admission")
        return True

    def probation(self, _fault, remedy_id):
        if self.crash_probation:
            raise RuntimeError("injected crash during semantic probation")
        return self.probation_outcome


def process_fault():
    return Fault(
        "worker-exit:17",
        "owner-local:worker",
        "generation-7",
        "exit=17",
        FaultKind.WORKER_CRASH,
        RecoveryContext(
            original_deadline="2099-09-14T04:03:00Z",
            allowance=1,
            failed_action_value="generation-7",
        ),
    )


def test_unchanged_remedy_is_rejected_before_an_external_effect(tmp_path):
    ports = RecoveryPorts(
        ProbeObservation.settled(
            ChangedAction(
                dimension="process-generation",
                before="generation-7",
                after="generation-7",
                source="supervisor-process-table",
            )
        )
    )

    result = IncidentEngine(tmp_path, "run-1", ports, Redactor({})).report(process_fault())

    receipt = json.loads(result.receipt_path.read_text())
    assert ports.effects == []
    assert receipt["changed_action"]["accepted"] is False
    assert receipt["final_outcome"] == "contained"
    assert receipt["consumed_allowance"] == 0


def test_changed_remedy_with_failed_probation_is_not_resolved(tmp_path):
    changed = ChangedAction(
        dimension="process-generation",
        before="generation-7",
        after="generation-8",
        source="supervisor-process-table",
    )
    ports = RecoveryPorts(ProbeObservation.settled(changed), ProbationOutcome.FAILED)

    result = IncidentEngine(tmp_path, "run-1", ports, Redactor({})).report(process_fault())

    receipt = json.loads(result.receipt_path.read_text())
    assert ports.effects == [("replace-owned-process", changed)]
    assert receipt["changed_action"]["accepted"] is True
    assert receipt["probation_outcome"] == "failed"
    assert receipt["final_outcome"] == "contained"
    assert receipt["consumed_allowance"] == 1


def test_replay_does_not_repeat_a_remedy_that_may_have_launched(tmp_path):
    changed = ChangedAction(
        dimension="process-generation",
        before="generation-7",
        after="generation-8",
        source="supervisor-process-table",
    )
    crashing = RecoveryPorts(ProbeObservation.settled(changed), crash_remedy=True)
    try:
        IncidentEngine(tmp_path, "run-1", crashing, Redactor({})).report(process_fault())
    except RuntimeError as error:
        assert str(error) == "injected crash after Remedy admission"
    replay = RecoveryPorts(ProbeObservation.settled(changed))

    result = IncidentEngine(tmp_path, "run-1", replay, Redactor({})).replay()[0]

    receipt = json.loads(result.receipt_path.read_text())
    assert replay.effects == []
    assert receipt["final_outcome"] == "contained"
    assert receipt["original_deadline"] == "2099-09-14T04:03:00Z"
    assert receipt["consumed_allowance"] == 1


def test_replay_preserves_original_deadline_and_allowance(tmp_path):
    changed = ChangedAction(
        dimension="process-generation",
        before="generation-7",
        after="generation-8",
        source="supervisor-process-table",
    )
    crashing = RecoveryPorts(ProbeObservation.settled(changed), crash_probe=True)
    try:
        IncidentEngine(tmp_path, "run-1", crashing, Redactor({})).report(process_fault())
    except RuntimeError as error:
        assert str(error) == "injected crash during fixed probe"
    replay = RecoveryPorts(ProbeObservation.settled(changed))

    result = IncidentEngine(tmp_path, "run-1", replay, Redactor({})).replay()[0]

    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["original_deadline"] == "2099-09-14T04:03:00Z"
    assert receipt["allowance"] == 1
    assert receipt["consumed_allowance"] == 1
    assert receipt["probation_outcome"] == "passed"
    assert receipt["final_outcome"] == "resolved"
    assert verify_receipt(result.receipt_path) == result.receipt_path


@pytest.mark.parametrize(
    ("probation", "final_outcome"),
    [(ProbationOutcome.PASSED, "resolved"), (ProbationOutcome.FAILED, "contained")],
)
def test_probation_outcome_replays_without_repeating_the_remedy(tmp_path, probation, final_outcome):
    changed = ChangedAction(
        dimension="process-generation",
        before="generation-7",
        after="generation-8",
        source="supervisor-process-table",
    )
    crashing = RecoveryPorts(ProbeObservation.settled(changed), crash_probation=True)
    with pytest.raises(RuntimeError, match="semantic probation"):
        IncidentEngine(tmp_path, "run-1", crashing, Redactor({})).report(process_fault())
    replay = RecoveryPorts(ProbeObservation.settled(changed), probation)

    result = IncidentEngine(tmp_path, "run-1", replay, Redactor({})).replay()[0]

    receipt = json.loads(result.receipt_path.read_text())
    assert len(crashing.effects) == 1
    assert replay.effects == []
    assert receipt["probation_outcome"] == probation.value
    assert receipt["final_outcome"] == final_outcome
    assert receipt["consumed_allowance"] == 1


DOMAIN_FIXTURES = (
    (FaultKind.WORKER_CRASH, "owner-local:worker", "process-generation", "generation-7", "generation-8"),
    (
        FaultKind.ROUTE_LOCAL_INFERENCE,
        "owner-local:inference-native",
        "inference-route",
        "native-codex",
        "private-cpa",
    ),
    (FaultKind.TARGET_RESEARCH, "external:target", "request-attempt", "request-4", "request-5"),
    (FaultKind.INSTANCE, "external:instance", "instance-authority-join", "join-2", "join-3"),
    (
        FaultKind.SUBMISSION_AMBIGUITY,
        "run-shared:submission",
        "submission-epoch",
        "epoch-5",
        "epoch-6",
    ),
    (FaultKind.STORAGE, "run-shared:storage", "storage-revision", "revision-9", "revision-10"),
    (
        FaultKind.FINAL_INTERVAL,
        "run-shared:final-interval",
        "final-interval-phase",
        "draining",
        "reconciling",
    ),
)


@pytest.mark.parametrize(("kind", "scope", "dimension", "before", "after"), DOMAIN_FIXTURES)
def test_each_core_fault_class_uses_one_fixed_changed_remedy(tmp_path, kind, scope, dimension, before, after):
    changed = ChangedAction(dimension, before, after, "authoritative-domain-projection")
    ports = RecoveryPorts(ProbeObservation.settled(changed))
    fault = Fault(
        f"{kind.value}:fixture",
        scope,
        "generation-7",
        "controlled fault",
        kind,
        RecoveryContext("2099-09-14T04:03:00Z", 1, before),
    )

    result = IncidentEngine(tmp_path, "run-1", ports, Redactor({})).report(fault)

    receipt = json.loads(result.receipt_path.read_text())
    assert ports.containment == ["fence", "evidence", "teardown"]
    assert len(ports.effects) == 1
    assert receipt["catalogue_version"] == "deterministic-recovery-v1"
    assert receipt["changed_action"] == {**changed.document(accepted=True)}
    assert receipt["probation_outcome"] == "passed"
    assert receipt["final_outcome"] == "resolved"
    assert verify_receipt(result.receipt_path) == result.receipt_path


@pytest.mark.parametrize(("kind", "scope", "_dimension", "before", "_after"), DOMAIN_FIXTURES)
def test_unsettled_probe_preserves_fences_without_inferring_a_remedy(tmp_path, kind, scope, _dimension, before, _after):
    ports = RecoveryPorts(ProbeObservation.unsettled("authoritative read did not settle"))
    fault = Fault(
        f"{kind.value}:unsettled",
        scope,
        "generation-7",
        "controlled unsettled read",
        kind,
        RecoveryContext("2099-09-14T04:03:00Z", 1, before),
    )

    result = IncidentEngine(tmp_path, "run-1", ports, Redactor({})).report(fault)

    receipt = json.loads(result.receipt_path.read_text())
    assert ports.containment == ["fence", "evidence", "teardown"]
    assert ports.effects == []
    assert receipt["probe"]["outcome"] == "unsettled"
    assert receipt["authority_state"] == "aborted"
    assert receipt["consumed_allowance"] == 0
    assert receipt["final_outcome"] == ""
    assert receipt["disposition"] == "probation"
    assert verify_receipt(result.receipt_path) == result.receipt_path


def test_catalogue_rejects_a_remedy_outside_its_fault_scope(tmp_path):
    changed = ChangedAction(
        "submission-epoch",
        "epoch-5",
        "epoch-6",
        "authoritative-domain-projection",
    )
    ports = RecoveryPorts(ProbeObservation.settled(changed))
    fault = Fault(
        "submission:wrong-scope",
        "owner-local:attempt",
        "generation-7",
        "ambiguous POST",
        FaultKind.SUBMISSION_AMBIGUITY,
        RecoveryContext("2099-09-14T04:03:00Z", 1, "epoch-5"),
    )

    result = IncidentEngine(tmp_path, "run-1", ports, Redactor({})).report(fault)

    receipt = json.loads(result.receipt_path.read_text())
    assert ports.containment == []
    assert ports.effects == []
    assert receipt["authority_state"] == "aborted"
    assert receipt["final_outcome"] == "contained"
    assert verify_receipt(result.receipt_path) == result.receipt_path
