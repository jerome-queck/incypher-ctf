"""Independent signed host-Evaluator contract for final-interval evidence."""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

from solver.event_store_storage import canonical_bytes, digest_bytes
from solver.final_interval import verify_receipt as verify_runtime_receipt
from solver.manifest import attach_requirement_receipt, manifest_digest
from solver.submission.ambiguity import verify_receipt as verify_ambiguity_receipt
from solver.submission.ambiguity_types import CompleteSubmissionIdentity
from solver.submission.receipt import verify_receipt as verify_submission_receipt

KIND = "final-interval-evaluator"
REF = "receipt:final-interval"
ROW = "core.submission-tail"
SOURCES = (
    "final-interval.receipt.json",
    "host-observation.json",
    "final-interval-profile.json",
    "final-interval-profile.sig",
    "configuration.json",
    "configuration.sig",
    "production-observation.json",
    "serial-submission.receipt.json",
    "ambiguous-submission.receipt.json",
)


def verify_receipt(path: Path, source_root: Path | None = None) -> Path:
    receipt_path = Path(path)
    document = json.loads(receipt_path.read_bytes())
    if receipt_path.read_bytes() != canonical_bytes(document) + b"\n":
        raise ValueError("final-interval Evaluator receipt is not canonical")
    if (
        set(document)
        != {
            "schema_version",
            "kind",
            "producer",
            "binding",
            "sources",
            "sanitization",
            "verdict",
            "signer_public_key",
            "signer_public_key_digest",
            "signature",
        }
        or document["schema_version"] != 1
        or document["kind"] != KIND
        or document["producer"] != "external-evaluator"
        or document["verdict"] != "pass"
        or document["sanitization"]
        != {"candidate_values_excluded": True, "credentials_excluded": True, "host_paths_excluded": True}
    ):
        raise ValueError("final-interval Evaluator receipt shape is invalid")
    root = source_root or receipt_path.parent
    sources = document["sources"]
    if not isinstance(sources, Mapping) or set(sources) != set(SOURCES):
        raise ValueError("final-interval Evaluator source set is incomplete")
    bodies = {}
    for name in SOURCES:
        raw = (root / name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != sources[name]:
            raise ValueError("final-interval Evaluator source digest is invalid")
        if name.endswith(".json"):
            bodies[name] = json.loads(raw)
    runtime = bodies["final-interval.receipt.json"]
    host = bodies["host-observation.json"]
    selected_profile = bodies["final-interval-profile.json"]
    configuration = bodies["configuration.json"]
    production = bodies["production-observation.json"]
    serial = bodies["serial-submission.receipt.json"]
    ambiguity = bodies["ambiguous-submission.receipt.json"]
    verify_runtime_receipt(runtime)
    verify_submission_receipt(root / "serial-submission.receipt.json")
    verify_ambiguity_receipt(root / "ambiguous-submission.receipt.json")
    _verify_host_observation(runtime, host, production)
    if (
        selected_profile["lifecycle"] != "sealed"
        or selected_profile["image_digest"] != document["binding"]["image_digest"]
    ):
        raise ValueError("Evaluator did not observe a sealed exact-image final-interval profile")
    _verify_profile_signature(root, document)
    _verify_control_signature(root, document, "configuration.json", "configuration.sig")
    _verify_production_observation(runtime, production, serial, ambiguity, configuration, document)
    _verify_signature(document)
    return receipt_path


def _verify_host_observation(runtime, host, production) -> None:
    if set(host) != {
        "schema_version",
        "run_id",
        "board_posts",
        "released_instance_challenges",
        "instances_after",
        "observed_close_at",
    }:
        raise ValueError("host observation shape is invalid")
    posts = host["board_posts"]
    production_posts = [
        {key: request.get(key) for key in ("body_digest", "candidate_digest", "challenge_id")}
        for request in production.get("board_requests", ())
        if request.get("method") == "POST" and request.get("path") == "/api/v1/challenges/attempt"
    ]
    released = host["released_instance_challenges"]
    runtime_candidates = [
        row["candidate_id"] for row in runtime.get("trace", ()) if row.get("record") == "drain-result"
    ]
    if (
        host["schema_version"] != 1
        or host["run_id"] != runtime["run_id"]
        or not isinstance(posts, list)
        or any(
            not isinstance(row, Mapping) or set(row) != {"body_digest", "candidate_digest", "challenge_id"}
            for row in posts
        )
        or len({row["body_digest"] for row in posts}) != len(posts)
        or len(posts) != len(runtime_candidates)
        or posts != production_posts
        or len(runtime_candidates) != len(set(runtime_candidates))
        or not isinstance(released, list)
        or len(released) != len(set(released))
        or host["instances_after"] != []
        or any(runtime["terminal"]["cleanup"].get(f"instance:{challenge}") != "released" for challenge in released)
        or dt_from(host["observed_close_at"]) < dt_from(runtime["window"]["ends_at"])
    ):
        raise ValueError("host observation contradicts final-interval authority")


def dt_from(value):
    import datetime as dt

    parsed = dt.datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError("host clock is not timezone-aware")
    return parsed


def _verify_signature(document) -> None:
    public = base64.b64decode(document["signer_public_key"], validate=True)
    signature = base64.b64decode(document["signature"], validate=True)
    if hashlib.sha256(public).hexdigest() != document["signer_public_key_digest"]:
        raise ValueError("final-interval Evaluator signer identity is invalid")
    unsigned = dict(document)
    unsigned.pop("signature")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        body, key, signed = root / "body", root / "key", root / "signature"
        body.write_bytes(canonical_bytes(unsigned) + b"\n")
        key.write_bytes(public)
        signed.write_bytes(signature)
        result = subprocess.run(
            ["openssl", "pkeyutl", "-verify", "-rawin", "-pubin", "-inkey", key, "-in", body, "-sigfile", signed],
            capture_output=True,
            check=False,
        )
    if result.returncode:
        raise ValueError("final-interval Evaluator signature is invalid")


def _verify_profile_signature(root: Path, document) -> None:
    _verify_control_signature(root, document, "final-interval-profile.json", "final-interval-profile.sig")


def _verify_control_signature(root: Path, document, source: str, signature: str) -> None:
    public = base64.b64decode(document["signer_public_key"], validate=True)
    with tempfile.TemporaryDirectory() as directory:
        key = Path(directory) / "evaluator-public.pem"
        key.write_bytes(public)
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                key,
                "-in",
                root / source,
                "-sigfile",
                root / signature,
            ],
            capture_output=True,
            check=False,
        )
    if result.returncode:
        raise ValueError(f"signed final-interval source is invalid: {source}")


