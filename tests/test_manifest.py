"""Release-candidate manifest: one honest, deterministic draft boundary."""

import copy
import hashlib
import json

import pytest

from solver.manifest import (
    CORE_REQUIREMENT_IDS,
    DRAFT_MANIFEST_KIND,
    MANIFEST_SCHEMA_RECEIPT_DIGEST,
    PACK_REQUIREMENT_IDS,
    ManifestValidationError,
    ReleaseCandidateManifestDraft,
    canonical_manifest_bytes,
    generate_manifest,
    manifest_digest,
    parse_manifest,
    validate_manifest,
)
from solver.cpa_contracts import CPAConfig, CPAHarnessRecorded
from solver.cpa_receipt import attach_cpa_receipt, build_cpa_receipt, config_digest, manifest_receipt
from solver.event_store import EventStore
from solver.redaction import Redactor


def bind_profile_digest(profile: dict[str, object]) -> dict[str, object]:
    profile.pop("profile_digest", None)
    profile["profile_digest"] = hashlib.sha256(
        json.dumps(profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return profile


def storage_reservation(multiplier: int) -> dict[str, object]:
    return {
        "bytes": 1000 * multiplier,
        "filesystem_objects": 10 * multiplier,
        "operations": {
            "create": multiplier,
            "append": multiplier,
            "rename": multiplier,
            "unlink": multiplier,
            "durability": multiplier,
        },
    }


def release_candidate_profile() -> dict[str, object]:
    return bind_profile_digest(
        {
            "primary_route": "native-codex",
            "alternate_route": "private-cpa",
            "route_change_policy": "classified-route-local-failure-only",
            "lanes": 1,
            "specialists": 0,
            "model": {"requested": "gpt-5", "effective": "gpt-5", "effort": "medium"},
            "prompt_bundle": "prompt-v1",
            "tool_policy": "tool-floor-v1",
            "decision_policies": {
                "triage": "triage-v1",
                "order": "order-v1",
                "tier": "tier-v1",
                "cut": "cut-v1",
                "submission": "submission-v1",
            },
            "resources": {"cpu_limit": 6, "memory_bytes": 16 * 1024**3, "pid_limit": 2048},
            "storage": {
                "writable_envelope": storage_reservation(100),
                "host_filesystem_safety_floor": storage_reservation(1),
                "pressure_thresholds": {
                    "soft_remaining": storage_reservation(40),
                    "hard_remaining": storage_reservation(20),
                },
                "ordinary_producer_envelopes": [
                    {"producer": "canonical-record", "reservation": storage_reservation(2)},
                    {"producer": "evidence-blob", "reservation": storage_reservation(4)},
                ],
                "authority_effect_envelopes": [
                    {"transaction": "reconciliation", "reservation": storage_reservation(3)},
                    {"transaction": "submission", "reservation": storage_reservation(5)},
                ],
                "maximum_authority_effect_reservation": storage_reservation(5),
                "maximum_concurrent_authority_set": [
                    {"transaction": "reconciliation", "maximum_concurrent": 1},
                    {"transaction": "submission", "maximum_concurrent": 2},
                ],
                "shared_authority_pool": storage_reservation(13),
                "terminal_floor": storage_reservation(2),
                "recovery_floor": storage_reservation(3),
                "incident_evidence_limits": {
                    "per_incident": storage_reservation(2),
                    "aggregate": storage_reservation(8),
                },
            },
            "isolation": {"profile_id": "strict-v1", "profile_digest": "d" * 64},
            "enabled_packs": [],
        }
    )


def test_cpa_receipt_links_the_actual_candidate_manifest_row(tmp_path):
    config = CPAConfig(2, 1, ("inspect",))
    store = EventStore(tmp_path, run_id="run-1", redactor=Redactor({}))
    store.append(
        CPAHarnessRecorded(
            event_id="cpa:000001",
            request_id="probe",
            record="probe",
            status="refused",
            config_digest=config_digest(config),
            peer_digest="a" * 64,
            detail="capability refused",
            ts="2026-09-12T00:00:00Z",
        ),
        body=b"",
    )
    receipt = build_cpa_receipt(store, config)
    with pytest.raises(TypeError):
        manifest_receipt(receipt)
    manifest = generate_manifest(
        image_digest="sha256:" + "1" * 64, release_candidate_profile=release_candidate_profile()
    )

    linked = attach_cpa_receipt(manifest, receipt, store=store, config=config)

    row = next(item for item in linked["requirements"] if item["row_id"] == "core.inference-cpa")
    assert row["receipt_ref"] == "receipt:cpa-harness"
    assert next(item for item in linked["receipts"] if item["ref"] == row["receipt_ref"])["kind"] == "cpa-harness"


def draft(**kwargs: object) -> dict[str, object]:
    kwargs.setdefault("release_candidate_profile", release_candidate_profile())
    kwargs.setdefault("image_digest", "sha256:" + "a" * 64)
    return generate_manifest(**kwargs)  # type: ignore[arg-type]


def receipt(ref: str, kind: str = "implementation", digest: str = "b" * 64) -> dict[str, str]:
    return {"ref": ref, "kind": kind, "digest": digest}


def test_profile_is_required_and_has_no_arbitrary_shape() -> None:
    with pytest.raises(ManifestValidationError, match="selected_profile"):
        generate_manifest(image_digest="sha256:" + "a" * 64, release_candidate_profile={})

    profile = release_candidate_profile()
    profile["unbounded_option"] = "not part of the sealed profile"
    with pytest.raises(ManifestValidationError, match="outside the Release-candidate profile schema"):
        generate_manifest(image_digest="sha256:" + "a" * 64, release_candidate_profile=profile)  # type: ignore[arg-type]


def test_profile_digest_binds_every_other_profile_field() -> None:
    changed = copy.deepcopy(draft())
    changed["selected_profile"]["lanes"] = 2

    with pytest.raises(ManifestValidationError, match="profile_digest does not match"):
        validate_manifest(changed)


def test_storage_profile_seals_complete_reservation_contract() -> None:
    storage = draft()["selected_profile"]["storage"]

    assert set(storage) == {
        "writable_envelope",
        "host_filesystem_safety_floor",
        "pressure_thresholds",
        "ordinary_producer_envelopes",
        "authority_effect_envelopes",
        "maximum_authority_effect_reservation",
        "maximum_concurrent_authority_set",
        "shared_authority_pool",
        "terminal_floor",
        "recovery_floor",
        "incident_evidence_limits",
    }


def test_storage_profile_rejects_nonpositive_reservation_dimensions() -> None:
    profile = release_candidate_profile()
    profile["storage"]["terminal_floor"]["filesystem_objects"] = 0
    bind_profile_digest(profile)

    with pytest.raises(ManifestValidationError, match="positive integer"):
        draft(release_candidate_profile=profile)


def test_storage_profile_rejects_pressure_thresholds_out_of_order() -> None:
    profile = release_candidate_profile()
    profile["storage"]["pressure_thresholds"]["hard_remaining"] = storage_reservation(50)
    bind_profile_digest(profile)

    with pytest.raises(ManifestValidationError, match="safety floor <= hard <= soft <= writable envelope"):
        draft(release_candidate_profile=profile)


def test_storage_profile_rejects_an_undersized_shared_authority_pool() -> None:
    profile = release_candidate_profile()
    profile["storage"]["shared_authority_pool"] = storage_reservation(12)
    bind_profile_digest(profile)

    with pytest.raises(ManifestValidationError, match="concurrent authority set"):
        draft(release_candidate_profile=profile)


def test_storage_profile_rejects_aggregate_incident_limit_below_one_incident() -> None:
    profile = release_candidate_profile()
    profile["storage"]["incident_evidence_limits"]["aggregate"] = storage_reservation(1)
    bind_profile_digest(profile)

    with pytest.raises(ManifestValidationError, match="aggregate.*per-Incident"):
        draft(release_candidate_profile=profile)


def test_generate_manifest_is_an_explicit_provisional_draft() -> None:
    manifest = draft()

    assert manifest["schema_version"] == 1
    assert manifest["kind"] == DRAFT_MANIFEST_KIND
    assert manifest["lifecycle"] == "provisional"
    assert manifest["candidate"]["identity"].startswith("provisional:")
    assert manifest["selected_profile"]["primary_route"] == "native-codex"
    assert {row["class"] for row in manifest["requirements"]} == {"core", "pack"}
    assert "qualified" not in json.dumps(manifest).lower()
    assert "gate-passing" not in json.dumps(manifest).lower()


def test_public_draft_contract_uses_release_candidate_vocabulary() -> None:
    assert ReleaseCandidateManifestDraft.__required_keys__ >= frozenset({"candidate", "selected_profile"})


def test_manifest_contains_every_core_row_and_unproved_packs_are_disabled() -> None:
    manifest = draft()

    validate_manifest(manifest)

    rows = {row["row_id"]: row for row in manifest["requirements"]}
    assert tuple(rows) == (*CORE_REQUIREMENT_IDS, *PACK_REQUIREMENT_IDS)
    assert rows["core.manifest-schema"]["status"] == "implemented"
    assert rows["core.manifest-schema"]["receipt_ref"] == "receipt:manifest-schema"
    assert all(rows[row_id]["class"] == "core" for row_id in CORE_REQUIREMENT_IDS)
    assert all(
        rows[row_id]["status"] == "planned" for row_id in CORE_REQUIREMENT_IDS if row_id != "core.manifest-schema"
    )
    assert all(rows[row_id]["status"] == "planned" for row_id in PACK_REQUIREMENT_IDS)
    assert all(rows[row_id]["supported"] is False for row_id in PACK_REQUIREMENT_IDS)
    assert all(rows[row_id]["pack_state"] in {"disabled", "omitted"} for row_id in PACK_REQUIREMENT_IDS)


def test_generator_adds_and_links_a_deterministic_manifest_schema_receipt() -> None:
    manifest = draft(receipts=[])
    schema_receipts = [item for item in manifest["receipts"] if item["kind"] == "manifest-schema"]

    assert schema_receipts == [
        {
            "ref": "receipt:manifest-schema",
            "kind": "manifest-schema",
            "digest": MANIFEST_SCHEMA_RECEIPT_DIGEST,
        }
    ]
    schema_row = next(row for row in manifest["requirements"] if row["row_id"] == "core.manifest-schema")
    assert schema_row["receipt_ref"] == "receipt:manifest-schema"


def test_generator_rejects_an_unknown_requirement_row_id() -> None:
    requirements = copy.deepcopy(draft()["requirements"])
    requirements[-1]["row_id"] = "pack.not-a-real-capability"

    with pytest.raises(ManifestValidationError, match="unknown row IDs"):
        draft(requirements=requirements)


def test_generator_rejects_duplicate_requirement_row_ids() -> None:
    requirements = copy.deepcopy(draft()["requirements"])
    requirements[-1]["row_id"] = requirements[-2]["row_id"]

    with pytest.raises(ManifestValidationError, match="duplicate row IDs"):
        draft(requirements=requirements)


def test_generator_rejects_unordered_requirement_rows() -> None:
    requirements = copy.deepcopy(draft()["requirements"])
    requirements[0], requirements[1] = requirements[1], requirements[0]

    with pytest.raises(ManifestValidationError, match="stable row-ID order"):
        draft(requirements=requirements)


def test_identical_inputs_have_identical_canonical_bytes_and_digest() -> None:
    first = draft()
    second = draft()

    assert canonical_manifest_bytes(first) == canonical_manifest_bytes(second)
    assert manifest_digest(first) == manifest_digest(second)


def test_changing_an_evidence_reference_changes_the_manifest_digest() -> None:
    first = draft()
    requirements = copy.deepcopy(first["requirements"])
    requirements[1].update(status="implemented", receipt_ref="receipt:implementation", evidence_refs=["evidence-a"])
    changed = draft(requirements=requirements, receipts=[receipt("receipt:implementation")])

    assert manifest_digest(first) != manifest_digest(changed)


def test_digest_changes_for_a_modified_document_before_revalidation() -> None:
    first = draft()
    changed = copy.deepcopy(first)
    changed["requirements"][1]["evidence_refs"] = ["evidence-a"]

    assert manifest_digest(first) != manifest_digest(changed)


def test_modified_document_must_be_regenerated_before_validation() -> None:
    changed = copy.deepcopy(draft())
    changed["requirements"][1]["evidence_refs"] = ["evidence-a"]

    with pytest.raises(ManifestValidationError, match="identity does not match"):
        validate_manifest(changed)


def test_pack_support_is_orthogonal_to_enabled_disabled_or_omitted_disposition() -> None:
    base = draft()
    requirements = copy.deepcopy(base["requirements"])
    rows = {row["row_id"]: row for row in requirements}
    rows["pack.model-assisted-recovery"].update(
        status="proved",
        supported=True,
        pack_state="enabled",
        receipt_ref="receipt:pack-enabled",
        evidence_refs=["evidence-enabled"],
    )
    rows["pack.host-observer"].update(
        status="proved",
        supported=True,
        pack_state="disabled",
        receipt_ref="receipt:pack-supported",
        evidence_refs=["evidence-supported"],
    )
    rows["pack.practice-image-repair"].update(pack_state="omitted", reason="not admitted to this candidate")
    profile = release_candidate_profile()
    profile["enabled_packs"] = ["pack.model-assisted-recovery"]
    bind_profile_digest(profile)

    manifest = draft(
        release_candidate_profile=profile,
        requirements=requirements,
        receipts=[receipt("receipt:pack-enabled"), receipt("receipt:pack-supported", digest="c" * 64)],
    )

    validate_manifest(manifest)
    pack_rows = [row for row in manifest["requirements"] if row["class"] == "pack"]
    assert {row["pack_state"] for row in pack_rows} == {"enabled", "disabled", "omitted"}
    assert {row["row_id"] for row in pack_rows if row["supported"]} == {
        "pack.model-assisted-recovery",
        "pack.host-observer",
    }


def test_missing_pack_cannot_be_supported() -> None:
    requirements = copy.deepcopy(draft()["requirements"])
    next(row for row in requirements if row["row_id"] == "pack.host-observer").update(
        status="missing", supported=True, reason="not built"
    )

    with pytest.raises(ManifestValidationError, match="supported without its proof"):
        draft(requirements=requirements)


def test_unproved_pack_cannot_be_enabled() -> None:
    requirements = copy.deepcopy(draft()["requirements"])
    next(row for row in requirements if row["row_id"] == "pack.host-observer").update(
        supported=True, pack_state="enabled"
    )

    with pytest.raises(ManifestValidationError, match="enabled without its proof"):
        draft(requirements=requirements)


def test_profile_enabled_packs_must_match_pack_rows() -> None:
    profile = release_candidate_profile()
    profile["enabled_packs"] = ["pack.model-assisted-recovery"]
    bind_profile_digest(profile)

    with pytest.raises(ManifestValidationError, match="disagrees with Pack row states"):
        draft(release_candidate_profile=profile)


@pytest.mark.parametrize("pack_id", ["pack.host-observer", "pack.practice-image-repair"])
def test_only_behavior_changing_in_image_pack_can_be_enabled(pack_id: str) -> None:
    profile = release_candidate_profile()
    profile["enabled_packs"] = [pack_id]
    bind_profile_digest(profile)

    with pytest.raises(ManifestValidationError, match="may enable only pack.model-assisted-recovery"):
        draft(release_candidate_profile=profile)


def test_sealed_manifest_is_structurally_distinct_and_not_owned_by_this_ticket() -> None:
    sealed = copy.deepcopy(draft())
    sealed["kind"] = "release-candidate-manifest"
    sealed["lifecycle"] = "sealed"

    with pytest.raises(ManifestValidationError, match="sealed Release-candidate manifests are owned"):
        validate_manifest(sealed)


def test_image_digest_must_be_hex() -> None:
    with pytest.raises(ManifestValidationError, match="SHA-256"):
        draft(image_digest="sha256:" + "g" * 64)


def test_complete_synthetic_draft_round_trips_through_canonical_bytes() -> None:
    manifest = draft()

    restored = parse_manifest(canonical_manifest_bytes(manifest))

    assert restored == manifest
    assert manifest_digest(restored) == manifest_digest(manifest)
