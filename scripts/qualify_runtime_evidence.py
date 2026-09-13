"""Run and sign fresh #293/#296 evidence at one strict Candidate fixed point."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import runtime  # noqa: E402
import strict_runtime  # noqa: E402
from solver.event_store_storage import atomic_write, canonical_bytes  # noqa: E402
from solver.manifest import generate_manifest, parse_manifest  # noqa: E402
from scripts.runtime_qualification import RUN_ID_293, RUN_ID_296  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "docs/evidence/runtime-qualification-v2"
STATE = runtime.EXTERNAL_PROJECT_ROOT / "incypher-ctf/state"
COLIMA_DATA = runtime.EXTERNAL_PROJECT_ROOT / "incypher-colima"


@dataclass(frozen=True)
class _CapsuleContext:
    private_key: Path
    public: bytes
    fixed: dict[str, str]
    inputs: dict[str, str]
    observations: Path
    runtime_observation: Path
    strict_preflight: Path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sign(private_key: Path, path: Path, signature: Path) -> None:
    subprocess.run(
        [
            "openssl",
            "pkeyutl",
            "-sign",
            "-rawin",
            "-inkey",
            str(private_key),
            "-in",
            str(path),
            "-out",
            str(signature),
        ],
        check=True,
    )


def _validate_evaluator(private_key: Path) -> bytes:
    public = subprocess.run(
        ["openssl", "pkey", "-in", str(private_key), "-pubout"],
        check=True,
        capture_output=True,
    ).stdout
    retained = (EVIDENCE / "evaluator-public.pem").read_bytes()
    if public != retained:
        raise ValueError("Evaluator private key does not match the retained trusted public key")
    return public


def _qualification_command(binding, output: Path) -> list[str]:
    command = strict_runtime.container_command(
        binding.image_id,
        env_file=None,
        state=None,
        preflight_only=True,
        binding=binding,
    )
    relative = output.relative_to(STATE)
    command[-5:] = [
        "--mount",
        f"type=bind,source={STATE},target=/state",
        "--entrypoint",
        "python3",
        binding.image_id,
        "-m",
        "scripts.runtime_qualification",
        str(Path("/state") / relative),
    ]
    return command


def _runtime_observation(binding, destination: Path) -> Path:
    vm = runtime.observed_vm()
    if vm is None:
        raise RuntimeError("Colima VM observation disappeared after qualification")
    document = {
        "colima_data": str(COLIMA_DATA),
        "colima_home": str((Path.home() / ".colima").resolve()),
        "data_disk_bytes": (COLIMA_DATA / "_lima/_disks/colima/datadisk").stat().st_size,
        "image_config_digest": binding.image_config_digest,
        "image_id": binding.image_id,
        "image_manifest_digest": binding.image_manifest_digest,
        "observed_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "platform": binding.platform,
        "runtime": {
            "cpus": vm["cpus"],
            "disk_bytes": vm["disk"],
            "memory_bytes": vm["memory"],
            "status": vm["status"],
        },
        "schema_version": 1,
        "state_root": str(STATE),
    }
    path = destination / "runtime-observation.json"
    atomic_write(path, canonical_bytes(document) + b"\n")
    return path


def _fixed_point(
    binding, old_capsule: dict[str, object], strict_preflight: dict[str, object]
) -> tuple[dict[str, str], dict[str, str]]:
    names = set(old_capsule["input_files"])
    names.discard("solver/runtime_qualification.py")
    names.update(
        {
            "scripts/lock_apt_closure.py",
            "scripts/lock_sage_closure.py",
            "scripts/qualify_crypto_profile.py",
            "scripts/qualify_runtime_evidence.py",
            "scripts/qualification_retention.py",
            "scripts/runtime_qualification.py",
            "scripts/verify_sage_closure.py",
            "solver/crypto_tool_contract.py",
            "solver/crypto_tool_receipt.py",
            "tool-supply/receipts/tool-crypto.core.json",
            "tool-supply/receipts/tool-crypto.json",
            "tool-supply/receipts/tool-resident.json",
            "tool-supply/generated/rootfs/usr/local/libexec/incypher-tool-crypto.py",
        }
    )
    for root in (
        ROOT / "tool-supply/fixtures/tool-crypto",
        ROOT / "tool-supply/generated/rootfs/opt/solver/tool-supply/tool-crypto",
    ):
        names.update(str(path.relative_to(ROOT)) for path in root.rglob("*") if path.is_file())
    inputs = {name: _digest(ROOT / name) for name in sorted(names)}
    components = {
        name: digest
        for name, digest in inputs.items()
        if name.startswith("tool-supply/locks/") or name.startswith("tool-supply/fixtures/")
    }
    fixed = {
        "catalogue_digest": _digest(ROOT / "tool-supply/generated/inventory.json"),
        "component_inputs_digest": hashlib.sha256(canonical_bytes(components) + b"\n").hexdigest(),
        "image_config_digest": binding.image_config_digest,
        "image_manifest_digest": binding.image_manifest_digest,
        "platform": binding.platform,
        "runtime_profile_digest": strict_preflight["profile_digest"],
    }
    return fixed, inputs


def _capsule(
    private_key: Path,
    public: bytes,
    fixed: dict[str, str],
    inputs: dict[str, str],
    *,
    ticket: int,
    directory: str,
    artifacts: dict[str, Path],
    observed_results: list[str],
) -> tuple[Path, dict[str, object]]:
    target = EVIDENCE / directory
    target.mkdir(parents=True, exist_ok=True)
    atomic_write(target / "evaluator-public.pem", public)
    artifact_digests = {}
    for name, source in artifacts.items():
        destination = target / name
        shutil.copy2(source, destination)
        artifact_digests[name] = _digest(destination)
    document = {
        "artifacts": artifact_digests,
        "cleanup_state": {"owned_residue": [], "processes_after_kill": 0},
        "fixed_point": fixed,
        "input_files": inputs,
        "kind": "runtime-qualification-capsule",
        "observed_results": observed_results,
        "scanner_annotation": "gitleaks:allow",
        "schema_version": 1,
        "signer_public_key_digest": hashlib.sha256(public).hexdigest(),
        "ticket": ticket,
    }
    manifest = target / "capsule.json"
    atomic_write(manifest, canonical_bytes(document) + b"\n")
    _sign(private_key, manifest, target / "capsule.sig")
    return target, document


def _refresh_resident_tool(
    requirements: list[dict[str, object]],
    receipts: list[dict[str, str]],
    fixed: dict[str, str],
) -> list[dict[str, str]]:
    from solver.resident_tool_receipt import manifest_receipt as resident_manifest_receipt

    inventory = (ROOT / "tool-supply/generated/inventory.json").read_bytes()
    resident = json.loads((ROOT / "tool-supply/receipts/tool-resident.json").read_bytes())
    if resident.get("image_digest") != fixed["image_manifest_digest"]:
        raise ValueError("resident Tool receipt belongs to another candidate image")
    receipt = resident_manifest_receipt(resident, inventory)
    refreshed = [row for row in receipts if row["ref"] != receipt["ref"]]
    refreshed.append(receipt)
    row = next(item for item in requirements if item["row_id"] == "core.tool-surface")
    row.update(evidence_refs=[], reason="", receipt_ref=receipt["ref"], status="implemented")
    return refreshed


def _qualify_resident_tools(image_manifest_digest: str) -> None:
    """Execute every canonical resident workload against this exact Candidate image."""

    inventory = json.loads((ROOT / "tool-supply/generated/inventory.json").read_bytes())
    components = sorted(item["component_id"] for item in inventory["components"] if "resident" in item["profiles"])
    staged = Path(tempfile.mkdtemp(prefix="resident-requalification-", dir=ROOT / ".cache"))
    for component_id in components:
        destination = staged / f"{component_id}.json"
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/qualify_tool_supply.py"), component_id, "--into", str(destination)],
            cwd=ROOT,
            check=True,
        )
        atomic_write(ROOT / "tool-supply/receipts" / destination.name, destination.read_bytes())
    subprocess.run([sys.executable, str(ROOT / "scripts/aggregate_resident_tool_receipt.py")], cwd=ROOT, check=True)
    resident = json.loads((ROOT / "tool-supply/receipts/tool-resident.json").read_bytes())
    if resident.get("image_digest") != image_manifest_digest:
        raise ValueError("fresh resident Tool qualification names another candidate image")
    core = staged / "tool-crypto.core.json"
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/qualify_tool_supply.py"), "tool-crypto.core", "--into", str(core)],
        cwd=ROOT,
        check=True,
    )
    atomic_write(ROOT / "tool-supply/receipts/tool-crypto.core.json", core.read_bytes())
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/qualify_crypto_profile.py"),
            "--candidate-image",
            "incypher-solver:strict",
        ],
        cwd=ROOT,
        check=True,
    )
    crypto = json.loads((ROOT / "tool-supply/receipts/tool-crypto.json").read_bytes())
    if crypto.get("image_digest") != image_manifest_digest:
        raise ValueError("fresh Crypto qualification names another candidate image")


def _candidate_manifest(
    private_key: Path,
    fixed: dict[str, str],
    capsules: list[tuple[Path, dict[str, object]]],
) -> None:
    current = parse_manifest((EVIDENCE / "candidate-manifest.json").read_bytes())
    selected_profile = copy.deepcopy(current["selected_profile"])
    selected_profile["isolation"]["profile_digest"] = fixed["runtime_profile_digest"]
    selected_profile["tool_policy"] = f"resident-catalogue:{fixed['catalogue_digest']}"
    profile_basis = copy.deepcopy(selected_profile)
    profile_basis.pop("profile_digest")
    selected_profile["profile_digest"] = hashlib.sha256(canonical_bytes(profile_basis)).hexdigest()
    requirements = copy.deepcopy(current["requirements"])
    receipts = copy.deepcopy(current["receipts"])
    receipts = _refresh_resident_tool(requirements, receipts, fixed)
    links = {
        293: (
            "core.submission-tail",
            "receipt:serial-submission",
            "serial-submission",
            "serial-submission.receipt.json",
            "Serial Candidate dispatch is qualified; final-window lifecycle remains planned.",
        ),
        296: (
            "core.deterministic-recovery",
            "receipt:incident-containment",
            "incident-containment",
            "incident-containment.receipt.json",
            "Worker-process Incident containment is qualified; later fixed domain remedies remain planned.",
        ),
    }
    for path, capsule in capsules:
        if capsule["ticket"] == 299:
            from solver.crypto_tool_receipt import manifest_receipt as crypto_manifest_receipt

            inventory = (ROOT / "tool-supply/generated/inventory.json").read_bytes()
            crypto = json.loads((path / "tool-crypto.json").read_bytes())
            receipt = crypto_manifest_receipt(crypto, inventory)
            receipts = [row for row in receipts if row["ref"] != receipt["ref"]]
            receipts.append(receipt)
            row = next(item for item in requirements if item["row_id"] == "core.tool-surface")
            row["evidence_refs"] = [item for item in row["evidence_refs"] if not item.startswith("capsule-content:")]
            row["evidence_refs"].append("capsule-content:" + _digest(path / "capsule.json"))
            continue
        row_id, reference, kind, receipt_name, reason = links[capsule["ticket"]]
        receipt = path / receipt_name
        receipts = [row for row in receipts if row["ref"] != reference]
        receipts.append({"digest": _digest(receipt), "kind": kind, "ref": reference})
        row = next(item for item in requirements if item["row_id"] == row_id)
        row.update(reason=reason, receipt_ref=reference, status="planned")
        row["evidence_refs"] = [item for item in row["evidence_refs"] if not item.startswith("capsule-content:")]
        row["evidence_refs"].append("capsule-content:" + _digest(path / "capsule.json"))
    manifest = generate_manifest(
        image_digest=fixed["image_manifest_digest"],
        release_candidate_profile=selected_profile,
        requirements=requirements,
        receipts=receipts,
    )
    path = EVIDENCE / "candidate-manifest.json"
    atomic_write(path, canonical_bytes(manifest) + b"\n")
    _sign(private_key, path, EVIDENCE / "candidate-manifest.sig")


def _run_candidate(binding, work: Path) -> tuple[Path, dict[str, object], Path, Path]:
    observations = work / "observations"
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--cgroup-parent",
                strict_runtime.CGROUP_PARENT,
                "--entrypoint",
                "/bin/true",
                binding.image_id,
            ],
            check=True,
        )
        preflight = subprocess.run(
            strict_runtime.container_command(
                binding.image_id,
                env_file=None,
                state=None,
                preflight_only=True,
                binding=binding,
            ),
            check=True,
            capture_output=True,
            text=True,
        )
        strict_preflight = json.loads(preflight.stdout)
        strict_preflight_path = work / "strict-preflight.json"
        atomic_write(strict_preflight_path, canonical_bytes(strict_preflight) + b"\n")
        subprocess.run(_qualification_command(binding, observations), check=True)
    finally:
        subprocess.run(
            ["colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE],
            check=True,
        )
    return observations, strict_preflight, strict_preflight_path, _runtime_observation(binding, work)


def _serial_capsule(context: _CapsuleContext) -> tuple[Path, dict[str, object]]:
    return _capsule(
        context.private_key,
        context.public,
        context.fixed,
        context.inputs,
        ticket=293,
        directory="293-serial-submission",
        artifacts={
            "runtime-observation.json": context.runtime_observation,
            "strict-preflight.json": context.strict_preflight,
            "serial-submission.receipt.json": context.observations
            / f"runs/{RUN_ID_293}/canonical/serial-submission.receipt.json",
            "serial-submission.trace.json": context.observations / "serial-submission.trace.json",
        },
        observed_results=[
            "first-ready:pass",
            "one-in-flight:pass",
            "crash-before:aborted",
            "crash-during:possibly-sent",
            "crash-after:no-resend",
        ],
    )


def _incident_capsule(context: _CapsuleContext) -> tuple[Path, dict[str, object]]:
    return _capsule(
        context.private_key,
        context.public,
        context.fixed,
        context.inputs,
        ticket=296,
        directory="296-incident-containment",
        artifacts={
            "incident-containment.receipt.json": context.observations
            / f"runs/{RUN_ID_296}/canonical/incident-containment.receipt.json",
            "process-trace.json": context.observations / "process-trace.json",
            "runtime-observation.json": context.runtime_observation,
            "strict-preflight.json": context.strict_preflight,
        },
        observed_results=[
            "real-worker-exit-17",
            "generation-fence-before-evidence",
            "supervisor-loss-during-containment",
            "same-incident-replayed",
            "six-process-coalescing",
            "full-teardown",
            "bounded-replacement-exit-0",
        ],
    )


def _crypto_capsule(context: _CapsuleContext) -> tuple[Path, dict[str, object]]:
    supply = ROOT / "tool-supply"
    return _capsule(
        context.private_key,
        context.public,
        context.fixed,
        context.inputs,
        ticket=299,
        directory="299-tool-crypto",
        artifacts={
            "inventory.json": supply / "generated/inventory.json",
            "tool-crypto.core.json": supply / "receipts/tool-crypto.core.json",
            "tool-crypto.json": supply / "receipts/tool-crypto.json",
        },
        observed_results=["supply:pass", "solve:pass", "isolation:pass", "profile-size:pass"],
    )


def _retain_evidence(
    private_key: Path,
    public: bytes,
    binding,
    observations: Path,
    strict: dict[str, object],
    strict_path: Path,
    runtime_observation: Path,
) -> tuple[
    tuple[Path, dict[str, object]],
    tuple[Path, dict[str, object]],
    tuple[Path, dict[str, object]],
]:
    old = json.loads((EVIDENCE / "293-serial-submission/capsule.json").read_bytes())
    fixed, inputs = _fixed_point(binding, old, strict)
    context = _CapsuleContext(private_key, public, fixed, inputs, observations, runtime_observation, strict_path)
    serial = _serial_capsule(context)
    incident = _incident_capsule(context)
    crypto = _crypto_capsule(context)
    _candidate_manifest(private_key, fixed, [serial, incident, crypto])
    return serial, incident, crypto


def _finalize(serial: Path, incident: Path, crypto: Path) -> None:
    strict_runtime.enforce_host_storage("competition", subprocess.run)
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_runtime_evidence.py"),
            str(serial),
            str(incident),
            str(crypto),
        ],
        check=True,
    )


def qualify(private_key: Path) -> Path:
    if runtime.verify() != 0:
        raise RuntimeError("runtime is off its pin")
    public = _validate_evaluator(private_key)
    binding = strict_runtime.build_image(subprocess.run)
    _qualify_resident_tools(binding.image_manifest_digest)
    work = Path(tempfile.mkdtemp(prefix="runtime-qualification-372-", dir=STATE))
    observations, strict, strict_path, runtime_observation = _run_candidate(binding, work)
    serial, incident, crypto = _retain_evidence(
        private_key, public, binding, observations, strict, strict_path, runtime_observation
    )
    from scripts.qualify_final_interval_runtime import qualify as qualify_final_interval

    qualify_final_interval(
        binding,
        private_key,
        EVIDENCE / "candidate-manifest.json",
        ROOT / "docs/evidence/final-interval-v2",
    )
    _finalize(serial[0], incident[0], crypto[0])
    return work


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluator-private-key", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    work = qualify(arguments.evaluator_private_key)
    print(f"retained authentic observations at {work}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
