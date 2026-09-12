"""Minimal Solver consumer contract for external fault-controller receipts."""

from __future__ import annotations

import hashlib
import json
import re
import base64
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import canonical_bytes

MANIFEST_ROW_ID = "core.controlled-proofs"
MANIFEST_RECEIPT_REF = "receipt:fault-controller"
_IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_PROFILE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_NAME = _PROFILE
_POINT = re.compile(r"[a-z][a-z0-9-]*(?:\.[a-z][a-z0-9-]*)+")
_VERSION = re.compile(r"[a-z][a-z0-9-]*/v[1-9][0-9]*")
_FAILURES = {
    "boundary-activation-failed",
    "boundary-activation-lifecycle",
    "boundary-handshake-failed",
    "boundary-observation-failed",
    "clock-drift",
    "control-point-handshake",
    "controller-lost",
    "duplicate-activation",
    "effect-mismatch",
    "incomplete-schedule",
    "unknown-activation",
    "unsupported-control-point",
}
_EFFECTS = {
    "pre-effect-crash": ("effect-absent", "process-crashed"),
    "post-effect-ambiguity": ("effect-present", "outcome-unknown"),
    "delay": ("response-delayed", "timeout-observed"),
    "storage-pressure": ("reservation-refused", "write-refused"),
}
_MANDATORY_MATRIX = {
    ("pre-effect-crash", "submission.before-effect"),
    ("post-effect-ambiguity", "submission.after-effect"),
    ("delay", "board.response"),
    ("storage-pressure", "storage.reserve"),
}


def verify_receipt(raw: bytes) -> dict[str, object]:
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("fault-controller receipt is not JSON") from error
    if not isinstance(document, dict) or raw != canonical_bytes(document) + b"\n":
        raise ValueError("fault-controller receipt is not canonical JSON")
    required = {
        "schema",
        "candidate_id",
        "profile_id",
        "control_point_versions",
        "schedule",
        "schedule_digest",
        "infrastructure_valid",
        "infrastructure_failure",
        "solver_outcome",
        "activations",
        "trace_digest",
        "manifest_digest",
        "signer_public_key_digest",
        "signer_public_key",
        "signature",
    }
    if set(document) != required or document["schema"] != "fault-controller/v1":
        raise ValueError("fault-controller receipt shape is invalid")
    supplied = document["trace_digest"]
    unsigned = dict(document)
    for field in ("trace_digest", "manifest_digest", "signer_public_key_digest", "signer_public_key", "signature"):
        unsigned.pop(field)
    if not isinstance(supplied, str) or supplied != _digest(canonical_bytes(unsigned)):
        raise ValueError("fault-controller trace digest is invalid")
    _verify_signature(document)
    if not _IMAGE.fullmatch(str(document["candidate_id"])) or not _PROFILE.fullmatch(str(document["profile_id"])):
        raise ValueError("fault-controller candidate/profile binding is invalid")
    schedule = document["schedule"]
    if not isinstance(schedule, list) or document["schedule_digest"] != _digest(canonical_bytes(schedule)):
        raise ValueError("fault-controller schedule digest is invalid")
    _verify_classification(document)
    _verify_activations(document, schedule)
    return document


def _verify_classification(document: Mapping[str, object]) -> None:
    valid = document["infrastructure_valid"]
    failure = document["infrastructure_failure"]
    outcome = document["solver_outcome"]
    if not isinstance(valid, bool) or valid != (failure is None):
        raise ValueError("fault-controller infrastructure classification is invalid")
    if failure not in {None, *_FAILURES}:
        raise ValueError("fault-controller infrastructure failure is unsupported")
    if outcome not in {"succeeded", "failed", "not-observed"} or (not valid and outcome != "not-observed"):
        raise ValueError("fault-controller Solver classification is invalid")


