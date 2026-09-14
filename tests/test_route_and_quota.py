"""Shared quota and bounded inference-route policy."""

import json
import math
import datetime as dt

import pytest

from solver.route_and_quota import (
    FailureKind,
    InferenceRoute,
    QuotaObservation,
    RequestObservation,
    RouteAndQuotaController,
    RouteAndQuotaPolicy,
    RouteObservation,
    RouteTransportFailure,
    manifest_receipt,
    link_manifest,
    verify_receipt,
    write_receipt,
)
from solver.record import Recorder
from solver.redaction import Redactor
from solver.recovery.runtime import DeterministicRecovery


@pytest.mark.parametrize(
    ("route", "failure", "expected_route", "switches", "may_request"),
    [
        (InferenceRoute.NATIVE, FailureKind.ROUTE_LOCAL, InferenceRoute.CPA, 1, True),
        (InferenceRoute.CPA, FailureKind.ROUTE_LOCAL, InferenceRoute.NATIVE, 1, True),
        (InferenceRoute.NATIVE, FailureKind.SHARED_EXHAUSTION, InferenceRoute.NATIVE, 0, False),
        (InferenceRoute.NATIVE, FailureKind.UNKNOWN, InferenceRoute.NATIVE, 0, False),
        (InferenceRoute.NATIVE, FailureKind.AUTH, InferenceRoute.CPA, 1, True),
        (InferenceRoute.NATIVE, FailureKind.CANCELLATION, InferenceRoute.NATIVE, 0, False),
        (InferenceRoute.NATIVE, FailureKind.RECOVERY, InferenceRoute.NATIVE, 0, True),
    ],
)
def test_failure_matrix_only_switches_for_classified_route_local_failure(
    route, failure, expected_route, switches, may_request
):
    policy = RouteAndQuotaPolicy(primary=route)

    evidence = "classified-health-probe" if failure in {FailureKind.ROUTE_LOCAL, FailureKind.AUTH} else ""
    decision = policy.replay((RouteObservation(1, failure, route=route, evidence=evidence),))

    assert (decision.route, decision.switch_count, decision.may_request) == (
        expected_route,
        switches,
        may_request,
    )


def test_both_routes_report_one_shared_quota_projection_without_double_counting():
    policy = RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE)
    observations = (
        QuotaObservation(1, "snapshot-1", InferenceRoute.NATIVE, "codex", 98.0, "account/rateLimits/read"),
        QuotaObservation(2, "snapshot-1", InferenceRoute.CPA, "codex", 98.0, "account/rateLimits/read"),
        QuotaObservation(3, "snapshot-2", InferenceRoute.CPA, "codex", 100.0, "account/rateLimits/read"),
    )

    decision = policy.replay(observations)

    assert decision.quota.limits == (("codex", 100.0),)
    assert decision.quota.observation_ids == ("snapshot-1", "snapshot-2")
    assert decision.quota.provenance == ("account/rateLimits/read",)
    assert decision.may_request is False


def test_replay_fences_a_request_id_before_controller_dispatch():
    controller = RouteAndQuotaController(RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE))
    observations = (RequestObservation(1, "request-1", InferenceRoute.NATIVE),)

    first = controller.admit("request-2", observations)
    duplicate = controller.admit("request-1", observations)
    replayed = controller.admit("request-1", observations)

    assert (first.dispatch, first.route) == (True, InferenceRoute.NATIVE)
    assert duplicate.dispatch is False
    assert replayed == duplicate


def test_second_local_failure_cannot_switch_again_and_unproved_failure_never_switches():
    decision = RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE).replay(
        (
            RouteObservation(1, FailureKind.ROUTE_LOCAL, InferenceRoute.NATIVE, "native-probe"),
            RouteObservation(2, FailureKind.ROUTE_LOCAL, InferenceRoute.CPA, "cpa-probe"),
        )
    )
    unproved = RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE).replay(
        (RouteObservation(1, FailureKind.ROUTE_LOCAL, InferenceRoute.NATIVE),)
    )

    assert (decision.route, decision.switch_count, decision.may_request) == (InferenceRoute.CPA, 1, False)
    assert (unproved.route, unproved.switch_count, unproved.may_request) == (InferenceRoute.NATIVE, 0, False)


def test_route_and_quota_receipt_is_sanitized_recomputed_and_manifest_linkable(tmp_path):
    observations = (
        QuotaObservation(1, "snapshot-1", InferenceRoute.NATIVE, "codex", 98.0, "control"),
        RouteObservation(2, FailureKind.ROUTE_LOCAL, InferenceRoute.NATIVE, "secret diagnostic"),
        RequestObservation(3, "request-1", InferenceRoute.CPA),
    )
    policy = RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE)

    path = write_receipt(tmp_path, "run-1", policy, observations)
    document = json.loads(path.read_text())

    assert verify_receipt(path) == path
    assert "secret diagnostic" not in path.read_text()
    assert document["classifier_outputs"] == ["route-local"]
    assert document["switch"] == {"count": 1, "reason": "route-local"}
    assert document["request_ids"] == ["request-1"]
    assert manifest_receipt(path) == {
        "ref": "receipt:route-and-quota",
        "kind": "route-and-quota",
        "digest": document["receipt_digest"],
    }


