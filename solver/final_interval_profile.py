"""Signed, exact-image selection contract for the production final interval."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import canonical_bytes
from solver.submission.authority import (
    ACCOUNT_POST_INTERVAL_SECONDS,
    SUBMISSION_REQUEST_DEADLINE_SECONDS,
    SUBMISSION_UNCERTAINTY_MARGIN_SECONDS,
)

KIND = "final-interval-selected-profile"
SCHEMA_VERSION = 1


def verify_selected_profile(
    profile_path: Path,
    signature_path: Path,
    public_key_path: Path,
    manifest: Mapping[str, object],
    *,
    trusted_key_digest: str,
    expected_image_digest: str = "",
) -> Mapping[str, object]:
    """Verify a sealed #295 row without promoting its provisional aggregate."""

    public = public_key_path.read_bytes()
    if hashlib.sha256(public).hexdigest() != trusted_key_digest:
        raise ValueError("untrusted evaluator key")
    verified = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-rawin",
            "-pubin",
            "-inkey",
            str(public_key_path),
            "-in",
            str(profile_path),
            "-sigfile",
            str(signature_path),
        ],
        capture_output=True,
        check=False,
    )
    if verified.returncode:
        raise ValueError("invalid final-interval profile signature")
    raw = profile_path.read_bytes()
    document = json.loads(raw)
    if raw != canonical_bytes(document) + b"\n":
        raise ValueError("final-interval profile is not canonical")
    required = {
        "schema_version",
        "kind",
        "lifecycle",
        "aggregate_lifecycle",
        "image_digest",
        "profile_digest",
        "lanes",
        "resources",
        "window_seconds",
        "final_submission_reserve_seconds",
        "attempt_floor_seconds",
        "account_post_interval_seconds",
        "submission_request_deadline_seconds",
        "submission_uncertainty_margin_seconds",
        "maximum_authority_effect_reservation",
        "signer_public_key_digest",
    }
    if set(document) != required or document["schema_version"] != SCHEMA_VERSION or document["kind"] != KIND:
        raise ValueError("final-interval profile shape is invalid")
    if document["lifecycle"] != "sealed" or document["aggregate_lifecycle"] not in {"provisional", "sealed"}:
        raise ValueError("final-interval row is not sealed")
    selected = manifest["selected_profile"]
    candidate = manifest["candidate"]
    if (
        document["image_digest"] != candidate["image_digest"]
        or document["profile_digest"] != selected["profile_digest"]
        or document["lanes"] != selected["lanes"]
        or document["resources"] != selected["resources"]
        or document["maximum_authority_effect_reservation"]
        != selected["storage"]["maximum_authority_effect_reservation"]
        or document["signer_public_key_digest"] != trusted_key_digest
    ):
        raise ValueError("final-interval profile does not match the selected Candidate")
    if expected_image_digest and document["image_digest"] != expected_image_digest:
        raise ValueError("final-interval profile names another exact image")
    if not isinstance(document["window_seconds"], int) or document["window_seconds"] <= 0:
        raise ValueError("final-interval window must be positive")
    if not isinstance(document["final_submission_reserve_seconds"], int) or not isinstance(
        document["attempt_floor_seconds"], int
    ):
        raise ValueError("final-interval reserves must be integers")
    for name in (
        "account_post_interval_seconds",
        "submission_request_deadline_seconds",
        "submission_uncertainty_margin_seconds",
    ):
        if not isinstance(document[name], (int, float)) or isinstance(document[name], bool) or document[name] <= 0:
            raise ValueError("final-interval submission timing must be positive")
    return document


def selected_profile_document(
    manifest: Mapping[str, object],
    *,
    signer_public_key_digest: str,
    window_seconds: int,
    final_submission_reserve_seconds: int,
    attempt_floor_seconds: int,
    account_post_interval_seconds: float = ACCOUNT_POST_INTERVAL_SECONDS,
    submission_request_deadline_seconds: float = SUBMISSION_REQUEST_DEADLINE_SECONDS,
    submission_uncertainty_margin_seconds: float = SUBMISSION_UNCERTAINTY_MARGIN_SECONDS,
) -> dict[str, object]:
    """Build the canonical unsigned row signed only by the host Evaluator."""

    selected = manifest["selected_profile"]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": KIND,
        "lifecycle": "sealed",
        "aggregate_lifecycle": manifest["lifecycle"],
        "image_digest": manifest["candidate"]["image_digest"],
        "profile_digest": selected["profile_digest"],
        "lanes": selected["lanes"],
        "resources": selected["resources"],
        "window_seconds": window_seconds,
        "final_submission_reserve_seconds": final_submission_reserve_seconds,
        "attempt_floor_seconds": attempt_floor_seconds,
        "account_post_interval_seconds": account_post_interval_seconds,
        "submission_request_deadline_seconds": submission_request_deadline_seconds,
        "submission_uncertainty_margin_seconds": submission_uncertainty_margin_seconds,
        "maximum_authority_effect_reservation": selected["storage"]["maximum_authority_effect_reservation"],
        "signer_public_key_digest": signer_public_key_digest,
    }
