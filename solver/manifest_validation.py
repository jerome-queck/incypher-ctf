"""Validation for release-candidate manifest drafts."""

from collections.abc import Mapping
from typing import TypeGuard, cast

from solver.manifest_contracts import (
    CANDIDATE_IDENTITY_KEYS,
    CORE_REQUIREMENT_IDS,
    CORE_ROW_KEYS,
    DECISION_POLICY_KEYS,
    DRAFT_MANIFEST_KIND,
    INFERENCE_ROUTES,
    ISOLATION_KEYS,
    KNOWN_ROW_IDS,
    MANIFEST_KEYS,
    MANIFEST_KIND,
    MANIFEST_SCHEMA_RECEIPT_REF,
    MANIFEST_SCHEMA_ROW_ID,
    MODEL_KEYS,
    PACK_REQUIREMENT_IDS,
    PACK_ROW_KEYS,
    PACK_STATES,
    PROFILE_KEYS,
    PROVISIONAL,
    RECEIPT_KEYS,
    REQUIREMENT_STATUSES,
    RESOURCE_KEYS,
    ROUTE_CHANGE_POLICY,
    RUNTIME_ENABLED_PACK_IDS,
    SCHEMA_VERSION,
    SEALED,
    ReleaseCandidateManifestDraft,
    PackState,
    RequirementClass,
    RequirementRowID,
    RequirementStatus,
)
from solver.manifest_schema import schema_receipt
from solver.manifest_storage import validate_storage_profile
from solver.manifest_values import (
    digest,
    fail as _fail,
    mapping as _mapping,
    positive_int as _positive_int,
    provisional_identity_basis,
    sha256 as _sha256,
    string as _string,
    string_list as _string_list,
)


def _validate_profile(value: object) -> set[str]:
    profile = _mapping(value, "selected_profile")
    if set(profile) != PROFILE_KEYS:
        _fail("selected_profile has fields outside the Release-candidate profile schema")

    primary = _string(profile["primary_route"], "selected_profile.primary_route")
    alternate = _string(profile["alternate_route"], "selected_profile.alternate_route")
    if primary not in INFERENCE_ROUTES or alternate not in INFERENCE_ROUTES:
        _fail("selected_profile routes must be native-codex or private-cpa")
    if primary == alternate:
        _fail("selected_profile primary and alternate routes must differ")
    if profile["route_change_policy"] != ROUTE_CHANGE_POLICY:
        _fail("selected_profile.route_change_policy is not the classified route-local-failure policy")

    lanes = _positive_int(profile["lanes"], "selected_profile.lanes")
    specialists = profile["specialists"]
    if not isinstance(specialists, int) or isinstance(specialists, bool) or specialists < 0 or specialists > 2:
        _fail("selected_profile.specialists must be an integer from 0 through 2")
    if lanes > 2:
        _fail("selected_profile.lanes must be an integer from 1 through 2")

    model = _mapping(profile["model"], "selected_profile.model")
    if set(model) != MODEL_KEYS:
        _fail("selected_profile.model must bind requested, effective and effort")
    for key in MODEL_KEYS:
        _string(model[key], f"selected_profile.model.{key}")

    _string(profile["prompt_bundle"], "selected_profile.prompt_bundle")
    _string(profile["tool_policy"], "selected_profile.tool_policy")
    policies = _mapping(profile["decision_policies"], "selected_profile.decision_policies")
    if set(policies) != DECISION_POLICY_KEYS:
        _fail("selected_profile.decision_policies must bind triage, order, tier, cut and submission")
    for key in DECISION_POLICY_KEYS:
        _string(policies[key], f"selected_profile.decision_policies.{key}")

    resources = _mapping(profile["resources"], "selected_profile.resources")
    if set(resources) != RESOURCE_KEYS:
        _fail("selected_profile.resources must bind cpu, memory and pid limits")
    for key in RESOURCE_KEYS:
        _positive_int(resources[key], f"selected_profile.resources.{key}")

    validate_storage_profile(profile["storage"])

    isolation = _mapping(profile["isolation"], "selected_profile.isolation")
    if set(isolation) != ISOLATION_KEYS:
        _fail("selected_profile.isolation must bind a profile id and digest")
    _string(isolation["profile_id"], "selected_profile.isolation.profile_id")
    _sha256(isolation["profile_digest"], "selected_profile.isolation.profile_digest")

    enabled_packs = _string_list(profile["enabled_packs"], "selected_profile.enabled_packs")
    if enabled_packs != sorted(enabled_packs):
        _fail("selected_profile.enabled_packs are not in stable order")
    unknown = sorted(set(enabled_packs) - set(PACK_REQUIREMENT_IDS))
    if unknown:
        _fail(f"selected_profile.enabled_packs contain unknown Packs: {', '.join(unknown)}")
    if not set(enabled_packs) <= RUNTIME_ENABLED_PACK_IDS:
        _fail("selected_profile may enable only pack.model-assisted-recovery")
    supplied_digest = _sha256(profile["profile_digest"], "selected_profile.profile_digest")
    digest_basis = dict(profile)
    digest_basis.pop("profile_digest")
    if supplied_digest != digest(digest_basis):
        _fail("selected_profile.profile_digest does not match the profile contents")
    return set(enabled_packs)


