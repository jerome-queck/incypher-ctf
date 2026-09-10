"""Contracts for release-candidate manifest drafts."""

from typing import Literal, TypeAlias, TypedDict, get_args

SCHEMA_VERSION = 1
DRAFT_MANIFEST_KIND = "release-candidate-manifest-draft"
MANIFEST_KIND = "release-candidate-manifest"
PROVISIONAL = "provisional"
SEALED = "sealed"

RequirementStatus: TypeAlias = Literal["planned", "implemented", "proved", "missing"]
PackState: TypeAlias = Literal["enabled", "disabled", "omitted"]
RequirementClass: TypeAlias = Literal["core", "pack"]
InferenceRoute: TypeAlias = Literal["native-codex", "private-cpa"]
RouteChangePolicy: TypeAlias = Literal["classified-route-local-failure-only"]

REQUIREMENT_STATUSES = frozenset(get_args(RequirementStatus))
PACK_STATES = frozenset(get_args(PackState))
INFERENCE_ROUTES = frozenset(get_args(InferenceRoute))

# Each stable identifier and its display title have one runtime source. Literal
# aliases expand those same tuples, so runtime validation and static types cannot drift.
CORE_REQUIREMENTS = (
    ("core.manifest-schema", "Release-candidate manifest schema"),
    ("core.intake", "Intake"),
    ("core.triage-order", "Deterministic Triage and Order"),
    ("core.adaptive-routing", "Model Triage and adaptive routing"),
    ("core.persistent-solve-lead", "Persistent Solve Lead"),
    ("core.lane-specialist-topology", "Lane and Specialist support"),
    ("core.board-target-lease", "Board, Target and Lease lifecycles"),
    ("core.submission-tail", "Submission and final-window lifecycles"),
    ("core.inference-native", "Native Codex route"),
    ("core.inference-cpa", "Private CPA route"),
    ("core.brokered-credentials-egress", "Brokered credentials and typed egress"),
    ("core.strict-isolation", "Strict hostile execution"),
    ("core.supervisor", "Supervisor lifecycle authority"),
    ("core.canonical-state-replay-restart", "Canonical state, replay and restart"),
    ("core.deterministic-recovery", "Deterministic Recovery"),
    ("core.no-inference-safety", "Safe no-inference posture"),
    ("core.tool-surface", "Fieldable Tool surface"),
    ("core.receipts-capsules", "Receipts and sanitized capsules"),
    ("core.controlled-proofs", "Controlled proofs"),
    ("core.full-window-gate", "Full-window Gate"),
)
PACK_REQUIREMENTS = (
    ("pack.model-assisted-recovery", "Model-assisted novel Recovery"),
    ("pack.host-observer", "Full host-side Observer CLI"),
    ("pack.practice-image-repair", "Practice candidate-image repair"),
)
CORE_REQUIREMENT_IDS = tuple(row_id for row_id, _ in CORE_REQUIREMENTS)
PACK_REQUIREMENT_IDS = tuple(row_id for row_id, _ in PACK_REQUIREMENTS)
CORE_TITLES = dict(CORE_REQUIREMENTS)
PACK_TITLES = dict(PACK_REQUIREMENTS)

CoreRequirementID: TypeAlias = Literal[*CORE_REQUIREMENT_IDS]
PackRequirementID: TypeAlias = Literal[*PACK_REQUIREMENT_IDS]
RequirementRowID: TypeAlias = CoreRequirementID | PackRequirementID
KNOWN_ROW_IDS = frozenset((*CORE_REQUIREMENT_IDS, *PACK_REQUIREMENT_IDS))

MANIFEST_SCHEMA_ROW_ID = "core.manifest-schema"
MANIFEST_SCHEMA_RECEIPT_REF = "receipt:manifest-schema"
MANIFEST_SCHEMA_RECEIPT_KIND = "manifest-schema"
ROUTE_CHANGE_POLICY = "classified-route-local-failure-only"
RUNTIME_ENABLED_PACK_IDS = frozenset({"pack.model-assisted-recovery"})


