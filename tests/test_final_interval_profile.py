import hashlib
import subprocess

import pytest

from solver.event_store_storage import canonical_bytes
from solver.final_interval_profile import selected_profile_document, verify_selected_profile
from solver.manifest import generate_manifest
from test_manifest import release_candidate_profile


def _sealed(tmp_path):
    key = tmp_path / "private.pem"
    public = tmp_path / "public.pem"
    subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", key], check=True)
    subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-out", public], check=True)
    manifest = generate_manifest(
        image_digest="sha256:" + "a" * 64,
        release_candidate_profile=release_candidate_profile(),
    )
    document = selected_profile_document(
        manifest,
        signer_public_key_digest=hashlib.sha256(public.read_bytes()).hexdigest(),
        window_seconds=3600,
        final_submission_reserve_seconds=300,
        attempt_floor_seconds=300,
    )
    profile = tmp_path / "final-profile.json"
    signature = tmp_path / "final-profile.sig"
    profile.write_bytes(canonical_bytes(document) + b"\n")
    subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", key, "-in", profile, "-out", signature],
        check=True,
    )
    return manifest, profile, signature, public


def test_sealed_final_interval_row_remains_independently_verifiable_in_provisional_candidate(tmp_path):
    manifest, profile, signature, public = _sealed(tmp_path)

    selected = verify_selected_profile(
        profile,
        signature,
        public,
        manifest,
        trusted_key_digest=hashlib.sha256(public.read_bytes()).hexdigest(),
        expected_image_digest="sha256:" + "a" * 64,
    )

    assert selected["lifecycle"] == "sealed"
    assert selected["aggregate_lifecycle"] == "provisional"
    assert selected["lanes"] == manifest["selected_profile"]["lanes"]


def test_final_interval_row_refuses_another_exact_image(tmp_path):
    manifest, profile, signature, public = _sealed(tmp_path)

    with pytest.raises(ValueError, match="another exact image"):
        verify_selected_profile(
            profile,
            signature,
            public,
            manifest,
            trusted_key_digest=hashlib.sha256(public.read_bytes()).hexdigest(),
            expected_image_digest="sha256:" + "b" * 64,
        )
