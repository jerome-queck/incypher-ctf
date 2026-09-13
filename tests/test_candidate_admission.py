"""Candidate admission: proposal-only model output becomes sealed, replayable readiness."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from solver.candidate_admission import (
    AdmissionDecision,
    CandidateAdmission,
    CandidateDerivation,
    CandidateDisposition,
    CandidateProposal as CandidateProposalContract,
    CandidateProvenance,
    DerivationKind,
)
from solver.candidate_admission_contracts import SubmissionContext
from solver.candidate_admission_receipt import link_manifest, verify_receipt, write_receipt
from solver.candidate_vault import CandidateVaultCipher
from solver.event_store import EventStore, InvalidReceiptError, ObservationRecorded
from solver.event_store_storage import canonical_bytes
from solver.lead_contracts import (
    LeadBinding,
    LeadClassification,
    LeadEngagementRecorded,
    LeadRecord,
)
from solver.manifest import generate_manifest
from solver.redaction import Redactor
from solver.tool_control_contracts import ToolControlRecorded, ToolRecord
from solver.work_generation import GenerationDisposition, GenerationFence
from test_manifest import release_candidate_profile


WRAPPERS = (r"zephyr\{[^}]{1,64}\}", r"FLAG-[0-9a-f]{8}")
CANDIDATE = b"zephyr{earned_not_stated}"
VAULT_KEY = b"candidate-vault-test-key-material-32-bytes"
SUBMISSION_CONTEXT = SubmissionContext.static("challenge-revision-1", "board-1")


def CandidateProposal(
    proposal_id,
    challenge_id,
    generation_id,
    candidate,
    provenance,
    schema_version=1,
):
    """Build proposals with an explicit canonical submission context."""
    return CandidateProposalContract(
        proposal_id,
        challenge_id,
        generation_id,
        candidate,
        provenance,
        SUBMISSION_CONTEXT,
        schema_version,
    )


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def recorded_provenance(
    store,
    generation_id,
    *,
    proposal_id="proposal-1",
    candidate=CANDIDATE,
    suffix="1",
    secret=b"",
    carry_candidate=True,
) -> CandidateProvenance:
    evidence_value = candidate if carry_candidate else b"source fragments without final assembly"
    observation = store.append(
        ObservationRecorded("attempt-1", int(suffix), "solve", "solve", "bash"),
        body=secret + b"shell observation " + evidence_value,
    )
    binding = {
        "run_id": "run-1",
        "boot_id": "boot-1",
        "generation_id": generation_id,
        "lane_id": "lane-1",
        "attempt_id": "attempt-1",
        "step_id": f"step-{suffix}",
    }
    tool = store.append(
        ToolControlRecorded(
            f"tool-{suffix}:completed",
            f"tool-{suffix}",
            ToolRecord.COMPLETED,
            binding,
            "solver.smt",
            "component-1",
            "1",
            f"sha256:{'a' * 64}",
            "b" * 64,
            "c" * 64,
            "d" * 64,
            {"wall_ms": 1},
        ),
        body=b"tool receipt " + evidence_value,
    )
    lead_binding = LeadBinding(
        run_id="run-1",
        boot_id="boot-1",
        generation_id=generation_id,
        lane_id="lane-1",
        attempt_id="attempt-1",
        work_id="integer:42",
        engagement_id="engagement-1",
        owner_id="lead",
        parent_engagement_id="",
        context_digest="e" * 64,
        evidence_digest="e" * 64,
        prompt_bundle_digest="e" * 64,
        playbook_digest="e" * 64,
        tool_schema_digest="e" * 64,
        capability_digest="e" * 64,
        harness="native",
        requested_route="model",
        requested_model="model",
        selected_model="model",
        effective_model="model",
        requested_effort="low",
        effective_effort="low",
        catalog_digest="e" * 64,
        admitted_budget=1000,
        deadline="2026-09-12T01:00:00Z",
        started_at="2026-09-12T00:00:00Z",
    )
    evidence_refs = [observation.event_digest, tool.event_digest]
    derivation = CandidateDerivation(
        DerivationKind.FRAGMENTS,
        (observation.event_digest, tool.event_digest),
        "Tool output confirms the ordered fragments recorded by the Observation.",
        proposal_id,
    )
    proposal_document = {
        "kind": "candidate",
        "value": candidate.decode(),
        "evidence_refs": evidence_refs,
        "derivation": derivation.document(),
    }
    request_document = {"fixture": "candidate admission"}
    turn_document = {
        "approach": "use observed value",
        "proposal": proposal_document,
        "measure": {
            "model": "model",
            "duration_ms": 0,
            "tokens_in": 0,
            "tokens_out": 0,
            "usage_known": False,
        },
    }
    model = store.append(
        LeadEngagementRecorded(
            f"engagement-1:turn-00000{suffix}",
            LeadRecord.TURN,
            lead_binding,
            "run-1",
            f"{generation_id}:authority-000001",
            2,
            int(suffix),
            LeadClassification.ACCEPTED,
            digest(canonical_bytes(request_document)),
            transition_digest="1" * 64,
            proposal_id=proposal_id,
            proposal_kind="candidate",
            proposal_digest=digest(canonical_bytes(proposal_document)),
            output_bytes=len(canonical_bytes(turn_document)),
            model="model",
        ),
        body=canonical_bytes({"request": request_document, "turn": turn_document}),
    )
    return CandidateProvenance(
        CandidateDisposition.DERIVED,
        (observation.event_digest,),
        (tool.event_digest,),
        model.event_digest,
        derivation,
    )


def service(
    tmp_path,
    *,
    redactor=None,
    proposal_id="proposal-1",
    candidate=CANDIDATE,
    secret=b"",
    carry_candidate=True,
):
    censor = redactor or Redactor({})

    def clock():
        return "2026-09-12T00:00:00Z"

    fence = GenerationFence(tmp_path, "run-1", censor, clock)
    generation = fence.acquire("integer:42", "attempt-1")
    admission = CandidateAdmission(tmp_path, "run-1", censor, clock, WRAPPERS, VAULT_KEY, fence)
    evidence = recorded_provenance(
        EventStore(tmp_path, run_id="run-1", redactor=censor),
        generation.generation_id,
        proposal_id=proposal_id,
        candidate=candidate,
        secret=secret,
        carry_candidate=carry_candidate,
    )
    return admission, generation.generation_id, evidence


def test_raw_candidate_is_admitted_with_stable_identity_and_replays_ready_after_restart(tmp_path):
    proposal_id = "lead-1:proposal-000001"
    admission, generation_id, evidence = service(tmp_path, proposal_id=proposal_id)
    proposal = CandidateProposal(proposal_id, 42, generation_id, CANDIDATE, evidence)

    outcome = admission.admit(proposal)

    assert outcome.decision is AdmissionDecision.ADMITTED
    assert outcome.candidate is not None
    expected = {
        "challenge_id": 42,
        "candidate_digest": CandidateVaultCipher(VAULT_KEY).digest(CANDIDATE),
        "generation_id": generation_id,
        "disposition": "derived",
        "observation_digests": list(evidence.observation_digests),
        "tool_receipt_digests": list(evidence.tool_receipt_digests),
        "model_digest": evidence.model_digest,
        "derivation": evidence.derivation.document(),
        "admission_rule": "candidate-admission-v1",
        "submission_context": {
            "challenge_revision": SUBMISSION_CONTEXT.challenge_revision,
            "instance_provenance": SUBMISSION_CONTEXT.instance_provenance,
            "instance_kind": SUBMISSION_CONTEXT.instance_kind.value,
        },
    }
    assert outcome.candidate.identity == digest(json.dumps(expected, sort_keys=True, separators=(",", ":")).encode())
    assert outcome.candidate.candidate == CANDIDATE
    assert CandidateAdmission(tmp_path, "run-1", Redactor({}), lambda: "later", WRAPPERS, VAULT_KEY).ready() == (
        outcome.candidate,
    )


def test_each_stated_wrapper_is_parsed_separately_from_the_same_proposal(tmp_path):
    proposal_id = "lead-1:proposal-000001"
    alternate = b"FLAG-deadbeef"
    admission, generation_id, evidence = service(tmp_path, proposal_id=proposal_id, candidate=alternate)
    proposal = CandidateProposal(proposal_id, 42, generation_id, alternate, evidence)

    outcome = admission.admit(proposal)

    assert outcome.candidate is not None
    assert outcome.candidate.candidate == alternate


@pytest.mark.parametrize(
    ("value", "decision"),
    [
        (b"description says submit the flag shown by the service", "malformed"),
        (b"zephyr{unterminated", "malformed"),
        (b"zephyr{...}", "wrapper-only"),
        (b"zephyr{FAKE_FLAG}", "wrapper-only"),
        (b"zephyr{[A-z0-9_-]+}", "wrapper-only"),
        (b"zephyr{sample}", "wrapper-only"),
        (b"zephyr{your flag here}", "wrapper-only"),
        (b"zephyr{changeme}", "wrapper-only"),
        (b"zephyr{todo_paste_insert_xxx}", "wrapper-only"),
        (b"x" * 4097, "oversize"),
        (b"zephyr{one} and FLAG-deadbeef", "malformed"),
    ],
)
def test_non_candidate_and_malformed_proposals_are_rejected(tmp_path, value, decision):
    admission, generation_id, evidence = service(tmp_path, candidate=value)

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, value, evidence))

    assert outcome.decision.value == decision
    assert admission.ready() == ()


def test_provenance_incomplete_proposal_cannot_admit_itself(tmp_path):
    admission, generation_id, _ = service(tmp_path)
    incomplete = CandidateProvenance(
        CandidateDisposition.DERIVED,
        (digest(b"observation"),),
        (),
        digest(b"model"),
    )

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, incomplete))

    assert outcome.decision is AdmissionDecision.PROVENANCE_INCOMPLETE
    assert admission.ready() == ()


def test_invented_provenance_digests_cannot_admit_model_output(tmp_path):
    admission, generation_id, _ = service(tmp_path)
    invented = CandidateProvenance(CandidateDisposition.DERIVED, ("a" * 64,), ("b" * 64,), "c" * 64)

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, invented))

    assert outcome.decision is AdmissionDecision.PROVENANCE_INCOMPLETE
    assert admission.ready() == ()


def test_unrelated_reachable_provenance_cannot_launder_another_candidate(tmp_path):
    admission, generation_id, evidence = service(tmp_path)

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, b"zephyr{other}", evidence))

    assert outcome.decision is AdmissionDecision.PROVENANCE_INCOMPLETE
    assert admission.ready() == ()


def test_observed_candidate_needs_only_the_carrying_observation(tmp_path):
    admission, generation_id, evidence = service(tmp_path)
    observed = CandidateProvenance(
        CandidateDisposition.OBSERVED,
        evidence.observation_digests,
        (),
    )

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, observed))

    assert outcome.decision is AdmissionDecision.ADMITTED
    assert outcome.candidate.provenance.disposition is CandidateDisposition.OBSERVED


def test_structured_model_derivation_can_transform_cited_evidence(tmp_path):
    assembled = b"zephyr{assembled}"
    admission, generation_id, evidence = service(tmp_path, candidate=assembled, carry_candidate=False)

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, assembled, evidence))

    assert outcome.decision is AdmissionDecision.ADMITTED
    assert outcome.candidate.provenance.disposition is CandidateDisposition.DERIVED


def test_malformed_provenance_types_are_rejected_without_crossing_the_boundary(tmp_path):
    admission, generation_id, _ = service(tmp_path)
    malformed = CandidateProvenance(
        CandidateDisposition.DERIVED,
        (1,),  # type: ignore[arg-type]
        ("b" * 64,),
        "c" * 64,
    )

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, malformed))

    assert outcome.decision is AdmissionDecision.PROVENANCE_INCOMPLETE
    assert admission.ready() == ()


@pytest.mark.parametrize("malformed", [None, 0, False])
def test_falsy_non_string_model_provenance_is_rejected(tmp_path, malformed):
    admission, generation_id, evidence = service(tmp_path)
    observed = CandidateProvenance(
        CandidateDisposition.OBSERVED,
        evidence.observation_digests,
        (),
        malformed,  # type: ignore[arg-type]
    )

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, observed))

    assert outcome.decision is AdmissionDecision.PROVENANCE_INCOMPLETE


@pytest.mark.parametrize("schema", [True, 1.0])
def test_non_integer_proposal_schema_is_unsupported(tmp_path, schema):
    admission, generation_id, evidence = service(tmp_path)

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence, schema))

    assert outcome.decision is AdmissionDecision.UNSUPPORTED


def test_unknown_proposal_schema_is_rejected_as_unsupported(tmp_path):
    admission, generation_id, evidence = service(tmp_path)

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence, 2))

    assert outcome.decision is AdmissionDecision.UNSUPPORTED
    assert admission.ready() == ()


def test_equivalent_duplicate_is_classified_and_keeps_one_ready_candidate(tmp_path):
    admission, generation_id, evidence = service(tmp_path)
    first = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence))
    duplicate_evidence = recorded_provenance(
        admission.store, generation_id, proposal_id="proposal-2", candidate=CANDIDATE, suffix="2"
    )

    duplicate = admission.admit(CandidateProposal("proposal-2", 42, generation_id, CANDIDATE, duplicate_evidence))

    assert duplicate.decision is AdmissionDecision.DUPLICATE
    assert duplicate.candidate is None
    assert admission.ready() == (first.candidate,)
    receipt = json.loads(write_receipt(tmp_path, "run-1", VAULT_KEY).read_text())
    assert receipt["decisions"][-1]["duplicate_class"] == "equivalent"


def test_same_proposal_identity_replays_the_admitted_candidate_without_a_new_vault(tmp_path):
    admission, generation_id, evidence = service(tmp_path)
    proposal = CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence)
    first = admission.admit(proposal)
    before = tuple(admission.store.events())

    replayed = admission.admit(proposal)

    assert replayed == first
    assert tuple(admission.store.events()) == before


def test_same_proposal_replay_repairs_a_missing_receipt(tmp_path):
    admission, generation_id, evidence = service(tmp_path)
    proposal = CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence)
    first = admission.admit(proposal)
    receipt = write_receipt(tmp_path, "run-1", VAULT_KEY)
    receipt.unlink()

    replayed = admission.admit(proposal)

    assert replayed == first
    assert verify_receipt(receipt, VAULT_KEY) == receipt


def test_same_challenge_and_candidate_are_equivalent_even_with_new_provenance(tmp_path):
    admission, generation_id, evidence = service(tmp_path)
    first = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence))
    changed = recorded_provenance(
        admission.store, generation_id, proposal_id="proposal-2", candidate=CANDIDATE, suffix="2"
    )

    duplicate = admission.admit(CandidateProposal("proposal-2", 42, generation_id, CANDIDATE, changed))

    assert duplicate.decision is AdmissionDecision.DUPLICATE
    assert admission.ready() == (first.candidate,)


def test_concurrent_equivalent_proposals_leave_exactly_one_ready_candidate(tmp_path):
    admission, generation_id, evidence = service(tmp_path)
    peer = CandidateAdmission(tmp_path, "run-1", Redactor({}), lambda: "later", WRAPPERS, VAULT_KEY)
    proposals = (
        CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence),
        CandidateProposal(
            "proposal-2",
            42,
            generation_id,
            CANDIDATE,
            recorded_provenance(
                admission.store, generation_id, proposal_id="proposal-2", candidate=CANDIDATE, suffix="2"
            ),
        ),
    )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda pair: pair[0].admit(pair[1]), zip((admission, peer), proposals)))

    assert {outcome.decision for outcome in outcomes} == {
        AdmissionDecision.ADMITTED,
        AdmissionDecision.DUPLICATE,
    }
    assert len(admission.ready()) == 1


def test_closed_generation_proposal_is_rejected_as_stale(tmp_path):
    admission, generation_id, evidence = service(tmp_path)
    admission._fence.close(generation_id, GenerationDisposition.SUPERSEDE)

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence))

    assert outcome.decision is AdmissionDecision.STALE_GENERATION
    assert admission.ready() == ()


def test_secret_bearing_observation_evidence_never_reaches_canonical_events_or_receipt(tmp_path):
    secret = b"credential-must-not-leak"
    redactor = Redactor({"TOKEN": secret.decode()})
    admission, generation_id, evidence = service(tmp_path, redactor=redactor, secret=secret)

    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence))

    assert outcome.decision is AdmissionDecision.ADMITTED
    run_files = tuple(path for path in (tmp_path / "runs" / "run-1").rglob("*") if path.is_file())
    assert all(secret not in path.read_bytes() for path in run_files)
    receipt = write_receipt(tmp_path, "run-1", VAULT_KEY).read_text()
    assert CANDIDATE.decode() not in receipt
    assert receipt.count(outcome.candidate.identity) >= 1


def test_exact_candidate_vault_is_authenticated_ciphertext_not_hex_encoding(tmp_path):
    admission, generation_id, evidence = service(tmp_path)

    admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence))

    event = next(item for item in admission.store.events() if item.event_type == "candidate-admission.recorded")
    envelope = json.loads(event.body)
    assert set(envelope) == {"schema_version", "nonce", "ciphertext"}
    assert CANDIDATE not in event.body
    assert CANDIDATE.hex() not in event.body.decode()


def test_receipt_is_independently_verified_and_restart_does_not_open_a_board_transport(tmp_path, monkeypatch):
    admission, generation_id, evidence = service(tmp_path)
    outcome = admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence))
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: pytest.fail("Board POST attempted"))

    restarted = CandidateAdmission(tmp_path, "run-1", Redactor({}), lambda: "later", WRAPPERS, VAULT_KEY)

    assert restarted.ready() == (outcome.candidate,)
    receipt = write_receipt(tmp_path, "run-1", VAULT_KEY)
    assert verify_receipt(receipt, VAULT_KEY) == receipt
    with pytest.raises(InvalidReceiptError):
        verify_receipt(receipt, b"wrong-candidate-vault-key-material-32-bytes")


def test_verified_receipt_links_the_submission_tail_manifest_row(tmp_path):
    admission, generation_id, evidence = service(tmp_path)
    admission.admit(CandidateProposal("proposal-1", 42, generation_id, CANDIDATE, evidence))
    manifest = generate_manifest(
        image_digest=f"sha256:{'a' * 64}",
        release_candidate_profile=release_candidate_profile(),
    )

    linked = link_manifest(manifest, write_receipt(tmp_path, "run-1", VAULT_KEY), VAULT_KEY)

    row = next(item for item in linked["requirements"] if item["row_id"] == "core.submission-tail")
    assert row["receipt_ref"] == "receipt:candidate-admission"
