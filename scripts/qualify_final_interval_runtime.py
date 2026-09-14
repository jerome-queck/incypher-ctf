"""Launch the exact Candidate's default Supervisor against the host qualification Board."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import tempfile
import threading
import time
import datetime as dt
from pathlib import Path

import strict_runtime
from scripts.qualification_board import TOKEN, QualificationBoard, server
from solver.event_store_storage import atomic_write, canonical_bytes
from solver.event_store import EventStore
from solver.final_interval_profile import selected_profile_document
from solver.final_interval_evaluator import link_manifest
from solver.manifest import generate_manifest, parse_manifest
from solver.submission.authority import (
    ACCOUNT_POST_INTERVAL_SECONDS,
    SUBMISSION_REQUEST_DEADLINE_SECONDS,
    SUBMISSION_UNCERTAINTY_MARGIN_SECONDS,
)
from scripts.eval_final_interval import evaluate

ROOT = Path(__file__).resolve().parent.parent
STATE_ROOT = ROOT / "state"
AUTH = Path("/Volumes/Working/001 Projects/incypher-ctf/state/codex/auth.json")
QUALIFICATION_WINDOW_SECONDS = 180
QUALIFICATION_CLOCK_RATE = 2
QUALIFICATION_FINAL_SUBMISSION_RESERVE_SECONDS = 60
QUALIFICATION_ATTEMPT_FLOOR_SECONDS = 60
QUALIFICATION_RESTART_HOST_BUDGET_SECONDS = 35
QUALIFICATION_SEEDED_CANDIDATE_COUNT = 2
QUALIFICATION_FINAL_CHANCE_TIMEOUT_SECONDS = 60
QUALIFICATION_TERMINAL_TIMEOUT_SECONDS = 90
TRUST_RECEIPT = {
    "digest": "672b8a4f435628a41eb590ee563c9e395cbebfbc766accf20efc2e028d302c5b",
    "kind": "evaluator-trust-anchor",
    "ref": "trust:evaluator-ed25519",
}


def final_interval_restart_headroom_seconds() -> float:
    """Return simulated seconds between the final-chance threshold and safe wire start.

    The qualification image's cold replacement took about 30 host seconds in the retained
    diagnostic, so the 35-second bound includes measured startup variation. Both seeded Candidates
    must also fit the account pacing interval. The calculation is deliberately tied to the sealed
    reserve and submission safety dials: changing the accelerated clock cannot silently make a
    restart miss the only legal POST start.
    """

    safe_start_window = (
        QUALIFICATION_FINAL_SUBMISSION_RESERVE_SECONDS
        + QUALIFICATION_ATTEMPT_FLOOR_SECONDS
        - SUBMISSION_REQUEST_DEADLINE_SECONDS
        - SUBMISSION_UNCERTAINTY_MARGIN_SECONDS
        - ACCOUNT_POST_INTERVAL_SECONDS * (QUALIFICATION_SEEDED_CANDIDATE_COUNT - 1)
    )
    return safe_start_window - QUALIFICATION_CLOCK_RATE * QUALIFICATION_RESTART_HOST_BUDGET_SECONDS


def _sign(private_key: Path, source: Path, destination: Path) -> None:
    subprocess.run(
        ["openssl", "pkeyutl", "-sign", "-rawin", "-inkey", private_key, "-in", source, "-out", destination],
        check=True,
    )


def _selected_manifest(source: Path, image_digest: str):
    current = parse_manifest(source.read_bytes())
    profile = copy.deepcopy(current["selected_profile"])
    profile["lanes"] = 2
    profile["storage"]["authority_effect_envelopes"][1]["reservation"]["bytes"] = 16_384
    profile["storage"]["maximum_authority_effect_reservation"]["bytes"] = 16_384
    shared = profile["storage"]["shared_authority_pool"]
    shared.update(bytes=524_288, filesystem_objects=512)
    shared["operations"] = {name: 64 for name in shared["operations"]}
    terminal = copy.deepcopy(profile["storage"]["maximum_authority_effect_reservation"])
    terminal.update(bytes=49_152, filesystem_objects=150)
    terminal["operations"] = {name: 15 for name in terminal["operations"]}
    profile["storage"]["terminal_floor"] = terminal
    hard = profile["storage"]["pressure_thresholds"]["hard_remaining"]
    hard.update(bytes=576_440, filesystem_objects=692)
    hard["operations"] = {name: 82 for name in hard["operations"]}
    soft = profile["storage"]["pressure_thresholds"]["soft_remaining"]
    soft.update(bytes=700_000, filesystem_objects=800)
    soft["operations"] = {name: 90 for name in soft["operations"]}
    writable = profile["storage"]["writable_envelope"]
    writable.update(bytes=1_048_576, filesystem_objects=1_024)
    writable["operations"] = {name: 100 for name in writable["operations"]}
    basis = copy.deepcopy(profile)
    basis.pop("profile_digest")
    profile["profile_digest"] = hashlib.sha256(canonical_bytes(basis)).hexdigest()
    receipts = list(current["receipts"])
    if not any(row["kind"] == TRUST_RECEIPT["kind"] for row in receipts):
        receipts.append(TRUST_RECEIPT)
    return generate_manifest(
        image_digest=image_digest,
        release_candidate_profile=profile,
        requirements=current["requirements"],
        receipts=receipts,
    )


def default_entrypoint_command(binding, env_file: Path, state: Path) -> list[str]:
    command = strict_runtime.container_command(
        binding.image_id, env_file=env_file, state=state, preflight_only=False, binding=binding
    )
    restart = command.index("--restart")
    command[restart : restart + 2] = ["--rm"]
    command[-1:-1] = ["--name", "incypher-final-interval-qualification"]
    return command


def prepare_strict_cgroup_command(binding) -> list[str]:
    return [
        "docker",
        "run",
        "--rm",
        "--cgroup-parent",
        strict_runtime.CGROUP_PARENT,
        "--entrypoint",
        "/bin/true",
        binding.image_id,
    ]


def launch_until_terminal(
    command: list[str], receipts: tuple[Path, ...], *, timeout: float = QUALIFICATION_TERMINAL_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess:
    """Observe the default Supervisor's terminal quiescence, then stop PID 1 cleanly."""
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not all(receipt.is_file() for receipt in receipts) and process.poll() is None:
        time.sleep(0.1)
    terminal_observed = all(receipt.is_file() for receipt in receipts)
    subprocess.run(
        ["docker", "stop", "--time", "3", "incypher-final-interval-qualification"],
        check=False,
        capture_output=True,
    )
    stdout, stderr = process.communicate(timeout=5)
    if not terminal_observed:
        raise RuntimeError(f"strict final-interval Candidate did not reach terminal state: {stderr[-5000:]}")
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def launch_until_final_chance_then_crash(
    command: list[str], state: Path, *, timeout: float = QUALIFICATION_FINAL_CHANCE_TIMEOUT_SECONDS
):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + timeout
    observed = []
    while time.monotonic() < deadline and process.poll() is None:
        observed = [
            event.payload["record"]
            for event in EventStore(state, run_id="controlled-final-interval").events()
            if event.event_type == "final-interval.recorded"
        ]
        if "entitlement-spent" in observed:
            break
        time.sleep(0.1)
    if "entitlement-spent" not in observed:
        subprocess.run(["docker", "kill", "incypher-final-interval-qualification"], capture_output=True)
        stdout, stderr = process.communicate(timeout=5)
        raise RuntimeError(f"strict Candidate did not reach final-chance admission: {stderr[-5000:]}")
    subprocess.run(["docker", "kill", "incypher-final-interval-qualification"], check=True, capture_output=True)
    stdout, stderr = process.communicate(timeout=5)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr), tuple(observed)