def _verify_production_observation(runtime, production, serial, ambiguity, configuration, document) -> None:
    container = production.get("container", {})
    if (
        configuration.get("kind") != "exact-image-qualification-clock"
        or configuration.get("image_digest") != document["binding"]["image_digest"]
        or container.get("image_manifest_digest") != document["binding"]["image_digest"]
        or container.get("image_id") != document["binding"]["image_digest"]
        or container.get("default_entrypoint") != ["python3", "-m", "solver.supervisor"]
        or container.get("strict_profile") is not True
    ):
        raise ValueError("production observation is not the strict exact-image default entrypoint")
    if container.get("exit_code") != 0:
        raise ValueError("production observation lacks a successful exact-image Run")
    terminal = runtime.get("terminal", {})
    if terminal.get("closed_at") != runtime.get("window", {}).get("ends_at"):
        raise ValueError("production observation did not reach the official close")
    remaining = terminal.get("remaining", {})
    if (
        production.get("schema_version") != 1
        or production.get("instances_after") != []
        or remaining.get("attempts")
        or remaining.get("instances")
        or any(outcome != "released" for outcome in terminal.get("cleanup", {}).values())
    ):
        raise ValueError("production observation did not reach clean terminal inventory")
    posts = [
        request
        for request in production.get("board_requests", [])
        if request.get("method") == "POST" and request.get("path") == "/api/v1/challenges/attempt"
    ]
    count = production.get("submission_count")
    if (
        not isinstance(count, int)
        or count != len(posts)
        or count != len(runtime.get("submissions", {}))
        or count != len(serial.get("submissions", ()))
        or (count > 0 and serial.get("in_flight_maximum") != 1)
        or any(
            request.get("authenticated") is not True
            or not request.get("body_digest")
            or not request.get("candidate_digest")
            or not isinstance(request.get("challenge_id"), int)
            for request in posts
        )
        or len({request["body_digest"] for request in posts}) != len(posts)
    ):
        raise ValueError("production observation does not prove one Board POST per final Candidate")
    _verify_submission_bindings(runtime, posts, serial, ambiguity)
    ambiguous_rows = [row for row in serial.get("submissions", ()) if row.get("states", [])[-1:] == ["possibly-sent"]]
    starts = [row for row in ambiguity.get("events", ()) if row.get("event") == "possibly-sent"]
    final = {row.get("candidate_id"): row for row in ambiguity.get("final_dispositions", ())}
    pending = sorted(candidate for candidate, row in final.items() if row.get("disposition") == "pending")
    if (
        {row.get("effect_id") for row in ambiguous_rows} != {row.get("effect_id") for row in starts}
        or any(row.get("candidate_id") not in final for row in starts)
        or any(row.get("posts") != 1 for row in ambiguity.get("no_resend_trace", ()))
        or sorted(remaining.get("submissions", ())) != pending
        or any(
            runtime.get("submissions", {}).get(candidate, {}).get("outcome") != "possibly-sent" for candidate in pending
        )
    ):
        raise ValueError("production ambiguity trace is incomplete or permits resend")
    if configuration.get("seed") == "final-interval-v1":
        restart = production.get("restart", {})
        records = restart.get("records_before_crash", ())
        if (
            count != 2
            or restart.get("launches") != 2
            or restart.get("first_exit_code") in {None, 0}
            or "entitlement-spent" not in records
            or sum(row.get("record") == "entitlement-spent" for row in runtime.get("trace", ()))
            > len(runtime.get("enabled_lanes", ()))
        ):
            raise ValueError("controlled final interval lacks restart-safe Lane and Candidate proof")