def _validate_receipts(value: object) -> set[str]:
    if not isinstance(value, list):
        _fail("receipts must be a list")
    refs: list[str] = []
    for index, receipt in enumerate(value):
        item = _mapping(receipt, f"receipts[{index}]")
        if set(item) != RECEIPT_KEYS:
            _fail(f"receipts[{index}] must contain only ref, kind and digest")
        refs.append(_string(item["ref"], f"receipts[{index}].ref"))
        _string(item["kind"], f"receipts[{index}].kind")
        _sha256(item["digest"], f"receipts[{index}].digest")
    if len(refs) != len(set(refs)):
        _fail("receipts contain duplicate refs")
    if refs != sorted(refs):
        _fail("receipts are not in stable order")
    return set(refs)


def _validate_requirement(row: object, index: int, receipt_refs: set[str]) -> tuple[RequirementRowID, PackState | None]:
    item = _mapping(row, f"requirements[{index}]")
    row_id_value = _string(item.get("row_id"), f"requirements[{index}].row_id")
    if row_id_value not in KNOWN_ROW_IDS:
        _fail(f"requirements contain unknown row IDs: {row_id_value}")
    row_id = cast(RequirementRowID, row_id_value)
    is_pack = row_id in PACK_REQUIREMENT_IDS
    expected_keys = PACK_ROW_KEYS if is_pack else CORE_ROW_KEYS
    if set(item) != expected_keys:
        _fail(f"requirements[{index}] has fields inconsistent with {row_id}")

    expected_class: RequirementClass = "pack" if is_pack else "core"
    if item.get("class") != expected_class:
        _fail(f"{row_id} is classified {item.get('class')!r}, expected {expected_class!r}")
    _string(item.get("title"), f"{row_id}.title")
    status_value = _string(item.get("status"), f"{row_id}.status")
    if status_value not in REQUIREMENT_STATUSES:
        _fail(f"{row_id} has unknown requirement status {status_value!r}")
    status = cast(RequirementStatus, status_value)

    receipt_ref = item.get("receipt_ref")
    if receipt_ref is not None:
        receipt_ref = _string(receipt_ref, f"{row_id}.receipt_ref")
        if receipt_ref not in receipt_refs:
            _fail(f"{row_id} references unknown receipt {receipt_ref!r}")
    evidence_refs = _string_list(item.get("evidence_refs"), f"{row_id}.evidence_refs")
    if evidence_refs != sorted(evidence_refs):
        _fail(f"{row_id}.evidence_refs are not in stable order")
    source_issue = item.get("source_issue")
    if not isinstance(source_issue, int) or isinstance(source_issue, bool) or source_issue < 1:
        _fail(f"{row_id}.source_issue must be a positive issue number")
    reason = item.get("reason")
    if not isinstance(reason, str):
        _fail(f"{row_id}.reason must be a string")

    if status in {"planned", "missing"} and (receipt_ref is not None or evidence_refs):
        _fail(f"{row_id} cannot attach evidence before it is implemented")
    if status == "missing" and not reason:
        _fail(f"{row_id} marks a missing requirement without a reason")
    if status in {"implemented", "proved"} and receipt_ref is None:
        _fail(f"{row_id} needs a receipt when it is {status}")
    if status == "proved" and not evidence_refs:
        _fail(f"{row_id} marks proof without evidence")

    if not is_pack:
        return row_id, None

    supported = item.get("supported")
    if not isinstance(supported, bool):
        _fail(f"{row_id}.supported must be a boolean")
    pack_state_value = _string(item.get("pack_state"), f"{row_id}.pack_state")
    if pack_state_value not in PACK_STATES:
        _fail(f"{row_id} has unknown Pack state {pack_state_value!r}")
    pack_state = cast(PackState, pack_state_value)
    if pack_state == "enabled" and (not supported or status != "proved"):
        _fail(f"{row_id} cannot be enabled without its proof and supported status")
    if supported and status != "proved":
        _fail(f"{row_id} cannot be supported without its proof")
    if pack_state in {"disabled", "omitted"} and not reason:
        _fail(f"{row_id} needs a reason when it is {pack_state}")
    return row_id, pack_state