def test_shared_exhaustion_waits_for_fresh_capacity_not_generic_recovery():
    policy = RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE)
    still_waiting = policy.replay(
        (
            RouteObservation(1, FailureKind.SHARED_EXHAUSTION),
            RouteObservation(2, FailureKind.RECOVERY),
        )
    )
    resumed = policy.replay(
        (
            RouteObservation(1, FailureKind.SHARED_EXHAUSTION),
            QuotaObservation(2, "snapshot-2", InferenceRoute.NATIVE, "codex", 70.0, "control"),
        )
    )

    assert still_waiting.may_request is False
    assert resumed.may_request is True


@pytest.mark.parametrize("used_percent", [math.nan, math.inf, -math.inf])
def test_quota_projection_rejects_non_finite_percentages(used_percent):
    policy = RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE)

    with pytest.raises(ValueError, match="observed range"):
        policy.replay((QuotaObservation(1, "snapshot-1", InferenceRoute.NATIVE, "codex", used_percent, "control"),))


def test_receipt_hashes_digest_prefixed_untrusted_evidence(tmp_path):
    path = write_receipt(
        tmp_path,
        "run-1",
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE),
        (RouteObservation(1, FailureKind.ROUTE_LOCAL, InferenceRoute.NATIVE, "digest:secret diagnostic"),),
    )

    assert "secret diagnostic" not in path.read_text()
    assert verify_receipt(path) == path


def test_fresh_partial_quota_does_not_clear_another_exhausted_limit():
    decision = RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE).replay(
        (
            QuotaObservation(1, "snapshot-1", InferenceRoute.NATIVE, "codex", 100.0, "control"),
            QuotaObservation(2, "snapshot-2", InferenceRoute.CPA, "model", 50.0, "control"),
        )
    )

    assert decision.may_request is False


def test_verified_receipt_links_controller_manifest_row(tmp_path):
    from solver.manifest import generate_manifest
    from test_manifest import release_candidate_profile

    receipt = write_receipt(tmp_path, "run-1", RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE), ())
    manifest = generate_manifest(
        release_candidate_profile=release_candidate_profile(), image_digest="sha256:" + "1" * 64
    )

    linked = link_manifest(manifest, receipt)

    row = next(item for item in linked["requirements"] if item["row_id"] == "core.adaptive-routing")
    assert row["receipt_ref"] == "receipt:route-and-quota"


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        ("timeout", FailureKind.ROUTE_LOCAL),
        ("auth-failure", FailureKind.AUTH),
        ("observed-exhaustion", FailureKind.SHARED_EXHAUSTION),
        ("cancelled", FailureKind.CANCELLATION),
        ("malformed-stream", FailureKind.UNKNOWN),
    ],
)
def test_classifier_maps_only_exact_transport_status(status, kind):
    assert RouteTransportFailure.classified(status, InferenceRoute.NATIVE, "a" * 64).kind is kind


def test_atomic_execute_reserves_before_transport_and_replays_without_duplicate(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    controller = RouteAndQuotaController(
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE), authority=recorder.write_authority
    )
    calls = []

    def native():
        calls.append("native")
        return {"text": "answer"}

    arguments = dict(
        request_id="attempt-1:turn:1",
        generation_id="generation-000001",
        payload_digest="b" * 64,
        observations=(),
        transports={InferenceRoute.NATIVE: native},
        encode=lambda result: result,
        decode=dict,
    )

    assert controller.execute(**arguments) == {"text": "answer"}
    assert controller.execute(**arguments) == {"text": "answer"}
    assert calls == ["native"]
    recorder.write_authority.close()


def test_execute_switches_once_for_typed_local_failure_but_never_shared(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    controller = RouteAndQuotaController(
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE), authority=recorder.write_authority
    )
    calls = []

    def native():
        calls.append("native")
        raise RouteTransportFailure.classified("timeout", InferenceRoute.NATIVE, "a" * 64)

    def cpa():
        calls.append("cpa")
        return {"text": "answer"}

    result = controller.execute(
        request_id="request-1",
        generation_id="generation-2",
        payload_digest="b" * 64,
        observations=(),
        transports={InferenceRoute.NATIVE: native, InferenceRoute.CPA: cpa},
        encode=lambda value: value,
        decode=dict,
    )

    assert result == {"text": "answer"}
    assert calls == ["native", "cpa"]
    recorder.write_authority.close()

    other = Recorder(tmp_path, "run-2", Redactor({}))
    shared = RouteAndQuotaController(
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE), authority=other.write_authority
    )
    calls.clear()

    def exhausted():
        calls.append("native")
        raise RouteTransportFailure.classified("observed-exhaustion", InferenceRoute.NATIVE, "c" * 64)

    with pytest.raises(RouteTransportFailure) as caught:
        shared.execute(
            request_id="request-2",
            generation_id="generation-2",
            payload_digest="d" * 64,
            observations=(),
            transports={InferenceRoute.NATIVE: exhausted, InferenceRoute.CPA: cpa},
            encode=lambda value: value,
            decode=dict,
        )
    assert caught.value.kind is FailureKind.SHARED_EXHAUSTION
    assert calls == ["native"]
    other.write_authority.close()


