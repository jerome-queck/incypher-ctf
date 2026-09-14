"""Generate the sanitized controlled final-interval capsule and host signature."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

try:
    from scripts.eval_final_interval import evaluate
except ModuleNotFoundError:  # direct `python scripts/qualify_final_interval.py`
    from eval_final_interval import evaluate
from solver.event_store_storage import atomic_write, canonical_bytes
from solver.final_interval import FinalIntervalController, RunInventory
from solver.final_interval_evaluator import link_manifest
from solver.final_interval_profile import selected_profile_document
from solver.manifest import generate_manifest

UTC = dt.timezone.utc


def qualify(private_key: Path, destination: Path, source_manifest: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    opened = dt.datetime(2026, 9, 22, 1, tzinfo=UTC)
    ends = opened + dt.timedelta(hours=1)
    clock = [ends - dt.timedelta(seconds=120)]
    reservations = []
    final = FinalIntervalController(
        state=destination / ".controlled-state",
        run_id="controlled-final-interval",
        opened_at=opened,
        ends_at=ends,
        final_submission_reserve_seconds=60,
        attempt_floor_seconds=300,
        enabled_lanes=("lane-1", "lane-2"),
        now=lambda: clock[0],
        reserve=lambda identity: reservations.append(identity),
    )
    for lane, challenge in (("lane-1", "challenge-a"), ("lane-2", "challenge-b")):
        final.admit_final_chance(lane, challenge)
    clock[0] = final.cutoff
    board_posts = []

    def submit(candidate):
        board_posts.append(candidate)
        return "possibly-sent" if candidate == "candidate-a" else "accepted"

    final.drain(("candidate-a", "candidate-b"), submit)
    restarted = FinalIntervalController(
        state=destination / ".controlled-state",
        run_id="controlled-final-interval",
        opened_at=opened,
        ends_at=ends,
        final_submission_reserve_seconds=60,
        attempt_floor_seconds=300,
        enabled_lanes=("lane-1", "lane-2"),
        now=lambda: clock[0],
        reserve=lambda identity: reservations.append(identity),
    )
    restarted.drain(("candidate-a",), submit)
    clock[0] = ends
    restarted.close(
        RunInventory(attempts=("attempt-unsettled",), instances=("lease-unsettled",), submissions=("candidate-a",)),
        lambda: {"lease-released": "released", "lease-unsettled": "unsettled"},
    )
    atomic_write(destination / "final-interval.receipt.json", canonical_bytes(restarted.receipt()) + b"\n")
    observation = {
        "schema_version": 1,
        "run_id": "controlled-final-interval",
        "board_posts": board_posts,
        "cleanup": {"lease-released": "released", "lease-unsettled": "unsettled"},
        "observed_close_at": ends.isoformat(),
    }
    atomic_write(destination / "host-observation.json", canonical_bytes(observation) + b"\n")
    manifest = json.loads(source_manifest.read_bytes())
    public = subprocess.run(["openssl", "pkey", "-in", private_key, "-pubout"], check=True, capture_output=True).stdout
    if not any(row["kind"] == "evaluator-trust-anchor" for row in manifest["receipts"]):
        receipts = [
            *manifest["receipts"],
            {
                "ref": "trust:evaluator-ed25519",
                "kind": "evaluator-trust-anchor",
                "digest": hashlib.sha256(public).hexdigest(),
            },
        ]
        manifest = generate_manifest(
            image_digest=manifest["candidate"]["image_digest"],
            release_candidate_profile=manifest["selected_profile"],
            requirements=manifest["requirements"],
            receipts=receipts,
        )
    selected_profile = selected_profile_document(
        manifest,
        signer_public_key_digest=hashlib.sha256(public).hexdigest(),
        window_seconds=3600,
        final_submission_reserve_seconds=60,
        attempt_floor_seconds=300,
    )
    selected_profile_path = destination / "final-interval-profile.json"
    selected_profile_signature = destination / "final-interval-profile.sig"
    atomic_write(selected_profile_path, canonical_bytes(selected_profile) + b"\n")
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            private_key,
            "-in",
            selected_profile_path,
            "-out",
            selected_profile_signature,
        ],
        check=True,
    )
    receipt = evaluate(destination, manifest, private_key, destination / "final-interval.evaluator.json")
    linked = link_manifest(manifest, receipt)
    atomic_write(destination / "candidate-manifest.json", canonical_bytes(linked) + b"\n")
    state = destination / ".controlled-state"
    for path in sorted(state.rglob("*"), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    state.rmdir()
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluator-private-key", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    arguments = parser.parse_args()
    qualify(arguments.evaluator_private_key, arguments.destination, arguments.source_manifest)