class ManifestValidationError(ValueError):
    """A manifest is not a complete, internally consistent schema document."""


class ProvisionalReleaseCandidateIdentity(TypedDict):
    identity: str
    image_digest: str


class ManifestReceipt(TypedDict):
    ref: str
    kind: str
    digest: str


class ModelProfile(TypedDict):
    requested: str
    effective: str
    effort: str


class DecisionPolicies(TypedDict):
    triage: str
    order: str
    tier: str
    cut: str
    submission: str


class ResourceProfile(TypedDict):
    cpu_limit: int
    memory_bytes: int
    pid_limit: int


class FilesystemOperationReservation(TypedDict):
    create: int
    append: int
    rename: int
    unlink: int
    durability: int


class StorageReservation(TypedDict):
    bytes: int
    filesystem_objects: int
    operations: FilesystemOperationReservation


class PressureThresholds(TypedDict):
    soft_remaining: StorageReservation
    hard_remaining: StorageReservation


class ProducerEnvelope(TypedDict):
    producer: str
    reservation: StorageReservation


class AuthorityEffectEnvelope(TypedDict):
    transaction: str
    reservation: StorageReservation


class ConcurrentAuthority(TypedDict):
    transaction: str
    maximum_concurrent: int


class IncidentEvidenceLimits(TypedDict):
    per_incident: StorageReservation
    aggregate: StorageReservation


class StorageProfile(TypedDict):
    writable_envelope: StorageReservation
    host_filesystem_safety_floor: StorageReservation
    pressure_thresholds: PressureThresholds
    ordinary_producer_envelopes: list[ProducerEnvelope]
    authority_effect_envelopes: list[AuthorityEffectEnvelope]
    maximum_authority_effect_reservation: StorageReservation
    maximum_concurrent_authority_set: list[ConcurrentAuthority]
    shared_authority_pool: StorageReservation
    terminal_floor: StorageReservation
    recovery_floor: StorageReservation
    incident_evidence_limits: IncidentEvidenceLimits


class IsolationProfile(TypedDict):
    profile_id: str
    profile_digest: str


class ReleaseCandidateProfile(TypedDict):
    primary_route: InferenceRoute
    alternate_route: InferenceRoute
    route_change_policy: RouteChangePolicy
    lanes: int
    specialists: int
    model: ModelProfile
    prompt_bundle: str
    tool_policy: str
    decision_policies: DecisionPolicies
    resources: ResourceProfile
    storage: StorageProfile
    isolation: IsolationProfile
    enabled_packs: list[PackRequirementID]
    profile_digest: str


CoreRequirementRow = TypedDict(
    "CoreRequirementRow",
    {
        "row_id": CoreRequirementID,
        "class": Literal["core"],
        "title": str,
        "status": RequirementStatus,
        "receipt_ref": str | None,
        "evidence_refs": list[str],
        "source_issue": int,
        "reason": str,
    },
)

PackRequirementRow = TypedDict(
    "PackRequirementRow",
    {
        "row_id": PackRequirementID,
        "class": Literal["pack"],
        "title": str,
        "status": RequirementStatus,
        "supported": bool,
        "pack_state": PackState,
        "receipt_ref": str | None,
        "evidence_refs": list[str],
        "source_issue": int,
        "reason": str,
    },
)
RequirementRow: TypeAlias = CoreRequirementRow | PackRequirementRow


class ReleaseCandidateManifestDraft(TypedDict):
    schema_version: Literal[1]
    kind: Literal["release-candidate-manifest-draft"]
    lifecycle: Literal["provisional"]
    candidate: ProvisionalReleaseCandidateIdentity
    selected_profile: ReleaseCandidateProfile
    requirements: list[RequirementRow]
    receipts: list[ManifestReceipt]