def test_production_route_switch_is_an_incident_authorized_changed_remedy(tmp_path):
    now = dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc)
    recorder = Recorder(tmp_path, "run-recovery", Redactor({}))
    recovery = DeterministicRecovery(
        tmp_path,
        "run-recovery",
        Redactor({}),
        now=lambda: now,
        authority=recorder.write_authority,
    )
    controller = RouteAndQuotaController(
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE),
        authority=recorder.write_authority,
        recovery=recovery,
        now=lambda: now,
    )
    calls = []

    def native():
        calls.append("native")
        raise RouteTransportFailure.classified("timeout", InferenceRoute.NATIVE, "a" * 64)

    def cpa():
        calls.append("cpa")
        return {"text": "answer"}

    result = controller.execute(
        request_id="request-1",
        generation_id="generation-2",
        payload_digest="b" * 64,
        observations=(),
        transports={InferenceRoute.NATIVE: native, InferenceRoute.CPA: cpa},
        encode=lambda value: value,
        decode=dict,
    )

    receipt = json.loads((tmp_path / "runs/run-recovery/canonical/incident-containment.receipt.json").read_text())
    assert result == {"text": "answer"}
    assert calls == ["native", "cpa"]
    assert receipt["fault_kind"] == "route-local-inference"
    assert receipt["changed_action"]["before"] == "native-codex"
    assert receipt["changed_action"]["after"] == "private-cpa"
    assert receipt["final_outcome"] == "resolved"
    recorder.write_authority.close()


def test_durable_failure_history_survives_controller_recreation_and_bounds_switch(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    calls = []
    controller = RouteAndQuotaController(
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE), authority=recorder.write_authority
    )

    def native():
        calls.append("native")
        raise RouteTransportFailure.classified("auth-failure", InferenceRoute.NATIVE, "a" * 64)

    def cpa():
        calls.append("cpa")
        return {"said": [], "stream": [], "quota": []}

    controller.execute(
        request_id="request-1",
        generation_id="generation-1",
        payload_digest="b" * 64,
        observations=(),
        transports={InferenceRoute.NATIVE: native, InferenceRoute.CPA: cpa},
        encode=lambda value: value,
        decode=dict,
    )
    restarted = RouteAndQuotaController(
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE), authority=recorder.write_authority
    )
    restarted.execute(
        request_id="request-2",
        generation_id="generation-2",
        payload_digest="c" * 64,
        observations=(),
        transports={InferenceRoute.NATIVE: native, InferenceRoute.CPA: cpa},
        encode=lambda value: value,
        decode=dict,
    )

    assert calls == ["native", "cpa", "cpa"]
    assert FailureKind.AUTH in {item.kind for item in restarted.observations() if isinstance(item, RouteObservation)}
    recorder.write_authority.close()


def test_durable_quota_result_pauses_later_requests_on_either_route(tmp_path):
    recorder = Recorder(tmp_path, "run-1", Redactor({}))
    controller = RouteAndQuotaController(
        RouteAndQuotaPolicy(primary=InferenceRoute.NATIVE), authority=recorder.write_authority
    )
    exhausted = {
        "said": [],
        "stream": [],
        "quota": [
            {
                "observation_id": "snapshot-1",
                "route": "native-codex",
                "limit_id": "codex",
                "used_percent": 100.0,
                "source": "codex-control",
            }
        ],
    }
    controller.execute(
        request_id="request-1",
        generation_id="generation-1",
        payload_digest="b" * 64,
        observations=(),
        transports={InferenceRoute.NATIVE: lambda: exhausted},
        encode=lambda value: value,
        decode=dict,
    )

    with pytest.raises(RuntimeError, match="policy-paused"):
        controller.execute(
            request_id="request-2",
            generation_id="generation-1",
            payload_digest="c" * 64,
            observations=(),
            transports={InferenceRoute.NATIVE: lambda: exhausted, InferenceRoute.CPA: lambda: {}},
            encode=lambda value: value,
            decode=dict,
        )
    recovered = controller.execute(
        request_id="request-3",
        generation_id="generation-3",
        payload_digest="d" * 64,
        observations=(QuotaObservation(1, "snapshot-2", InferenceRoute.NATIVE, "codex", 0.0, "codex-control"),),
        transports={InferenceRoute.NATIVE: lambda: {"said": ["recovered"], "stream": [], "quota": []}},
        encode=lambda value: value,
        decode=dict,
    )
    assert recovered["said"] == ["recovered"]
    recorder.write_authority.close()