def qualify(binding, private_key: Path, source_manifest: Path, destination: Path) -> Path:
    if not AUTH.is_file():
        raise RuntimeError("documented local Codex authentication is absent")
    if final_interval_restart_headroom_seconds() <= 0:
        raise RuntimeError("qualification clock cannot leave safe restart headroom for final submissions")
    board = QualificationBoard()
    endpoint = server(board)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    STATE_ROOT.mkdir(exist_ok=True)
    state = Path(tempfile.mkdtemp(prefix="final-interval-qualification-", dir=STATE_ROOT))
    try:
        (state / "codex").mkdir(mode=0o700)
        shutil.copy2(AUTH, state / "codex/auth.json")
        (state / "codex/auth.json").chmod(0o600)
        controls = state / "qualification"
        controls.mkdir(mode=0o700)
        public = subprocess.run(
            ["openssl", "pkey", "-in", private_key, "-pubout"], check=True, capture_output=True
        ).stdout
        atomic_write(controls / "evaluator-public.pem", public)
        manifest = _selected_manifest(source_manifest, binding.image_manifest_digest)
        atomic_write(controls / "candidate-manifest.json", canonical_bytes(manifest) + b"\n")
        _sign(private_key, controls / "candidate-manifest.json", controls / "candidate-manifest.sig")
        final_profile = selected_profile_document(
            manifest,
            signer_public_key_digest=hashlib.sha256(public).hexdigest(),
            window_seconds=QUALIFICATION_WINDOW_SECONDS,
            final_submission_reserve_seconds=QUALIFICATION_FINAL_SUBMISSION_RESERVE_SECONDS,
            attempt_floor_seconds=QUALIFICATION_ATTEMPT_FLOOR_SECONDS,
        )
        atomic_write(controls / "final-interval-profile.json", canonical_bytes(final_profile) + b"\n")
        _sign(private_key, controls / "final-interval-profile.json", controls / "final-interval-profile.sig")
        board_url = f"http://host.docker.internal:{endpoint.server_port}"
        configuration = {
            "image_digest": binding.image_manifest_digest,
            "kind": "exact-image-qualification-clock",
            "anchor_unix_seconds": time.time(),
            "opened_at": "2026-09-22T01:00:00+00:00",
            "rate": QUALIFICATION_CLOCK_RATE,
            "rules": {
                "event": "controlled-final-interval",
                "flag_wrappers": [r"qualification\{[^}]+\}"],
                "prohibitions": [],
                "requires": [],
                "url": board_url,
                "web_search": False,
                "window_seconds": QUALIFICATION_WINDOW_SECONDS,
            },
            "schema_version": 1,
            "seed": "final-interval-v1",
        }
        atomic_write(controls / "configuration.json", canonical_bytes(configuration) + b"\n")
        _sign(private_key, controls / "configuration.json", controls / "configuration.sig")
        environment = state / "qualification.env"
        atomic_write(
            environment,
            "\n".join(
                (
                    "RUN_ID=controlled-final-interval",
                    f"CTFD_URL={board_url}",
                    f"CTFD_API_TOKEN={TOKEN}",
                    "INCYPHER_CANDIDATE_MANIFEST=/state/qualification/candidate-manifest.json",
                    "INCYPHER_CANDIDATE_MANIFEST_SIGNATURE=/state/qualification/candidate-manifest.sig",
                    "INCYPHER_EVALUATOR_PUBLIC_KEY=/state/qualification/evaluator-public.pem",
                    "INCYPHER_FINAL_INTERVAL_PROFILE=/state/qualification/final-interval-profile.json",
                    "INCYPHER_FINAL_INTERVAL_PROFILE_SIGNATURE=/state/qualification/final-interval-profile.sig",
                    "INCYPHER_QUALIFICATION_CLOCK=/state/qualification/configuration.json",
                    "INCYPHER_QUALIFICATION_CLOCK_SIGNATURE=/state/qualification/configuration.sig",
                )
            ).encode()
            + b"\n",
        )
        environment.chmod(0o600)
        subprocess.run(prepare_strict_cgroup_command(binding), check=True, capture_output=True)
        command = default_entrypoint_command(binding, environment, state)
        run = state / "runs/controlled-final-interval"
        host_started = time.monotonic()
        try:
            crashed, pre_restart_records = launch_until_final_chance_then_crash(
                command, state, timeout=QUALIFICATION_FINAL_CHANCE_TIMEOUT_SECONDS
            )
            completed = launch_until_terminal(
                command,
                (
                    run / "final-interval.receipt.json",
                    run / "canonical" / "serial-submission.receipt.json",
                    run / "canonical" / "ambiguous-submission.receipt.json",
                ),
                timeout=QUALIFICATION_TERMINAL_TIMEOUT_SECONDS,
            )
        except RuntimeError:
            diagnostic = Path(tempfile.mkdtemp(prefix="final-interval-failure-", dir=ROOT / ".cache"))
            if (state / "runs").is_dir():
                shutil.copytree(state / "runs", diagnostic / "runs")
            raise
        endpoint.shutdown()
        thread.join(timeout=5)
        destination.mkdir(parents=True, exist_ok=True)
        canonical = run / "canonical"
        if not (run / "final-interval.receipt.json").is_file():
            raise RuntimeError(
                f"strict final-interval Candidate failed ({completed.returncode}): {completed.stderr[-1000:]}"
            )
        for name, source in (
            ("final-interval.receipt.json", run / "final-interval.receipt.json"),
            ("serial-submission.receipt.json", canonical / "serial-submission.receipt.json"),
            ("ambiguous-submission.receipt.json", canonical / "ambiguous-submission.receipt.json"),
        ):
            if source.is_file():
                shutil.copy2(source, destination / name)
        receipt = json.loads((run / "final-interval.receipt.json").read_bytes())
        host_terminal_elapsed = time.monotonic() - host_started
        sanitized_requests = []
        for request in board.observations:
            body = request["body"]
            sanitized = {
                **{key: value for key, value in request.items() if key != "body"},
                "body_digest": hashlib.sha256(canonical_bytes(body)).hexdigest() if body is not None else "",
            }
            if request["method"] == "POST" and request["path"] == "/api/v1/challenges/attempt":
                sanitized.update(
                    challenge_id=int(body["challenge_id"]),
                    candidate_digest=hashlib.sha256(str(body["submission"]).encode()).hexdigest(),
                )
            sanitized_requests.append(sanitized)
        board_posts = [
            {key: request[key] for key in ("body_digest", "candidate_digest", "challenge_id")}
            for request in sanitized_requests
            if request["method"] == "POST" and request["path"] == "/api/v1/challenges/attempt"
        ]
        observation = {
            "board_requests": sanitized_requests,
            "container": {
                "default_entrypoint": ["python3", "-m", "solver.supervisor"],
                "exit_code": completed.returncode,
                "image_config_digest": binding.image_config_digest,
                "image_id": binding.image_id,
                "image_manifest_digest": binding.image_manifest_digest,
                "strict_profile": True,
            },
            "instances_after": list(board.instances.values()),
            "schema_version": 1,
            "submission_count": len(board.submissions),
            "restart": {
                "first_exit_code": crashed.returncode,
                "launches": 2,
                "records_before_crash": list(pre_restart_records),
            },
        }
        atomic_write(destination / "production-observation.json", canonical_bytes(observation) + b"\n")
        atomic_write(
            destination / "host-observation.json",
            canonical_bytes(
                {
                    "board_posts": board_posts,
                    "released_instance_challenges": sorted(
                        int(request["path"].rsplit("=", 1)[-1])
                        for request in board.observations
                        if request["method"] == "DELETE"
                        and request["path"].startswith("/api/v1/plugins/ctfd-chall-manager/instance?")
                    ),
                    "instances_after": sorted(board.instances),
                    "observed_close_at": (
                        dt.datetime.fromisoformat(configuration["opened_at"])
                        + dt.timedelta(seconds=host_terminal_elapsed * configuration["rate"])
                    ).isoformat(),
                    "run_id": receipt["run_id"],
                    "schema_version": 1,
                }
            )
            + b"\n",
        )
        for name in (
            "configuration.json",
            "configuration.sig",
            "candidate-manifest.json",
            "candidate-manifest.sig",
            "final-interval-profile.json",
            "final-interval-profile.sig",
            "evaluator-public.pem",
        ):
            shutil.copy2(controls / name, destination / name)
        try:
            evaluator = evaluate(destination, manifest, private_key, destination / "final-interval.evaluator.json")
        except Exception:
            diagnostic = Path(tempfile.mkdtemp(prefix="final-interval-failure-", dir=ROOT / ".cache"))
            shutil.copytree(run, diagnostic / run.name)
            atomic_write(diagnostic / "container.stdout", completed.stdout.encode())
            atomic_write(diagnostic / "container.stderr", completed.stderr.encode())
            raise
        linked = link_manifest(manifest, evaluator)
        atomic_write(destination / "candidate-manifest.json", canonical_bytes(linked) + b"\n")
        _sign(private_key, destination / "candidate-manifest.json", destination / "candidate-manifest.sig")
        return destination
    finally:
        endpoint.shutdown()
        thread.join(timeout=5)
        subprocess.run(
            ["colima", "ssh", "--", "sudo", "rmdir", strict_runtime.CGROUP_SOURCE],
            check=False,
            capture_output=True,
        )