PROFILE_KEYS = ReleaseCandidateProfile.__required_keys__
MODEL_KEYS = ModelProfile.__required_keys__
DECISION_POLICY_KEYS = DecisionPolicies.__required_keys__
RESOURCE_KEYS = ResourceProfile.__required_keys__
STORAGE_KEYS = StorageProfile.__required_keys__
RESERVATION_KEYS = StorageReservation.__required_keys__
OPERATION_KEYS = FilesystemOperationReservation.__required_keys__
ISOLATION_KEYS = IsolationProfile.__required_keys__
CORE_ROW_KEYS = CoreRequirementRow.__required_keys__
PACK_ROW_KEYS = PackRequirementRow.__required_keys__
MANIFEST_KEYS = ReleaseCandidateManifestDraft.__required_keys__
CANDIDATE_IDENTITY_KEYS = ProvisionalReleaseCandidateIdentity.__required_keys__
RECEIPT_KEYS = ManifestReceipt.__required_keys__
PRESSURE_THRESHOLD_KEYS = PressureThresholds.__required_keys__
PRODUCER_ENVELOPE_KEYS = ProducerEnvelope.__required_keys__
AUTHORITY_EFFECT_ENVELOPE_KEYS = AuthorityEffectEnvelope.__required_keys__
CONCURRENT_AUTHORITY_KEYS = ConcurrentAuthority.__required_keys__
INCIDENT_EVIDENCE_LIMIT_KEYS = IncidentEvidenceLimits.__required_keys__
CONTRACT_FIELDS = {
    "authority_effect_envelope": AUTHORITY_EFFECT_ENVELOPE_KEYS,
    "candidate": CANDIDATE_IDENTITY_KEYS,
    "concurrent_authority": CONCURRENT_AUTHORITY_KEYS,
    "core_row": CORE_ROW_KEYS,
    "decision_policies": DECISION_POLICY_KEYS,
    "filesystem_operations": OPERATION_KEYS,
    "incident_evidence_limits": INCIDENT_EVIDENCE_LIMIT_KEYS,
    "isolation": ISOLATION_KEYS,
    "manifest": MANIFEST_KEYS,
    "model": MODEL_KEYS,
    "pack_row": PACK_ROW_KEYS,
    "pressure_thresholds": PRESSURE_THRESHOLD_KEYS,
    "producer_envelope": PRODUCER_ENVELOPE_KEYS,
    "profile": PROFILE_KEYS,
    "receipt": RECEIPT_KEYS,
    "reservation": RESERVATION_KEYS,
    "resources": RESOURCE_KEYS,
    "storage": STORAGE_KEYS,
}


__all__ = [
    "AuthorityEffectEnvelope",
    "ConcurrentAuthority",
    "CoreRequirementID",
    "CORE_REQUIREMENT_IDS",
    "DRAFT_MANIFEST_KIND",
    "DecisionPolicies",
    "ReleaseCandidateManifestDraft",
    "FilesystemOperationReservation",
    "IncidentEvidenceLimits",
    "InferenceRoute",
    "IsolationProfile",
    "MANIFEST_KIND",
    "MANIFEST_SCHEMA_RECEIPT_KIND",
    "MANIFEST_SCHEMA_RECEIPT_REF",
    "MANIFEST_SCHEMA_ROW_ID",
    "ManifestReceipt",
    "ManifestValidationError",
    "ModelProfile",
    "PACK_REQUIREMENT_IDS",
    "PACK_STATES",
    "PROVISIONAL",
    "PackRequirementID",
    "PackState",
    "PressureThresholds",
    "ProducerEnvelope",
    "ProvisionalReleaseCandidateIdentity",
    "REQUIREMENT_STATUSES",
    "ROUTE_CHANGE_POLICY",
    "ReleaseCandidateProfile",
    "RequirementRow",
    "RequirementRowID",
    "RequirementStatus",
    "ResourceProfile",
    "SCHEMA_VERSION",
    "SEALED",
    "StorageProfile",
    "StorageReservation",
]