def _verify_submission_bindings(runtime, posts, serial, ambiguity) -> None:
    wire_rows = [row for row in ambiguity.get("events", ()) if row.get("event") == "wire-started"]
    by_candidate = {str(row.get("candidate_id")): row for row in wire_rows}
    serial_by_effect = {str(row.get("effect_id")): row for row in serial.get("submissions", ())}
    runtime_rows = runtime.get("submissions", {})
    post_keys = [(row.get("challenge_id"), row.get("candidate_digest")) for row in posts]
    if (
        not isinstance(runtime_rows, Mapping)
        or len(by_candidate) != len(wire_rows)
        or set(by_candidate) != set(runtime_rows)
        or len(serial_by_effect) != len(serial.get("submissions", ()))
        or len(set(post_keys)) != len(post_keys)
    ):
        raise ValueError("production Candidate identities are not bijective")
    ambiguity_starts = {
        (str(row.get("candidate_id")), str(row.get("effect_id")))
        for row in ambiguity.get("events", ())
        if row.get("event") == "possibly-sent"
    }
    for candidate_id, runtime_row in runtime_rows.items():
        wire = by_candidate[candidate_id]
        supplied = wire.get("complete_identity")
        if not isinstance(supplied, Mapping):
            raise ValueError("production Candidate lacks complete wire identity")
        try:
            complete = CompleteSubmissionIdentity(
                str(supplied["board_identity"]),
                int(supplied["challenge_id"]),
                str(supplied["challenge_revision"]),
                str(supplied["instance_provenance"]),
                str(supplied["candidate_digest"]),
                int(supplied["submission_epoch"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("production Candidate complete identity is invalid") from error
        serial_row = serial_by_effect.get(complete.effect_id)
        states = serial_row.get("states", ()) if isinstance(serial_row, Mapping) else ()
        outcome = runtime_row.get("outcome") if isinstance(runtime_row, Mapping) else None
        if (
            dict(supplied) != complete.document()
            or wire.get("effect_id") != complete.effect_id
            or serial_row is None
            or serial_row.get("reservation_id") != complete.reservation_id
            or serial_row.get("candidate_id") != complete.payload_identity
            or post_keys.count((complete.challenge_id, wire.get("supplied_value_digest"))) != 1
        ):
            raise ValueError("production Candidate does not bind wire, serial authority, and Board POST")
        result = serial_row.get("result", {})
        if outcome == "possibly-sent":
            valid = states[-1:] == ["possibly-sent"] and (candidate_id, complete.effect_id) in ambiguity_starts
        elif outcome == "accepted":
            valid = (
                states[-1:] == ["committed"]
                and result.get("candidate_id") == candidate_id
                and result.get("outcome") == "correct"
            )
        elif outcome == "rejected":
            valid = (
                states[-1:] == ["committed"]
                and result.get("candidate_id") == candidate_id
                and result.get("outcome") in {"incorrect", "already_solved"}
            )
        else:
            valid = False
        if not valid:
            raise ValueError("production Candidate outcome contradicts its serial authority")


def descriptor(path: Path) -> dict[str, str]:
    verified = verify_receipt(path)
    return {"ref": REF, "kind": KIND, "digest": digest_bytes(verified.read_bytes())}


def link_manifest(manifest: Mapping[str, object], path: Path):
    document = json.loads(Path(path).read_bytes())
    trusted = [item["digest"] for item in manifest["receipts"] if item["kind"] == "evaluator-trust-anchor"]
    if (
        len(trusted) != 1
        or trusted[0] != document["signer_public_key_digest"]
        or document["binding"]["manifest_digest"] != manifest_digest(manifest)
        or document["binding"]["image_digest"] != manifest["candidate"]["image_digest"]
    ):
        raise ValueError("final-interval receipt belongs to another manifest or signer")
    return attach_requirement_receipt(manifest, ROW, descriptor(path))


__all__ = ["descriptor", "link_manifest", "verify_receipt"]
