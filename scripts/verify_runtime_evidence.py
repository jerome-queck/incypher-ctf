"""Independently verify retained, externally signed runtime qualification capsules."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


REQUIRED_FIXED_POINT = {
    "image_manifest_digest",
    "image_config_digest",
    "platform",
    "runtime_profile_digest",
    "catalogue_digest",
    "component_inputs_digest",
}
TRUSTED_EVALUATOR_KEY_DIGEST = "672b8a4f435628a41eb590ee563c9e395cbebfbc766accf20efc2e028d302c5b"  # gitleaks:allow
SEALED_HISTORICAL_IMAGES = {"sha256:c7c53ecd627bfcfe9fcb083f4e7d3c909f30fe9a091c881bb0e4b5898387c765"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _child(root: Path, name: str) -> Path:
    candidate = (root / name).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ValueError(f"capsule path escapes its root: {name}") from error
    return candidate


def verify_capsule(capsule: Path, checkout: Path | None) -> dict[str, object]:
    capsule = Path(capsule)
    checkout = Path(checkout) if checkout is not None else None
    manifest = capsule / "capsule.json"
    public = capsule / "evaluator-public.pem"
    signature = capsule / "capsule.sig"
    try:
        raw = manifest.read_bytes()
        document = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("capsule manifest cannot be read as JSON") from error
    canonical = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if raw != canonical or not isinstance(document, dict):
        raise ValueError("capsule manifest is not canonical JSON")
    if document.get("schema_version") != 1 or document.get("kind") != "runtime-qualification-capsule":
        raise ValueError("capsule schema is invalid")
    fixed = document.get("fixed_point")
    if not isinstance(fixed, dict) or set(fixed) != REQUIRED_FIXED_POINT:
        raise ValueError("capsule fixed point is incomplete")
    if checkout is None and fixed["image_manifest_digest"] not in SEALED_HISTORICAL_IMAGES:
        raise ValueError("sealed historical verification is unavailable for this fixed point")
    if (
        document.get("signer_public_key_digest") != TRUSTED_EVALUATOR_KEY_DIGEST
        or _digest(public) != TRUSTED_EVALUATOR_KEY_DIGEST
    ):
        raise ValueError("capsule signer identity changed")
    checked = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-rawin",
            "-pubin",
            "-inkey",
            str(public),
            "-in",
            str(manifest),
            "-sigfile",
            str(signature),
        ],
        capture_output=True,
        check=False,
    )
    if checked.returncode:
        raise ValueError("capsule signature is invalid")
    for name, expected in document.get("artifacts", {}).items():
        path = _child(capsule, name)
        if _digest(path) != expected:
            raise ValueError(f"capsule artifact changed: {name}")
    if checkout is not None:
        for name, expected in document.get("input_files", {}).items():
            path = _child(checkout, name)
            if _digest(path) != expected:
                raise ValueError(f"fixed-point input changed: {name}")
        if _digest(checkout / "tool-supply/generated/inventory.json") != fixed["catalogue_digest"]:
            raise ValueError("fixed-point catalogue digest changed")
    component_map = {
        name: digest
        for name, digest in document["input_files"].items()
        if name.startswith("tool-supply/locks/") or name.startswith("tool-supply/fixtures/")
    }
    component_digest = hashlib.sha256(
        (json.dumps(component_map, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    if not component_map or component_digest != fixed["component_inputs_digest"]:
        raise ValueError("fixed-point component inputs digest changed")
    cleanup = document.get("cleanup_state")
    if not isinstance(cleanup, dict) or cleanup.get("owned_residue") != []:
        raise ValueError("capsule does not retain clean teardown state")
    if not isinstance(document.get("observed_results"), list) or not document["observed_results"]:
        raise ValueError("capsule has no observed result")
    _verify_semantics(capsule, document)
    return document


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError(f"evidence is not an object: {path.name}")
    return value


def _verify_evaluator(path: Path) -> dict[str, object]:
    sealed = _json(path)
    signature = sealed.pop("signature", None)
    try:
        public = base64.b64decode(sealed["signer_public_key"], validate=True)
        signature_bytes = base64.b64decode(signature, validate=True)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("nested Evaluator signature is invalid") from error
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        body, key, sig = root / "body", root / "key", root / "sig"
        body.write_bytes((json.dumps(sealed, sort_keys=True, separators=(",", ":")) + "\n").encode())
        key.write_bytes(public)
        sig.write_bytes(signature_bytes)
        result = subprocess.run(
            [
                "openssl",
                "pkeyutl",
                "-verify",
                "-rawin",
                "-pubin",
                "-inkey",
                str(key),
                "-in",
                str(body),
                "-sigfile",
                str(sig),
            ],
            capture_output=True,
            check=False,
        )
    if (
        result.returncode
        or hashlib.sha256(public).hexdigest() != TRUSTED_EVALUATOR_KEY_DIGEST
        or sealed.get("signer_public_key_digest") != TRUSTED_EVALUATOR_KEY_DIGEST
    ):
        raise ValueError("nested Evaluator signature is invalid")
    return sealed


def _verify_269(capsule: Path, _document: dict[str, object], fixed: dict[str, object]) -> None:
    receipt = _json(capsule / "strict-isolation-preflight.receipt.json")
    if receipt.get("image_id") != fixed["image_manifest_digest"] or receipt.get("owned_residue") != []:
        raise ValueError("#269 receipt is not the clean fixed-point preflight")
    if set(receipt.get("checks", {}).values()) != {"pass"}:
        raise ValueError("#269 deny/preflight checks are incomplete")
    refusals = _json(capsule / "refusals.json")
    if any(refusals.get(name, {}).get("returncode") != 2 for name in ("ordinary_docker_run", "drifted_cgroup_mount")):
        raise ValueError("#269 ordinary and drifted Refusal fixtures are absent")
    if _json(capsule / "residue-inventory.json").get("owned_residue") != []:
        raise ValueError("#269 host residue inventory is not clean")


def _verify_270(capsule: Path, _document: dict[str, object], fixed: dict[str, object]) -> None:
    receipt = _json(capsule / "attempt-resource-envelope.receipt.json")
    outcomes = {row.get("outcome") for row in receipt.get("envelopes", [])}
    required = {
        "exited",
        "cpu-limit",
        "memory-limit",
        "pid-limit",
        "filesystem-limit",
        "network-limit",
        "wall-clock-limit",
    }
    if not required.issubset(outcomes) or receipt.get("binding", {}).get("image_id") != fixed["image_manifest_digest"]:
        raise ValueError("#270 fixed-point resource outcomes are incomplete")
    if any(
        not row.get("cleanup_complete") or row.get("observed", {}).get("processes_after_kill") != 0
        for row in receipt["envelopes"]
    ):
        raise ValueError("#270 cleanup evidence is incomplete")
    reconciled = _json(capsule / "crash-reconciliation.receipt.json").get("envelopes", [])
    if (
        len(reconciled) != 1
        or reconciled[0].get("outcome") != "reconciled-after-crash"
        or reconciled[0].get("observed", {}).get("reconciled") is not True
    ):
        raise ValueError("#270 has no real crash reconciliation outcome")


def _verify_281(capsule: Path, _document: dict[str, object], _fixed: dict[str, object]) -> None:
    green = _verify_evaluator(capsule / "green.evaluator.json")
    failure = _verify_evaluator(capsule / "isolation-failure.evaluator.json")
    if green.get("verdicts") != {"solve": "pass", "isolation": "pass"}:
        raise ValueError("#281 green verdict is absent")
    if failure.get("verdicts") != {"solve": "pass", "isolation": "fail"}:
        raise ValueError("#281 deliberate Isolation failure is absent")


def _verify_283(capsule: Path, _document: dict[str, object], _fixed: dict[str, object]) -> None:
    receipt = _json(capsule / "native-codex-control.receipt.json")
    if receipt.get("secret_probes") != {"environment": "clear", "event": "clear", "file": "clear"}:
        raise ValueError("#283 executor credential probes are not clear")
    expected_catalogue = hashlib.sha256(b'{"catalogue":[{"efforts":["low"],"model":"gpt-5.6-luna"}]}').hexdigest()
    if receipt.get("turn", {}).get("duration_ms", 0) <= 0 or receipt.get("catalogue_digest") != expected_catalogue:
        raise ValueError("#283 has no catalogued measured Turn")
    limits = receipt.get("limits", [])
    if not limits or limits[0].get("used_percent") is not None or limits[0].get("source") != "native-stream-absent":
        raise ValueError("#283 limit absence lost its provenance")
    events = [json.loads(line) for line in (capsule / "events.jsonl").read_text().splitlines()]
    turns = [
        event["payload"]
        for event in events
        if event.get("event_type") == "observation.recorded" and event.get("payload", {}).get("tool") == "codex"
    ]
    if not any(
        turn.get("exit_code") == 0 and turn.get("tokens_out", 0) > 0 and turn.get("usage_known") is True
        for turn in turns
    ):
        raise ValueError("#283 retained native call did not succeed with measured usage")


def _verify_298(capsule: Path, document: dict[str, object], fixed: dict[str, object]) -> None:
    from solver.tool_supply_receipt import validate_receipt

    receipts = [_json(capsule / name) for name in document["artifacts"] if name.startswith("resident.")]
    capabilities = set()
    for receipt in receipts:
        validate_receipt(receipt, expected_image_manifest_digest=str(fixed["image_manifest_digest"]))
        capabilities.update(row.get("capability_id") for row in receipt.get("handle_solve", {}).get("capabilities", []))
    if len(receipts) != 7 or len(capabilities) != 16:
        raise ValueError("#298 does not exercise seven components and sixteen Tool capabilities")


def _verify_external_runtime(capsule: Path, fixed: dict[str, object]) -> None:
    observed = _json(capsule / "runtime-observation.json")
    preflight = _json(capsule / "strict-preflight.json")
    runtime_pin = dict(preflight.get("runtime_pin", []))
    checks = dict(preflight.get("checks", []))
    vm = observed.get("runtime", {})
    expected_root = "/Volumes/Working/001 Projects"
    if (
        observed.get("schema_version") != 1
        or observed.get("state_root") != f"{expected_root}/incypher-ctf/state"
        or observed.get("colima_home") != f"{expected_root}/incypher-colima"
        or observed.get("colima_data") != f"{expected_root}/incypher-colima"
        or not isinstance(vm, dict)
        or vm.get("status") != "Running"
        or vm.get("cpus") != 8
        or vm.get("memory_bytes") != 24 * 1024**3
        or not isinstance(vm.get("disk_bytes"), int)
        or vm.get("disk_bytes") <= 0
        or observed.get("data_disk_bytes") != vm.get("disk_bytes")
        or observed.get("image_manifest_digest") != fixed["image_manifest_digest"]
        or observed.get("image_config_digest") != fixed["image_config_digest"]
        or observed.get("platform") != fixed["platform"]
        or preflight.get("profile_id") != "colima-namespace-cgroup-v1"
        or preflight.get("profile_digest") != fixed["runtime_profile_digest"]
        or preflight.get("image_id") != fixed["image_manifest_digest"]
        or runtime_pin != {"colima": "0.10.3", "cpu": 8, "docker": "29.7.2", "memory_gib": 24}
        or set(checks.values()) != {"pass"}
        or preflight.get("owned_residue") != []
        or preflight.get("processes_after_kill") != 0
    ):
        raise ValueError("qualification did not observe the required external runtime")


def _verify_293(capsule: Path, document: dict[str, object], fixed: dict[str, object]) -> None:
    from solver.submission.receipt import verify_receipt

    _verify_external_runtime(capsule, fixed)
    receipt = capsule / "serial-submission.receipt.json"
    verify_receipt(receipt)
    receipt_document = _json(receipt)
    trace = _json(capsule / "serial-submission.trace.json")
    first, earliest, later = "1" * 64, "2" * 64, "3" * 64
    if (
        receipt_document.get("evidence_class") != "controlled-runtime-trace"
        or receipt_document.get("in_flight_maximum") != 1
        or [row.get("states", [None])[-1] for row in receipt_document.get("submissions", [])]
        != ["committed", "committed", "committed", "possibly-sent", "aborted"]
        or trace
        != {
            "committed_replay_posts": 3,
            "competing_dispatch_order": [first, earliest, later],
            "competing_ready_orders": [0, 2, 1],
            "first_ready_candidate": earliest,
            "possibly_sent_replay": "EffectIndeterminate",
            "possibly_sent_replay_posts": 1,
            "wire_in_flight_maximum": 1,
        }
        or document.get("observed_results")
        != [
            "first-ready:pass",
            "one-in-flight:pass",
            "crash-before:aborted",
            "crash-during:possibly-sent",
            "crash-after:no-resend",
        ]
        or document.get("scanner_annotation") != "gitleaks:allow"
    ):
        raise ValueError("#293 serial submission proof is incomplete")


def _verify_296(capsule: Path, document: dict[str, object], fixed: dict[str, object]) -> None:
    from solver.recovery.incident import verify_receipt

    _verify_external_runtime(capsule, fixed)
    receipt = capsule / "incident-containment.receipt.json"
    verify_receipt(receipt)
    incident = _json(receipt)
    try:
        crash = json.loads(incident["evidence"]["projection"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("#296 real-process evidence is invalid") from error
    process = _json(capsule / "process-trace.json")
    if (
        incident.get("replay_count", 0) < 1
        or incident.get("disposition") != "replacement-admitted"
        or process.get("real_process") is not True
        or process.get("crashed_exit_code") != 17
        or process.get("replacement_exit_code") != 0
        or process.get("crashed_pid") == process.get("replacement_pid")
        or crash.get("pid") != process.get("crashed_pid")
        or crash.get("exit_code") != process.get("crashed_exit_code")
        or crash.get("group_extinguished") is not True
        or process.get("generation_active_after") is not False
        or process.get("group_extinguished") is not True
        or process.get("containment_crash", {}).get("injected") is not True
        or process.get("containment_crash", {}).get("step") != "evidence-capture"
        or process.get("replayed_incident_id") != incident.get("incident_id")
        or process.get("replay_count") != incident.get("replay_count")
        or process.get("concurrency", {}).get("process_count") != 6
        or process.get("concurrency", {}).get("duplicate_reports") != 5
        or len(set(process.get("concurrency", {}).get("incident_ids", []))) != 1
        or document.get("scanner_annotation") != "gitleaks:allow"
    ):
        raise ValueError("#296 Incident containment proof is incomplete")


def _verify_297(capsule: Path, document: dict[str, object], fixed: dict[str, object]) -> None:
    from solver.recovery.catalogue import CATALOGUE_VERSION
    from solver.recovery.incident import verify_receipt

    _verify_external_runtime(capsule, fixed)
    trace = _json(capsule / "deterministic-recovery.trace.json")
    dimensions = {
        "route-local-inference": "inference-route",
        "instance": "instance-authority-join",
        "submission-ambiguity": "submission-epoch",
        "storage": "storage-revision",
    }
    fault_kinds = {
        "worker-crash",
        "route-local-inference",
        "target-research",
        "instance",
        "submission-ambiguity",
        "storage",
        "final-interval",
    }
    changed = trace.get("changed_actions", [])
    unsettled = trace.get("no_inference", [])
    replayed = trace.get("cross_boot", [])
    expired = trace.get("expired_bound", {})
    receipt_names = {
        row.get("receipt")
        for rows in (changed, unsettled, replayed, [expired])
        for row in rows
        if isinstance(row, dict)
    }
    for name in receipt_names:
        if not isinstance(name, str):
            raise ValueError("#297 Recovery receipt reference is invalid")
        verify_receipt(capsule / name)
    if (
        trace.get("schema_version") != 1
        or trace.get("catalogue_version") != CATALOGUE_VERSION
        or {row.get("kind"): row.get("dimension") for row in changed} != dimensions
        or any(
            row.get("effect_count") != 1
            or row.get("before") == row.get("after")
            or row.get("final_outcome") != "resolved"
            or not str(row.get("source", ""))
            for row in changed
        )
        or {row.get("kind") for row in unsettled} != fault_kinds
        or any(
            row.get("effect_count") != 0 or row.get("final_outcome") != "" or row.get("authority_state") != "aborted"
            for row in unsettled
        )
        or {(row.get("probation_outcome"), row.get("final_outcome")) for row in replayed}
        != {("passed", "resolved"), ("failed", "contained")}
        or any(
            row.get("replay_count", 0) < 1
            or row.get("effect_count") != 1
            or row.get("allowance") != 1
            or row.get("consumed_allowance") != 1
            or not row.get("original_deadline")
            for row in replayed
        )
        or expired.get("replay_count", 0) < 1
        or expired.get("effect_count") != 0
        or expired.get("consumed_allowance") != 0
        or expired.get("final_outcome") != "contained"
        or expired.get("original_deadline") != "2026-09-14T00:03:00+00:00"
        or document.get("observed_results")
        != [
            "seven-fixed-probes:pass",
            "changed-actions:pass",
            "cross-boot-probation:pass",
            "bounded-escalation:pass",
            "no-inference-fences:pass",
        ]
        or document.get("scanner_annotation") != "gitleaks:allow"
    ):
        raise ValueError("#297 deterministic Recovery proof is incomplete")


def _verify_299(capsule: Path, _document: dict[str, object], fixed: dict[str, object]) -> None:
    from solver.crypto_tool_receipt import verify_receipt

    receipt = _json(capsule / "tool-crypto.json")
    inventory = (capsule / "inventory.json").read_bytes()
    verify_receipt(receipt, inventory)
    if (
        receipt.get("image_digest") != fixed["image_manifest_digest"]
        or receipt.get("catalogue_digest") != fixed["catalogue_digest"]
        or receipt.get("outcomes") != {"supply": "pass", "solve": "pass", "isolation": "pass"}
        or len(receipt.get("capability_ids", [])) != 9
    ):
        raise ValueError("#299 Crypto profile proof is incomplete")


TICKET_VERIFIERS = {
    269: _verify_269,
    270: _verify_270,
    281: _verify_281,
    283: _verify_283,
    293: _verify_293,
    296: _verify_296,
    297: _verify_297,
    298: _verify_298,
    299: _verify_299,
}


def _verify_semantics(capsule: Path, document: dict[str, object]) -> None:
    try:
        verifier = TICKET_VERIFIERS[document["ticket"]]
    except KeyError as error:
        raise ValueError(f"unsupported evidence ticket: {document['ticket']}") from error
    fixed = document["fixed_point"]
    assert isinstance(fixed, dict)
    verifier(capsule, document, fixed)


def verify_candidate(root: Path, capsules: list[tuple[Path, dict[str, object]]], checkout: Path) -> None:
    manifest = root / "candidate-manifest.json"
    signature = root / "candidate-manifest.sig"
    public = root / "evaluator-public.pem"
    if not manifest.exists():
        raise ValueError("candidate manifest is absent")
    checked = subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-verify",
            "-rawin",
            "-pubin",
            "-inkey",
            str(public),
            "-in",
            str(manifest),
            "-sigfile",
            str(signature),
        ],
        capture_output=True,
        check=False,
    )
    if checked.returncode or _digest(public) != TRUSTED_EVALUATOR_KEY_DIGEST:
        raise ValueError("candidate manifest signature is invalid")
    from solver.manifest import parse_manifest

    candidate = parse_manifest(manifest.read_bytes())
    fixed_points = {
        json.dumps(capsule["fixed_point"], sort_keys=True, separators=(",", ":")) for _path, capsule in capsules
    }
    if len(fixed_points) != 1:
        raise ValueError("capsules do not share one complete fixed point")
    rows = {row["row_id"]: row for row in candidate["requirements"]}
    from solver.resident_tool_receipt import manifest_receipt as resident_manifest_receipt

    inventory = (checkout / "tool-supply/generated/inventory.json").read_bytes()
    resident = _json(checkout / "tool-supply/receipts/tool-resident.json")
    resident_receipt = resident_manifest_receipt(resident, inventory)
    manifest_receipts = {row["ref"]: row for row in candidate["receipts"]}
    crypto_capsules = [path for path, capsule in capsules if capsule["ticket"] == 299]
    expected_tool_evidence = ["capsule-content:" + _digest(path / "capsule.json") for path in crypto_capsules]
    if (
        resident.get("image_digest") != candidate["candidate"]["image_digest"]
        or manifest_receipts.get(resident_receipt["ref"]) != resident_receipt
        or rows["core.tool-surface"]["receipt_ref"] != resident_receipt["ref"]
        or rows["core.tool-surface"]["status"] != "implemented"
        or not set(expected_tool_evidence).issubset(rows["core.tool-surface"]["evidence_refs"])
    ):
        raise ValueError("candidate manifest carries stale resident Tool evidence")
    if crypto_capsules:
        from solver.crypto_tool_receipt import manifest_receipt as crypto_manifest_receipt

        crypto = _json(crypto_capsules[0] / "tool-crypto.json")
        receipt = crypto_manifest_receipt(crypto, inventory)
        if manifest_receipts.get(receipt["ref"]) != receipt:
            raise ValueError("candidate manifest carries stale Crypto Tool evidence")
    expected_rows = {
        269: "core.strict-isolation",
        270: "core.strict-isolation",
        281: "core.controlled-proofs",
        283: "core.inference-native",
        293: "core.submission-tail",
        296: "core.deterministic-recovery",
        297: "core.deterministic-recovery",
        298: "core.tool-surface",
        299: "core.tool-surface",
    }
    for path, capsule in capsules:
        fixed = capsule["fixed_point"]
        if candidate["candidate"]["image_digest"] != fixed["image_manifest_digest"]:
            raise ValueError("candidate and capsule image bindings differ")
        selected = candidate["selected_profile"]
        if selected["isolation"]["profile_digest"] != fixed["runtime_profile_digest"]:
            raise ValueError("candidate and capsule runtime-profile bindings differ")
        if selected["tool_policy"] != f"resident-catalogue:{fixed['catalogue_digest']}":
            raise ValueError("candidate and capsule catalogue bindings differ")
        reference = "capsule-content:" + _digest(path / "capsule.json")
        if reference not in rows[expected_rows[capsule["ticket"]]]["evidence_refs"]:
            raise ValueError(f"candidate manifest does not link #{capsule['ticket']} capsule")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capsules", nargs="+", type=Path)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument(
        "--sealed-historical-inputs",
        action="store_true",
        help="verify the signed historical input digest set without requiring its source checkout",
    )
    arguments = parser.parse_args(argv)
    try:
        verified = []
        for capsule in arguments.capsules:
            document = verify_capsule(capsule, None if arguments.sealed_historical_inputs else arguments.checkout)
            verified.append((capsule, document))
            print(f"verified #{document['ticket']} {capsule}")
        tickets = {document["ticket"] for _capsule, document in verified}
        if tickets in ({269, 270, 281, 283, 298}, {293}, {293, 296}, {293, 296, 299}):
            roots = {capsule.parent.resolve() for capsule, _document in verified}
            if len(roots) != 1:
                raise ValueError("capsules do not share one candidate evidence root")
            verify_candidate(next(iter(roots)), verified, arguments.checkout)
            print("verified signed candidate manifest links")
    except (OSError, TypeError, ValueError) as error:
        print(f"verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["verify_capsule"]
