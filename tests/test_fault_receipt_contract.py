import base64
import hashlib
import json
import subprocess

import pytest

from solver.fault_receipt_contract import link_manifest, verify_receipt
from solver.manifest import generate_manifest, manifest_digest
from test_manifest import release_candidate_profile


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def signer(root, name):
    private, public = root / f"{name}.key", root / f"{name}.pub"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", private], check=True)
    subprocess.run(["openssl", "pkey", "-in", private, "-pubout", "-out", public], check=True)
    return private, public.read_bytes()


def candidate(key):
    digest = hashlib.sha256(key[1]).hexdigest()
    return generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
        receipts=[{"ref": "trust:fault-controller", "kind": "fault-controller-trust-anchor", "digest": digest}],
    )


def document(image, profile):
    matrix = (
        ("crash-before-submit", "pre-effect-crash", "submission.before-effect", "effect-absent", "process-crashed"),
        (
            "ambiguous-submit",
            "post-effect-ambiguity",
            "submission.after-effect",
            "effect-present",
            "outcome-unknown",
        ),
        ("slow-board", "delay", "board.response", "response-delayed", "timeout-observed"),
        ("full-storage", "storage-pressure", "storage.reserve", "reservation-refused", "write-refused"),
    )
    schedule = [
        {"name": name, "kind": kind, "control_point": point, "magnitude": 20} for name, kind, point, _, _ in matrix
    ]
    activations = []
    for index, (name, kind, point, boundary_effect, solver_effect) in enumerate(matrix):
        requested = index * 30_000_000 + 10
        activations.append(
            {
                "activation_id": f"83d19c22-a22a-4b88-867f-24f6d097dc2{index}",
                "name": name,
                "intended_kind": kind,
                "control_point": point,
                "control_point_version": "public-double/v1",
                "magnitude": 20,
                "requested_ns": requested,
                "injected_ns": requested + 2,
                "cleared_ns": requested + 20_000_002,
                "injection_deviation_ns": 2,
                "boundary_effect": boundary_effect,
                "solver_effect": solver_effect,
            }
        )
    result = {
        "schema": "fault-controller/v1",
        "candidate_id": image,
        "profile_id": profile,
        "control_point_versions": {point: "public-double/v1" for _, _, point, _, _ in matrix},
        "schedule": schedule,
        "schedule_digest": hashlib.sha256(canonical(schedule)).hexdigest(),
        "infrastructure_valid": True,
        "infrastructure_failure": None,
        "solver_outcome": "failed",
        "activations": activations,
    }
    result["trace_digest"] = hashlib.sha256(canonical(result)).hexdigest()
    return result


def seal(root, value, key, bound_manifest):
    value = dict(value)
    value.update(
        manifest_digest=bound_manifest,
        signer_public_key_digest=hashlib.sha256(key[1]).hexdigest(),
        signer_public_key=base64.b64encode(key[1]).decode(),
    )
    body, signature = root / "body", root / "sig"
    body.write_bytes(canonical(value) + b"\n")
    subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", key[0], "-in", body, "-out", signature],
        check=True,
    )
    value["signature"] = base64.b64encode(signature.read_bytes()).decode()
    return canonical(value) + b"\n"


def reseal(root, value, key, bound_manifest):
    for field in ("manifest_digest", "signer_public_key_digest", "signer_public_key", "signature", "trace_digest"):
        value.pop(field, None)
    value["schedule_digest"] = hashlib.sha256(canonical(value["schedule"])).hexdigest()
    value["trace_digest"] = hashlib.sha256(canonical(value)).hexdigest()
    return seal(root, value, key, bound_manifest)


def test_signed_receipt_links_exact_manifest(tmp_path):
    key = signer(tmp_path, "trusted")
    manifest = candidate(key)
    raw = seal(
        tmp_path,
        document(manifest["candidate"]["image_digest"], manifest["selected_profile"]["profile_digest"]),
        key,
        manifest_digest(manifest),
    )
    linked = link_manifest(manifest, raw)
    row = next(row for row in linked["requirements"] if row["row_id"] == "core.controlled-proofs")
    assert row["receipt_ref"] == "receipt:fault-controller"


@pytest.mark.parametrize(
    "missing_kind",
    (None, "pre-effect-crash", "post-effect-ambiguity", "delay", "storage-pressure"),
)
def test_signed_incomplete_mandatory_matrix_cannot_attach(tmp_path, missing_kind):
    key = signer(tmp_path, "trusted")
    manifest = candidate(key)
    value = document(manifest["candidate"]["image_digest"], manifest["selected_profile"]["profile_digest"])
    if missing_kind is None:
        value["schedule"] = []
        value["activations"] = []
        value["control_point_versions"] = {}
    else:
        value["schedule"] = [item for item in value["schedule"] if item["kind"] != missing_kind]
        value["activations"] = [item for item in value["activations"] if item["intended_kind"] != missing_kind]
        used_points = {item["control_point"] for item in value["schedule"]}
        value["control_point_versions"] = {
            point: version for point, version in value["control_point_versions"].items() if point in used_points
        }
    raw = reseal(tmp_path, value, key, manifest_digest(manifest))
    verify_receipt(raw)
    with pytest.raises(ValueError, match="mandatory fault matrix"):
        link_manifest(manifest, raw)


def test_forgery_cross_key_and_cross_candidate_fail(tmp_path):
    trusted, attacker = signer(tmp_path, "trusted"), signer(tmp_path, "attacker")
    manifest = candidate(trusted)
    md = manifest_digest(manifest)
    wrong = seal(tmp_path, document("sha256:" + "b" * 64, manifest["selected_profile"]["profile_digest"]), trusted, md)
    forged = seal(
        tmp_path,
        document(manifest["candidate"]["image_digest"], manifest["selected_profile"]["profile_digest"]),
        attacker,
        md,
    )
    with pytest.raises(ValueError, match="different candidate"):
        link_manifest(manifest, wrong)
    with pytest.raises(ValueError, match="trusted authority"):
        link_manifest(manifest, forged)
    with pytest.raises(ValueError, match="digest|signature"):
        verify_receipt(forged.replace(b"response-delayed", b"response-omitted"))


@pytest.mark.parametrize("failure", ("controller-lost", "clock-drift", "incomplete-schedule"))
def test_invalid_receipt_cannot_attach(tmp_path, failure):
    key = signer(tmp_path, "trusted")
    manifest = candidate(key)
    value = document(manifest["candidate"]["image_digest"], manifest["selected_profile"]["profile_digest"])
    value.update(infrastructure_valid=False, infrastructure_failure=failure, solver_outcome="not-observed")
    if failure == "incomplete-schedule":
        value["activations"] = []
    with pytest.raises(ValueError, match="qualifying complete proof"):
        link_manifest(manifest, reseal(tmp_path, value, key, manifest_digest(manifest)))


def test_signed_hostile_value_is_rejected(tmp_path):
    key = signer(tmp_path, "trusted")
    manifest = candidate(key)
    value = document("sha256:" + "a" * 64, "profile-v1")
    value["schedule"][0]["name"] = value["activations"][0]["name"] = "/Users/operator/oracle"
    with pytest.raises(ValueError, match="unsupported"):
        verify_receipt(reseal(tmp_path, value, key, manifest_digest(manifest)))
