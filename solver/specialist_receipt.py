"""Independent projection verification and manifest linkage for Specialist dispatch."""

import json
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes


def verify_receipt(path: Path, generations):
    document = json.loads(Path(path).read_bytes())
    if document.get("schema_version") != 1 or document.get("receipt_type") != "specialist-pool":
        raise ValueError("Specialist receipt schema is unsupported")
    control = {
        key: value
        for key, value in document.items()
        if key
        not in {"receipt_type", "controlled_proof_digest", "generation_digest", "control_digest", "manifest_link"}
    }
    if document.get("control_digest") != digest_bytes(canonical_bytes(control)):
        raise ValueError("Specialist receipt disagrees with durable control state")
    from solver.specialist_pool_proof import PROOF_DIGEST, load_controlled_proof

    load_controlled_proof()
    if document.get("controlled_proof_digest") != PROOF_DIGEST:
        raise ValueError("Specialist receipt is not bound to its controlled proof")
    control_path = Path(path).with_name("specialist-pool.control.json")
    if control_path.read_bytes() != canonical_bytes(control) + b"\n":
        raise ValueError("Specialist receipt is not the durable control projection")
    projection = generations.projection()
    if document.get("generation_digest") != projection.digest:
        raise ValueError("Specialist receipt is not bound to canonical generations")
    profile = document["profile"]
    results = document["results"]
    quota = document["quota_trace"]
    admissions = document["admissions"]
    if profile["maximum"] > 2 or len(quota) > profile["shared_quota_turns"]:
        raise ValueError("Specialist receipt exceeds its pool or shared quota")
    if len({row["task_id"] for row in results}) != len(results):
        raise ValueError("Specialist proposal was integrated more than once")
    if {row["task_id"] for row in admissions} - {row["task_id"] for row in results}:
        raise ValueError("Specialist result lacks one controller-owned task binding")
    if [row["sequence"] for row in quota] != list(range(1, len(quota) + 1)) or {row["task_id"] for row in quota} != {
        row["task_id"] for row in admissions
    }:
        raise ValueError("Specialist shared quota trace is not derived from admissions")
    quota_by_task = {
        task_id: [row for row in quota if row["task_id"] == task_id] for task_id in {row["task_id"] for row in quota}
    }
    if any([row["turn_index"] for row in rows] != list(range(1, len(rows) + 1)) for rows in quota_by_task.values()):
        raise ValueError("Specialist turn quota trace is not contiguous")
    states = {state.generation_id: state for state in projection.generations}
    admissions_by_task = {row["task_id"]: row for row in admissions}
    for admission in admissions:
        parent = states.get(admission["parent_generation_id"])
        private = states.get(admission["private_generation_id"])
        if parent is None or parent.attempt_id != admission["parent_attempt_id"]:
            raise ValueError("Specialist parent generation binding is not canonical")
        result = next(row for row in results if row["task_id"] == admission["task_id"])
        may_remain_active = result["verdict"] == "unsettled"
        if (
            private is None
            or private.attempt_id != admission["private_attempt_id"]
            or private.active != may_remain_active
        ):
            raise ValueError("Specialist private generation is not canonically closed")
        if any(item["generation_id"] != parent.generation_id for item in admission["evidence"]):
            raise ValueError("Specialist evidence crosses generation authority")
    for result in results:
        admission = admissions_by_task.get(result["task_id"])
        if admission is None:
            if result["private_generation_id"]:
                raise ValueError("Rejected Specialist result invents private authority")
            continue
        if result["private_generation_id"] != admission["private_generation_id"]:
            raise ValueError("Specialist result changed private generation")
        authorized = {item["ref"] for item in admission["evidence"]}
        if not set(result["accepted_evidence_refs"]) <= authorized:
            raise ValueError("Specialist result imported unauthorized evidence")
        if (
            result["turns"] > len(quota_by_task[result["task_id"]])
            or result["context_bytes"] > profile["max_context_bytes"]
        ):
            raise ValueError("Specialist result exceeds measured bounds")
        proposal = result.get("proposal")
        if proposal is not None:
            projected = {
                "summary": proposal["summary"],
                "evidence_refs": proposal["evidence_refs"],
                "candidate": proposal["candidate"],
            }
            if result["proposal_digest"] != digest_bytes(canonical_bytes(projected)):
                raise ValueError("Specialist proposal projection changed")
        termination = result.get("termination")
        if result["reason"] in {
            "bounded-termination",
            "termination-owner-survived",
            "termination-evidence-invalid",
        }:
            if termination is None or len(termination["evidence_digest"]) != 64:
                raise ValueError("Specialist termination lacks typed evidence")
            if result["verdict"] == "unsettled" and termination["ended"] and not termination["survivors"]:
                raise ValueError("Specialist unsettled termination claims no survivor")
    return document


def link_manifest(manifest, path, generations):
    from solver.manifest import attach_requirement_receipt, generate_manifest, parse_manifest

    document = verify_receipt(path, generations)
    if manifest["selected_profile"]["specialists"] != document["profile"]["maximum"]:
        raise ValueError("Specialist receipt does not prove the selected profile")
    receipt = {
        "ref": "receipt:specialist-pool",
        "kind": "specialist-pool",
        "digest": digest_bytes(Path(path).read_bytes()),
    }
    current = parse_manifest(manifest)
    row = next(item for item in current["requirements"] if item["row_id"] == "core.lane-specialist-topology")
    previous_ref = row["receipt_ref"]
    previous = next((item for item in current["receipts"] if item["ref"] == previous_ref), None)
    linked = attach_requirement_receipt(current, "core.lane-specialist-topology", receipt)
    if previous is None or previous_ref == receipt["ref"]:
        return linked
    requirements = [dict(item) for item in linked["requirements"]]
    linked_row = next(item for item in requirements if item["row_id"] == "core.lane-specialist-topology")
    linked_row["evidence_refs"] = sorted({*linked_row["evidence_refs"], previous_ref})
    receipts = [*linked["receipts"], previous]
    receipts.sort(key=lambda item: item["ref"])
    return generate_manifest(
        image_digest=linked["candidate"]["image_digest"],
        release_candidate_profile=linked["selected_profile"],
        requirements=requirements,
        receipts=receipts,
    )


__all__ = ["link_manifest", "verify_receipt"]
