"""Public-contract qualification of one coherent Board profile."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from solver.board_profile import (
    ProfileCycle,
    ProfileDocument,
    ProfileProbe,
    profile_cycle_documents,
    qualify,
    rules_document,
)
from solver.board_profile_contracts import BoardProfileObservationRecorded
from solver.board_profile_receipt import link_manifest, verify_receipt, write_receipt
from solver.board_profile_phase import ProfilePhaseWriter
from solver.event_store import EventStore
from solver.event_store_storage import canonical_bytes
from solver.redaction import Redactor
from solver.manifest import generate_manifest
from solver.profile import UNREADABLE, Rules

from test_manifest import release_candidate_profile


RULES = Rules(
    event="fixture",
    url="https://board.example",
    flag_wrappers=(r"flag\{[^}]+\}",),
    window_seconds=19_800,
    prohibitions=("do not brute-force flags",),
)


def document(
    request_id: str,
    endpoint: str,
    body: bytes,
    *,
    status: int = 200,
    content_type: str = "application/json; charset=utf-8",
) -> ProfileDocument:
    return ProfileDocument(request_id, endpoint, status, content_type, body)


def json_document(request_id: str, endpoint: str, data, *, success: bool = True) -> ProfileDocument:
    return document(request_id, endpoint, json.dumps({"success": success, "data": data}).encode())


def valid_cycle(prefix: str = "first") -> ProfileCycle:
    landing = b'<html><script>window.init = {"userId": 7, "teamId": 3, "userMode": "teams"};</script></html>'
    ledger = (
        b'<html><script>window.init = {"userId": 7, "teamId": 3, "userMode": "teams"};</script>'
        b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table></html>"
    )
    return ProfileCycle(
        identity=json_document(prefix + "-identity", "/api/v1/users/me", {"id": 7, "team_id": 3}),
        landing=document(prefix + "-landing", "/", landing, content_type="text/html; charset=utf-8"),
        read_contract=document(
            prefix + "-control",
            "/api/v1/challenges/0",
            b'{"message":"Challenge not found"}',
            status=404,
        ),
        challenges=json_document(
            prefix + "-challenges",
            "/api/v1/challenges",
            [{"id": 1, "name": "alpha", "type": "standard"}],
        ),
        ledger=document(prefix + "-ledger", "/plugins/ctfd-chall-manager/instances", ledger, content_type="text/html"),
        mana=json_document(prefix + "-mana", "/api/v1/plugins/ctfd-chall-manager/mana", {"used": 0, "total": 0}),
        configs=json_document(prefix + "-configs", "/api/v1/configs", []),
        anonymous_challenges=document(
            prefix + "-anonymous",
            "/api/v1/challenges",
            b'{"success":true,"data":[]}',
        ),
        challenge_details=(
            json_document(
                prefix + "-detail-1",
                "/api/v1/challenges/1",
                {"id": 1, "name": "alpha", "type": "standard", "category": "misc"},
            ),
        ),
    )


def probe(*cycles: ProfileCycle) -> ProfileProbe:
    return ProfileProbe("profile-probe-000001", "docs/competitions/fixture.board.json", tuple(cycles))


def record_probe(state: Path, observed: ProfileProbe, run_id: str = "run-1") -> None:
    ProfilePhaseWriter(state, run_id, Redactor({}), lambda: "2026-09-12T00:00:00Z").start(
        observed.probe_id,
        hashlib.sha256(canonical_bytes(rules_document(RULES))).hexdigest(),
        "fixture-peer",
    )
    store = EventStore(state, run_id=run_id)
    for cycle_index, cycle in enumerate(observed.cycles, start=1):
        for name, profile_document in profile_cycle_documents(cycle):
            store.append(
                BoardProfileObservationRecorded(
                    event_id=f"board-profile-observation:{observed.probe_id}:{cycle_index}:{name}",
                    probe_id=observed.probe_id,
                    cycle=cycle_index,
                    document_name=name,
                    request_id=profile_document.request_id,
                    endpoint=profile_document.endpoint,
                    http_status=profile_document.status,
                    content_type=profile_document.content_type,
                    original_bytes=profile_document.original_bytes,
                    complete=profile_document.complete,
                    ts="2026-09-12T00:00:00Z",
                ),
                body=profile_document.body,
            )


def decide_probe(state: Path, observed: ProfileProbe, path: Path, decision, run_id: str = "run-1") -> None:
    ProfilePhaseWriter(state, run_id, Redactor({}), lambda: "2026-09-12T00:00:00Z").decide(
        observed.probe_id,
        hashlib.sha256(canonical_bytes(rules_document(RULES))).hexdigest(),
        decision.authoritative,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def test_genuine_json_contracts_qualify_but_same_status_landing_catch_all_does_not() -> None:
    first = valid_cycle("first")
    second = valid_cycle("second")

    accepted = qualify(probe(first, second), RULES)

    assert accepted.authoritative is True
    assert accepted.profile is not None

    catch_all = ProfileCycle(
        **{
            **vars(second),
            "challenges": document(
                "second-challenges",
                "/api/v1/challenges",
                second.landing.body,
                content_type="text/html; charset=utf-8",
            ),
        }
    )
    refused = qualify(probe(first, catch_all), RULES)

    assert refused.authoritative is False
    assert refused.profile is None
    assert "landing-page catch-all" in refused.reason


def test_coherence_compares_profile_semantics_not_volatile_board_bytes() -> None:
    first = valid_cycle("first")
    second = valid_cycle("second")
    second_challenges = json_document(
        "second-challenges",
        "/api/v1/challenges",
        [{"type": "standard", "name": "alpha", "id": 1, "solves": 19}],
    )
    second = ProfileCycle(**{**vars(second), "challenges": second_challenges})

    decision = qualify(probe(first, second), RULES)

    assert decision.authoritative is True


def test_identity_change_between_cycles_never_acquires_authority() -> None:
    first = valid_cycle("first")
    second = valid_cycle("second")
    second = ProfileCycle(
        **{
            **vars(second),
            "identity": json_document("second-identity", "/api/v1/users/me", {"id": 8, "team_id": 3}),
        }
    )

    decision = qualify(probe(first, second), RULES)

    assert decision.authoritative is False
    assert "landing identity disagrees" in decision.reason


def test_mixed_profile_semantics_between_cycles_never_acquires_authority() -> None:
    first = valid_cycle("first")
    second = valid_cycle("second")
    second = ProfileCycle(
        **{
            **vars(second),
            "configs": json_document(
                "second-configs",
                "/api/v1/configs",
                [{"key": "incorrect_submissions_per_min", "value": 4}],
            ),
        }
    )

    decision = qualify(probe(first, second), RULES)

    assert decision.authoritative is False
    assert "changed between probe cycles" in decision.reason


def test_json_body_with_the_wrong_content_type_never_acquires_authority() -> None:
    first = valid_cycle("first")
    second = valid_cycle("second")
    second = ProfileCycle(
        **{
            **vars(second),
            "challenges": document(
                "second-challenges",
                "/api/v1/challenges",
                second.challenges.body,
                content_type="text/html",
            ),
        }
    )

    decision = qualify(probe(first, second), RULES)

    assert decision.authoritative is False
    assert "not a genuine JSON contract" in decision.reason


def test_generic_html_error_cannot_satisfy_the_known_absent_challenge_control() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(
            ProfileCycle(
                **{
                    **vars(cycle),
                    "read_contract": document(
                        prefix + "-control",
                        "/api/v1/challenges/0",
                        b"<html>temporarily unavailable</html>",
                        status=503,
                        content_type="text/html",
                    ),
                }
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "not a genuine negative contract" in decision.reason


def test_synthetic_empty_authenticated_collection_never_means_empty_board() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(replace(cycle, challenges=cycle.anonymous_challenges, challenge_details=()))

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "synthetic empty" in decision.reason


def test_instance_ledger_login_redirect_refuses_even_when_current_challenges_are_static() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(
            replace(
                cycle,
                ledger=document(
                    prefix + "-ledger",
                    "/plugins/ctfd-chall-manager/instances",
                    b"<html>login required</html>",
                    status=302,
                    content_type="text/html",
                ),
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "Instance ledger redirected" in decision.reason


def test_authenticated_empty_collection_with_distinct_refusal_and_negative_control_qualifies() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(
            replace(
                cycle,
                challenges=json_document(prefix + "-challenges", "/api/v1/challenges", []),
                anonymous_challenges=document(
                    prefix + "-anonymous",
                    "/api/v1/challenges",
                    b"authentication required",
                    status=403,
                    content_type="text/html",
                ),
                challenge_details=(),
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is True
    assert decision.profile is not None
    assert decision.profile.instanced_challenges == 0


def test_team_mode_without_a_complete_positive_team_identity_refuses_authority() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        partial = b'<script>window.init = {"userId": 7, "teamId": null, "userMode": "teams"};</script>'
        cycles.append(
            replace(
                cycle,
                identity=json_document(prefix + "-identity", "/api/v1/users/me", {"id": 7, "team_id": None}),
                landing=document(prefix + "-landing", "/", partial, content_type="text/html"),
                ledger=document(
                    prefix + "-ledger",
                    "/plugins/ctfd-chall-manager/instances",
                    partial + b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>",
                    content_type="text/html",
                ),
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "team identity is partial" in decision.reason


@pytest.mark.parametrize("user_id", (0, -1, True))
def test_authenticated_identity_requires_a_positive_integer_user_id(user_id) -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        landing = (
            f'<script>window.init = {{"userId": {json.dumps(user_id)}, "teamId": 3, "userMode": "teams"}};</script>'
        ).encode()
        cycles.append(
            replace(
                cycle,
                identity=json_document(prefix + "-identity", "/api/v1/users/me", {"id": user_id, "team_id": 3}),
                landing=document(prefix + "-landing", "/", landing, content_type="text/html"),
                ledger=document(
                    prefix + "-ledger",
                    "/plugins/ctfd-chall-manager/instances",
                    landing + b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>",
                    content_type="text/html",
                ),
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "identity" in decision.reason


def test_malformed_anonymous_collection_cannot_corroborate_an_authenticated_empty_board() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(
            replace(
                cycle,
                challenges=json_document(prefix + "-challenges", "/api/v1/challenges", []),
                anonymous_challenges=json_document(prefix + "-anonymous", "/api/v1/challenges", [{}]),
                challenge_details=(),
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "anonymous Challenge list is malformed" in decision.reason


def test_authenticated_collection_must_differ_from_anonymous_collection() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(replace(cycle, anonymous_challenges=cycle.challenges))

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "authenticated and anonymous Challenge lists agree" in decision.reason


def test_each_listed_challenge_needs_a_matching_detail_contract() -> None:
    cycles = [replace(valid_cycle(prefix), challenge_details=()) for prefix in ("first", "second")]

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "detail set does not corroborate" in decision.reason


def test_detail_open_strings_are_compared_across_cycles() -> None:
    first = valid_cycle("first")
    second = valid_cycle("second")
    changed = json_document(
        "second-detail-1",
        "/api/v1/challenges/1",
        {"id": 1, "name": "alpha", "type": "practice_new_type", "category": "practice_new_category"},
    )
    second = replace(second, challenge_details=(changed,))

    decision = qualify(probe(first, second), RULES)

    assert decision.authoritative is False
    assert "detail disagrees with its collection row" in decision.reason


def test_known_absence_control_cannot_be_a_positive_contract_catch_all() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(replace(cycle, read_contract=cycle.challenge_details[0]))

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "known-absent Challenge control" in decision.reason


def test_authenticated_generic_html_is_not_an_instance_ledger() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        challenges = json_document(
            prefix + "-challenges",
            "/api/v1/challenges",
            [{"id": 1, "name": "alpha", "type": "dynamic_iac"}],
        )
        details = (
            json_document(
                prefix + "-detail-1",
                "/api/v1/challenges/1",
                {"id": 1, "name": "alpha", "type": "dynamic_iac", "category": "misc"},
            ),
        )
        cycles.append(
            ProfileCycle(
                **{
                    **vars(cycle),
                    "challenges": challenges,
                    "challenge_details": details,
                    "ledger": document(
                        prefix + "-ledger",
                        "/plugins/ctfd-chall-manager/instances",
                        cycle.landing.body + b"<p>authenticated error</p>",
                        content_type="text/html",
                    ),
                }
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert "no compatible Instance ledger" in decision.reason


def test_configs_refusal_and_landing_window_are_explicit_and_source_preserving() -> None:
    landing = (
        b"<script>window.init = {'userId': 7, 'teamId': 3, 'userMode': 'teams', "
        b"'start': 1782835200, 'end': 1790006400, 'nested': {'volatile': 1}};</script>"
    )
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        ledger = landing + b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>"
        cycles.append(
            ProfileCycle(
                **{
                    **vars(cycle),
                    "landing": document(prefix + "-landing", "/", landing, content_type="text/html"),
                    "ledger": document(
                        prefix + "-ledger",
                        "/plugins/ctfd-chall-manager/instances",
                        ledger,
                        content_type="text/html",
                    ),
                    "configs": document(
                        prefix + "-configs",
                        "/api/v1/configs",
                        b'{"message":"Forbidden"}',
                        status=403,
                    ),
                }
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is True
    assert decision.profile is not None
    assert decision.profile.configs_outcome == "refused"
    assert decision.profile.submissions_per_minute_source == "assumed"
    assert decision.profile.board_window == {"start": 1782835200, "end": 1790006400}
    assert decision.profile.board_window_observations == {
        "configs": {"outcome": "refused", "value": {}},
        "landing": {"outcome": "published", "value": {"start": 1782835200, "end": 1790006400}},
        "disagrees": False,
    }


def test_config_and_landing_window_disagreement_refuses_authority() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        landing = (
            b'<script>window.init = {"userId": 7, "teamId": 3, "userMode": "teams", "start": 100, "end": 200};</script>'
        )
        ledger = landing + b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>"
        cycles.append(
            ProfileCycle(
                **{
                    **vars(cycle),
                    "landing": document(prefix + "-landing", "/", landing, content_type="text/html"),
                    "ledger": document(
                        prefix + "-ledger",
                        "/plugins/ctfd-chall-manager/instances",
                        ledger,
                        content_type="text/html",
                    ),
                    "configs": json_document(
                        prefix + "-configs",
                        "/api/v1/configs",
                        [{"key": "start", "value": 101}, {"key": "end", "value": 201}],
                    ),
                }
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert decision.profile is None
    assert decision.reason == "Board window observations disagree"


@pytest.mark.parametrize(
    ("landing_window", "config_window"),
    [
        ("", [("start", 100)]),
        ("", [("start", None), ("end", None)]),
        (', "start": 100', []),
    ],
)
def test_partial_or_explicitly_null_window_refuses_authority(landing_window, config_window) -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        landing = (
            b'<script>window.init = {"userId": 7, "teamId": 3, "userMode": "teams"'
            + landing_window.encode()
            + b"};</script>"
        )
        ledger = landing + b"<table><thead><tr><th>Challenge</th></tr></thead><tbody></tbody></table>"
        cycles.append(
            ProfileCycle(
                **{
                    **vars(cycle),
                    "landing": document(prefix + "-landing", "/", landing, content_type="text/html"),
                    "ledger": document(
                        prefix + "-ledger",
                        "/plugins/ctfd-chall-manager/instances",
                        ledger,
                        content_type="text/html",
                    ),
                    "configs": json_document(
                        prefix + "-configs",
                        "/api/v1/configs",
                        [{"key": key, "value": value} for key, value in config_window],
                    ),
                }
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is False
    assert decision.profile is None
    assert decision.reason == "Board window observation is partial"


def test_refused_window_receipt_marks_board_window_unsettled(tmp_path: Path) -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(
            ProfileCycle(
                **{
                    **vars(cycle),
                    "configs": json_document(
                        prefix + "-configs",
                        "/api/v1/configs",
                        [{"key": "start", "value": 100}],
                    ),
                }
            )
        )
    observed = probe(*cycles)
    decision = qualify(observed, RULES)
    record_probe(tmp_path, observed)
    path = write_receipt(tmp_path, "run-1", observed, RULES, decision)
    decide_probe(tmp_path, observed, path, decision)

    receipt = json.loads(verify_receipt(path).read_text())

    assert receipt["assessment"]["unsettled_fields"] == ["board_window"]


@pytest.mark.parametrize(
    ("failure", "expected_unsettled"),
    [
        (
            "auth",
            [
                "authenticated_identity",
                "read_contract",
                "instanced_challenges",
                "chall_manager",
                "mana",
                "submissions_per_minute",
                "configs_outcome",
                "board_window",
            ],
        ),
        ("catch-all", ["instanced_challenges"]),
        ("mixed", ["instanced_challenges"]),
        ("truncated", ["instanced_challenges"]),
    ],
)
def test_refused_receipt_names_the_fields_its_evidence_did_not_settle(
    tmp_path: Path, failure: str, expected_unsettled: list[str]
) -> None:
    cycles = [valid_cycle("first"), valid_cycle("second")]
    if failure == "auth":
        for index, cycle in enumerate(cycles):
            cycles[index] = replace(
                cycle,
                identity=json_document(cycle.identity.request_id, cycle.identity.endpoint, {"id": 8, "team_id": 3}),
            )
    elif failure == "catch-all":
        for index, cycle in enumerate(cycles):
            cycles[index] = replace(
                cycle,
                challenges=document(
                    cycle.challenges.request_id,
                    cycle.challenges.endpoint,
                    cycle.landing.body,
                    content_type="text/html",
                ),
            )
    elif failure == "mixed":
        cycle = cycles[1]
        cycles[1] = replace(
            cycle,
            challenges=json_document(
                cycle.challenges.request_id,
                cycle.challenges.endpoint,
                [{"id": 2, "name": "changed", "type": "standard"}],
            ),
        )
    else:
        cycle = cycles[0]
        cycles[0] = replace(
            cycle,
            challenges=replace(
                cycle.challenges,
                original_bytes=cycle.challenges.original_bytes + 1,
                complete=False,
            ),
        )
    observed = probe(*cycles)
    decision = qualify(observed, RULES)
    record_probe(tmp_path, observed)
    path = write_receipt(tmp_path, "run-1", observed, RULES, decision)
    decide_probe(tmp_path, observed, path, decision)

    receipt = json.loads(verify_receipt(path).read_text())

    assert decision.authoritative is False
    assert receipt["assessment"]["unsettled_fields"] == expected_unsettled


def test_unreadable_optional_anonymous_probe_remains_explicit() -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(
            ProfileCycle(
                **{
                    **vars(cycle),
                    "anonymous_challenges": document(
                        prefix + "-anonymous",
                        "/api/v1/challenges",
                        b"",
                        status=0,
                        content_type="",
                    ),
                }
            )
        )

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is True
    assert decision.profile is not None
    assert decision.profile.unauthenticated_read == "unreadable"


def test_receipt_independently_recomputes_the_combined_rules_and_wire_profile(tmp_path: Path) -> None:
    observed = probe(valid_cycle("first"), valid_cycle("second"))
    decision = qualify(observed, RULES)
    record_probe(tmp_path, observed)

    path = write_receipt(tmp_path, "run-1", observed, RULES, decision)
    decide_probe(tmp_path, observed, path, decision)
    receipt = json.loads(verify_receipt(path).read_text())

    assert receipt["receipt_type"] == "board-profile"
    assert receipt["rules"]["source"] == "docs/competitions/fixture.board.json"
    assert receipt["rules"]["digest"]
    assert receipt["probe"]["cycles"][0]["documents"]["challenges"]["body_digest"]
    assert receipt["decision"]["authoritative"] is True
    assert receipt["acceptance_scope"] == "public-contract-double"
    assert receipt["external_evidence"] == "non-acceptance-boundary"
    assert receipt["assessment"]["snapshot_identity"] == "profile-probe-000001"
    assert receipt["assessment"]["compatibility_verdict"] == "compatible"
    assert receipt["assessment"]["catch_all_comparisons"]["first:challenges"] == "distinct"
    assert receipt["assessment"]["field_provenance"]["instanced_challenges"] == [
        "first-challenges",
        "first-detail-1",
        "second-challenges",
        "second-detail-1",
    ]
    assert receipt["assessment"]["unsettled_fields"] == []
    assert receipt["manifest_link"] == {
        "row_id": "core.board-target-lease",
        "receipt_ref": "receipt:board-profile",
    }

    receipt["rules"]["document"]["prohibitions"] = []
    path.write_text(json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n")

    with pytest.raises(ValueError, match="rules digest"):
        verify_receipt(path)


def test_unknown_optional_instance_feature_stays_unreadable_when_no_challenge_needs_it(tmp_path: Path) -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        cycles.append(
            ProfileCycle(
                **{
                    **vars(cycle),
                    "ledger": document(
                        prefix + "-ledger",
                        "/plugins/ctfd-chall-manager/instances",
                        b"temporarily unavailable",
                        status=503,
                        content_type="text/plain",
                    ),
                    "mana": document(
                        prefix + "-mana",
                        "/api/v1/plugins/ctfd-chall-manager/mana",
                        b"temporarily unavailable",
                        status=503,
                        content_type="text/plain",
                    ),
                }
            )
        )

    observed = probe(*cycles)
    decision = qualify(observed, RULES)

    assert decision.authoritative is True
    assert decision.profile is not None
    assert decision.profile.chall_manager == UNREADABLE
    assert decision.profile.mana is None
    assert decision.profile.mana_outcome == UNREADABLE

    record_probe(tmp_path, observed)
    path = write_receipt(tmp_path, "run-1", observed, RULES, decision)
    decide_probe(tmp_path, observed, path, decision)
    receipt = json.loads(verify_receipt(path).read_text())
    assert receipt["assessment"]["unsettled_fields"] == ["chall_manager", "mana"]


@pytest.mark.parametrize(("mana_data", "expected_outcome"), [(None, "absent"), ({"used": 2, "total": 0}, "answered")])
def test_mana_absence_and_answered_disabled_are_distinct(mana_data, expected_outcome) -> None:
    cycles = []
    for prefix in ("first", "second"):
        cycle = valid_cycle(prefix)
        mana = (
            document(
                prefix + "-mana",
                "/api/v1/plugins/ctfd-chall-manager/mana",
                b'{"success":false}',
                status=404,
            )
            if mana_data is None
            else json_document(prefix + "-mana", "/api/v1/plugins/ctfd-chall-manager/mana", mana_data)
        )
        cycles.append(replace(cycle, mana=mana))

    decision = qualify(probe(*cycles), RULES)

    assert decision.authoritative is True
    assert decision.profile is not None
    assert decision.profile.mana_outcome == expected_outcome
    assert (decision.profile.mana is None) is (expected_outcome == "absent")
    if decision.profile.mana is not None:
        assert decision.profile.mana.total == 0


def test_verified_profile_receipt_links_the_board_lifecycle_manifest_row(tmp_path: Path) -> None:
    observed = probe(valid_cycle("first"), valid_cycle("second"))
    record_probe(tmp_path, observed)
    path = write_receipt(tmp_path, "run-1", observed, RULES, qualify(observed, RULES))
    decide_probe(tmp_path, observed, path, qualify(observed, RULES))
    manifest = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )

    linked = link_manifest(manifest, path)

    row = next(item for item in linked["requirements"] if item["row_id"] == "core.board-target-lease")
    assert row["receipt_ref"] == "receipt:board-profile"