def validate_manifest(manifest: Mapping[str, object]) -> TypeGuard[ReleaseCandidateManifestDraft]:
    """Validate one complete, provisional draft at the public JSON seam.

    The sealed glossary artifact is intentionally rejected here: release tickets own
    policy qualification and Gate evidence, not the draft schema ticket.
    """

    document = _mapping(manifest, "manifest")
    required = MANIFEST_KEYS
    if set(document) - required:
        _fail("manifest contains unknown fields")
    if set(document) < required:
        _fail("manifest is missing required fields")
    if document["schema_version"] != SCHEMA_VERSION:
        _fail(f"unsupported manifest schema version {document['schema_version']!r}")
    kind = _string(document["kind"], "kind")
    lifecycle = _string(document["lifecycle"], "lifecycle")
    if kind == MANIFEST_KIND and lifecycle == SEALED:
        _fail("sealed Release-candidate manifests are owned by later release work")
    if kind != DRAFT_MANIFEST_KIND or lifecycle != PROVISIONAL:
        _fail("draft manifest must use release-candidate-manifest-draft and provisional")

    candidate = _mapping(document["candidate"], "candidate")
    if set(candidate) != CANDIDATE_IDENTITY_KEYS:
        _fail("candidate must contain only identity and image_digest")
    identity = _string(candidate["identity"], "candidate.identity")
    _sha256(candidate["image_digest"], "candidate.image_digest", prefixed=True)
    prefix, _, supplied = identity.partition(":")
    if (
        prefix != PROVISIONAL
        or len(supplied) != 64
        or any(character not in "0123456789abcdef" for character in supplied)
    ):
        _fail("provisional candidate.identity must be provisional:<sha256>")
    _validate_profile(document["selected_profile"])
    if supplied != digest(provisional_identity_basis(document)):
        _fail("candidate.identity does not match the manifest contents")
    requirements = document["requirements"]
    if not isinstance(requirements, list):
        _fail("requirements must be a list")
    receipt_refs = _validate_receipts(document["receipts"])
    expected_schema_receipt = schema_receipt()
    supplied_schema_receipts = [
        item
        for item in document["receipts"]
        if isinstance(item, Mapping) and item.get("ref") == MANIFEST_SCHEMA_RECEIPT_REF
    ]
    if supplied_schema_receipts != [expected_schema_receipt]:
        _fail("draft manifest must include the deterministic manifest-schema receipt")

    actual_ids: list[RequirementRowID] = []
    pack_states: dict[str, PackState] = {}
    for index, row in enumerate(requirements):
        row_id, pack_state = _validate_requirement(row, index, receipt_refs)
        actual_ids.append(row_id)
        if pack_state is not None:
            pack_states[row_id] = pack_state

    expected_ids = [*CORE_REQUIREMENT_IDS, *PACK_REQUIREMENT_IDS]
    if actual_ids != expected_ids:
        if len(actual_ids) != len(set(actual_ids)):
            _fail("requirements contain duplicate row IDs")
        unknown = sorted(set(actual_ids) - KNOWN_ROW_IDS)
        if unknown:
            _fail(f"requirements contain unknown row IDs: {', '.join(unknown)}")
        missing = sorted(KNOWN_ROW_IDS - set(actual_ids))
        if missing:
            _fail(f"requirements are missing row IDs: {', '.join(missing)}")
        _fail("requirements are not in stable row-ID order")

    schema_row = next(
        row for row in requirements if isinstance(row, Mapping) and row.get("row_id") == MANIFEST_SCHEMA_ROW_ID
    )
    if schema_row.get("receipt_ref") != MANIFEST_SCHEMA_RECEIPT_REF or schema_row.get("status") not in {
        "implemented",
        "proved",
    }:
        _fail("core.manifest-schema must be implemented by this draft")

    profile = _mapping(document["selected_profile"], "selected_profile")
    enabled_packs = set(_string_list(profile["enabled_packs"], "selected_profile.enabled_packs"))
    row_enabled_packs = {row_id for row_id, state in pack_states.items() if state == "enabled"}
    if enabled_packs != row_enabled_packs:
        _fail("selected_profile.enabled_packs disagrees with Pack row states")
    return True