def _verify_activations(document: Mapping[str, object], schedule: list[object]) -> None:
    versions = document["control_point_versions"]
    activations = document["activations"]
    if not isinstance(versions, dict) or not isinstance(activations, list):
        raise ValueError("fault-controller activation collection is invalid")
    if any(
        not isinstance(point, str)
        or _POINT.fullmatch(point) is None
        or not isinstance(version, str)
        or _VERSION.fullmatch(version) is None
        for point, version in versions.items()
    ):
        raise ValueError("fault-controller control-point versions are invalid")
    planned = set()
    for item in schedule:
        if not isinstance(item, dict) or set(item) != {
            "name",
            "kind",
            "control_point",
            "magnitude",
        }:
            raise ValueError("fault-controller schedule entry is invalid")
        identity = (item["name"], item["kind"], item["control_point"], item["magnitude"])
        if (
            not isinstance(item["name"], str)
            or _NAME.fullmatch(item["name"]) is None
            or not isinstance(item["control_point"], str)
            or _POINT.fullmatch(item["control_point"]) is None
            or type(item["magnitude"]) is not int
            or item["magnitude"] <= 0
            or item["kind"] not in _EFFECTS
            or identity in planned
        ):
            raise ValueError("fault-controller schedule entry is unsupported or duplicated")
        planned.add(identity)
    observed = set()
    last = -1
    fields = {
        "activation_id",
        "name",
        "intended_kind",
        "control_point",
        "control_point_version",
        "magnitude",
        "requested_ns",
        "injected_ns",
        "cleared_ns",
        "injection_deviation_ns",
        "boundary_effect",
        "solver_effect",
    }
    for item in activations:
        if not isinstance(item, dict) or set(item) != fields:
            raise ValueError("fault-controller activation shape is invalid")
        identity = (
            item["name"],
            item["intended_kind"],
            item["control_point"],
            item["magnitude"],
        )
        if identity not in planned or identity in observed:
            raise ValueError("fault-controller activation does not match schedule")
        observed.add(identity)
        try:
            uuid.UUID(str(item["activation_id"]))
        except ValueError as error:
            raise ValueError("fault-controller activation ID is invalid") from error
        requested = item["requested_ns"]
        injected = item["injected_ns"]
        cleared = item["cleared_ns"]
        deviation = item["injection_deviation_ns"]
        if (
            type(requested) is not int
            or type(injected) is not int
            or type(cleared) is not int
            or requested < last
            or injected < requested
            or cleared < injected
            or deviation != injected - requested
        ):
            raise ValueError("fault-controller lifecycle timing is invalid")
        last = cleared
        point = item["control_point"]
        if item["control_point_version"] != versions.get(point):
            raise ValueError("fault-controller control-point version is unbound")
        if (item["boundary_effect"], item["solver_effect"]) != _EFFECTS[item["intended_kind"]]:
            raise ValueError("fault-controller effects are unreconciled")
    if document["infrastructure_valid"] and observed != planned:
        raise ValueError("fault-controller valid receipt has an incomplete schedule")


def manifest_receipt(raw: bytes) -> dict[str, str]:
    verify_receipt(raw)
    return {
        "ref": MANIFEST_RECEIPT_REF,
        "kind": "fault-controller",
        "digest": hashlib.sha256(raw).hexdigest(),
    }


def link_manifest(manifest: Mapping[str, object], raw: bytes):
    from solver.manifest import attach_requirement_receipt, manifest_digest

    receipt = verify_receipt(raw)
    schedule = receipt["schedule"]
    activations = receipt["activations"]
    if (
        receipt["infrastructure_valid"] is not True
        or not isinstance(schedule, list)
        or not isinstance(activations, list)
        or len(schedule) != len(activations)
    ):
        raise ValueError("fault-controller receipt is not a qualifying complete proof")
    scheduled_matrix = {(item["kind"], item["control_point"]) for item in schedule}
    if not _MANDATORY_MATRIX <= scheduled_matrix:
        raise ValueError("fault-controller receipt lacks the mandatory fault matrix")
    candidate = manifest.get("candidate")
    profile = manifest.get("selected_profile")
    trusted = _trusted_key_digest(manifest)
    if (
        not isinstance(candidate, Mapping)
        or not isinstance(profile, Mapping)
        or receipt["candidate_id"] != candidate.get("image_digest")
        or receipt["profile_id"] != profile.get("profile_digest")
        or receipt["manifest_digest"] != manifest_digest(manifest)
    ):
        raise ValueError("fault-controller receipt belongs to a different candidate manifest")
    if receipt["signer_public_key_digest"] != trusted:
        raise ValueError("fault-controller receipt signer is not the trusted authority")
    return attach_requirement_receipt(manifest, MANIFEST_ROW_ID, manifest_receipt(raw))


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _verify_signature(document: Mapping[str, object]) -> None:
    if not _DIGEST.fullmatch(str(document["manifest_digest"])):
        raise ValueError("fault-controller manifest binding is invalid")
    try:
        key = base64.b64decode(document["signer_public_key"], validate=True)
        signature = base64.b64decode(document["signature"], validate=True)
    except (TypeError, ValueError) as error:
        raise ValueError("fault-controller signature is invalid") from error
    if hashlib.sha256(key).hexdigest() != document["signer_public_key_digest"]:
        raise ValueError("fault-controller signer identity is invalid")
    unsigned = dict(document)
    unsigned.pop("signature")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        body, public, signed = root / "body", root / "public", root / "signature"
        body.write_bytes(canonical_bytes(unsigned) + b"\n")
        public.write_bytes(key)
        signed.write_bytes(signature)
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", public, "-in", body, "-sigfile", signed],
            capture_output=True,
            check=False,
        )
    if result.returncode:
        raise ValueError("fault-controller signature is invalid")


def _trusted_key_digest(manifest: Mapping[str, object]) -> str:
    receipts = manifest.get("receipts")
    if not isinstance(receipts, list):
        raise ValueError("candidate manifest has no fault-controller trust anchor")
    values = [
        item.get("digest")
        for item in receipts
        if isinstance(item, Mapping) and item.get("kind") == "fault-controller-trust-anchor"
    ]
    if len(values) != 1 or not _DIGEST.fullmatch(str(values[0])):
        raise ValueError("candidate manifest has no unique fault-controller trust anchor")
    return str(values[0])


__all__ = ["link_manifest", "manifest_receipt", "verify_receipt"]
